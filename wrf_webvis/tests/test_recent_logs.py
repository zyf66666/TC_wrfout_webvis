from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from wrf_webvis.app.main import app


# 功能说明：写入两份日志后请求最后两行，确认接口分别返回每份日志的末尾两行。
def test_recent_logs_endpoint_returns_only_requested_tail_lines(tmp_path: Path):
    (tmp_path / "uvicorn.log").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    (tmp_path / "uvicorn-error.log").write_text("错误一\n错误二\n错误三\n", encoding="utf-8")

    with patch.dict(os.environ, {"WRF_WEBVIS_LOG_DIR": str(tmp_path)}):
        response = TestClient(app, client=("127.0.0.1", 50000)).get("/api/logs/recent?lines=2")

    assert response.status_code == 200
    payload = response.json()
    assert payload["line_count"] == 2
    assert payload["logs"] == [
        {"name": "uvicorn.log", "exists": True, "content": "three\nfour"},
        {"name": "uvicorn-error.log", "exists": True, "content": "错误二\n错误三"},
    ]


# 功能说明：日志目录为空时请求接口，确认仍成功返回两份“文件不存在且内容为空”的记录。
def test_recent_logs_endpoint_reports_missing_files_without_failing(tmp_path: Path):
    with patch.dict(os.environ, {"WRF_WEBVIS_LOG_DIR": str(tmp_path)}):
        response = TestClient(app, client=("127.0.0.1", 50000)).get("/api/logs/recent")

    assert response.status_code == 200
    assert response.json()["logs"] == [
        {"name": "uvicorn.log", "exists": False, "content": ""},
        {"name": "uvicorn-error.log", "exists": False, "content": ""},
    ]


# 功能说明：请求 0 行和超过 2000 行日志，确认接口参数校验返回 422。
def test_recent_logs_endpoint_limits_requested_line_count():
    client = TestClient(app, client=("127.0.0.1", 50000))

    assert client.get("/api/logs/recent?lines=0").status_code == 422
    assert client.get("/api/logs/recent?lines=2001").status_code == 422
