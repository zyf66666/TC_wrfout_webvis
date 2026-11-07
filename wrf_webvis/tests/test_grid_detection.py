"""绘图端自动识别网格间距和极坐标径向间隔（2026-11-04，wrf_webvis/app/services/grid_detection.py）。

前半部分只测决策函数，不需要 netCDF4；后半部分用真实 NetCDFStorage 写处理文件，走 plan_for_plot 和绘图 dry run。
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from wrf_webvis.app.services.grid_detection import (
    choose_domain_config,
    choose_polar_resolution,
    spacing_from_metadata,
)

D03_DEFAULT = {"dx_km": 1.0, "dy_km": 1.0, "nx": 805, "ny": 805}


def _infos(table):
    return lambda member: iter(table.get(member, []))


def _no_wrfout(member):
    return None


def test_spacing_from_metadata_reads_dx_dy():
    assert spacing_from_metadata({"grid_spacing": {"dx_km": 2.0, "dy_km": 2.0}}) == (2.0, 2.0)
    assert spacing_from_metadata({"grid_spacing": {"dx_km": 3.0}}) == (3.0, 3.0)
    assert spacing_from_metadata({"grid_spacing": {"dx_km": 0}}) is None
    assert spacing_from_metadata({}) is None
    assert spacing_from_metadata(None) is None


def test_processed_metadata_wins_and_shape_comes_from_cartesian_file():
    infos = _infos({"exp": [
        {"path": "a.nc", "layout": "scalar", "grid_spacing_km": (2.0, 2.0)},
        {"path": "b.nc", "layout": "cartesian_3d", "nx": 401, "ny": 399},
    ]})
    config, warnings = choose_domain_config(3, ["exp"], infos, _no_wrfout, D03_DEFAULT)
    assert config["dx_km"] == 2.0 and config["dy_km"] == 2.0
    assert (config["nx"], config["ny"]) == (401, 399)
    assert config["source"] == "processed_metadata"
    assert warnings == []


def test_wrfout_used_when_files_have_no_spacing():
    wrfout = lambda member: {"dx_km": 3.0, "dy_km": 3.0, "nx": 300, "ny": 300, "wrfout_path": "/x/wrfout_d03"}
    config, warnings = choose_domain_config(3, ["exp"], _infos({"exp": [{"path": "a.nc"}]}), wrfout, D03_DEFAULT)
    assert config["dx_km"] == 3.0 and config["source"] == "wrfout"
    assert (config["nx"], config["ny"]) == (300, 300)
    assert any("wrfout" in message for message in warnings)


def test_default_table_only_as_last_resort_with_warning():
    config, warnings = choose_domain_config(3, ["exp"], _infos({}), _no_wrfout, D03_DEFAULT)
    assert config["dx_km"] == 1.0 and config["source"] == "fallback"
    assert any("默认 1 km" in message for message in warnings)


def test_members_with_different_spacing_are_rejected():
    infos = _infos({
        "a": [{"path": "a.nc", "grid_spacing_km": (1.0, 1.0)}],
        "b": [{"path": "b.nc", "grid_spacing_km": (2.0, 2.0)}],
    })
    with pytest.raises(ValueError, match="网格间距不同"):
        choose_domain_config(3, ["a", "b"], infos, _no_wrfout, D03_DEFAULT)


def test_float32_rounding_counts_as_same_spacing():
    infos = _infos({
        "a": [{"path": "a.nc", "grid_spacing_km": (1.0, 1.0)}],
        "b": [{"path": "b.nc", "grid_spacing_km": (float(np.float32(1.0000001)), 1.0)}],
    })
    config, _ = choose_domain_config(3, ["a", "b"], infos, _no_wrfout, D03_DEFAULT)
    assert config["dx_km"] == 1.0


def test_member_without_spacing_must_match_default_to_be_mixed():
    infos = _infos({"a": [{"path": "a.nc", "grid_spacing_km": (2.0, 2.0)}]})
    with pytest.raises(ValueError, match="读不到网格间距"):
        choose_domain_config(3, ["a", "b"], infos, _no_wrfout, D03_DEFAULT)


def test_polar_resolution_from_file_overrides_blank_and_mismatched_input():
    assert choose_polar_resolution({"a.nc": 0.5}, None) == (0.5, [])
    value, warnings = choose_polar_resolution({"a.nc": 0.5}, 1.0)
    assert value == 0.5 and "已按文件" in warnings[0]
    assert choose_polar_resolution({}, 2.0) == (2.0, [])
    assert choose_polar_resolution({"a.nc": 1.0, "b.nc": 1.0}, 1.0) == (1.0, [])
    with pytest.raises(ValueError, match="径向间隔不同"):
        choose_polar_resolution({"a.nc": 1.0, "b.nc": 2.0}, None)


# ---------------------------------------------------------------- 真实 NetCDF（需要 netCDF4）

def _spec(name, layout):
    return SimpleNamespace(name=name, storage_layout=layout, units="K", options={"algorithm_version": "web-test"})


def _write(root, task, values, layout, metadata):
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    storage = NetCDFStorage(root, time_origin="2023-09-01_00:00:00")
    return storage.save(task, values, _spec(task.variable, layout), metadata=metadata)


def test_plan_reads_2km_grid_from_processed_metadata(tmp_path):
    pytest.importorskip("netCDF4")
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.data_manager import DataManager
    from 处理数据_v3.wrf_processing_v3.request import Task

    root = tmp_path / "processed_v3"
    _write(root, Task("exp", 3, 0, "theta", 100), np.ones((40, 50), dtype=np.float32), "cartesian_3d",
           {"grid_spacing": {"dx_km": 2.0, "dy_km": 2.0}})
    request = PlotRequest(
        member_name="exp", data_root=root, fig_type=0,
        time={"values": [0]}, height={"values": [100]},
        variables=[{"name": "theta", "use_polar": False}],
    )
    plan = DataManager.from_plot_request(request).plan_for_plot(request)
    config = plan.as_dict()["domain_config"]
    assert config["dx_km"] == 2.0 and config["dy_km"] == 2.0
    assert (config["nx"], config["ny"]) == (50, 40)
    assert config["source"] == "processed_metadata"


def test_plan_reads_polar_radius_resolution_from_file(tmp_path):
    pytest.importorskip("netCDF4")
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.data_manager import DataManager
    from 处理数据_v3.wrf_processing_v3.request import Task

    root = tmp_path / "processed_v3"
    _write(root, Task("exp", 3, 0, "theta_polar", 100), np.ones((360, 21), dtype=np.float32), "polar_3d",
           {"grid_spacing": {"dx_km": 2.0, "dy_km": 2.0}, "radius_resolution_km": 0.5})
    request = PlotRequest(
        member_name="exp", data_root=root, fig_type=1,
        time={"values": [0]}, height={"values": [100]},
        variables=[{"name": "theta"}],
    )
    plan = DataManager.from_plot_request(request).plan_for_plot(request)
    assert plan.polar_radius_resolution_km == 0.5
    assert plan.as_dict()["domain_config"]["dx_km"] == 2.0


def test_dry_run_reports_detected_grid(tmp_path):
    pytest.importorskip("netCDF4")
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.plot_service import PlotService
    from 处理数据_v3.wrf_processing_v3.request import Task

    root = tmp_path / "processed_v3"
    _write(root, Task("exp", 3, 0, "theta", 100), np.ones((30, 30), dtype=np.float32), "cartesian_3d",
           {"grid_spacing": {"dx_km": 3.0, "dy_km": 3.0}})
    request = PlotRequest(
        member_name="exp", data_root=root, processed_root=root, fig_type=0,
        time={"values": [0]}, height={"values": [100]},
        variables=[{"name": "theta", "use_polar": False}],
    )
    plan = PlotService(figure_root=tmp_path / "figures").dry_run(request)["plan"]
    assert plan["domain_config"]["dx_km"] == 3.0
    assert plan["domain_config"]["source"] == "processed_metadata"
