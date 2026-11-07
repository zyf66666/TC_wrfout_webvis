import numpy as np


def test_theta_anomaly_is_registered_as_three_dimensional_layer_center_variable():
    from wrf_webvis.app.core.registry import (
        DEFAULT_HEIGHT_LEVELS_M,
        get_variable_item,
        plotting_variable_type_registry,
        resolve_variable_name,
    )
    from wrf_webvis.app.plotting.draw_utils import VARIABLE_TYPE_REGISTRY

    assert get_variable_item("theta").dimension.value == "3d"
    assert get_variable_item("theta_anomaly").dimension.value == "3d"
    assert resolve_variable_name("theta_anomaly1").polar_name == (
        "theta_anomaly1_polar"
    )
    assert plotting_variable_type_registry()["theta_anomaly1"] == "3d"
    assert VARIABLE_TYPE_REGISTRY["theta_anomaly"] == "3d"
    assert 15000 in DEFAULT_HEIGHT_LEVELS_M


def test_explicit_polar_name_keeps_layer_center_and_storage_name(tmp_path):
    from wrf_webvis.app.core.registry import resolve_variable_name
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.plot_service import PlotService

    resolved = resolve_variable_name("diabatic_heating1_polar")
    assert resolved.base_name == "diabatic_heating"
    assert resolved.center_mode == "layer"
    assert resolved.polar_name == "diabatic_heating1_polar"

    root = tmp_path / "processed_v3"
    request = PlotRequest(
        member_name="member",
        data_root=root,
        processed_root=root,
        fig_type=1,
        height={"values": [100, 200]},
        variables=[{"name": "diabatic_heating1_polar"}],
    )
    plan = PlotService().dry_run(request)["plan"]
    assert len(plan["required_files"]) == 2
    assert {item["variable"] for item in plan["required_files"]} == {"diabatic_heating1_polar"}


def test_theta_anomaly1_radius_height_plan_reads_one_file_per_height(tmp_path):
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.plot_service import PlotService
    from v3_fixtures import save_at_legacy_path

    for height in (100, 200):
        path = (
            tmp_path
            / "member/d03/theta_anomaly1_polar/0h"
            / f"{height}m.npy"
        )
        save_at_legacy_path(path, np.zeros((360, 101)))  # 2026-11-01：写成 v3 切片

    request = PlotRequest(
        member_name="member",
        data_root=str(tmp_path),
        processed_root=str(tmp_path),
        fig_type=1,
        height={"values": [100, 200]},
        variables=[{"name": "theta_anomaly1"}],
    )

    result = PlotService().dry_run(request)

    assert result["plan"]["missing_files"] == []
    # v3 里所有高度在同一个 theta_anomaly1_polar.nc 里，每个高度一个切片。
    assert [(item["path"], item["height_m"]) for item in result["plan"]["required_files"]] == [
        (str(tmp_path / "member/d03/theta_anomaly1_polar.nc"), 100),
        (str(tmp_path / "member/d03/theta_anomaly1_polar.nc"), 200),
    ]
