"""slp_center v2（平滑首猜 + 浮点 PCC）的单元测试。"""

from __future__ import annotations

import numpy as np
import pytest

from wrf_processing_v3._center_algorithm import (
    find_pressure_center,
    find_pressure_center_v2,
    pcc_float_iterate,
    pcc_basic_value,
)
from wrf_processing_v3.variables._center_options import (
    center_algorithm_options,
    slp_center_algorithm,
)


def _vortex(shape=(401, 401), center=(205.3, 198.6), depth=20.0, scale=60.0):
    yy, xx = np.indices(shape, dtype=float)
    r2 = (yy - center[0]) ** 2 + (xx - center[1]) ** 2
    return 1005.0 - depth * np.exp(-r2 / (2 * scale * scale))


def test_float_pcc_recovers_subgrid_center_from_offset_start():
    field = _vortex()
    background = pcc_basic_value(field, border=10)
    y, x, iterations, converged = pcc_float_iterate(
        field, center_y=190.0, center_x=215.0, background=background,
        pcc_radius=60, margin=70, max_iterations=80, tolerance=0.01,
    )
    assert converged
    assert abs(y - 205.3) < 0.5 and abs(x - 198.6) < 0.5


def test_v2_ignores_narrow_deep_band_that_traps_v1():
    """宽涡旋旁边有一条更低的窄雨带低压：v1 被拉到雨带，v2 留在涡旋中心。"""
    field = _vortex(depth=12.0, scale=45.0)
    yy, xx = np.indices(field.shape, dtype=float)
    # 涡旋中心东南约 35 格处，一条细长低压带，比涡旋中心低 2 hPa。
    band = np.exp(-(((yy - 180) * 0.8 + (xx - 228) * 0.6) ** 2) / 8.0 - (((yy - 180) * 0.6 - (xx - 228) * 0.8) ** 2) / 300.0)
    field = field - 14.0 * band
    assert np.hypot(*(np.subtract(np.unravel_index(np.argmin(field), field.shape), (180, 228)))) < 5

    v1 = find_pressure_center(
        field, dx_km=1.0, search_radius_km=30.0, cross_radius_grid=100,
        pcc_radius_grid=44, boundary_width=10, max_iterations=10,
    )
    y, x, _, info = find_pressure_center_v2(
        field, dx_km=1.0, first_guess_radius_km=50.0, pcc_radius_km=80.0,
        cross_radius_grid=100, boundary_width=10, max_iterations=50, tolerance_grid=0.05,
    )
    assert info["converged"]
    v1_error = np.hypot(v1[0] - 205.3, v1[1] - 198.6)
    v2_error = np.hypot(y - 205.3, x - 198.6)
    assert v1_error > 5.0  # v1 被雨带拉偏约 7.6 格
    assert v2_error < 2.0  # v2 约 1.3 格


def test_v2_with_initial_guess_stays_inside_search_radius():
    field = _vortex(center=(205.0, 260.0))
    y, x, _, _ = find_pressure_center_v2(
        field, dx_km=1.0, initial_yx=[205.0, 200.0], search_radius_km=20.0,
        pcc_radius_km=60.0, cross_radius_grid=70, boundary_width=10,
    )
    assert np.hypot(y - 205.0, x - 200.0) <= 20.0 + 1e-6
    assert x > 215.0  # 被真实中心拉向东边，停在搜索圆边上


def test_center_options_keep_v1_for_layers_and_select_v2_for_slp():
    options = {
        "center_options_by_domain": {
            3: {
                "algorithm": "smooth_fg_float_pcc_v2",
                "first_guess_radius_km": 40.0,
                "pcc_radius_km": 70.0,
                "float_max_iterations": 30,
                "search_radius_km": 30.0,
                "max_iterations": 10,
            }
        }
    }
    v1 = center_algorithm_options(options, 3)
    assert v1["max_iterations"] == 10 and "algorithm" not in v1 and "pcc_radius_km" not in v1
    name, params = slp_center_algorithm(options, 3)
    assert name == "smooth_fg_float_pcc_v2"
    assert params["max_iterations"] == 30 and params["pcc_radius_km"] == 70.0
    assert slp_center_algorithm({}, 3)[0] == "bezier_pcc_v1"


def test_unknown_center_algorithm_is_rejected():
    with pytest.raises(ValueError, match="未知的中心算法"):
        slp_center_algorithm({"center_options_by_domain": {3: {"algorithm": "magic"}}}, 3)


def test_project_config_defaults_to_reproduced_bezier_pcc():
    import project_config

    assert slp_center_algorithm(project_config.OPTIONS, 3)[0] == "bezier_pcc_v1"
    assert slp_center_algorithm(project_config.OPTIONS, 2)[0] == "bezier_pcc_v1"
    assert center_algorithm_options(project_config.OPTIONS, 3)["max_iterations"] == 10
    assert center_algorithm_options(project_config.OPTIONS, 3)["search_radius_km"] == 60
