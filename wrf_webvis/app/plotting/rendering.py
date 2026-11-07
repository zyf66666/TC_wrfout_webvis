"""Shared Matplotlib serialization and atomic image publication."""
from __future__ import annotations

from functools import wraps
from pathlib import Path
from threading import RLock
from uuid import uuid4

from ..services.progress import check_cancellation

_RENDER_LOCK = RLock()


# 功能说明：给绘图函数加上排队规则，让同一个服务进程里的 Matplotlib 绘图一次只执行一个，避免图形互相干扰。
def serialized_render(function):
    """All HTTP and CLI render paths share the same process-local lock."""
    # 功能说明：等待取得绘图使用权，等待期间检查用户是否取消；执行实际绘图后，无论成功失败都释放使用权。
    @wraps(function)
    def wrapped(*args, **kwargs):
        progress = kwargs.get('progress')
        if not _RENDER_LOCK.acquire(blocking=False):
            if progress is not None:
                progress('等待绘图资源，其他图片正在渲染')
            while not _RENDER_LOCK.acquire(timeout=0.2):
                check_cancellation(progress)
        try:
            check_cancellation(progress)
            if progress is not None:
                progress('开始渲染图片')
            return function(*args, **kwargs)
        finally:
            _RENDER_LOCK.release()
    return wrapped


# 功能说明：先把完整图片写到临时文件，成功后再替换目标图片，并清理临时文件，避免留下只写了一半的成品。
def save_figure(fig, output_path: str | Path, **options) -> None:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f'.{target.stem}.{uuid4().hex}{target.suffix}')
    try:
        fig.savefig(temporary, **options)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    # 记录各子图的位置和坐标范围，供看板批注按物理坐标定位（失败不影响出图）。
    if target.suffix.lower() in {'.png', '.jpg', '.jpeg'}:
        from .figure_axes import write_axes_sidecar
        write_axes_sidecar(fig, target, options)
