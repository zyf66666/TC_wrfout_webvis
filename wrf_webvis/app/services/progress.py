from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import json
import os
import multiprocessing
from pathlib import Path
import sqlite3
from threading import BoundedSemaphore, RLock
from typing import Any, Callable, Dict, Optional

from ..core.config import STORAGE_ROOT


ProgressCallback = Callable[[str], None]


class JobCancelled(RuntimeError):
    """Raised by long-running jobs when the user requests cancellation."""


class JobConcurrencyLimiter:
    """Limit independent web jobs while keeping each job internally serial."""

    # 功能说明：设定最多允许几个任务同时运行，像发放有限数量的入场券；未指定时允许3个任务。
    def __init__(self, max_concurrent: int = 3) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        self.max_concurrent = max_concurrent
        self._slots = BoundedSemaphore(max_concurrent)

    # 功能说明：申请运行名额，满额时等待并检查用户是否取消；任务结束或出错后归还名额，让后面的任务继续。
    @contextmanager
    def acquire(
        self,
        *,
        on_wait: Callable[[], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ):
        if not self._slots.acquire(blocking=False):
            if on_wait is not None:
                on_wait()
            while not self._slots.acquire(timeout=0.2):
                if cancel_check is not None:
                    cancel_check()
        try:
            if cancel_check is not None:
                cancel_check()
            yield
        finally:
            self._slots.release()


class ProgressStore:
    # 功能说明：建立保存任务状态的空表，并设置访问保护，避免多个任务同时修改时互相干扰。
    def __init__(self, database_path: str | Path | None = None) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = RLock()
        self.database_path = Path(database_path) if database_path is not None else None
        if self.database_path is not None:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, payload TEXT NOT NULL)"
                )
                for job_id, payload in connection.execute("SELECT id, payload FROM jobs ORDER BY updated_at, rowid"):
                    self._jobs[job_id] = json.loads(payload)
            for job_id, job in self._jobs.items():
                if job["status"] in {"queued", "running"}:
                    job["status"] = "interrupted"
                    job["error"] = "服务重启，任务已中断；可重新提交并复用完成切片"
                    job["messages"].append({
                        "time": datetime.now().isoformat(timespec="seconds"),
                        "message": job["error"],
                    })
                    self._persist(job_id)

    def _connect(self):
        return sqlite3.connect(self.database_path, timeout=30)

    def _persist(self, job_id: str) -> None:
        if self.database_path is None:
            return
        job = self._jobs[job_id]
        job["messages"] = job["messages"][-200:]
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs (id, updated_at, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at, payload=excluded.payload",
                (job_id, datetime.now().isoformat(timespec="seconds"), json.dumps(job, ensure_ascii=False, default=str)),
            )

    # 功能说明：新建任务进度记录并写入初始状态和标题。
    def start(self, job_id: str, title: str, *, status: str = "running", kind: str = "plot", request: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "title": title,
                "status": status,
                "messages": [],
                "result": None,
                "error": None,
                "cancel_requested": False,
                "kind": kind,
                "request": request or {},
            }
            self.add(job_id, "任务已创建")

    # 功能说明：将指定任务改为运行中，并在取消请求时中断。
    def running(self, job_id: str) -> None:
        with self._lock:
            self.cancel_if_requested(job_id)
            self._jobs[job_id]["status"] = "running"
            self._persist(job_id)

    # 功能说明：给指定任务追加一条带时间的进度消息。
    def add(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs.setdefault(
                job_id,
                {
                    "id": job_id,
                    "status": "running",
                    "messages": [],
                    "result": None,
                    "error": None,
                    "cancel_requested": False,
                },
            )
            job["messages"].append({"time": datetime.now().isoformat(timespec="seconds"), "message": message})
            self._persist(job_id)

    # 功能说明：标记任务已请求取消，并追加停止提示。
    def cancel(self, job_id: str) -> None:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(f"Job not found: {job_id}")
            job = self._jobs[job_id]
            if job.get("status") in {"complete", "failed", "canceled"}:
                return
            job["cancel_requested"] = True
            self.add(job_id, "收到终止请求，正在停止当前任务")

    # 功能说明：检查任务是否请求取消；若是则抛出中止异常。
    def cancel_if_requested(self, job_id: str) -> None:
        with self._lock:
            if self._jobs.get(job_id, {}).get("cancel_requested"):
                raise JobCancelled("任务已终止")

    # 功能说明：保存任务结果并标记完成；已请求取消时改为取消状态。
    def complete(self, job_id: str, result: Any) -> None:
        with self._lock:
            if self._jobs.get(job_id, {}).get("status") == "canceled":
                return
            if self._jobs.get(job_id, {}).get("cancel_requested"):
                self.canceled(job_id)
                return
            self.add(job_id, "任务完成")
            self._jobs[job_id]["status"] = "complete"
            self._jobs[job_id]["result"] = result
            self._persist(job_id)

    # 功能说明：把任务标记为已取消并保存取消原因。
    def canceled(self, job_id: str) -> None:
        with self._lock:
            self.add(job_id, "任务已终止")
            self._jobs[job_id]["status"] = "canceled"
            self._jobs[job_id]["error"] = "任务已终止"
            self._persist(job_id)

    # 功能说明：把任务标记失败并保存错误信息。
    def fail(self, job_id: str, error: str) -> None:
        with self._lock:
            if self._jobs.get(job_id, {}).get("status") == "canceled":
                return
            self.add(job_id, f"任务失败: {error}")
            self._jobs[job_id]["status"] = "failed"
            self._jobs[job_id]["error"] = error
            self._persist(job_id)

    # 功能说明：按任务编号读取进度并交回一份独立副本，避免网页查看时改变原记录；没有这个任务时提示错误。
    def get(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(f"Job not found: {job_id}")
            return deepcopy(self._jobs[job_id])

    def list_jobs(self, limit: int = 100, kind: str | None = None) -> list[Dict[str, Any]]:
        with self._lock:
            values = [job for job in self._jobs.values() if kind is None or job.get("kind") == kind][-limit:]
            return [deepcopy(item) for item in reversed(values)]


progress_store = ProgressStore(
    database_path=(
        os.environ.get("WRF_WEBVIS_JOB_DB", str(STORAGE_ROOT / "jobs.sqlite"))
        if multiprocessing.current_process().name == "MainProcess" else None
    )
)
job_concurrency_limiter = JobConcurrencyLimiter(max_concurrent=3)


class JobProgressCallback:
    # 功能说明：保存任务编号，供后续进度回调定位任务。
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id

    # 功能说明：检查当前回调所属任务是否已取消。
    def check_cancelled(self) -> None:
        progress_store.cancel_if_requested(self.job_id)

    # 功能说明：把当前回调所属任务标记为运行中。
    def started(self) -> None:
        progress_store.running(self.job_id)

    # 功能说明：在绘图过程上报一条消息前后都检查取消请求；未取消时将消息写入该任务的进度记录。
    def __call__(self, message: str) -> None:
        self.check_cancelled()
        progress_store.add(self.job_id, message)
        self.check_cancelled()


# 功能说明：为指定任务创建可上报进度的回调对象。
def callback_for_job(job_id: str) -> JobProgressCallback:
    return JobProgressCallback(job_id)


# 功能说明：如果进度对象支持取消检查，就立即执行该检查。
def check_cancellation(progress: Optional[ProgressCallback]) -> None:
    if isinstance(progress, JobProgressCallback):
        progress.check_cancelled()


# 功能说明：如果进度对象支持开始通知，就更新其运行状态。
def mark_running(progress: Optional[ProgressCallback]) -> None:
    if isinstance(progress, JobProgressCallback):
        progress.started()


# 功能说明：把一条消息交给进度回调记录。
def emit(progress: Optional[ProgressCallback], message: str) -> None:
    if progress is not None:
        progress(message)
