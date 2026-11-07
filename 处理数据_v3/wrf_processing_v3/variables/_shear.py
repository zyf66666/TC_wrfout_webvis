"""在源域的物理距离环带内计算 200–850 hPa 环境风切变。"""

from __future__ import annotations

import numpy as np

from ..common import cartesian_to_polar


def _annulus_mean_km(
    field, *, center_y, center_x, dx_km, dy_km, inner_km, outer_km
) -> float:
    if not np.isfinite([inner_km, outer_km]).all() or inner_km < 0 or outer_km <= inner_km:
        raise ValueError("风切变环带需要 0 <= 内半径 < 外半径，单位为 km")
    resolution_km = min(dx_km, dy_km)
    polar, _ = cartesian_to_polar(
        field,
        center_y=center_y,
        center_x=center_x,
        dx_km=dx_km,
        dy_km=dy_km,
        radius_km=outer_km,
        radius_resolution_km=resolution_km,
    )
    radii_km = np.arange(polar.shape[1]) * resolution_km
    annulus = polar[:, (radii_km >= inner_km - 1e-8) & (radii_km <= outer_km + 1e-8)]
    if not np.isfinite(annulus).any():
        raise ValueError(f"风切变 {inner_km:g}–{outer_km:g} km 环带没有有效值")
    return float(np.nanmean(annulus))


def shear_values(ctx) -> dict[str, float]:
    """使用同一时次源域中心与风场；网格间距取自源域风场元数据。"""
    config = ctx.spec.options
    spacing = ctx.read_metadata("_ua_upper").get("grid_spacing")
    if not isinstance(spacing, dict) or not {"dx_km", "dy_km"} <= spacing.keys():
        raise ValueError("风切变源域风场缺少 dx_km/dy_km 网格间距元数据")
    center = np.asarray(ctx.read("slp_center"), dtype=float).ravel()
    if center.size < 2:
        raise ValueError("风切变源域中心须包含 y、x 两个坐标")
    means = {
        name: _annulus_mean_km(
            ctx.read(name),
            center_y=float(center[0]),
            center_x=float(center[1]),
            dx_km=float(spacing["dx_km"]),
            dy_km=float(spacing["dy_km"]),
            inner_km=float(config["shear_radius_inner_km"]),
            outer_km=float(config["shear_radius_outer_km"]),
        )
        for name in ("_ua_upper", "_ua_lower", "_va_upper", "_va_lower")
    }
    u = means["_ua_upper"] - means["_ua_lower"]
    v = means["_va_upper"] - means["_va_lower"]
    return {
        "u": u,
        "v": v,
        "magnitude": float(np.hypot(u, v)),
        "direction": float(-np.degrees(np.arctan2(v, u))),
    }
