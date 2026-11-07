from __future__ import annotations

import threading
import time
import os
import signal

import pytest

from wrf_webvis.app.services.progress import JobConcurrencyLimiter


def _blocked_process_worker(progress):
    """A deliberately non-cooperative native-style operation."""
    progress("native renderer entered")
    while True:
        time.sleep(1)


def _failing_process_worker(_progress):
    raise ValueError("bad render")


def _crashing_process_worker(_progress):
    os._exit(17)


def _term_ignoring_process_worker(progress):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    progress("native renderer entered")
    while True:
        time.sleep(1)


# 功能说明：占满三个任务名额后提交第四个，确认它等待其中一个任务结束才开始。
def test_limiter_queues_a_fourth_job_until_one_of_three_slots_is_released():
    limiter = JobConcurrencyLimiter(max_concurrent=3)
    fourth_job_started = threading.Event()

    # 功能说明：标记第四个任务已经得到执行，用来观察排队是否解除。
    def run_fourth_job() -> None:
        with limiter.acquire():
            fourth_job_started.set()

    with limiter.acquire(), limiter.acquire(), limiter.acquire():
        thread = threading.Thread(target=run_fourth_job)
        thread.start()
        assert not fourth_job_started.wait(timeout=0.1)

    assert fourth_job_started.wait(timeout=1)
    thread.join(timeout=1)
    assert not thread.is_alive()


# 功能说明：同时从直接绘图入口启动任务，确认它们也受后台并发上限约束。
def test_direct_plot_calls_share_the_background_job_limit(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import pytest
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.plot_service import PlotService
    from wrf_webvis.app.services.progress import job_concurrency_limiter, JobCancelled

    entered = threading.Event()
    service = PlotService(tmp_path)
    # 功能说明：记录一次任务真正开始，供测试判断是否超过并发名额。
    def begin(request):
        entered.set()
        raise JobCancelled('test complete')
    monkeypatch.setattr(service, '_apply_fig_defaults', begin)
    with ThreadPoolExecutor(1) as executor:
        with job_concurrency_limiter.acquire(), job_concurrency_limiter.acquire(), job_concurrency_limiter.acquire():
            future = executor.submit(service.create_plot, PlotRequest())
            assert not entered.wait(.2)
        with pytest.raises(JobCancelled):
            future.result(timeout=2)


# 功能说明：在更新进度的同时多次读取，确认每次读取都是完整稳定的快照。
def test_progress_reads_are_stable_snapshots():
    from wrf_webvis.app.services.progress import ProgressStore
    store = ProgressStore()
    store.start('test', 'snapshot')
    snapshot = store.get('test')
    store.add('test', 'new message')
    assert len(snapshot['messages']) == 1


# 功能说明：原生渲染长时间不返回时，取消请求也必须在有限时间内回收子进程。
def test_isolated_runner_terminates_a_blocked_worker_after_cancellation():
    from wrf_webvis.app.services.isolated_job_runner import run_isolated_job
    from wrf_webvis.app.services.progress import JobCancelled

    cancelled = threading.Event()
    entered = threading.Event()

    def progress(message: str) -> None:
        if message == "native renderer entered":
            entered.set()
        if cancelled.is_set():
            raise JobCancelled("任务已终止")

    def request_cancel() -> None:
        assert entered.wait(timeout=3)
        cancelled.set()

    def check_cancelled() -> None:
        if cancelled.is_set():
            raise JobCancelled("任务已终止")

    canceller = threading.Thread(target=request_cancel)
    canceller.start()
    started = time.monotonic()
    with pytest.raises(JobCancelled):
        run_isolated_job(
            _blocked_process_worker,
            progress=progress,
            cancel_check=check_cancelled,
            poll_interval=0.02,
        )
    assert time.monotonic() - started < 3
    canceller.join(timeout=1)


# 功能说明：子进程异常应回传给父进程并让任务记录失败原因，而非静默挂起。
def test_isolated_runner_forwards_worker_errors():
    from wrf_webvis.app.services.isolated_job_runner import run_isolated_job

    with pytest.raises(RuntimeError, match="bad render"):
        run_isolated_job(_failing_process_worker, poll_interval=0.02)


# 功能说明：子进程未回传消息就退出时，父进程仍要报告退出码而非卡在管道读取。
def test_isolated_runner_reports_an_unexpected_child_exit():
    from wrf_webvis.app.services.isolated_job_runner import run_isolated_job

    with pytest.raises(RuntimeError, match="exit code 17"):
        run_isolated_job(_crashing_process_worker, poll_interval=0.02)


# 功能说明：子进程忽略常规终止信号时，取消请求仍使用强制结束作为有界后备方案。
def test_isolated_runner_kills_a_worker_that_ignores_termination():
    from wrf_webvis.app.services.isolated_job_runner import run_isolated_job
    from wrf_webvis.app.services.progress import JobCancelled

    entered = threading.Event()
    cancelled = threading.Event()

    def progress(message: str) -> None:
        if message == "native renderer entered":
            entered.set()

    def cancel_check() -> None:
        if cancelled.is_set():
            raise JobCancelled("任务已终止")

    canceller = threading.Thread(target=lambda: (entered.wait(3), cancelled.set()))
    canceller.start()
    with pytest.raises(JobCancelled):
        run_isolated_job(
            _term_ignoring_process_worker,
            progress=progress,
            cancel_check=cancel_check,
            poll_interval=0.02,
        )
    canceller.join(timeout=1)


# 功能说明：两个异步 API 入口都委托给隔离运行器，使原生绘图不能卡住网页进程。
def test_async_plot_endpoints_use_the_isolated_runner(monkeypatch):
    from wrf_webvis.app.api import dashboards, plots
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.progress import progress_store

    calls = []

    def run(worker, **kwargs):
        calls.append((worker, kwargs))
        return {"plot_id": "isolated", "static_path": "/static/isolated.png"}

    monkeypatch.setattr(plots, "run_isolated_job", run)
    monkeypatch.setattr(dashboards, "run_isolated_job", run)
    monkeypatch.setattr(dashboards.store, "add_image", lambda _id, image: image)
    request = PlotRequest(title="isolated")
    progress_store.start("isolated-plot", "plot")
    progress_store.start("isolated-image", "image")

    plots._run_plot_job(request, "isolated-plot")
    dashboards._run_image_job("dashboard", request, "isolated-image")

    assert len(calls) == 2
    assert progress_store.get("isolated-plot")["status"] == "complete"
    assert progress_store.get("isolated-image")["status"] == "complete"


def test_plot_child_does_not_acquire_second_parent_job_slot(monkeypatch):
    from wrf_webvis.app.api import dashboards, plots
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.plot_service import PlotService

    monkeypatch.setattr(PlotService, "create_plot", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("nested slot")))
    monkeypatch.setattr(PlotService, "_create_plot", lambda _self, _request, progress=None: {"ok": True})
    request = PlotRequest()
    assert plots._create_plot_in_child(request, None) == {"ok": True}
    assert dashboards._create_plot_in_child(request, None) == {"ok": True}
