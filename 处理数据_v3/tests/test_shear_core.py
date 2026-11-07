"""内核区风切变 shear_core_200_850_*（2026-11-02）：目标域 slp_center 150–350 km 环带，200–850 hPa。"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from wrf_processing_v3.register import build_registry
from wrf_processing_v3.variables import (
    shear_core_200_850_direction,
    shear_core_200_850_magnitude,
    shear_core_200_850_u,
    shear_core_200_850_v,
)


CORE_NAMES = tuple(f"shear_core_200_850_{part}" for part in ("u", "v", "magnitude", "direction"))


def test_core_shear_registered_on_target_domain():
    registry = build_registry({}, times_minutes=(0,))
    for name in CORE_NAMES:
        spec = registry.resolve(name)
        assert spec.dimensions == "scalar"
        assert spec.units == ("degree" if name.endswith("direction") else "m s-1")
        # 不指定 domain：与目标域相同（处理套餐用 d03），区别于环境切变的 d02。
        assert [(dep.name, dep.domain) for dep in spec.dependencies] == [
            ("_ua_upper", None),
            ("_ua_lower", None),
            ("_va_upper", None),
            ("_va_lower", None),
            ("slp_center", None),
        ]
        assert spec.options["shear_radius_inner_km"] == 150.0
        assert spec.options["shear_radius_outer_km"] == 350.0
        assert spec.options["algorithm_version"] == "shear_core_v1"
        # 环境切变不受影响
        assert registry.resolve(name.replace("core_", "")).dependencies[0].domain == 2


def test_core_shear_radii_can_be_overridden():
    registry = build_registry({"core_shear_radius_inner_km": 0, "core_shear_radius_outer_km": 200}, times_minutes=(0,))
    spec = registry.resolve("shear_core_200_850_u")
    assert (spec.options["shear_radius_inner_km"], spec.options["shear_radius_outer_km"]) == (0.0, 200.0)


@pytest.mark.parametrize(
    ("module", "part"),
    [
        (shear_core_200_850_u, "u"),
        (shear_core_200_850_v, "v"),
        (shear_core_200_850_magnitude, "magnitude"),
        (shear_core_200_850_direction, "direction"),
    ],
)
def test_core_shear_uses_150_to_350_km_ring_on_1km_grid(module, part):
    # d03 式 1 km 网格；U 上层随距离线性增大，所以环带均值约等于环带的平均半径（≈250 km）。
    yy, xx = np.indices((801, 801))
    distance_km = np.hypot(yy - 400, xx - 400) * 1.0
    data = {
        "slp_center": np.array([400.0, 400.0]),
        "_ua_upper": distance_km,
        "_ua_lower": np.zeros_like(distance_km),
        "_va_upper": np.full_like(distance_km, -2.0),
        "_va_lower": np.full_like(distance_km, 4.0),
    }
    spec_options = build_registry({}, times_minutes=(0,)).resolve(f"shear_core_200_850_{part}").options

    class Context:
        task = SimpleNamespace(variable=f"shear_core_200_850_{part}", domain=3)
        spec = SimpleNamespace(options=spec_options)

        def read(self, variable):
            return data[variable]

        def read_metadata(self, variable):
            assert variable == "_ua_upper"
            return {"grid_spacing": {"dx_km": 1.0, "dy_km": 1.0}}

        def save(self, variable, values, metadata=None):
            self.saved = (variable, values, metadata)
            return values

    ctx = Context()
    result = module.process(ctx)
    expected = {
        "u": 250.0,
        "v": -6.0,
        "magnitude": np.hypot(250.0, -6.0),
        "direction": -np.degrees(np.arctan2(-6.0, 250.0)),
    }
    np.testing.assert_allclose(result, [expected[part]], atol=0.6, rtol=0)
    assert ctx.saved[0] == ctx.task.variable
    assert ctx.saved[2]["shear_source"] == "target_domain"
