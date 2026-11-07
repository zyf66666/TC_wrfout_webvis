from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pytest

from wrf_webvis.app.plotting import draw_format
from wrf_webvis.app.plotting.draw_format import set_format1


# 功能说明：设置半径—高度图的字体后，确认横纵坐标刻度都使用指定字体。
def test_radius_height_x_ticks_use_the_configured_font():
    fig, ax = plt.subplots()
    try:
        set_format1(
            ax,
            member_name="member",
            domain=3,
            times=[0],
            angle=[0, 360],
            draw_variables={"rh": {}},
            config={
                "common": {"fontname": "DejaVu Serif", "fontsize": 12},
                "radius_height": {"xlim": [0, 20], "ylim": [0, 1000]},
            },
        )

        assert {label.get_fontname() for label in ax.get_xticklabels()} == {"DejaVu Serif"}
        assert {label.get_fontname() for label in ax.get_yticklabels()} == {"DejaVu Serif"}
    finally:
        plt.close(fig)


def test_radius_height_ticks_preserve_requested_limits():
    fig, ax = plt.subplots()
    try:
        set_format1(ax, 'member', 3, [0], [0, 360], {'rh': {}},
                    config={'radius_height': {'xlim': [0, 380], 'radius_tick_interval_km': 50}})
        assert ax.get_xlim() == (0, 380)
        assert all(0 <= tick <= 380 for tick in ax.get_xticks())
    finally:
        plt.close(fig)


def test_radius_height_defaults_load_displayed_radius():
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.plot_service import PlotService
    request = PlotRequest(fig_type=1, format={'radius_height': {'xlim': [0, 380]}})
    PlotService()._apply_fig_defaults(request)
    assert request.radius.values == [0, 380]


def test_radius_height_defaults_to_levels_inside_displayed_height_range():
    from wrf_webvis.app.core.registry import DEFAULT_HEIGHT_LEVELS_M
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.plot_service import PlotService

    request = PlotRequest(fig_type=1, format={"radius_height": {"ylim": [0, 10000]}})
    PlotService()._apply_fig_defaults(request)

    assert request.height.values == [height for height in DEFAULT_HEIGHT_LEVELS_M if height <= 10000]


def test_radius_height_keeps_explicit_height_selection():
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.plot_service import PlotService

    request = PlotRequest(
        fig_type=1,
        height={"values": [500, 1000]},
        format={"radius_height": {"ylim": [0, 10000]}},
    )
    PlotService()._apply_fig_defaults(request)

    assert request.height.values == [500, 1000]


@pytest.mark.parametrize("source", ["rmw_by_height", "rmw_by_layer_center"])
def test_radius_height_rmw_overlay_averages_times_and_leaves_nan_gaps(tmp_path, source):
    from 处理数据_v3.wrf_processing_v3.register import build_registry
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    storage = NetCDFStorage(tmp_path / "processed_v3", time_origin="2023-09-01_00:00:00")
    spec = build_registry().resolve(source)
    for minute, values in ((0, [20, np.nan, np.nan]), (60, [30, 50, np.nan])):
        for height, value in zip((100, 200, 300), values):
            storage.save(Task("member", 3, minute, source, height), np.asarray([value]), spec)

    fig, ax = plt.subplots()
    try:
        set_format1(
            ax, "member", 3, [0, 1], [0, 360], {"vt": {}},
            data_root=tmp_path / "processed_v3", heights_m=[100, 200, 300],
            config={"radius_height": {
                "draw_rmw": True, "rmw_source": source,
                "rmw_color": "magenta", "rmw_linestyle": "--", "rmw_linewidth": 2,
                "xlim": [0, 100], "ylim": [0, 400],
            }},
        )
        assert len(ax.lines) == 1
        line = ax.lines[0]
        np.testing.assert_allclose(line.get_xdata()[:2], [25, 50])
        assert np.isnan(line.get_xdata()[2])
        np.testing.assert_array_equal(line.get_ydata(), [100, 200, 300])
        assert line.get_color() == "magenta"
        assert line.get_linestyle() == "--"
        assert line.get_linewidth() == 2
        assert line.get_label() == (
            "RMW (SLP center)" if source == "rmw_by_height" else "RMW (layer center)"
        )
        assert ax.get_legend() is None
    finally:
        plt.close(fig)


def test_radius_height_rmw_plan_checks_each_time_height_slice(tmp_path):
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.data_manager import DataManager
    from 处理数据_v3.wrf_processing_v3.register import build_registry
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    root = tmp_path / "processed_v3"
    source = "rmw_by_layer_center"
    storage = NetCDFStorage(root, time_origin="2023-09-01_00:00:00")
    storage.save(Task("member", 3, 0, source, 100), np.asarray([20]), build_registry().resolve(source))
    request = PlotRequest(
        member_name="member", data_root=root, fig_type=1,
        time={"values": [0]}, height={"values": [100, 200]},
        format={"radius_height": {"draw_rmw": True, "rmw_source": source}},
    )

    plan = DataManager.from_plot_request(request).plan_for_plot(request)

    assert [(item.variable, item.height_m, item.exists) for item in plan.required_files] == [
        (source, 100, True), (source, 200, False),
    ]


def test_radius_height_plot_service_draws_rmw_from_processed_v3(tmp_path):
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.plot_service import PlotService
    from 处理数据_v3.wrf_processing_v3.register import build_registry
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    root = tmp_path / "processed_v3"
    storage = NetCDFStorage(root, time_origin="2023-09-01_00:00:00")
    registry = build_registry()
    for height, rmw in ((100, 1.0), (200, 2.0)):
        storage.save(
            Task("member", 3, 0, "vt_polar", height),
            np.tile(np.asarray([1.0, 2.0, 3.0]) + height / 100, (4, 1)),
            registry.resolve("vt_polar"),
            metadata={"radius_resolution_km": 1.0},
        )
        storage.save(
            Task("member", 3, 0, "rmw_by_height", height),
            np.asarray([rmw]), registry.resolve("rmw_by_height"),
        )
    request = PlotRequest(
        member_name="member", data_root=root, fig_type=1,
        variables=[{"name": "vt", "plot_type": "contourf"}],
        time={"values": [0]}, height={"values": [100, 200]},
        angle={"values": [0, 360]}, radius={"values": [0, 2]},
        format={"radius_height": {"draw_rmw": True, "rmw_source": "rmw_by_height"}},
    )

    result = PlotService(figure_root=tmp_path / "figures").create_plot(request)

    assert (tmp_path / "figures" / f"{result['plot_id']}.png").is_file()
    assert str(storage.path_for(Task("member", 3, 0, "rmw_by_height", 100))) in result["used_files"]


def test_rmw_height_smoothing_uses_height_distance_and_preserves_missing_gap():
    heights = np.asarray([0, 100, 200, 1000, 1100], dtype=float)
    values = np.asarray([0, 100, np.nan, 0, 100], dtype=float)

    result = draw_format.smooth_radius_height_rmw(values, heights, sigma_km=0.1)

    expected_first = 100 * np.exp(-0.5) / (1 + np.exp(-0.5))
    np.testing.assert_allclose(result[[0, 3]], [expected_first, expected_first])
    np.testing.assert_allclose(result[[1, 4]], [100 - expected_first, 100 - expected_first])
    assert np.isnan(result[2])
    np.testing.assert_array_equal(draw_format.smooth_radius_height_rmw(values, heights, sigma_km=0), values)


def test_rmw_height_smoothing_rejects_negative_sigma():
    with pytest.raises(ValueError, match="sigma"):
        draw_format.smooth_radius_height_rmw(np.asarray([10, 20]), np.asarray([100, 200]), sigma_km=-1)


def test_radius_height_rmw_overlay_applies_smoothing_when_enabled(tmp_path):
    from 处理数据_v3.wrf_processing_v3.register import build_registry
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    storage = NetCDFStorage(tmp_path / "processed_v3", time_origin="2023-09-01_00:00:00")
    spec = build_registry().resolve("rmw_by_height")
    for height, value in ((100, 50), (200, 150), (300, 50)):
        storage.save(Task("member", 3, 0, "rmw_by_height", height), np.asarray([value]), spec)

    fig, ax = plt.subplots()
    try:
        set_format1(
            ax, "member", 3, [0], [0, 360], {"vt": {}},
            data_root=tmp_path / "processed_v3", heights_m=[100, 200, 300],
            config={"radius_height": {"draw_rmw": True, "rmw_smooth_sigma_km": 0.1}},
        )
        line = ax.lines[0]
        assert 50 < line.get_xdata()[0] < 150
        assert 50 < line.get_xdata()[1] < 150
        assert "smooth 0.1 km" in line.get_label()
        np.testing.assert_array_equal(storage.load(Task("member", 3, 0, "rmw_by_height", 200)), [150])
    finally:
        plt.close(fig)
