from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from wrf_webvis.app.core.schemas import PlotRequest, SelectionSpec, VariableConfig
from wrf_webvis.app.main import app
from wrf_webvis.app.services.data_manager import DataManager
from wrf_webvis.app.services.plot_service import PlotService
from wrf_webvis.tests.v3_fixtures import save_v3


# 功能说明：按给定目录建立一份只请求海平面气压第 0 小时图像的标准绘图请求。
def _request(root: Path) -> PlotRequest:
    return PlotRequest(
        processed_root=root,
        data_root=root,
        member_name="member",
        domain=3,
        variables=[VariableConfig(name="slp")],
        time=SelectionSpec(values=[0]),
    )


# 功能说明：在预处理目录为空时请求绘图，确认开始计算前就指出缺少的气压文件。
def test_missing_processed_data_stops_before_any_processing(tmp_path: Path) -> None:
    service = PlotService(tmp_path / "figures")

    with pytest.raises(FileNotFoundError, match="缺少绘图所需的预处理数据") as caught:
        service.create_plot(_request(tmp_path))

    # 2026-11-01：绘图只读 processed_v3，缺失提示指向 v3 文件和具体时次。
    assert "member/d03/slp.nc" in str(caught.value)
    assert "0 h" in str(caught.value)


# 功能说明：先放入已有数组和失效来源记录，确认绘图计划只看文件存在而不重验旧元数据。
def test_plot_planning_trusts_an_existing_file_without_metadata_validation(tmp_path: Path) -> None:
    save_v3(tmp_path, "member", "slp", np.ones((2, 2), dtype=np.float32), time_hour=0,
            metadata={"source_wrfout": "/missing/stale/wrfout"})

    plan = DataManager(tmp_path).plan_for_plot(_request(tmp_path))

    assert plan.missing_files == []


# 功能说明：确认网页只通过显式的 v3 计划和任务接口处理数据，绘图接口不自动计算。
def test_web_application_exposes_explicit_v3_processing_api() -> None:
    paths = TestClient(app, client=("127.0.0.1", 50000)).get("/openapi.json").json()["paths"]

    assert "/api/processing/v3/plan" in paths
    assert "/api/processing/v3/jobs" in paths
    assert not any(path.startswith("/api/process") and not path.startswith("/api/processing/v3/") for path in paths)


# 功能说明：向绘图接口提交缺少数据的请求，确认它返回带错误代码和具体缺失文件的 422 回复。
def test_plot_api_returns_a_structured_missing_data_error(tmp_path: Path) -> None:
    response = TestClient(app, client=("127.0.0.1", 50000)).post(
        "/api/plots/create",
        json={
            "processed_root": str(tmp_path),
            "data_root": str(tmp_path),
            "member_name": "member",
            "domain": 3,
            "variables": [{"name": "slp"}],
            "time": {"values": [0]},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MISSING_PROCESSED_DATA"
    assert response.json()["detail"]["missing_files"] == [str(tmp_path / "member/d03/slp.nc")]


# 功能说明：检查绘图服务模块，确认它没有引入处理管理器、极坐标转换器或中心定位器。
def test_plot_service_has_no_processing_runtime_dependency() -> None:
    import wrf_webvis.app.services.plot_service as module

    assert not hasattr(module, "ProcessingManager")
    assert not hasattr(module, "PolarTransformer")
    assert not hasattr(module, "CenterManager")


# 功能说明：准备轨迹中心、海岸线和 d01 降雨却不提供同时次 XLAT/XLONG，确认预检准确列出这两个 v3 切片。
def test_track_rain_background_requires_processed_grid_coordinates(tmp_path: Path) -> None:
    save_v3(tmp_path, "member", "slp_center", [1, 1], time_hour=0)
    save_v3(tmp_path, "member", "land_boundary", np.ones((2, 2)), time_hour=0, domain=1)
    save_v3(tmp_path, "member", "RAINNC", np.ones((2, 2)), time_hour=120, domain=1)
    request = PlotRequest(
        processed_root=tmp_path,
        data_root=tmp_path,
        member_name="member",
        domain=3,
        fig_type=6,
        variables=[],
        format={"track": {"draw_rainnc_background": True}},
    )

    missing = PlotService(tmp_path / "figures").dry_run(request)["plan"]["missing_files"]

    assert {(item["variable"], item["path"], item["time_hour"]) for item in missing} == {
        ("XLAT", str(tmp_path / "member/d01/XLAT.nc"), 120.0),
        ("XLONG", str(tmp_path / "member/d01/XLONG.nc"), 120.0),
    }


def test_planning_never_stats_metadata(tmp_path, monkeypatch):
    save_v3(tmp_path, "member", "slp", np.ones((2, 2), dtype=np.float32), time_hour=0)
    original = Path.exists

    def exists(path):
        assert path.suffix != '.json', 'plot planning must not query metadata'
        return original(path)

    monkeypatch.setattr(Path, 'exists', exists)
    assert not DataManager(tmp_path).plan_for_plot(_request(tmp_path)).missing_files


def test_planning_reports_file_progress_and_can_cancel(tmp_path):
    from wrf_webvis.app.services.progress import JobCancelled, callback_for_job, progress_store
    request = _request(tmp_path)
    request.time = SelectionSpec(values=list(range(100)))
    messages = []
    DataManager(tmp_path).plan_for_plot(request, progress=messages.append)
    assert any('100/100' in message for message in messages)
    progress_store.start('cancel-file-check', 'test')
    progress_store.cancel('cancel-file-check')
    with pytest.raises(JobCancelled):
        DataManager(tmp_path).plan_for_plot(request, progress=callback_for_job('cancel-file-check'))
