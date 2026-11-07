from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Query


router = APIRouter(prefix="/api/logs", tags=["logs"])
LOG_FILE_NAMES = ("uvicorn.log", "uvicorn-error.log")
DEFAULT_LOG_DIR = Path.home() / "Library" / "Logs" / "WRFWebVis"


# 功能说明：从日志文件末尾倒读指定行数，返回解码后的最近日志文本。
def _tail_text(path: Path, line_count: int) -> str:
    block_size = 8192
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        chunks: list[bytes] = []
        newline_count = 0
        while position > 0 and newline_count <= line_count:
            read_size = min(block_size, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")
    text = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-line_count:])


# 功能说明：读取各个应用日志的末尾内容，返回存在状态和文本快照。
@router.get("/recent")
def recent_logs(lines: int = Query(default=200, ge=1, le=2000)) -> dict[str, object]:
    log_dir = Path(os.environ.get("WRF_WEBVIS_LOG_DIR", DEFAULT_LOG_DIR))
    snapshots: list[dict[str, object]] = []
    for name in LOG_FILE_NAMES:
        path = log_dir / name
        exists = path.is_file()
        snapshots.append(
            {
                "name": name,
                "exists": exists,
                "content": _tail_text(path, lines) if exists else "",
            }
        )
    return {"line_count": lines, "logs": snapshots}
