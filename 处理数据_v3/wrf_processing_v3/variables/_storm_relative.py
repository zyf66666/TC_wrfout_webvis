"""风暴相对风（2026-11-03）：从 ua、va 减去风暴移速，再算各种与风有关的诊断。

命名约定（用户 11-03）：与风速有关的变量，后缀 `_sr` 表示用风暴相对风计算；末尾再带 1 的
（如 `vr_sr1`）表示相对当前高度的气压中心 layer_center 分解，否则相对海平面中心 slp_center。

风暴移速与超梯度诊断（_agradient.py）同一口径：海平面中心经纬度 center_geo 在 t ± 3 h 的中心差分，
公式 motion_from_centers。t − 3 h 早于 0 时用 [t, t + 3 h] 向前差分；t + 3 h 的中心还不存在
（超出已算的中心时次，通常是模拟末尾）时用 [t − 3 h, t] 向后差分。差分方式写进切片元数据。
t ± 3 h 的中心从已处理的 center_geo 文件读取（basic_tc 套餐每 5 min 都有）；当前时次的中心是声明的依赖。

若某次配置打开了旧开关 motion_correct（ua、va 本身已减移速），注册表会把这些变量的风依赖换成
私有的地球相对风 _ua_earth、_va_earth，这里按声明的依赖名读取，避免减两次移速。
"""

from __future__ import annotations

import numpy as np

from .. import common
from ..common import moist_air_density
from ..request import Task
from ._agradient import motion_from_centers
from ._helpers import center, save


def _geo(ctx, minute):
    if minute < 0:
        return None
    task = Task(ctx.task.experiment, ctx.task.domain, int(minute), "center_geo", None)
    try:
        if not ctx.storage.exists(task):
            return None
        return np.asarray(ctx.storage.load(task), dtype=float).ravel()[:2]
    except (FileNotFoundError, KeyError):
        return None


def storm_motion(ctx):
    """返回 (u, v, 差分方式)，单位 m s-1。"""
    half = int(round(float(ctx.spec.options.get("motion_half_window_h", 3.0)) * 60))
    t = int(ctx.task.time_minutes)
    now = np.asarray(ctx.read("center_geo"), dtype=float).ravel()[:2]
    before, after = _geo(ctx, t - half), _geo(ctx, t + half)
    if before is not None and after is not None:
        start, end, seconds, method = before, after, 2 * half * 60.0, "central"
    elif after is not None:
        start, end, seconds, method = now, after, half * 60.0, "forward"
    elif before is not None:
        start, end, seconds, method = before, now, half * 60.0, "backward"
    else:
        raise ValueError(
            f"算风暴移速需要 {t - half} 或 {t + half} min 的海平面中心经纬度 center_geo；请先处理 basic_tc 套餐的海平面中心"
        )
    u, v = motion_from_centers(start[0], start[1], end[0], end[1], seconds)
    return u, v, method


def wind_name(ctx, component):
    """component 为 "ua" 或 "va"；motion_correct 打开时依赖已换成 _ua_earth / _va_earth。"""
    earth = f"_{component}_earth"
    names = {getattr(dep, "name", None) for dep in getattr(ctx.spec, "dependencies", ()) or ()}
    return earth if earth in names else component


def _metadata(ctx, motion_u, motion_v, method, derived_from):
    return {
        "derived_from": derived_from,
        "storm_relative": True,
        "storm_motion_u_v_m_s": [float(motion_u), float(motion_v)],
        "motion_method": method,
        "motion_half_window_h": float(ctx.spec.options.get("motion_half_window_h", 3.0)),
    }


def relative_component(ctx, component):
    """笛卡尔坐标的风暴相对 ua_sr / va_sr。"""
    motion_u, motion_v, method = storm_motion(ctx)
    motion = motion_u if component == "ua" else motion_v
    values = np.asarray(ctx.read(wind_name(ctx, component)), dtype=float) - motion
    save(ctx, values, units="m s-1", **_metadata(ctx, motion_u, motion_v, method, [component, "center_geo"]))


def _radial_tangential(ctx, *, layer):
    """返回 (vr, vt, 中心名, 移速与差分方式)。"""
    motion_u, motion_v, method = storm_motion(ctx)
    center_name = "layer_center" if layer else "slp_center"
    cy, cx = center(ctx, center_name)
    dx, dy = common.grid_km(ctx)
    u = np.asarray(ctx.read(wind_name(ctx, "ua")), dtype=float) - motion_u
    v = np.asarray(ctx.read(wind_name(ctx, "va")), dtype=float) - motion_v
    parts = [
        common.radial_tangential_wind(u, v, center_y=cy, center_x=cx, dx_km=dx, dy_km=dy, component=part)
        for part in ("vr", "vt")
    ]
    return parts[0], parts[1], center_name, (motion_u, motion_v, method)


def process_component(ctx, component, *, layer):
    """vr_sr / vt_sr / vr_sr1 / vt_sr1：径向（向外为正）或切向（逆时针为正）风暴相对风。"""
    vr, vt, center_name, motion = _radial_tangential(ctx, layer=layer)
    save(
        ctx, vr if component == "vr" else vt, units="m s-1",
        center_mode="layer" if layer else "slp",
        **_metadata(ctx, *motion, ["ua", "va", center_name, "center_geo"]),
    )


def inflow_angle(ctx, *, layer):
    """inflow_angle_sr / inflow_angle_sr1：atan2(−vr, vt)，与 inflow_angle 同一公式，只是用风暴相对风。"""
    vr, vt, center_name, motion = _radial_tangential(ctx, layer=layer)
    save(
        ctx, np.degrees(np.arctan2(-vr, vt)), units="degree",
        center_mode="layer" if layer else "slp",
        **_metadata(ctx, *motion, ["ua", "va", center_name, "center_geo"]),
    )


def radial_moisture_flux(ctx, *, layer):
    """qflux_r_sr / qflux_r_sr1：ρ × QVAPOR × vr，与 qflux_r 同一公式，只是用风暴相对径向风。"""
    vr, _, center_name, motion = _radial_tangential(ctx, layer=layer)
    qvapor = ctx.read("QVAPOR")
    rho = moist_air_density(ctx.read("pressure"), ctx.read("temp"), qvapor)
    save(
        ctx, rho * qvapor * vr, units="kg m-2 s-1",
        center_mode="layer" if layer else "slp",
        **_metadata(ctx, *motion, ["ua", "va", "pressure", "temp", "QVAPOR", center_name, "center_geo"]),
    )


def rmw_by_height(ctx, *, layer):
    """rmw_by_height_sr / rmw_by_layer_center_sr：风暴相对切向风方位平均最大处的半径（km）。

    与 rmw_by_height / rmw_by_layer_center 同一算法，只是切向风用风暴相对风。整圈平均时均匀移速的
    切向分量基本抵消，所以数值通常与相对地面的版本几乎相同，只在中心附近离散采样或域边缘不完整的圆环处略有差别。
    """
    _, vt, center_name, motion = _radial_tangential(ctx, layer=layer)
    cy, cx = center(ctx, center_name)
    dx, dy = common.grid_km(ctx)
    radii, means = common.azimuthal_mean(vt, center_y=cy, center_x=cx, dx_km=dx, dy_km=dy)
    index = int(np.nanargmax(means))
    save(
        ctx, np.asarray([radii[index]]), units="km",
        center_mode="layer_center" if layer else "slp_center",
        axis_mean_max_wind_m_s=float(means[index]),
        **_metadata(ctx, *motion, ["ua", "va", center_name, "center_geo"]),
    )
