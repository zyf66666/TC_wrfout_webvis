"""Run web plot work outside the API process.

Matplotlib and the native libraries it invokes may block without reaching a
cooperative cancellation callback.  The parent owns cancellation, progress,
and the shared concurrency limit; a child owns only one render.
"""
from __future__ import annotations

import multiprocessing as mp
import traceback
from collections.abc import Callable
from multiprocessing.connection import Connection
from typing import Any

from .progress import JobCancelled, ProgressCallback, job_concurrency_limiter, mark_running


Worker = Callable[[ProgressCallback], Any]


def _worker_entry(worker: Worker, sender: Connection) -> None:
    def report(message: str) -> None:
        sender.send(("progress", message))

    try:
        sender.send(("result", worker(report)))
    except BaseException as exc:  # send the child failure to the parent job record
        sender.send(("error", (type(exc).__name__, str(exc), traceback.format_exc())))
    finally:
        sender.close()


def _stop_process(process: mp.Process, *, timeout: float = 0.5) -> None:
    if not process.is_alive():
        process.join(timeout=timeout)
        return
    process.terminate()
    process.join(timeout=timeout)
    if process.is_alive():
        process.kill()
        process.join(timeout=timeout)


def run_isolated_job(
    worker: Worker,
    *,
    progress: ProgressCallback | None = None,
    cancel_check: Callable[[], None] | None = None,
    poll_interval: float = 0.1,
) -> Any:
    """Execute *worker* in a spawned process and forward its messages.

    Cancellation is checked by the parent while waiting, then the child is
    terminated (and killed as a bounded fallback) even if it is blocked in
    native code.
    """
    def waiting() -> None:
        if progress is not None:
            progress("已有 3 个任务在运行，当前任务排队等待")

    with job_concurrency_limiter.acquire(on_wait=waiting, cancel_check=cancel_check):
        if cancel_check is not None:
            cancel_check()
        mark_running(progress)
        context = mp.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_worker_entry, args=(worker, sender))
        try:
            process.start()
        except BaseException:
            sender.close()
            receiver.close()
            process.close()
            raise
        sender.close()
        try:
            while True:
                if cancel_check is not None:
                    cancel_check()
                if receiver.poll(poll_interval):
                    try:
                        kind, payload = receiver.recv()
                    except EOFError as exc:
                        process.join(timeout=0.5)
                        raise RuntimeError(
                            f"任务子进程异常退出 (exit code {process.exitcode})"
                        ) from exc
                    if kind == "progress":
                        if progress is not None:
                            progress(payload)
                    elif kind == "result":
                        process.join(timeout=0.5)
                        return payload
                    else:
                        name, message, details = payload
                        raise RuntimeError(f"{name}: {message}\n{details}")
                elif not process.is_alive():
                    process.join(timeout=0.5)
                    raise RuntimeError(f"任务子进程异常退出 (exit code {process.exitcode})")
        except JobCancelled:
            _stop_process(process)
            raise
        finally:
            if process.is_alive():
                _stop_process(process)
            receiver.close()
            process.close()
