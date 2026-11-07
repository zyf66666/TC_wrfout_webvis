"""风暴相对风 vr_sr / vt_sr / vr_sr1 / vt_sr1（2026-11-03）：减去 center_geo 在 t ± 3 h 中心差分的移速。"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from wrf_processing_v3.register import build_registry
from wrf_processing_v3.variables import (
    inflow_angle_sr, inflow_angle_sr1, qflux_r_sr, qflux_r_sr1, ua_sr, va_sr, vr_sr, vr_sr1, vt_sr, vt_sr1,
)
from wrf_processing_v3.variables._agradient import EARTH_RADIUS_M


def test_registry_dependencies_and_polar_names():
    registry = build_registry({}, times_minutes=(0,))
    for name, center in [("vr_sr", "slp_center"), ("vt_sr", "slp_center"), ("vr_sr1", "layer_center"), ("vt_sr1", "layer_center")]:
        spec = registry.resolve(name)
        assert spec.dimensions == "height" and spec.units == "m s-1"
        assert [(dep.name, dep.height_m) for dep in spec.dependencies] == [
            ("ua", "same"), ("va", "same"), (center, "same"), ("center_geo", None),
        ]
        assert spec.options["motion_half_window_h"] == 3.0
    # 以 1 结尾的风暴相对风本身就按逐层中心分解：X1_polar 的源变量是它自己
    layered = registry.resolve("vr_sr1_polar")
    assert [dep.name for dep in layered.dependencies] == ["vr_sr1", "layer_center"]
    surface = registry.resolve("vt_sr_polar")
    assert [dep.name for dep in surface.dependencies] == ["vt_sr", "slp_center"]


def _ctx(variable, minute, geos, u, v, center=(10.0, 10.0)):
    """geos: {分钟: (lat, lon)}；当前时次也要在里面。"""

    class Storage:
        def exists(self, task):
            return task.variable == "center_geo" and task.time_minutes in geos

        def load(self, task):
            return np.asarray(geos[task.time_minutes])

    data = {"ua": np.full((21, 21), u), "va": np.full((21, 21), v),
            "slp_center": np.asarray(center), "layer_center": np.asarray(center),
            "pressure": np.full((21, 21), 90000.0), "temp": np.full((21, 21), 290.0),
            "QVAPOR": np.full((21, 21), 0.015)}

    class Context:
        task = SimpleNamespace(variable=variable, experiment="exp", domain=3, time_minutes=minute, height_m=1000)
        spec = SimpleNamespace(options={"motion_half_window_h": 3.0}, units="m s-1")
        options = {"domain_config": {3: {"dx_km": 1.0, "dy_km": 1.0}}}
        storage = Storage()

        def read(self, name):
            return np.asarray(geos[minute]) if name == "center_geo" else data[name]

        def save(self, name, values, metadata=None):
            self.saved = (name, np.asarray(values), metadata)

    return Context()


def _lon_step(speed_ms, seconds, lat=20.0):
    return np.rad2deg(speed_ms * seconds / (EARTH_RADIUS_M * np.cos(np.deg2rad(lat))))


@pytest.mark.parametrize("module", [vr_sr, vt_sr, vr_sr1, vt_sr1])
def test_wind_equal_to_storm_motion_gives_zero(module):
    # 风暴以 6 m/s 向东移动；均匀东风 6 m/s，风暴相对风应处处为 0
    step = _lon_step(6.0, 3 * 3600)
    geos = {180: (20.0, 120.0 - step), 360: (20.0, 120.0), 540: (20.0, 120.0 + step)}
    ctx = _ctx(module.__name__.rsplit(".", 1)[-1], 360, geos, 6.0, 0.0)
    module.process(ctx)
    np.testing.assert_allclose(ctx.saved[1], 0.0, atol=1e-6)
    assert ctx.saved[2]["motion_method"] == "central"
    np.testing.assert_allclose(ctx.saved[2]["storm_motion_u_v_m_s"], [6.0, 0.0], atol=1e-6)


def test_relative_wind_is_projected_after_subtracting_motion():
    # 风暴向北 5 m/s，均匀北风分量 v = 5 + 2：风暴相对风为 v' = 2（向北）
    lat_step = np.rad2deg(5.0 * 3 * 3600 / EARTH_RADIUS_M)
    geos = {0: (20.0, 120.0), 180: (20.0 + lat_step, 120.0)}
    ctx = _ctx("vr_sr", 0, geos, 0.0, 7.0)
    vr_sr.process(ctx)
    values = ctx.saved[1]
    assert ctx.saved[2]["motion_method"] == "forward"   # 开头没有 t − 3 h
    np.testing.assert_allclose(values[20, 10], 2.0, atol=1e-6)    # 中心正北：向外为正
    np.testing.assert_allclose(values[0, 10], -2.0, atol=1e-6)    # 中心正南
    np.testing.assert_allclose(values[10, 20], 0.0, atol=1e-6)    # 中心正东：与径向垂直
    ctx = _ctx("vt_sr", 0, geos, 0.0, 7.0)
    vt_sr.process(ctx)
    np.testing.assert_allclose(ctx.saved[1][10, 20], 2.0, atol=1e-6)  # 正东处向北 = 逆时针


def test_backward_difference_at_the_end_and_error_without_neighbours():
    step = _lon_step(4.0, 3 * 3600)
    ctx = _ctx("vt_sr1", 600, {420: (20.0, 120.0), 600: (20.0, 120.0 + step)}, 4.0, 0.0)
    vt_sr1.process(ctx)
    assert ctx.saved[2]["motion_method"] == "backward"
    np.testing.assert_allclose(ctx.saved[1], 0.0, atol=1e-6)
    with pytest.raises(ValueError, match="center_geo"):
        vr_sr.process(_ctx("vr_sr", 600, {600: (20.0, 120.0)}, 1.0, 0.0))


# ---- 11-03：其余与风有关的 _sr 变量 ----

def test_all_storm_relative_variables_registered_with_center_geo():
    from wrf_processing_v3._scientific_registry import LAYER_CENTER_WINDS, STORM_RELATIVE_VARIABLES

    registry = build_registry({}, times_minutes=(0,))
    for name in STORM_RELATIVE_VARIABLES:
        spec = registry.resolve(name)
        assert spec.dimensions == "height" and spec.options["algorithm_version"] == "storm_relative_v1"
        assert spec.dependencies[-1] == registry.resolve("vr_sr").dependencies[-1]  # center_geo，无高度
        assert ("layer_center" in [dep.name for dep in spec.dependencies]) == name.endswith("1")
        if name.endswith("1"):
            assert name in LAYER_CENTER_WINDS
            polar = registry.resolve(f"{name}_polar")
            assert [dep.name for dep in polar.dependencies] == [name, "layer_center"]
    assert [dep.name for dep in registry.resolve("ua_sr").dependencies] == ["ua", "center_geo"]
    assert [dep.name for dep in registry.resolve("inflow_angle_sr1_polar").dependencies] == ["inflow_angle_sr1", "layer_center"]
    assert [dep.name for dep in registry.resolve("vr_sr1_ventilation_polar").dependencies] == [
        "density1_polar", "theta_e1_asy_polar", "vr_sr1_polar", "vr_sr1_asy_polar",
    ]


def test_motion_correct_switch_does_not_subtract_twice():
    from wrf_processing_v3._scientific_registry import STORM_RELATIVE_VARIABLES

    registry = build_registry({"motion_correct": True}, times_minutes=(0,))
    for name in STORM_RELATIVE_VARIABLES:
        names = [dep.name for dep in registry.resolve(name).dependencies]
        assert "ua" not in names and "va" not in names
        assert {"_ua_earth", "_va_earth"} & set(names)
    # 处理器按声明的依赖名读风
    step = _lon_step(6.0, 3 * 3600)
    geos = {180: (20.0, 120.0 - step), 360: (20.0, 120.0), 540: (20.0, 120.0 + step)}
    ctx = _ctx("ua_sr", 360, geos, 6.0, 0.0)
    ctx.spec = SimpleNamespace(options={"motion_half_window_h": 3.0}, units="m s-1",
                               dependencies=registry.resolve("ua_sr").dependencies)
    original = ctx.read
    ctx.read = lambda name: np.full((21, 21), 6.0) if name == "_ua_earth" else (
        (_ for _ in ()).throw(AssertionError("读了已减移速的 ua")) if name == "ua" else original(name))
    ua_sr.process(ctx)
    np.testing.assert_allclose(ctx.saved[1], 0.0, atol=1e-6)


def test_cartesian_inflow_angle_and_moisture_flux():
    from wrf_processing_v3.common import moist_air_density

    # 风暴向东 5 m/s；均匀风 (u, v) = (5, 3)：风暴相对风 (0, 3)
    step = _lon_step(5.0, 3 * 3600)
    geos = {180: (20.0, 120.0 - step), 360: (20.0, 120.0), 540: (20.0, 120.0 + step)}
    ctx = _ctx("ua_sr", 360, geos, 5.0, 3.0)
    ua_sr.process(ctx)
    np.testing.assert_allclose(ctx.saved[1], 0.0, atol=1e-6)
    assert ctx.saved[2]["storm_relative"] is True
    ctx = _ctx("va_sr", 360, geos, 5.0, 3.0)
    va_sr.process(ctx)
    np.testing.assert_allclose(ctx.saved[1], 3.0, atol=1e-6)

    # 中心正东格点：风暴相对风向北 3 m/s = 纯切向（逆时针），入流角 0；正北格点：向外，入流角 −90°
    for module in (inflow_angle_sr, inflow_angle_sr1):
        ctx = _ctx(module.__name__.rsplit(".", 1)[-1], 360, geos, 5.0, 3.0)
        module.process(ctx)
        np.testing.assert_allclose(ctx.saved[1][10, 20], 0.0, atol=1e-6)
        np.testing.assert_allclose(ctx.saved[1][20, 10], -90.0, atol=1e-6)

    for module in (qflux_r_sr, qflux_r_sr1):
        ctx = _ctx(module.__name__.rsplit(".", 1)[-1], 360, geos, 5.0, 3.0)
        module.process(ctx)
        rho = moist_air_density(np.full((21, 21), 90000.0), np.full((21, 21), 290.0), np.full((21, 21), 0.015))
        np.testing.assert_allclose(ctx.saved[1][20, 10], (rho * 0.015 * 3.0)[20, 10], rtol=1e-6)   # 正北：向外
        np.testing.assert_allclose(ctx.saved[1][10, 20], 0.0, atol=1e-9)                          # 正东：无径向


def test_rmw_by_height_storm_relative():
    from wrf_processing_v3._scientific_registry import STORM_RELATIVE_ALL
    from wrf_processing_v3.variables import rmw_by_height_sr, rmw_by_layer_center_sr

    registry = build_registry({}, times_minutes=(0,))
    assert [(d.name, d.height) for d in registry.resolve("rmw_by_height_sr").dependencies] == [
        ("ua", "same"), ("va", "same"), ("slp_center", None), ("center_geo", None)]
    assert [d.name for d in registry.resolve("rmw_by_layer_center_sr").dependencies] == ["ua", "va", "layer_center", "center_geo"]
    assert registry.resolve("rmw_by_height_sr").dimensions == "height_scalar"
    corrected = build_registry({"motion_correct": True}, times_minutes=(0,))
    assert {"rmw_by_height_sr", "rmw_by_layer_center_sr"} <= set(STORM_RELATIVE_ALL)
    assert "ua" not in [d.name for d in corrected.resolve("rmw_by_layer_center_sr").dependencies]

    # 风暴向东 5 m/s；相对地面的风 = 移速 + 以 (10,10) 为中心、半径 4 km 处最大的兰金涡
    step = _lon_step(5.0, 3 * 3600)
    geos = {180: (20.0, 120.0 - step), 360: (20.0, 120.0), 540: (20.0, 120.0 + step)}
    yy, xx = np.indices((21, 21), dtype=float)
    dx, dy = xx - 10.0, yy - 10.0
    r = np.hypot(dx, dy)
    speed = np.where(r <= 4.0, 10.0 * r / 4.0, 10.0 * 4.0 / np.maximum(r, 1e-9))
    with np.errstate(invalid="ignore", divide="ignore"):
        u = np.where(r > 0, -speed * dy / r, 0.0) + 5.0
        v = np.where(r > 0, speed * dx / r, 0.0)
    for module in (rmw_by_height_sr, rmw_by_layer_center_sr):
        ctx = _ctx(module.__name__.rsplit(".", 1)[-1], 360, geos, 0.0, 0.0)
        original = ctx.read
        ctx.read = lambda name, original=original: u if name == "ua" else v if name == "va" else original(name)
        module.process(ctx)
        np.testing.assert_allclose(ctx.saved[1], [4.0])
        assert ctx.saved[2]["storm_relative"] is True and ctx.saved[2]["motion_method"] == "central"
