from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from wrf_webvis.app.core.schemas import PlotRequest
from wrf_webvis.app.services.plot_service import PlotService
from wrf_webvis.app.services.progress import JobCancelled, callback_for_job, progress_store


def request(code, **kwargs):
    return PlotRequest(fig_type=9, custom_code=code, title="custom", dpi=80, **kwargs)


@pytest.fixture(autouse=True)
def isolated_progress(monkeypatch):
    import sys
    from wrf_webvis.app.services import progress
    from wrf_webvis.app.api import dashboards, plots
    store = progress.ProgressStore()
    monkeypatch.setattr(progress, "progress_store", store)
    monkeypatch.setattr(sys.modules[__name__], "progress_store", store)
    monkeypatch.setattr(plots, "progress_store", store)
    monkeypatch.setattr(dashboards, "progress_store", store)


def test_custom_calls_share_the_existing_capacity_limit(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from wrf_webvis.app.services.progress import job_concurrency_limiter
    entered = threading.Event()
    service = PlotService(tmp_path)
    def render(req, progress=None):
        entered.set()
        return {"ok": True}
    monkeypatch.setattr(service, "_create_plot", render)
    with ThreadPoolExecutor(1) as executor:
        with job_concurrency_limiter.acquire(), job_concurrency_limiter.acquire(), job_concurrency_limiter.acquire():
            future = executor.submit(service.create_plot, request("pass"))
            assert not entered.wait(.2)
        assert future.result(timeout=2) == {"ok": True}


@pytest.fixture
def conda(monkeypatch):
    executable = os.environ.get("CONDA_EXE") or shutil.which("conda")
    if not executable:
        pytest.skip("Conda is required for custom-code integration tests")
    monkeypatch.setenv("WRF_WEBVIS_CONDA_EXE", executable)
    return executable


def test_custom_code_is_saved_in_request_and_changes_hash():
    first = request("print('one')")
    assert first.model_dump()["custom_code"] == "print('one')"
    assert PlotService().plot_id(first) != PlotService().plot_id(request("print('two')"))


def test_fixed_plot_hash_remains_compatible_with_requests_before_custom_code():
    import hashlib
    import json
    from wrf_webvis.app.services.plot_service import _jsonable
    fixed = PlotRequest(title="existing plot")
    previous_payload = _jsonable(fixed.model_dump())
    previous_payload.pop("custom_code", None)
    # 2026-11-02 新增的 center_schemes 字段为空时不参与编号，旧请求的编号保持不变。
    previous_payload.pop("center_schemes", None)
    previous_hash = hashlib.sha256(json.dumps(previous_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    assert PlotService().plot_id(fixed) == previous_hash


def test_conda_is_located_with_the_launchagent_minimal_path(monkeypatch):
    from wrf_webvis.app.services.custom_code import conda_executable
    monkeypatch.delenv("WRF_WEBVIS_CONDA_EXE", raising=False)
    monkeypatch.delenv("CONDA_EXE", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert conda_executable().endswith("/bin/conda")


def test_custom_runs_reuse_persistent_matplotlib_cache(tmp_path, monkeypatch):
    from wrf_webvis.app.services import custom_code

    monkeypatch.setattr(custom_code, "conda_executable", lambda: "/fake/conda")
    monkeypatch.setattr(custom_code, "REPOSITORY_ROOT", tmp_path / "project")
    monkeypatch.delenv("MPLCONFIGDIR", raising=False)
    environments = []
    source_paths = []

    def fake_run(command, *, cwd, environment, timeout, progress):
        cache = Path(environment["MPLCONFIGDIR"])
        cache.mkdir(parents=True, exist_ok=True)
        sentinel = cache / "font-cache-sentinel"
        environments.append(environment["MPLCONFIGDIR"])
        source_paths.append(Path(command[-4]))
        seen = sentinel.read_text() if sentinel.exists() else ""
        sentinel.write_text(seen + "x")

    monkeypatch.setattr(custom_code, "_run", fake_run)
    req = request("pass")
    custom_code.run_custom_code(req, tmp_path, "hash", check_only=True)
    custom_code.run_custom_code(req, tmp_path, "hash", check_only=True)

    assert environments == [environments[0], environments[0]]
    assert environments[0].endswith(".runtime/matplotlib")
    assert (Path(environments[0]) / "font-cache-sentinel").read_text() == "xx"
    assert source_paths[0] != source_paths[1]
    assert not source_paths[0].exists()
    assert not source_paths[1].exists()


def test_custom_runs_honor_inherited_matplotlib_cache(tmp_path, monkeypatch):
    from wrf_webvis.app.services import custom_code

    configured = tmp_path / "configured-cache"
    monkeypatch.setattr(custom_code, "conda_executable", lambda: "/fake/conda")
    monkeypatch.setenv("MPLCONFIGDIR", str(configured))
    seen = []
    monkeypatch.setattr(custom_code, "_run", lambda *args, **kwargs: seen.append(kwargs["environment"]["MPLCONFIGDIR"]))
    custom_code.run_custom_code(request("pass"), tmp_path, "hash", check_only=True)
    assert seen == [str(configured)]


@pytest.mark.parametrize("code", [None, "", " \n\t"])
def test_custom_code_requires_nonempty_source(code):
    with pytest.raises(ValueError, match="custom_code"):
        request(code)


def test_auto_capture_show_main_and_environment(tmp_path, conda):
    from wrf_webvis.app.core.config import REPOSITORY_ROOT
    code = """import sys, os
import matplotlib.pyplot as plt
assert sys.prefix.endswith('/envs/wrf-webvis'), sys.prefix
assert os.getcwd() == REPOSITORY
if __name__ == '__main__':
    plt.plot([1, 2, 3], [3, 1, 2])
    print('script executed', flush=True)
    plt.show()
""".replace("REPOSITORY", repr(str(REPOSITORY_ROOT)))
    messages = []
    service = PlotService(tmp_path)
    first = service.create_plot(request(code, data_root=str(tmp_path / "no-wrf-data")), progress=messages.append)
    second = service.create_plot(request(code))
    assert first["static_path"].endswith(".png")
    assert first["image_path"] != second["image_path"]
    assert first["metadata"]["conda_env"] == "wrf-webvis"
    assert first["metadata"]["python_executable"].endswith("wrf-webvis/bin/python")
    assert any("script executed" in message for message in messages)
    assert Image.open(first["image_path"]).format == "PNG"
    assert first["processed_files"] == []


def test_explicit_output_takes_priority_over_multiple_figures(tmp_path, conda):
    result = PlotService(tmp_path).create_plot(request("""from PIL import Image
import matplotlib.pyplot as plt
plt.figure()
plt.figure()
Image.new('RGB', (12, 8), 'red').save(OUTPUT_PATH)
"""))
    with Image.open(result["image_path"]) as image:
        assert image.size == (12, 8)


@pytest.mark.parametrize("code, error", [
    ("print('no figure')", "没有"),
    ("import matplotlib.pyplot as plt\nplt.figure()\nplt.figure()", "OUTPUT_PATH"),
    ("open(OUTPUT_PATH, 'w').write('not a png')", "PNG"),
    ("raise ValueError('intentional failure')", "intentional failure"),
    ("import nonexistent_wrf_webvis_dependency", "ModuleNotFoundError"),
    ("if :", "SyntaxError"),
])
def test_failures_leave_no_published_images(tmp_path, conda, code, error):
    with pytest.raises(RuntimeError, match=error):
        PlotService(tmp_path).create_plot(request(code))
    assert not list(tmp_path.glob("*.png"))


def test_syntax_check_never_executes_code(tmp_path, conda):
    sentinel = tmp_path / "should-not-exist"
    code = f"open({str(sentinel)!r}, 'w').write('executed')\nraise RuntimeError('do not run')"
    result = PlotService(tmp_path).dry_run(request(code))
    assert result["code_checked"] is True
    assert result["plan"]["missing_files"] == []
    assert not sentinel.exists()
    assert not list(tmp_path.glob("*.png"))
    with pytest.raises(RuntimeError, match="SyntaxError"):
        PlotService(tmp_path).dry_run(request("if :"))


def test_missing_conda_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("WRF_WEBVIS_CONDA_EXE", str(tmp_path / "missing-conda"))
    with pytest.raises(RuntimeError, match="Conda"):
        PlotService(tmp_path).create_plot(request("print(1)"))


def test_missing_environment_does_not_fall_back(tmp_path, conda, monkeypatch):
    monkeypatch.setenv("WRF_WEBVIS_CUSTOM_CONDA_ENV", "wrf-webvis-nonexistent-test")
    with pytest.raises(RuntimeError, match="wrf-webvis-nonexistent-test"):
        PlotService(tmp_path).create_plot(request("import matplotlib.pyplot as plt\nplt.plot([1])"))
    assert not list(tmp_path.glob("*.png"))


def test_timeout_kills_process_group_and_publishes_nothing(tmp_path, conda, monkeypatch):
    monkeypatch.setenv("WRF_WEBVIS_CUSTOM_TIMEOUT_SECONDS", "2")
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="超时"):
        PlotService(tmp_path).create_plot(request("while True: pass"))
    assert time.monotonic() - started < 6
    assert not list(tmp_path.glob("*.png"))


def test_cancel_kills_script_and_its_spawned_child(tmp_path, conda):
    pid_file = tmp_path / "child.pid"
    code = f"""import subprocess, sys, time, signal
child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)'])
open({str(pid_file)!r}, 'w').write(str(child.pid))
print('ready to cancel', flush=True)
while True: time.sleep(1)
"""
    job_id = "custom-cancel-test"
    progress_store.start(job_id, "custom", status="queued")

    def cancel_when_ready():
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if pid_file.exists():
                progress_store.cancel(job_id)
                return
            time.sleep(.05)

    canceller = threading.Thread(target=cancel_when_ready)
    canceller.start()
    try:
        with pytest.raises(JobCancelled):
            PlotService(tmp_path).create_plot(request(code), progress=callback_for_job(job_id))
    finally:
        canceller.join(timeout=16)
    assert pid_file.exists()
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(.05)
    else:
        os.kill(pid, 9)
        pytest.fail("script child survived cancellation")
    assert not list(tmp_path.glob("*.png"))


def test_logs_are_bounded_and_traceback_keeps_user_line(tmp_path, conda):
    messages = []
    with pytest.raises(RuntimeError, match="custom_code.py.*") as exc:
        PlotService(tmp_path).create_plot(request("print('x' * 300000)\nraise ValueError('tail failure')"), progress=messages.append)
    assert "line 2" in str(exc.value)
    assert "tail failure" in str(exc.value)
    assert sum(map(len, messages)) < 80000
    assert len(str(exc.value)) < 20000


def test_custom_async_routes_do_not_wrap_the_process_group_in_multiprocessing(monkeypatch):
    from wrf_webvis.app.api import dashboards, plots
    calls = []
    def run(self, req, progress=None):
        calls.append(req.custom_code)
        return {"plot_id": "custom", "static_path": "/static/figures/custom.png"}
    monkeypatch.setattr(PlotService, "create_plot", run)
    monkeypatch.setattr(plots, "run_isolated_job", lambda *_a, **_k: pytest.fail("nested process"))
    monkeypatch.setattr(dashboards, "run_isolated_job", lambda *_a, **_k: pytest.fail("nested process"))
    monkeypatch.setattr(dashboards.store, "add_image", lambda *_a: None)
    for name in ("custom-plot-route", "custom-image-route"):
        progress_store.start(name, "test")
    plots._run_plot_job(request("pass"), "custom-plot-route")
    dashboards._run_image_job("dashboard", request("pass"), "custom-image-route")
    assert calls == ["pass", "pass"]
    assert progress_store.get("custom-plot-route")["status"] == "complete"
    assert progress_store.get("custom-image-route")["status"] == "complete"


@pytest.mark.parametrize("path", ["/api/plots/create", "/api/plots/jobs", "/api/plots/dry-run", "/api/dashboards/test/images", "/api/dashboards/test/images/jobs"])
def test_custom_execution_requires_write_when_router_is_mounted_alone(path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from wrf_webvis.app.api import dashboards, plots
    app = FastAPI()
    app.include_router(plots.router)
    app.include_router(dashboards.router)
    response = TestClient(app, client=("203.0.113.8", 50000)).post(path, json={"fig_type": 9, "custom_code": "pass"})
    assert response.status_code == 403

