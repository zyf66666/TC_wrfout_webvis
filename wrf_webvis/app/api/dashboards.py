from __future__ import annotations

import re
import uuid
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from ..core.schemas import DashboardImage, PlotRequest, PlotType
from ..core.access import require_write
from ..storage.user_store import LOCAL_ROOT, Principal
from ..core.config import FIGURE_ROOT
from ..plotting.figure_axes import data_to_image, read_axes_sidecar
from ..services.plot_service import PlotService
from ..services.progress import JobCancelled, callback_for_job, progress_store
from ..services.isolated_job_runner import run_isolated_job
from ..services.plot_permissions import check_plot_request, remember_job
from ..storage.dashboard_store import DashboardStore, DashboardTreeError, ImageTransferError

router = APIRouter(prefix="/api/dashboards", tags=["dashboards"])
store = DashboardStore()
plot_service = PlotService()


# ---------------------------------------------------------------------- 权限（2026-11-06）
# 中间件（core/access.py）已挡掉 root 以外的写操作；这里再按看板编号检查“能不能看、能不能批注”。
# 路由被单独挂载（测试）时没有中间件，按本机 root 处理。
NO_VIEW_DETAIL = {"code": "FORBIDDEN", "message": "当前账号没有权限查看这个看板"}
NO_ANNOTATE_DETAIL = {"code": "READ_ONLY", "message": "当前账号不能在这个看板上批注"}


def _viewer(request: Request) -> Principal:
    return getattr(request.state, "principal", None) or LOCAL_ROOT


def _require_view(request: Request, dashboard_id: str) -> Principal:
    principal = _viewer(request)
    if principal.is_root:
        return principal
    parents = store.parent_map()
    if dashboard_id not in parents or not principal.can_view_dashboard(dashboard_id, parents[dashboard_id]):
        raise HTTPException(status_code=403, detail=NO_VIEW_DETAIL)
    return principal


def _require_annotate(request: Request, dashboard_id: str) -> Principal:
    principal = _require_view(request, dashboard_id)
    if not principal.is_root and not principal.can_annotate:
        raise HTTPException(status_code=403, detail=NO_ANNOTATE_DETAIL)
    return principal


def _require_own_annotation(principal: Principal, dashboard_id: str, image_id: str, annotation_id: str) -> None:
    if principal.is_root:
        return
    if store.get_annotation(dashboard_id, image_id, annotation_id).get("created_by") != principal.username:
        raise HTTPException(status_code=403, detail={"code": "READ_ONLY", "message": "只能修改或删除自己写的批注"})


def _require_own_image(request: Request, dashboard_id: str, lookup, image_id: str) -> None:
    """勾了“可出图”的普通账号只能删除（和撤销删除）自己出的图；root 不受限。找不到图时抛 KeyError（404）。"""
    principal = _viewer(request)
    if principal.is_root:
        return
    _require_view(request, dashboard_id)
    image = lookup(dashboard_id, image_id)
    if not principal.may_plot or image.get("created_by") != principal.username:
        raise HTTPException(status_code=403, detail={"code": "READ_ONLY", "message": "只能删除自己出的图"})


# 功能说明：读取保存的仪表盘记录，返回网页可以直接显示的仪表盘列表（只含当前账号能看的看板）。
# summary=1 时只返回编号、标题、父看板和图片数（侧栏用；完整列表含全部出图参数，十几 MB）。
@router.get("")
def list_dashboards(request: Request, summary: bool = False):
    principal = _viewer(request)
    if summary:
        rows = store.summaries()
        return [row for row in rows if principal.can_view_dashboard(row["id"], row["parent_id"])]
    dashboards = store.list()
    if principal.is_root:
        return dashboards
    return [item for item in dashboards if principal.can_view_dashboard(item.id, item.parent_id)]


# 功能说明：读取提交标题，创建一个带新编号的仪表盘并保存，然后返回它；带 parent_id 时建成子看板。
@router.post("")
def create_dashboard(payload: Optional[Dict[str, Any]] = None):
    title = (payload or {}).get("title", "New dashboard")
    parent_id = (payload or {}).get("parent_id") or None
    if parent_id is not None and not isinstance(parent_id, str):
        raise HTTPException(status_code=422, detail="parent_id 要写成看板编号")
    try:
        return store.create(title=title, parent_id=parent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DashboardTreeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# 功能说明：按仪表盘编号读取记录；没有对应记录时返回网页的“未找到”错误。
# light=1：去掉每张图元数据里的 used_files（用到的数据文件清单，每张图十几 KB，网页不用），只留个数。
@router.get("/{dashboard_id}")
def get_dashboard(dashboard_id: str, request: Request, light: bool = False):
    _require_view(request, dashboard_id)
    try:
        dashboard = store.get(dashboard_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not light:
        return dashboard
    data = _model_dump(dashboard)
    for image in data.get("images") or []:
        metadata = image.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("used_files"), list):
            metadata["used_files_count"] = len(metadata.pop("used_files"))
    return data


# 功能说明：按仪表盘编号删除保存的记录，并返回删除成功的状态。有子看板时拒绝。
@router.delete("/{dashboard_id}")
def delete_dashboard(dashboard_id: str):
    try:
        store.delete(dashboard_id)
    except DashboardTreeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "deleted"}


class ParentPayload(BaseModel):
    parent_id: Optional[str] = None


# 功能说明：把看板挂到某个顶层看板下面成为子看板，或 parent_id=null 移回顶层。
@router.patch("/{dashboard_id}/parent")
def move_dashboard(dashboard_id: str, payload: ParentPayload):
    try:
        return store.set_parent(dashboard_id, payload.parent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DashboardTreeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# 功能说明：用提交的新标题改写指定仪表盘，并返回改名后的记录。
@router.patch("/{dashboard_id}")
def rename_dashboard(dashboard_id: str, payload: Dict[str, Any]):
    try:
        return store.rename(dashboard_id, str(payload.get("title", "")).strip() or "Untitled dashboard")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# 功能说明：按仪表盘编号取出其中图片列表，并返回给网页。
@router.get("/{dashboard_id}/images")
def list_images(dashboard_id: str, request: Request):
    _require_view(request, dashboard_id)
    try:
        return store.get(dashboard_id).images
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# 功能说明：把绘图请求绑定到仪表盘，生成图片、保存图片记录并返回仪表盘内容。
@router.post("/{dashboard_id}/images")
def create_image(dashboard_id: str, request: PlotRequest, http_request: Request):
    if request.fig_type == PlotType.CUSTOM_CODE:
        require_write(http_request)
    request.dashboard_id = dashboard_id
    try:
        result = plot_service.create_plot(request)
        image = DashboardImage(
            id=uuid.uuid4().hex[:12],
            title=request.title,
            image_path=result["static_path"],
            request=_model_dump(request),
            metadata=result,
        )
        return store.add_image(dashboard_id, image)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# 功能说明：创建图片生成任务，交给后台执行并立即返回任务编号。
@router.post("/{dashboard_id}/images/jobs")
def create_image_job(dashboard_id: str, request: PlotRequest, background_tasks: BackgroundTasks, http_request: Request):
    if request.fig_type == PlotType.CUSTOM_CODE:
        require_write(http_request)
    principal = _viewer(http_request)
    if not principal.is_root:
        # 普通账号（勾了“可出图”）：只能加到自己能看的看板，请求内容另有限制
        _require_view(http_request, dashboard_id)
        check_plot_request(principal, request)
    job_id = uuid.uuid4().hex[:12]
    remember_job(job_id, principal)
    request.dashboard_id = dashboard_id
    progress_store.start(job_id, f"生成图片: {request.title}", status="queued")
    created_by = "" if principal.is_root else principal.username
    background_tasks.add_task(_run_image_job, dashboard_id, request, job_id, created_by)
    return {"job_id": job_id}


class ImageOrderRequest(BaseModel):
    before_id: Optional[str] = None


@router.patch("/{dashboard_id}/images/{image_id}/order")
def move_image(dashboard_id: str, image_id: str, payload: ImageOrderRequest):
    try:
        return store.move_image(dashboard_id, image_id, payload.before_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class ImageTransferRequest(BaseModel):
    target_dashboard_id: str
    before_id: Optional[str] = None


# 功能说明：把图片（连同批注）移到另一个看板，返回目标看板。只有 root 能用（中间件拦下其他账号的写请求）。
@router.post("/{dashboard_id}/images/{image_id}/transfer")
def transfer_image(dashboard_id: str, image_id: str, payload: ImageTransferRequest):
    try:
        return store.transfer_image(dashboard_id, image_id, payload.target_dashboard_id, payload.before_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ImageTransferError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# 功能说明：从指定仪表盘移除图片，写回文件并返回更新记录。
@router.delete("/{dashboard_id}/images/{image_id}")
def delete_image(dashboard_id: str, image_id: str, request: Request):
    try:
        _require_own_image(request, dashboard_id, store.get_image, image_id)
        return store.delete_image(dashboard_id, image_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# 功能说明：最近删除的图片（可撤销）。
@router.get("/{dashboard_id}/deleted-images")
def deleted_images(dashboard_id: str):
    try:
        return store.list_deleted_images(dashboard_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# 功能说明：撤销删除，把图片放回原位置。
@router.post("/{dashboard_id}/images/{image_id}/restore")
def restore_image(dashboard_id: str, image_id: str, request: Request):
    try:
        _require_own_image(request, dashboard_id, store.get_deleted_image, image_id)
        return store.restore_image(dashboard_id, image_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class ImageNotePayload(BaseModel):
    note: str = Field(default="", max_length=20000)


# 功能说明：保存图片下方的文字批注。
@router.put("/{dashboard_id}/images/{image_id}/note")
def update_image_note(dashboard_id: str, image_id: str, payload: ImageNotePayload, request: Request):
    principal = _require_annotate(request, dashboard_id)
    try:
        return store.set_image_note(dashboard_id, image_id, payload.note.strip(),
                                    updated_by=None if principal.is_local else principal.username)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# 功能说明：修改指定图片标题和元数据，写回文件并返回更新记录。
@router.patch("/{dashboard_id}/images/{image_id}")
def rename_image(dashboard_id: str, image_id: str, payload: Dict[str, Any]):
    try:
        return store.rename_image(dashboard_id, image_id, str(payload.get("title", "")).strip() or "Untitled image")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------- 图片批注
SHAPE_POINTS = {"arrow": (2, 2), "line": (2, 2), "rect": (2, 2), "ellipse": (2, 2), "freehand": (2, 2000),
                "text": (1, 1), "pin": (1, 1)}


class AnnotationShape(BaseModel):
    """一个批注形状。coords="image"：点是图片归一化坐标（左上 0,0，右下 1,1）；
    coords="data"：点是第 panel 个子图里的数据坐标（如 半径 km、时间 h），由后端换算。"""
    kind: Literal["arrow", "line", "rect", "ellipse", "freehand", "text", "pin"]
    points: List[List[float]] = Field(min_length=1, max_length=2000)
    coords: Literal["image", "data"] = "image"
    panel: Optional[int] = None
    text: str = Field(default="", max_length=1000)
    color: str = "#e4572e"
    width: float = Field(default=2.5, ge=0.5, le=20)
    font_size: float = Field(default=15, ge=8, le=60)

    @field_validator("color")
    @classmethod
    def valid_color(cls, value: str) -> str:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("颜色要写成 #RRGGBB")
        return value

    @field_validator("points")
    @classmethod
    def valid_points(cls, value: List[List[float]]) -> List[List[float]]:
        for point in value:
            if len(point) != 2:
                raise ValueError("每个点要写成 [x, y]")
        return value


class AnnotationPayload(BaseModel):
    shapes: List[AnnotationShape] = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=5000)
    author: Literal["user", "ai"] = "user"


class AnnotationChanges(BaseModel):
    shapes: Optional[List[AnnotationShape]] = Field(default=None, max_length=200)
    note: Optional[str] = Field(default=None, max_length=5000)


def _image_axes(image: dict[str, Any]) -> dict[str, Any] | None:
    metadata = image.get("metadata") or {}
    axes = metadata.get("axes") if isinstance(metadata, dict) else None
    if axes:
        return axes
    path = FIGURE_ROOT / Path(str(image.get("image_path") or "")).name
    return read_axes_sidecar(path)


def _normalize_shapes(shapes: List[AnnotationShape], axes: dict[str, Any] | None) -> list[dict[str, Any]]:
    """检查点数；把数据坐标换算成图片坐标（保留原始数据坐标备查）。"""
    result = []
    for index, shape in enumerate(shapes, 1):
        low, high = SHAPE_POINTS[shape.kind]
        if not low <= len(shape.points) <= high:
            raise ValueError(f"第 {index} 个形状（{shape.kind}）需要 {low}{'' if low == high else f'～{high}'} 个点")
        item = shape.model_dump()
        if shape.coords == "data":
            if not axes:
                raise ValueError("这张图没有记录坐标轴信息（较早生成的图），请改用图片坐标 coords=\"image\"")
            panels = {panel["index"]: panel for panel in axes.get("panels") or []}
            if shape.panel not in panels:
                raise ValueError(f"第 {index} 个形状：没有编号为 {shape.panel} 的子图，可选 {sorted(panels)}")
            item["data_points"] = shape.points
            item["points"] = [list(data_to_image(panels[shape.panel], x, y)) for x, y in shape.points]
            item["coords"] = "image"
        for x, y in item["points"]:
            if not (-0.05 <= x <= 1.05 and -0.05 <= y <= 1.05):
                raise ValueError(f"第 {index} 个形状的点 ({x:.3f}, {y:.3f}) 超出图片范围")
        item["points"] = [[round(min(1.0, max(0.0, x)), 5), round(min(1.0, max(0.0, y)), 5)] for x, y in item["points"]]
        result.append(item)
    return result


@router.get("/{dashboard_id}/revision")
def dashboard_revision(dashboard_id: str, request: Request):
    _require_view(request, dashboard_id)
    try:
        return {"revision": store.revision(dashboard_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{dashboard_id}/annotations")
def dashboard_annotations(dashboard_id: str, request: Request):
    _require_view(request, dashboard_id)
    try:
        return store.list_annotations(dashboard_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{dashboard_id}/images/{image_id}/axes")
def image_axes(dashboard_id: str, image_id: str, request: Request):
    """图片里各子图的位置和坐标范围（AI 按物理坐标批注前先读这个）。"""
    _require_view(request, dashboard_id)
    try:
        image = store.get_image(dashboard_id, image_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    axes = _image_axes(image)
    return {"image_id": image_id, "title": image.get("title"), "image_path": image.get("image_path"),
            "axes": axes, "has_axes": bool(axes)}


@router.get("/{dashboard_id}/images/{image_id}/annotations")
def image_annotations(dashboard_id: str, image_id: str, request: Request):
    _require_view(request, dashboard_id)
    try:
        store.get_image(dashboard_id, image_id)
        return store.list_annotations(dashboard_id, image_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{dashboard_id}/images/{image_id}/annotations")
def create_annotation(dashboard_id: str, image_id: str, payload: AnnotationPayload, request: Request):
    principal = _require_annotate(request, dashboard_id)
    # 普通账号写的批注一律记为 user，并记下是谁写的（只能改删自己的）。
    author = payload.author if principal.is_root else "user"
    created_by = None if principal.is_local else principal.username
    try:
        image = store.get_image(dashboard_id, image_id)
        shapes = _normalize_shapes(payload.shapes, _image_axes(image))
        return store.add_annotation(dashboard_id, image_id, author, shapes, payload.note, created_by=created_by)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/{dashboard_id}/images/{image_id}/annotations/{annotation_id}")
def update_annotation(dashboard_id: str, image_id: str, annotation_id: str, payload: AnnotationChanges,
                      request: Request):
    principal = _require_annotate(request, dashboard_id)
    try:
        _require_own_annotation(principal, dashboard_id, image_id, annotation_id)
        image = store.get_image(dashboard_id, image_id)
        shapes = _normalize_shapes(payload.shapes, _image_axes(image)) if payload.shapes is not None else None
        return store.update_annotation(dashboard_id, image_id, annotation_id, shapes=shapes, note=payload.note)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{dashboard_id}/images/{image_id}/annotations/{annotation_id}")
def delete_annotation(dashboard_id: str, image_id: str, annotation_id: str, request: Request):
    """只删除这条批注，图片不动。"""
    principal = _require_annotate(request, dashboard_id)
    try:
        _require_own_annotation(principal, dashboard_id, image_id, annotation_id)
        store.delete_annotation(dashboard_id, image_id, annotation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted"}


# 功能说明：后台生成图片；成功时保存图片和结果，失败或取消时记录任务状态。
def _create_plot_in_child(request: PlotRequest, progress) -> dict:
    """Child-process entrypoint kept module-level for multiprocessing spawn."""
    return PlotService()._create_plot(request, progress=progress)


def _run_image_job(dashboard_id: str, request: PlotRequest, job_id: str, created_by: str = "") -> None:
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
        image = DashboardImage(
            id=uuid.uuid4().hex[:12],
            title=request.title,
            image_path=result["static_path"],
            request=_model_dump(request),
            metadata=result,
            created_by=created_by,
        )
        store.add_image(dashboard_id, image)
        progress_store.complete(job_id, {"dashboard_id": dashboard_id, "image": _model_dump(image), "plot": result})
    except JobCancelled:
        progress_store.canceled(job_id)
    except Exception as exc:  # noqa: BLE001
        progress_store.fail(job_id, str(exc))


# 功能说明：把模型对象转换为普通字典，兼容新旧两种模型接口。
def _model_dump(model: Any) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    if hasattr(model, "dict"):
        return model.dict()
    return dict(model)
