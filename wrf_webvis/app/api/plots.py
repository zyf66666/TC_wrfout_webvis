from __future__ import annotations

from typing import Dict

import uuid
from functools import partial

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from ..core.schemas import PlotRequest, PlotType
from ..core.access import require_write
from ..services.plot_service import PlotService
from ..services.plot_inputs import MissingProcessedDataError
from ..services.progress import JobCancelled, callback_for_job, progress_store
from ..services.isolated_job_runner import run_isolated_job
from ..services.plot_permissions import check_plot_request
from ..storage.user_store import LOCAL_ROOT

router = APIRouter(prefix="/api/plots", tags=["plots"])
plot_service = PlotService()
_plots: Dict[str, dict] = {}


# 功能说明：只检查绘图计划和缺失数据，不生成文件，并返回检查结果。
@router.post("/dry-run")
def dry_run(request: PlotRequest, http_request: Request):
    if request.fig_type == PlotType.CUSTOM_CODE:
        require_write(http_request)
    # 勾了“可出图”的普通账号也能预检，但请求内容受限（中间件已挡掉其他账号）
    check_plot_request(getattr(http_request.state, "principal", None) or LOCAL_ROOT, request)
    try:
        return plot_service.dry_run(request)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# 功能说明：生成图片、暂存结果，并把成功结果返回给网页。
@router.post("/create")
def create_plot(request: PlotRequest, http_request: Request):
    if request.fig_type == PlotType.CUSTOM_CODE:
        require_write(http_request)
    try:
        result = plot_service.create_plot(request)
        _plots[result["plot_id"]] = result
        return result
    except MissingProcessedDataError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "MISSING_PROCESSED_DATA",
                "message": str(exc),
                "missing_files": [str(path) for path in exc.missing_files],
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# 功能说明：登记后台绘图任务并把任务编号交给网页轮询。
@router.post("/jobs")
def create_plot_job(request: PlotRequest, background_tasks: BackgroundTasks, http_request: Request):
    if request.fig_type == PlotType.CUSTOM_CODE:
        require_write(http_request)
    job_id = uuid.uuid4().hex[:12]
    progress_store.start(job_id, f"生成图片: {request.title}", status="queued")
    background_tasks.add_task(_run_plot_job, request, job_id)
    return {"job_id": job_id}


# 功能说明：按图片编号从内存结果表取回已生成图片；没有则报未找到。
@router.get("/{plot_id}")
def get_plot(plot_id: str):
    if plot_id not in _plots:
        raise HTTPException(status_code=404, detail=f"Plot not found: {plot_id}")
    return _plots[plot_id]


# 功能说明：后台执行绘图，将结果、取消状态或错误写入任务进度。
def _create_plot_in_child(request: PlotRequest, progress) -> dict:
    """Child-process entrypoint kept module-level for multiprocessing spawn."""
    return PlotService()._create_plot(request, progress=progress)


def _run_plot_job(request: PlotRequest, job_id: str) -> None:
    try:
        callback = callback_for_job(job_id)
        if request.fig_type == PlotType.CUSTOM_CODE:
            result = plot_service.create_plot(request, progress=callback)
        else:
            result = run_isolated_job(
                partial(_create_plot_in_child, request),
                progress=callback,
                cancel_check=callback.check_cancelled,
            )
        progress_store.cancel_if_requested(job_id)
        _plots[result["plot_id"]] = result
        progress_store.complete(job_id, result)
    except JobCancelled:
        progress_store.canceled(job_id)
    except Exception as exc:  # noqa: BLE001
        progress_store.fail(job_id, str(exc))
