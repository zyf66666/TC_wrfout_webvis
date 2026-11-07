import matplotlib

matplotlib.use("Agg")

from wrf_webvis.app.plotting.draw_main import resolve_figure_layout
from wrf_webvis.app.plotting.draw_format import colorbar_layout, resolve_colorbar_ticks
from wrf_webvis.app.plotting.draw_variables import apply_postprocess
import numpy as np


# 功能说明：指定多面板图尺寸，确认画布按面板数扩大。
def test_explicit_figsize_scales_with_multi_panel_plot():
    # Existing dashboard requests store the dimensions of each panel.
    assert resolve_figure_layout({"figsize": [18, 5]}, 1, 3, {}, 3) == (54.0, 5.0)


# 功能说明：生成时间半径仪表盘布局，确认每个面板仍有可读空间。
def test_time_radius_dashboard_keeps_each_panel_readable():
    assert resolve_figure_layout({"figsize": [9, 15]}, 2, 8, {}, 2) == (72.0, 30.0)


# 功能说明：使用竖直色标时检查位置，确认它占用坐标轴外的预留区域。
def test_vertical_colorbar_is_reserved_outside_axes():
    layout = colorbar_layout({}, has_colorbar=True, orientation="vertical")
    assert layout["left"] > 0.90
    assert layout["axes_right"] < layout["left"]


# 功能说明：请求过多色标刻度，确认只压缩刻度数而不改颜色范围。
def test_colorbar_ticks_are_capped_at_eleven_without_changing_range():
    ticks = resolve_colorbar_ticks(0.0, 120.0, 5.0)
    assert len(ticks) <= 11
    assert ticks[0] == 0.0
    assert ticks[-1] == 120.0


# 功能说明：请求九个色标刻度，确认低于上限时完整保留。
def test_colorbar_keeps_nine_requested_ticks_when_under_default_limit():
    ticks = resolve_colorbar_ticks(0.0, 40.0, 5.0)
    assert ticks.tolist() == [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0]


# 功能说明：平滑方位角边界两端的数据，确认首尾相接而不产生断裂。
def test_angle_smoothing_wraps_across_the_time_azimuth_boundary():
    values = np.zeros((3, 360), dtype=float)
    values[1, 0] = 1.0

    smoothed = apply_postprocess(
        "bl_mfc",
        values,
        {"angle_smooth_sigma": 2.0, "time_smooth_sigma": 0.0},
    )

    assert smoothed[1, -1] > 0.0
    assert smoothed[1, 1] > 0.0
    assert smoothed[0, 0] == 0.0
