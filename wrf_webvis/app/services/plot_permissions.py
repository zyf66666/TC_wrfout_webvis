"""普通账号出图时的限制（2026-11-06）。

root 不受限制。勾了“可出图”的普通账号：
- 只能往自己能看的看板里加图（看板编号在 api/dashboards.py 里查）；
- 不能运行“自定义代码”图（那是在服务器上执行任意 Python）；
- 数据根目录只能是网页的数据集目录（processed_v3、post 及其 profiles/ 子目录），
  不能填 wrfout 路径，也不能在格式里指定任何单独的文件路径（*_path）；
- 只能查看、停止自己提交的绘图任务。
"""
from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import HTTPException

from ..core.config import DEFAULT_PLOT_PROCESSED_ROOT, resolve_plot_processed_root
from ..core.schemas import PlotRequest, PlotType
from ..storage.user_store import Principal

PLOT_FORBIDDEN = {"code": "READ_ONLY", "message": "当前账号不能出图"}

_job_owners: dict[str, str] = {}
_owners_lock = Lock()


def _forbid(message: str) -> HTTPException:
    return HTTPException(status_code=403, detail={"code": "READ_ONLY", "message": message})


def _allowed_roots() -> list[Path]:
    roots = [Path(DEFAULT_PLOT_PROCESSED_ROOT)]
    try:
        from .datasets import dataset_roots

        roots.extend(dataset_roots().values())
    except Exception:  # noqa: BLE001 - 处理管线不可用时只认默认目录
        pass
    return [Path(root).expanduser().resolve() for root in roots]


def _inside(path: Path, roots: list[Path]) -> bool:
    resolved = path.expanduser().resolve()
    return any(resolved == root or root in resolved.parents for root in roots)


def _explicit_paths(value: Any, prefix: str = "") -> list[str]:
    """找出请求里所有填了值的 *_path / *_file 字段。"""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            name = f"{prefix}{key}"
            if isinstance(key, str) and (key.endswith("_path") or key.endswith("_file")) and item not in (None, "", [], {}):
                found.append(name)
            else:
                found.extend(_explicit_paths(item, f"{name}."))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_explicit_paths(item, f"{prefix}{index}."))
    return found


def check_plot_request(principal: Principal, request: PlotRequest) -> None:
    """普通账号的出图请求不合规时抛 403；root 直接通过。"""
    if principal.is_root:
        return
    if not principal.may_plot:
        raise HTTPException(status_code=403, detail=PLOT_FORBIDDEN)
    if request.fig_type == PlotType.CUSTOM_CODE or request.custom_code:
        raise _forbid("自定义代码图只有 root 能运行")
    if request.wrfout_root or request.wrfout_file:
        raise _forbid("普通账号不能直接读 wrfout，请用已处理的数据集")
    roots = _allowed_roots()
    for value in (request.data_root, request.processed_root):
        if value in (None, ""):
            continue
        if not _inside(resolve_plot_processed_root(value), roots):
            raise _forbid(f"数据根目录只能用网页的数据集目录（{', '.join(str(root) for root in roots)}）")
    dump = request.model_dump(mode="json") if hasattr(request, "model_dump") else request.dict()
    for key in ("data_root", "processed_root", "wrfout_root", "wrfout_file", "dashboard_id"):
        dump.pop(key, None)
    paths = _explicit_paths(dump)
    if paths:
        raise _forbid(f"普通账号不能指定单独的文件路径：{', '.join(paths[:5])}")


def remember_job(job_id: str, principal: Principal) -> None:
    if principal.is_root:
        return
    with _owners_lock:
        _job_owners[job_id] = principal.username


def check_job_access(principal: Principal, job_id: str) -> None:
    """普通账号只能看、停自己提交的绘图任务（能看数据管理页的账号还能看处理任务进度，由调用方放行）。"""
    if principal.is_root:
        return
    with _owners_lock:
        owner = _job_owners.get(job_id)
    if owner != principal.username:
        raise _forbid("只能查看或停止自己提交的任务")
