"""记录图片里每个子图的位置和坐标范围（旁注文件 <图名>.axes.json），让批注可以按物理坐标定位。

位置用“图片归一化坐标”：左上角 (0, 0)，右下角 (1, 1)，与网页上批注用的坐标一致。
保存时如果用了 bbox_inches="tight"，按实际裁剪后的范围换算。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def sidecar_path(image_path: str | Path) -> Path:
    path = Path(image_path)
    return path.with_name(path.stem + ".axes.json")


def _saved_bbox(fig, options: dict[str, Any]):
    """返回保存时实际使用的图幅范围（英寸）。"""
    from matplotlib import rcParams
    from matplotlib.transforms import Bbox

    bbox_inches = options.get("bbox_inches")
    if bbox_inches == "tight":
        renderer = fig.canvas.get_renderer()
        tight = fig.get_tightbbox(renderer, bbox_extra_artists=options.get("bbox_extra_artists"))
        pad = options.get("pad_inches", rcParams["savefig.pad_inches"])
        if pad == "layout":
            pad = 0
        return tight.padded(pad or 0)
    if isinstance(bbox_inches, Bbox):
        return bbox_inches
    width, height = fig.get_size_inches()
    return Bbox.from_bounds(0, 0, width, height)


def describe_axes(fig, options: dict[str, Any]) -> dict[str, Any]:
    bbox = _saved_bbox(fig, options)
    dpi = options.get("dpi") or fig.dpi
    if dpi == "figure":
        dpi = fig.dpi
    panels = []
    for index, ax in enumerate(fig.axes):
        if not ax.get_visible():
            continue
        position = ax.get_position()  # 图幅分数坐标（左下原点）
        width, height = fig.get_size_inches()
        x0, x1 = position.x0 * width, position.x1 * width
        y0, y1 = position.y0 * height, position.y1 * height
        left = (x0 - bbox.x0) / bbox.width
        right = (x1 - bbox.x0) / bbox.width
        top = 1 - (y1 - bbox.y0) / bbox.height
        bottom = 1 - (y0 - bbox.y0) / bbox.height
        projection = getattr(ax, "name", "rectilinear")
        label = ax.get_label() or ""
        panel: dict[str, Any] = {
            "index": index,
            "bbox": [round(left, 5), round(top, 5), round(right, 5), round(bottom, 5)],
            "projection": projection,
            "colorbar": label == "<colorbar>",
            "title": ax.get_title() or ax.get_title(loc="left") or "",
            "xlabel": ax.get_xlabel(),
            "ylabel": ax.get_ylabel(),
        }
        if projection == "rectilinear":
            xlim = [float(value) for value in ax.get_xlim()]
            ylim = [float(value) for value in ax.get_ylim()]
            if all(math.isfinite(value) for value in xlim + ylim):
                panel.update({"xlim": xlim, "ylim": ylim, "xscale": ax.get_xscale(), "yscale": ax.get_yscale()})
        panels.append(panel)
    return {
        "version": 1,
        "pixel_size": [int(round(bbox.width * float(dpi))), int(round(bbox.height * float(dpi)))],
        "panels": panels,
    }


def write_axes_sidecar(fig, image_path: str | Path, options: dict[str, Any]) -> dict[str, Any] | None:
    """尽力写旁注文件；任何失败都不影响出图。"""
    try:
        if not hasattr(fig, "axes") or not hasattr(fig, "get_tightbbox"):
            return None
        data = describe_axes(fig, options)
        target = sidecar_path(image_path)
        temporary = target.with_name("." + target.name + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)
        return data
    except Exception:  # noqa: BLE001 - 旁注是附加信息
        return None


def read_axes_sidecar(image_path: str | Path) -> dict[str, Any] | None:
    try:
        return json.loads(sidecar_path(image_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _scale(value: float, scale: str) -> float:
    if scale == "log":
        if value <= 0:
            raise ValueError("对数坐标轴上的值必须大于 0")
        return math.log10(value)
    return value


def data_to_image(panel: dict[str, Any], x: float, y: float) -> tuple[float, float]:
    """把子图里的数据坐标换算成图片归一化坐标（左上原点）。"""
    if "xlim" not in panel:
        raise ValueError(f"子图 {panel.get('index')} 不是普通直角坐标（{panel.get('projection')}），只能用图片坐标批注")
    left, top, right, bottom = panel["bbox"]
    x0, x1 = (_scale(value, panel.get("xscale", "linear")) for value in panel["xlim"])
    y0, y1 = (_scale(value, panel.get("yscale", "linear")) for value in panel["ylim"])
    fx = (_scale(float(x), panel.get("xscale", "linear")) - x0) / (x1 - x0)
    fy = (_scale(float(y), panel.get("yscale", "linear")) - y0) / (y1 - y0)
    return left + fx * (right - left), bottom - fy * (bottom - top)
