from __future__ import annotations

import numpy as np

from wrf_webvis.app.core.registry import VARIABLE_REGISTRY
from wrf_webvis.app.core.schemas import VariableDimension
from wrf_processing.diagnostics import ONE_D_DIAGNOSTIC_VARIABLES
from wrf_processing.diagnostics import common


# 功能说明：给出风速先超过再降到 34 节以下的径向剖面，确认半径在最外侧交点按线性插值计算。
def test_r34_interpolates_the_first_outer_34_knot_crossing():
    threshold_mps = 34.0 * 0.514444444

    radius = common.outer_threshold_radius(
        radii_km=np.asarray([0.0, 20.0, 40.0, 60.0]),
        azimuthal_mean_wind_mps=np.asarray([5.0, 25.0, 20.0, 15.0]),
        threshold_mps=threshold_mps,
    )

    expected = 40.0 + (threshold_mps - 20.0) / (15.0 - 20.0) * 20.0
    np.testing.assert_allclose(radius, expected)


# 功能说明：给出峰值始终不到 34 节的风速剖面，确认系统以 NaN 表示没有 R34 半径。
def test_r34_is_nan_when_peak_wind_does_not_reach_34_knots():
    radius = common.outer_threshold_radius(
        radii_km=np.asarray([0.0, 20.0, 40.0]),
        azimuthal_mean_wind_mps=np.asarray([2.0, 17.0, 10.0]),
        threshold_mps=34.0 * 0.514444444,
    )

    assert np.isnan(radius)


# 功能说明：给出到计算区域边缘仍高于 34 节的风速，确认没有外侧交点时返回 NaN。
def test_r34_is_nan_when_wind_remains_above_34_knots_at_domain_edge():
    radius = common.outer_threshold_radius(
        radii_km=np.asarray([0.0, 20.0, 40.0]),
        azimuthal_mean_wind_mps=np.asarray([2.0, 25.0, 20.0]),
        threshold_mps=34.0 * 0.514444444,
    )

    assert np.isnan(radius)


# 功能说明：检查变量登记和一维诊断量清单，确认 r_34 已作为一维诊断量公开。
def test_r34_is_registered_as_a_one_dimensional_diagnostic():
    assert "r_34" in VARIABLE_REGISTRY
    assert VARIABLE_REGISTRY["r_34"].dimension == VariableDimension.ONE_D
    assert "r_34" in ONE_D_DIAGNOSTIC_VARIABLES
