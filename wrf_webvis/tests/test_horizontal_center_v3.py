"""水平图（center_km）必须以 processed_v3 的 slp_center 为原点，而不是悄悄退回网格中点。"""

from __future__ import annotations

import warnings
from types import SimpleNamespace

import numpy as np
import pytest


def _spec(layout):
    return SimpleNamespace(name="x", storage_layout=layout, units="", options={"algorithm_version": "web-test"})


def _v3_root_with_centers(tmp_path):
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    root = tmp_path / "processed_v3"
    storage = NetCDFStorage(root, time_origin="2023-09-01_00:00:00")
    # 22 h 的地面中心偏离网格中点；1000 m 层中心另取一个值。
    storage.save(Task("exp", 3, 22 * 60, "slp_center", None), np.array([3, 7], dtype=np.float32), _spec("vector"))
    storage.save(Task("exp", 3, 22 * 60, "layer_center", 1000), np.array([5, 6], dtype=np.float32), _spec("height_vector"))
    return root


def test_horizontal_km_origin_uses_v3_slp_center(tmp_path):
    from wrf_webvis.app.plotting.draw_variables import resolve_horizontal_center_grid
    from wrf_webvis.app.plotting.draw_format import resolve_center

    root = _v3_root_with_centers(tmp_path)
    data_center = resolve_horizontal_center_grid(
        data_root=root, member_name="exp", domain=3, times=[22.0], grid_shape=(11, 11),
        fmt={"center_enabled": True, "coordinate_system": "center_km"},
    )
    format_center = resolve_center(
        data_root=root, member_name="exp", domain=3, times=[22.0], grid_shape=(11, 11),
        center_enabled=True, center_path=None, center_height_m=None, center_index=None,
    )
    assert data_center == (3.0, 7.0)
    # 数据坐标与叠加层（RMW、高层中心等）必须用同一个中心。
    assert format_center == data_center


def test_horizontal_km_origin_uses_v3_layer_center(tmp_path):
    from wrf_webvis.app.plotting.draw_variables import resolve_horizontal_center_grid

    root = _v3_root_with_centers(tmp_path)
    center = resolve_horizontal_center_grid(
        data_root=root, member_name="exp", domain=3, times=[22.0], grid_shape=(11, 11),
        fmt={"center_enabled": True, "center_height_m": 1000},
    )
    assert center == (5.0, 6.0)


def test_missing_center_warns_and_falls_back_to_grid_midpoint(tmp_path):
    from wrf_webvis.app.plotting.draw_variables import resolve_horizontal_center_grid

    root = _v3_root_with_centers(tmp_path)
    with pytest.warns(UserWarning, match="找不到中心"):
        center = resolve_horizontal_center_grid(
            data_root=root, member_name="exp", domain=3, times=[23.0], grid_shape=(11, 11),
            fmt={"center_enabled": True},
        )
    assert center == (5.0, 5.0)


def test_center_disabled_uses_grid_midpoint_without_warning(tmp_path):
    from wrf_webvis.app.plotting.draw_variables import resolve_horizontal_center_grid

    root = _v3_root_with_centers(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        center = resolve_horizontal_center_grid(
            data_root=root, member_name="exp", domain=3, times=[22.0], grid_shape=(11, 11),
            fmt={"center_enabled": False},
        )
    assert center == (5.0, 5.0)


# ---- 其余读中心的地方：三维图、时间—方位图的层中心方向、路径图、路径图的文件检查 ----

def _save(root, task, values, layout):
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    return NetCDFStorage(root, time_origin="2023-09-01_00:00:00").save(task, np.asarray(values, dtype=np.float32), _spec(layout))


def test_3d_surface_and_layer_centers_use_v3(tmp_path):
    import plotly.graph_objects as go
    from wrf_webvis.app.plotting.plotly_3d import add_layer_center_trace, load_surface_center
    from 处理数据_v3.wrf_processing_v3.request import Task

    root = _v3_root_with_centers(tmp_path)
    assert load_surface_center(root, "exp", 3, 22.0, (11, 11)) == (3.0, 7.0)

    with pytest.warns(UserWarning, match="2 km"):
        trace = add_layer_center_trace(
            fig=go.Figure(), data_root=root, member_name="exp", domain=3, time_hour=22.0,
            surface_center=(3.0, 7.0), dx_km=1.0, dy_km=1.0, center_heights_km=[1, 2],
        )
    # 地面点 + 1 km 层中心 (5, 6)：x = 6-7 = -1，y = 5-3 = 2。
    np.testing.assert_allclose(trace["x"], [0.0, -1.0])
    np.testing.assert_allclose(trace["y"], [0.0, 2.0])
    np.testing.assert_allclose(trace["z"], [0.0, 1.0])

    with pytest.warns(UserWarning, match="网格中点"):
        assert load_surface_center(root, "exp", 3, 23.0, (11, 11)) == (5.0, 5.0)


def test_center_height_angle_uses_v3_and_skips_missing_times(tmp_path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from wrf_webvis.app.plotting.draw_format import maybe_draw_center_height_angle
    from 处理数据_v3.wrf_processing_v3.request import Task

    root = tmp_path / "processed_v3"
    for hour, top in ((0, (5, 5)), (2, (4, 6))):
        _save(root, Task("exp", 3, hour * 60, "layer_center", 2000), [5, 5], "height_vector")
        _save(root, Task("exp", 3, hour * 60, "layer_center", 6000), top, "height_vector")
    _save(root, Task("exp", 3, 60, "layer_center", 2000), [5, 5], "height_vector")  # 1 h 缺 6000 m

    fig, ax = plt.subplots()
    try:
        with pytest.warns(UserWarning, match="1 h"):
            maybe_draw_center_height_angle(
                ax, root, "exp", 3, [0, 1, 2], {"center_height_m": 6000, "center_reference_height_m": 2000}
            )
        line = ax.get_lines()[0]
        # 角度与时次一一对应：缺的 1 h 被跳过，不会把 2 h 的角度画到 1 h 上。
        assert list(line.get_ydata()) == [0, 2]
    finally:
        plt.close(fig)


def test_track_uses_v3_slp_center_and_center_geo(tmp_path):
    from wrf_webvis.app.plotting.track_plot import load_center_track
    from 处理数据_v3.wrf_processing_v3.request import Task

    root = tmp_path / "processed_v3"
    _save(root, Task("exp", 3, 0, "slp_center", None), [3, 7], "vector")
    _save(root, Task("exp", 3, 60, "slp_center", None), [3, 7], "vector")
    _save(root, Task("exp", 3, 0, "center_geo", None), [20.0, 120.0], "vector")
    # 1 h 没有 center_geo：从同时次移动网格的 XLAT/XLONG 取 (3, 7) 处经纬度。
    _save(root, Task("exp", 3, 60, "XLAT", None), np.full((11, 11), 20.0), "cartesian_2d")
    _save(root, Task("exp", 3, 60, "XLONG", None), np.full((11, 11), 121.0), "cartesian_2d")

    track = load_center_track(data_root=root, member_name="exp", domain=3, times=None)

    assert track["path"].name == "slp_center.nc"
    np.testing.assert_allclose(track["times"], [0.0, 1.0])
    assert track["coordinate_source"] == "lat_lon"
    # 格点没动，但移动嵌套网格的经度东移 1°，轨迹应向东约 104 km。
    assert track["x"][1] > 100.0
    assert abs(track["y"][1]) < 1.0


def test_track_input_check_accepts_v3_slp_center(tmp_path):
    from wrf_webvis.app.core.schemas import PlotRequest, PlotType
    from wrf_webvis.app.services.plot_inputs import check_additional_plot_inputs
    from 处理数据_v3.wrf_processing_v3.request import Task

    root = tmp_path / "processed_v3"
    center = _save(root, Task("exp", 3, 0, "slp_center", None), [3, 7], "vector")
    request = PlotRequest(
        data_root=root, processed_root=root, member_name="exp", domain=3,
        selection={"members": ["exp"]}, fig_type=PlotType.TRACK,
        format={"track": {"draw_land_boundary": False}},
    )
    check = check_additional_plot_inputs(request)
    assert center in check.required_files
    assert check.missing_files == []
