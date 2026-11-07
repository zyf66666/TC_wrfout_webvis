from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..services.plot_permissions import check_job_access
from ..services.progress import progress_store
from ..storage.user_store import LOCAL_ROOT


# 普通账号：能看数据管理页的可看全部任务进度；只勾了“可出图”的只能看、停自己提交的绘图任务。
def _check(request: Request, job_id: str, *, cancel: bool = False) -> None:
    principal = getattr(request.state, "principal", None) or LOCAL_ROOT
    if principal.is_root or (not cancel and principal.can_see_page("data")):
        return
    check_job_access(principal, job_id)


router = APIRouter(prefix="/api/jobs", tags=["plot-jobs"])


@router.get("")
def list_jobs(limit: int = 100, kind: str | None = None):
    return progress_store.list_jobs(max(1, min(limit, 500)), kind=kind)


# 功能说明：按任务编号读取进度；不存在时返回“未找到”错误。
@router.get("/{job_id}/progress")
def get_job_progress(job_id: str, request: Request):
    _check(request, job_id)
    try:
        return progress_store.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# 功能说明：请求停止指定任务，并返回任务停止请求后的最新状态。
@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, request: Request):
    _check(request, job_id, cancel=True)
    try:
        progress_store.cancel(job_id)
        return progress_store.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
