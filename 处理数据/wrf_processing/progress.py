from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from threading import BoundedSemaphore
from typing import Any, Callable, Dict, Optional


ProgressCallback = Callable[[str], None]


class JobCancelled(RuntimeError):
    """Raised by long-running jobs when the user requests cancellation."""


class JobConcurrencyLimiter:
    """Limit independent web jobs while keeping each job internally serial."""

    # 功能说明：把最大并发数保存为信号量，限制同时运行的处理任务数量。
    def __init__(self, max_concurrent: int = 3) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        self.max_concurrent = max_concurrent
        self._slots = BoundedSemaphore(max_concurrent)

    # 功能说明：在并发计数达到上限时等待；获得名额后返回可释放的上下文管理器。
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
    # 功能说明：创建保存任务状态的内存字典；此实现不使用线程锁，调用方直接读写同一份记录。
    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}

    # 功能说明：创建任务进度记录，写入任务标题并标记为运行中。
    def start(self, job_id: str, title: str) -> None:
        self._jobs[job_id] = {
            "id": job_id,
            "title": title,
            "status": "running",
            "messages": [],
            "result": None,
            "error": None,
            "cancel_requested": False,
        }
        self.add(job_id, "任务已创建")

    # 功能说明：向指定任务追加一条进度消息，供回调或界面读取。
    def add(self, job_id: str, message: str) -> None:
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

    # 功能说明：为指定任务设置“已请求取消”标记并追加提示；实际运行代码下次检查该标记时才会停止。
    def cancel(self, job_id: str) -> None:
        if job_id not in self._jobs:
            raise KeyError(f"Job not found: {job_id}")
        job = self._jobs[job_id]
        if job.get("status") in {"complete", "failed", "canceled"}:
            return
        job["cancel_requested"] = True
        self.add(job_id, "收到终止请求，正在停止当前任务")

    # 功能说明：检查任务是否收到取消请求，若已取消则抛出取消异常停止计算。
    def cancel_if_requested(self, job_id: str) -> None:
        if self._jobs.get(job_id, {}).get("cancel_requested"):
            raise JobCancelled("任务已终止")

    # 功能说明：记录任务成功结果并改为完成状态；若此前已请求取消，则改记为取消而不保存成功结果。
    def complete(self, job_id: str, result: Any) -> None:
        if self._jobs.get(job_id, {}).get("status") == "canceled":
            return
        if self._jobs.get(job_id, {}).get("cancel_requested"):
            self.canceled(job_id)
            return
        self.add(job_id, "任务完成")
        self._jobs[job_id]["status"] = "complete"
        self._jobs[job_id]["result"] = result

    # 功能说明：把任务标记为已取消，并写入取消消息和取消原因。
    def canceled(self, job_id: str) -> None:
        self.add(job_id, "任务已终止")
        self._jobs[job_id]["status"] = "canceled"
        self._jobs[job_id]["error"] = "任务已终止"

    # 功能说明：记录异常信息并将任务状态设为失败。
    def fail(self, job_id: str, error: str) -> None:
        if self._jobs.get(job_id, {}).get("status") == "canceled":
            return
        self.add(job_id, f"任务失败: {error}")
        self._jobs[job_id]["status"] = "failed"
        self._jobs[job_id]["error"] = error

    # 功能说明：按任务编号返回当前进度、状态和结果所在的原始记录；编号不存在时抛出错误。
    def get(self, job_id: str) -> Dict[str, Any]:
        if job_id not in self._jobs:
            raise KeyError(f"Job not found: {job_id}")
        return self._jobs[job_id]


progress_store = ProgressStore()
job_concurrency_limiter = JobConcurrencyLimiter(max_concurrent=3)


# 功能说明：创建绑定任务编号的回调；回调收到消息时会检查取消、保存消息并再次检查。
def callback_for_job(job_id: str) -> ProgressCallback:
    # 功能说明：把单条进度消息写入对应任务，并在写入前后响应取消请求。
    def _callback(message: str) -> None:
        progress_store.cancel_if_requested(job_id)
        progress_store.add(job_id, message)
        progress_store.cancel_if_requested(job_id)

    return _callback


# 功能说明：若调用方提供进度回调，就把一条文字消息交给它；本函数不计算百分比。
def emit(progress: Optional[ProgressCallback], message: str) -> None:
    if progress is not None:
        progress(message)
