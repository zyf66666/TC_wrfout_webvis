"""变量处理器共享的纯数值算法。"""

from __future__ import annotations
import numpy as np

RD = 287.05
DEFAULT_DOMAIN_CONFIG = {
    1: {"dx_km": 25.0, "dy_km": 25.0},
    2: {"dx_km": 5.0, "dy_km": 5.0},
    3: {"dx_km": 1.0, "dy_km": 1.0},
}


def fill_nan(values):
    """复刻旧二维缺测填补：线性插值、最近邻边缘、零值兜底。"""
    array = np.asarray(values)
    if not np.isnan(array).any():
        return array
    if array.ndim != 2:
        return np.nan_to_num(array, nan=0.0, posinf=0.0, neginf=0.0)
    try:
        from scipy.interpolate import griddata
    except ImportError:
        return np.nan_to_num(array, nan=0.0, posinf=0.0, neginf=0.0)
    yy, xx = np.indices(array.shape)
    valid = ~np.isnan(array)
    if not valid.any():
        return np.zeros_like(array)
    points = np.column_stack((yy[valid], xx[valid]))
    filled = array.copy()
    filled[~valid] = griddata(
        points, array[valid], np.column_stack((yy[~valid], xx[~valid])), method="linear"
    )
    remaining = np.isnan(filled)
    if remaining.any():
        filled[remaining] = griddata(
            points,
            array[valid],
            np.column_stack((yy[remaining], xx[remaining])),
            method="nearest",
        )
    return np.nan_to_num(filled, nan=0.0, posinf=0.0, neginf=0.0)


def grid_km(ctx):
    config = ctx.options.get("domain_config", DEFAULT_DOMAIN_CONFIG)
    item = config.get(ctx.task.domain) or config.get(str(ctx.task.domain))
    if item is None:
        raise ValueError(f"缺少 domain_config 中 domain={ctx.task.domain} 的网格间距")
    dx = float(item.get("dx_km", 0))
    dy = float(item.get("dy_km", dx))
    if dx <= 0 or dy <= 0:
        raise ValueError("dx_km 与 dy_km 必须为正数")
    return dx, dy


def moist_air_density(pressure_hpa, temperature_k, qv):
    p, t, r = (np.asarray(x, dtype=float) for x in (pressure_hpa, temperature_k, qv))
    if p.shape != t.shape or p.shape != r.shape:
        raise ValueError("pressure、temp 和 QVAPOR 形状必须一致")
    if np.any(t <= 0):
        raise ValueError("temperature_k 必须为正数")
    return p * 100.0 / (RD * t * (1.0 + 0.61 * r))


def horizontal_convergence(u, v, *, dx_m, dy_m):
    u, v = np.asarray(u, float), np.asarray(v, float)
    if u.shape != v.shape or u.ndim != 2:
        raise ValueError("u 和 v 必须是形状相同的二维数组")
    return -(np.gradient(u, dx_m, axis=1) + np.gradient(v, dy_m, axis=0))


def radial_tangential_wind(u, v, *, center_y, center_x, dx_km, dy_km, component):
    u, v = np.asarray(u, float), np.asarray(v, float)
    if u.shape != v.shape or u.ndim != 2:
        raise ValueError("ua/va 必须为同形二维数组")
    yy, xx = np.indices(u.shape, dtype=float)
    x, y = (xx - center_x) * dx_km, (yy - center_y) * dy_km
    radius = np.hypot(x, y)
    cos = np.divide(x, radius, out=np.zeros_like(x), where=radius > 0)
    sin = np.divide(y, radius, out=np.zeros_like(y), where=radius > 0)
    if component == "vr":
        return u * cos + v * sin
    if component == "vt":
        return -u * sin + v * cos
    raise ValueError("component 必须为 vr 或 vt")


def azimuthal_mean(values, *, center_y, center_x, dx_km, dy_km):
    data = np.asarray(values, float)
    yy, xx = np.indices(data.shape, dtype=float)
    bins = np.rint(
        np.hypot((xx - center_x) * dx_km, (yy - center_y) * dy_km) / dx_km
    ).astype(int)
    valid = np.isfinite(data)
    if not valid.any():
        raise ValueError("cannot compute azimuthal mean from all-NaN data")
    sums = np.bincount(bins[valid].ravel(), weights=data[valid].ravel())
    counts = np.bincount(bins[valid].ravel(), minlength=len(sums))
    means = np.divide(sums, counts, out=np.full(len(sums), np.nan), where=counts > 0)
    radii = np.arange(len(sums)) * dx_km
    # 与旧实现一致：没有有效格点的环不作为剖面输出，避免空的零半径环。
    available = np.isfinite(means)
    return radii[available], means[available]


def outer_threshold_radius(radii, means, threshold):
    radii = np.asarray(radii, dtype=float)
    means = np.asarray(means, dtype=float)
    if radii.ndim != 1 or means.ndim != 1 or radii.size != means.size:
        raise ValueError("radii and means 必须为等长一维数组")
    if not radii.size or not np.isfinite(means).any():
        return float("nan")
    i = int(np.nanargmax(means))
    if means[i] < threshold:
        return float("nan")
    for j in range(i + 1, len(means)):
        if (
            np.isfinite(means[j - 1])
            and np.isfinite(means[j])
            and means[j] <= threshold
        ):
            if means[j] == means[j - 1]:
                return float(radii[j])
            return float(
                radii[j - 1]
                + (threshold - means[j - 1])
                * (radii[j] - radii[j - 1])
                / (means[j] - means[j - 1])
            )
    return float("nan")


def polar_geometry(
    shape, center_y, center_x, dx_km, dy_km, radius_km=None, radius_resolution_km=None
):
    # 边界距离必须包含零；否则中心贴边时会对网格外错误地做最近邻外推。
    if len(shape) != 2 or min(shape) < 1:
        raise ValueError("极坐标插值需要非空二维场")
    if (
        not np.isfinite([center_y, center_x, dx_km, dy_km]).all()
        or dx_km <= 0
        or dy_km <= 0
    ):
        raise ValueError("中心和网格间距必须为有限数，间距必须为正")
    if not (0 <= center_y <= shape[0] - 1 and 0 <= center_x <= shape[1] - 1):
        raise ValueError("中心位于源网格之外")
    safe = min(
        center_y * dy_km,
        (shape[0] - 1 - center_y) * dy_km,
        center_x * dx_km,
        (shape[1] - 1 - center_x) * dx_km,
    )
    requested = safe if radius_km is None else float(radius_km)
    if requested > safe + 1e-8:
        raise ValueError(f"请求半径 {requested:g} km 超出安全半径 {safe:g} km")
    if not np.isfinite(requested) or requested < 0:
        raise ValueError("radius_km 不能为负")
    resolution = float(dx_km if radius_resolution_km is None else radius_resolution_km)
    if not np.isfinite(resolution) or resolution <= 0:
        raise ValueError("radius_resolution_km 必须为正")
    return (
        requested,
        resolution,
        int(np.floor(requested / resolution + 1e-10)) + 1,
        safe,
    )


def cartesian_to_polar(
    data, *, center_y, center_x, dx_km, dy_km, radius_km=None, radius_resolution_km=None,
    coordinate_cache=None,
):
    requested, resolution, count, safe = polar_geometry(
        np.shape(data),
        center_y,
        center_x,
        dx_km,
        dy_km,
        radius_km,
        radius_resolution_km,
    )
    def coordinates():
        angle = np.deg2rad(np.arange(360.0))[:, None]
        radius = np.arange(count)[None, :] * resolution
        yy, xx = (
            center_y + radius * np.sin(angle) / dy_km,
            center_x + radius * np.cos(angle) / dx_km,
        )
        return np.vstack((yy.ravel(), xx.ravel()))
    key = ("polar_coordinates", tuple(np.shape(data)), float(center_y), float(center_x),
           float(dx_km), float(dy_km), requested, resolution, count)
    points = coordinate_cache(key, coordinates) if coordinate_cache is not None else coordinates()
    try:
        from scipy.ndimage import map_coordinates

        out = map_coordinates(
            np.asarray(data, float),
            points,
            order=1,
            mode="nearest",
        ).reshape(360, count)
    except ImportError:
        yy, xx = points.reshape(2, 360, count)
        out = bilinear_sample(np.asarray(data, float), yy, xx)
    return out, {
        "radius_km": (count - 1) * resolution,
        "safe_radius_km": safe,
        "radius_resolution_km": resolution,
        "angle_count": 360,
    }


def bilinear_sample(data, yy, xx):
    y = np.clip(yy, 0, data.shape[0] - 1)
    x = np.clip(xx, 0, data.shape[1] - 1)
    y0 = np.floor(y).astype(int)
    x0 = np.floor(x).astype(int)
    y1 = np.minimum(y0 + 1, data.shape[0] - 1)
    x1 = np.minimum(x0 + 1, data.shape[1] - 1)
    wx = x - x0
    wy = y - y0
    return (data[y0, x0] * (1 - wx) + data[y0, x1] * wx) * (1 - wy) + (
        data[y1, x0] * (1 - wx) + data[y1, x1] * wx
    ) * wy


def polar_to_cartesian(
    data,
    *,
    center_y,
    center_x,
    output_shape,
    dx_km,
    dy_km,
    radius_resolution_km,
    fill_value=np.nan,
):
    yy, xx = np.indices(output_shape, float)
    angle = (
        np.degrees(np.arctan2((yy - center_y) * dy_km, (xx - center_x) * dx_km)) + 360
    ) % 360
    r = (
        np.hypot((xx - center_x) * dx_km, (yy - center_y) * dy_km)
        / radius_resolution_km
    )
    values = np.asarray(data, float)
    out = bilinear_sample(np.vstack((values, values[:1])), angle, r)
    return np.where(r <= values.shape[1] - 1, out, fill_value)


def asymmetric(data):
    values = _validated_polar_array(data)
    return values - np.mean(values, axis=0, keepdims=True)


def extract_wave(data, wavenumber, *, coefficient_cache=None):
    values = _validated_polar_array(data)
    n = values.shape[0]
    if not 0 <= int(wavenumber) < n:
        raise ValueError("wavenumber 超出方位角维范围")
    def coefficients():
        return np.fft.fft(values, axis=0)
    coeff = coefficient_cache(("azimuth_fft",), coefficients) if coefficient_cache is not None else coefficients()
    keep = np.zeros_like(coeff)
    keep[wavenumber] = coeff[wavenumber]
    if wavenumber:
        keep[-wavenumber] = coeff[-wavenumber]
    return np.real(np.fft.ifft(keep, axis=0))


def _validated_polar_array(data):
    """应用旧 Fourier 输入约束：至少二维且没有缺测值。"""
    values = np.asarray(data, dtype=float)
    if values.ndim < 2:
        raise ValueError(
            f"Fourier decomposition expects at least 2 dimensions, got {values.shape}"
        )
    if np.isnan(values).any():
        raise ValueError("Fourier decomposition source contains NaN values")
    return values


# 功能说明：取某个极坐标变量在某个 domain 的最大半径（km）。
def polar_radius_km(options, variable, domain):
    """优先级：radius_km_by_variable[变量][domain] > radius_km_by_domain[domain]
    > radius_km > polar_max_radius_km。都没有时返回 None。

    radius_km_by_variable 的值可以是按 domain 的字典，也可以是对所有 domain 生效的数值；
    domain 键可写成整数或字符串。插值（variables/polar.py）与按层安全检查
    （layer_polar_safety.py）都通过这里取半径，保证两者一致。
    """
    def pick(mapping):
        if not isinstance(mapping, dict):
            raise ValueError("半径配置必须是按 domain 编号组织的字典")
        return mapping.get(domain, mapping.get(str(domain)))

    by_variable = options.get("radius_km_by_variable") or {}
    if not isinstance(by_variable, dict):
        raise ValueError("radius_km_by_variable 必须是按变量名组织的字典")
    entry = by_variable.get(variable)
    if entry is not None:
        radius = pick(entry) if isinstance(entry, dict) else entry
        if radius is not None:
            return radius
    radius = pick(options.get("radius_km_by_domain") or {})
    if radius is not None:
        return radius
    return options.get("radius_km", options.get("polar_max_radius_km"))
