"""看板图片缩略图（2026-11-06）：公网经 SSH 隧道带宽有限，看板卡片先显示缩小的图，点“放大”再看原图。

``GET /api/figures/thumb/<文件名>?w=800`` 返回宽度不超过 w 像素的 WebP（Pillow 不支持 WebP 时用 JPEG），
缓存在 ``storage_data/figure_thumbs/``；原图更新后自动重做。原图不改动。
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..core.config import FIGURE_ROOT, STORAGE_ROOT

router = APIRouter(tags=["figures"])

THUMB_ROOT = STORAGE_ROOT / "figure_thumbs"
ALLOWED_WIDTHS = (480, 800, 1200)
CACHE_HEADERS = {"Cache-Control": "private, max-age=2592000"}
_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.(png|jpg|jpeg)$", re.IGNORECASE)


def _thumb_format() -> tuple[str, str, str]:
    from PIL import features

    if features.check("webp"):
        return "WEBP", ".webp", "image/webp"
    return "JPEG", ".jpg", "image/jpeg"


def make_thumbnail(source: Path, width: int, root: Path = THUMB_ROOT) -> tuple[Path, str]:
    from PIL import Image

    fmt, suffix, media = _thumb_format()
    target = root / f"{source.stem}_w{width}{suffix}"
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target, media
    root.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image.load()
        if image.mode not in ("RGB", "L"):
            background = Image.new("RGB", image.size, "white")
            rgba = image.convert("RGBA")
            background.paste(rgba, mask=rgba.split()[-1])
            image = background
        if image.width > width:
            image = image.resize((width, max(1, round(image.height * width / image.width))), Image.LANCZOS)
        handle, temp = tempfile.mkstemp(dir=root, suffix=suffix)
        os.close(handle)
        try:
            image.save(temp, fmt, quality=85, method=4) if fmt == "WEBP" else image.save(temp, fmt, quality=88, optimize=True)
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    return target, media


@router.get("/api/figures/thumb/{name}")
def figure_thumbnail(name: str, w: int = 800):
    if not _NAME.fullmatch(name):
        raise HTTPException(status_code=404, detail="只支持看板里的 PNG/JPEG 图片")
    source = FIGURE_ROOT / name
    if not source.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    width = min(ALLOWED_WIDTHS, key=lambda item: abs(item - w))
    try:
        target, media = make_thumbnail(source, width)
    except Exception:  # noqa: BLE001 - 缩略图失败就直接给原图，不影响看图
        return FileResponse(source, headers=CACHE_HEADERS)
    return FileResponse(target, media_type=media, headers=CACHE_HEADERS)
