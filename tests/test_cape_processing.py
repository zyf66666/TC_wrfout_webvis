from __future__ import annotations

import numpy as np

from wrf_webvis.app.core.registry import VARIABLE_REGISTRY
from wrf_webvis.app.core.schemas import VariableDimension
from wrf_processing import processing_manager


# 功能说明：构造含四个 CAPE 分量的三维数组，确认系统取出第一个 MCAPE 平面作为二维 CAPE。
def test_cape_uses_the_mcape_component_of_cape_2d():
    cape_2d = np.stack(
        [
            np.full((2, 3), 1000.0),
            np.full((2, 3), -50.0),
            np.full((2, 3), 800.0),
            np.full((2, 3), 1200.0),
        ]
    )

    cape = processing_manager.prepare_wrf_output("cape", cape_2d)

    np.testing.assert_array_equal(cape, cape_2d[0])


# 功能说明：核对变量登记表，确认 CAPE 的原始字段为 cape_2d，且按需要极坐标处理的二维变量登记。
def test_cape_is_registered_as_a_polar_two_dimensional_variable():
    assert "cape" in VARIABLE_REGISTRY
    cape = VARIABLE_REGISTRY["cape"]
    assert cape.wrf_name == "cape_2d"
    assert cape.dimension == VariableDimension.TWO_D
    assert cape.needs_polar is True
