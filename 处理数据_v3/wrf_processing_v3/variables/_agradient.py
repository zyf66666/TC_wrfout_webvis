"""超梯度力（径向非梯度风力）诊断的共享算法（2026-10-26，Claude）。

风暴相对、等高面、f 平面的径向动量方程（向外为正，单位 m s-2）：

    Dv_r/Dt = PGF_r + CEN_r + COR_r + MOTF_r + （摩擦、湍流混合）

    PGF_r  = −(1/ρ) ∂p/∂r                    气压梯度力径向分量（等高面上求导，p 用 Pa）
    CEN_r  = v_t'² / r                       离心力项（v_t' 为风暴相对切向风）
    COR_r  = f v_t'                          科氏力项
    MOTF_r = f c_t                           匀速平移坐标系修正：科氏力作用在风暴移速 c 上的部分，
                                             c_t = −c_x sinθ + c_y cosθ 为移速在当地切向的分量；
                                             它与环境（地转）气压梯度相抵，PGF 用全气压时必须加上
    AGF_r  = 上面四项之和                     > 0 超梯度（净力向外，入流减速）；< 0 次梯度

口径：原点为 slp_center（与 vr、vt、*_polar 一致）；风暴移速由 center_geo（经纬度，适用于移动嵌套 d03）
在 t ± motion_half_window_h 的中心差分求得，窗口默认 ±3 h，用来压住地面中心逐时 5–10 km 的跳动；
忽略风暴加速度项；f 为常数（本项目 16 个试验 d03 wrfout 的 F 均为 4.9881e-5 s-1）。
网格约定与 common.radial_tangential_wind 相同：数组下标 [y, x]，y 向北、x 向东。
"""

from __future__ import annotations

import numpy as np

from .. import common
from ._helpers import center

EARTH_RADIUS_M = 6371000.0
DEFAULT_CORIOLIS_F = 4.9881e-05
DEFAULT_MOTION_HALF_WINDOW_H = 3.0
MIN_RADIUS_KM = 2.0


# ---------------------------------------------------------------- 纯数值函数（测试直接调用）

def polar_geometry(shape, center_y, center_x, dx_m, dy_m):
    """返回每个格点到中心的距离 r（m）及径向单位矢量 (cosθ, sinθ)。"""
    yy, xx = np.indices(shape, dtype=float)
    x = (xx - center_x) * dx_m
    y = (yy - center_y) * dy_m
    radius = np.hypot(x, y)
    cos = np.divide(x, radius, out=np.zeros_like(x), where=radius > 0)
    sin = np.divide(y, radius, out=np.zeros_like(y), where=radius > 0)
    return radius, cos, sin


def pressure_gradient_force_r(pressure_hpa, density, cos, sin, dx_m, dy_m):
    """−(1/ρ)(cosθ ∂p/∂x + sinθ ∂p/∂y)，p 输入 hPa。"""
    pressure = np.asarray(pressure_hpa, dtype=float) * 100.0
    dpdy, dpdx = np.gradient(pressure, dy_m, dx_m)
    return -(cos * dpdx + sin * dpdy) / np.asarray(density, dtype=float)


def tangential_wind(u, v, cos, sin):
    return -np.asarray(u, dtype=float) * sin + np.asarray(v, dtype=float) * cos


def centrifugal_r(vt, radius_m, min_radius_m=MIN_RADIUS_KM * 1000.0):
    out = np.divide(vt ** 2, radius_m, out=np.full_like(radius_m, np.nan, dtype=float), where=radius_m > 0)
    out[radius_m < min_radius_m] = np.nan
    return out


def coriolis_r(vt, f):
    return float(f) * vt


def motion_force_r(motion_u, motion_v, cos, sin, f):
    return float(f) * (-float(motion_u) * sin + float(motion_v) * cos)


def motion_from_centers(lat0, lon0, lat1, lon1, dt_seconds):
    """两个时刻中心经纬度 → 平均移速 (u, v)（m s-1），公式同 storm_motion。"""
    if dt_seconds <= 0:
        raise ValueError("移速差分的时间间隔必须为正")
    mean_latitude = np.deg2rad((lat0 + lat1) * 0.5)
    u = np.deg2rad(lon1 - lon0) * EARTH_RADIUS_M * np.cos(mean_latitude) / dt_seconds
    v = np.deg2rad(lat1 - lat0) * EARTH_RADIUS_M / dt_seconds
    return float(u), float(v)


# ---------------------------------------------------------------- 读取 ctx 的组合函数

def coriolis_f(ctx):
    return float(ctx.spec.options.get("coriolis_f", DEFAULT_CORIOLIS_F))


def half_window_minutes(ctx):
    return int(round(float(ctx.spec.options.get("motion_half_window_h", DEFAULT_MOTION_HALF_WINDOW_H)) * 60))


def storm_motion(ctx):
    half = half_window_minutes(ctx)
    t = int(ctx.task.time_minutes)
    lat0, lon0 = np.asarray(ctx.read("center_geo", time=t - half), dtype=float).ravel()[:2]
    lat1, lon1 = np.asarray(ctx.read("center_geo", time=t + half), dtype=float).ravel()[:2]
    return motion_from_centers(lat0, lon0, lat1, lon1, 2 * half * 60.0)


def geometry(ctx, shape):
    cy, cx = center(ctx, "slp_center")
    dx_km, dy_km = common.grid_km(ctx)
    radius, cos, sin = polar_geometry(shape, cy, cx, dx_km * 1000.0, dy_km * 1000.0)
    return radius, cos, sin, dx_km * 1000.0, dy_km * 1000.0, (cy, cx)


def storm_relative_vt(ctx):
    # 注册表在配置已做移动订正时改为读地球相对风 _ua_earth/_va_earth（见 _scientific_registry）。
    u = np.asarray(ctx.read(ctx.spec.options.get("wind_u", "ua")), dtype=float)
    v = np.asarray(ctx.read(ctx.spec.options.get("wind_v", "va")), dtype=float)
    if u.shape != v.shape or u.ndim != 2:
        raise ValueError("ua/va 必须为同形二维数组")
    motion_u, motion_v = storm_motion(ctx)
    radius, cos, sin, _, _, centre = geometry(ctx, u.shape)
    vt = tangential_wind(u - motion_u, v - motion_v, cos, sin)
    return vt, radius, (motion_u, motion_v), centre


def common_metadata(ctx, centre, motion=None, **extra):
    meta = {
        "center_mode": "slp_center",
        "center_yx": [float(centre[0]), float(centre[1])],
        "coriolis_f": coriolis_f(ctx),
        "sign": "positive outward",
        **extra,
    }
    if motion is not None:
        meta["storm_motion_u_v_m_s"] = [float(motion[0]), float(motion[1])]
        meta["motion_half_window_h"] = half_window_minutes(ctx) / 60.0
    return meta
