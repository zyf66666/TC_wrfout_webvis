from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

CENTER_ALGORITHM = "bezier_pcc_v1"
DEFAULT_SEARCH_RADIUS_KM = 60.0
DEFAULT_CROSS_RADIUS_GRID = 121
DEFAULT_PCC_RADIUS_GRID = 44
DEFAULT_BOUNDARY_WIDTH = 20
DEFAULT_MAX_ITERATIONS = 10


# 功能说明：从中心位置表按预报时次找出一行，返回该时次的 y、x 网格坐标。
def load_center_yx(path: str | Path, time_hour: int | float) -> tuple[float, float]:
    data = _load_center_table(path)
    time_value = float(time_hour)
    if data.shape[1] >= 3:
        matches = np.where(np.isclose(data[:, 0].astype(float), time_value))[0]
        if matches.size == 0:
            available = ", ".join(f"{value:g}" for value in data[:, 0].astype(float)[:10])
            raise KeyError(f"center file has no time={time_value:g}. Available times: {available}")
        row = data[int(matches[0])]
        return float(row[1]), float(row[2])

    row_index = int(time_value)
    if row_index >= data.shape[0]:
        raise IndexError(f"center file has {data.shape[0]} rows, cannot read row {row_index}")
    row = data[row_index]
    return float(row[0]), float(row[1])


# 功能说明：读取带时次的中心表，将每行按时次整理成字典并返回。
def read_new_center_rows(path: Path) -> dict[int | float, list[float]]:
    if not path.exists():
        return {}
    data = _load_center_table(path)
    if data.shape[1] < 3:
        return {}
    rows: dict[int | float, list[float]] = {}
    for row in data:
        time_value = float(row[0])
        rounded = round(time_value)
        key: int | float = int(rounded) if abs(time_value - rounded) < 1e-10 else round(time_value, 10)
        rows[key] = [float(value) for value in row]
    return rows


# 功能说明：清除中心表缓存，保证下一次读取使用刚更新的文件。
def clear_center_cache() -> None:
    """Clear cached center tables after a center file is rewritten."""
    _load_center_table_cached.cache_clear()


# 功能说明：根据文件路径、修改时间和大小读取或复用中心位置数组。
def _load_center_table(path: str | Path) -> np.ndarray:
    center_path = Path(path)
    stat = center_path.stat()
    return _load_center_table_cached(str(center_path.resolve()), stat.st_mtime_ns, stat.st_size)


# 功能说明：实际读取中心文本文件，并确保结果始终是二维数组。
@lru_cache(maxsize=512)
def _load_center_table_cached(path: str, mtime_ns: int, size: int) -> np.ndarray:
    del mtime_ns, size
    return np.atleast_2d(np.loadtxt(path))


# 功能说明：检查中心表的一行是否含有有效的纬度和经度数值。
def center_row_has_lat_lon(row: list[float]) -> bool:
    return len(row) >= 5 and np.isfinite(float(row[3])) and np.isfinite(float(row[4]))


# 功能说明：打开 WRF 输出文件，在给定时次和格点读取并返回纬度、经度。
def read_wrf_lat_lon(
    wrfout_path: str | Path,
    *,
    y: float,
    x: float,
    time_index: int = 0,
) -> tuple[float, float]:
    from netCDF4 import Dataset

    path = Path(wrfout_path)
    with Dataset(path, "r") as dataset:
        lat_var = _first_variable(dataset, ("XLAT", "XLAT_M", "lat", "LAT"))
        lon_var = _first_variable(dataset, ("XLONG", "XLONG_M", "lon", "LON"))
        if lat_var is None or lon_var is None:
            raise KeyError(f"WRF file has no XLAT/XLONG variables: {path}")

        lat = _read_grid_value(lat_var, y=y, x=x, time_index=time_index)
        lon = _read_grid_value(lon_var, y=y, x=x, time_index=time_index)
        return float(lat), float(lon)


# 功能说明：从候选名称中找出数据集实际拥有的第一个变量；找不到时返回空。
def _first_variable(dataset, names: tuple[str, ...]):
    for name in names:
        if name in dataset.variables:
            return dataset.variables[name]
    return None


# 功能说明：把网格坐标限制在变量边界内，读取并返回该时次的一个数值。
def _read_grid_value(variable, *, y: float, x: float, time_index: int) -> float:
    shape = tuple(variable.shape)
    yi = int(round(float(y)))
    xi = int(round(float(x)))
    if len(shape) >= 3:
        ti = min(max(int(time_index), 0), shape[0] - 1)
        yi = min(max(yi, 0), shape[-2] - 1)
        xi = min(max(xi, 0), shape[-1] - 1)
        return float(variable[ti, yi, xi])
    if len(shape) == 2:
        yi = min(max(yi, 0), shape[0] - 1)
        xi = min(max(xi, 0), shape[1] - 1)
        return float(variable[yi, xi])
    raise ValueError(f"Unsupported lat/lon variable shape: {shape}")


# 功能说明：从气压低值附近寻找初始中心，再依据周围气压分布反复修正中心位置，返回最终格点和该点气压；不是直接把最低点当成最终中心。
def find_pressure_center(
    field: np.ndarray,
    *,
    dx_km: float = 1.0,
    dy_km: float | None = None,
    initial_yx: tuple[float, float] | None = None,
    search_radius_km: float = DEFAULT_SEARCH_RADIUS_KM,
    cross_radius_grid: int = DEFAULT_CROSS_RADIUS_GRID,
    pcc_radius_grid: int = DEFAULT_PCC_RADIUS_GRID,
    boundary_width: int = DEFAULT_BOUNDARY_WIDTH,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> tuple[float, float, float]:
    """Find a center using the uploaded firstguess + Bezier PCC algorithm.

    ``dy_km`` and ``initial_yx`` remain in the public signature for backward
    compatibility.  The reference algorithm uses ``dist=int(60/dx)`` and runs
    its own first guess independently for every pressure field and height.
    """
    del dy_km, initial_yx
    data = np.asarray(field, dtype=float)
    if data.ndim != 2:
        raise ValueError(f"center field must be 2D, got shape={data.shape}")
    if not np.isfinite(data).all():
        raise ValueError("center field contains NaN or Inf; inspect the processed pressure file")
    dx = float(dx_km or 1.0)
    if dx <= 0:
        raise ValueError(f"dx_km must be positive: {dx}")

    dist = int(float(search_radius_km) / dx)
    center_y, center_x = pcc_first_guess(data, dist=dist)
    background = pcc_basic_value(data, border=boundary_width)
    center_y, center_x = pcc_iterate(
        data,
        center_y=center_y,
        center_x=center_x,
        background=background,
        cross_radius=cross_radius_grid,
        pcc_radius=pcc_radius_grid,
        max_iterations=max_iterations,
    )
    return float(center_y), float(center_x), float(data[center_y, center_x])


# 功能说明：先找全场最低值，在它周围的候选点比较圆形邻域平均气压，选择平均值最低的位置作为后续中心修正的起点。
def pcc_first_guess(data: np.ndarray, *, dist: int) -> tuple[int, int]:
    """Exact vectorized equivalent of uploaded ``firstguess`` + ``hgtcount``."""
    if dist < 0:
        raise ValueError(f"first-guess distance must be non-negative: {dist}")
    values = np.asarray(data, dtype=float)
    y0, x0 = np.unravel_index(np.argmin(values), values.shape)
    if dist == 0:
        return int(y0), int(x0)

    from scipy.signal import fftconvolve

    yy, xx = np.ogrid[-dist : dist + 1, -dist : dist + 1]
    kernel = (yy * yy + xx * xx <= dist * dist).astype(float)
    local_sum = fftconvolve(values, kernel, mode="same")
    local_count = fftconvolve(np.ones(values.shape, dtype=float), kernel, mode="same")
    local_mean = local_sum / local_count

    candidate = np.full(values.shape, np.inf, dtype=float)
    y_start, y_end = max(0, y0 - dist), min(values.shape[0], y0 + dist + 1)
    x_start, x_end = max(0, x0 - dist), min(values.shape[1], x0 + dist + 1)
    cy, cx = np.ogrid[y_start:y_end, x_start:x_end]
    mask = (cy - y0) ** 2 + (cx - x0) ** 2 <= dist * dist
    window = candidate[y_start:y_end, x_start:x_end]
    window[mask] = local_mean[y_start:y_end, x_start:x_end][mask]
    best_y, best_x = np.unravel_index(np.argmin(candidate), candidate.shape)
    return int(best_y), int(best_x)


# 功能说明：平均二维场四周的边界带，返回背景参考值。
def pcc_basic_value(data: np.ndarray, *, border: int = DEFAULT_BOUNDARY_WIDTH) -> float:
    """Uploaded ``get_basic_value``: equally average four boundary-strip means."""
    ny, nx = data.shape
    width = min(max(int(border), 1), ny // 4, nx // 4)
    if width < 1:
        raise ValueError(f"center field is too small for boundary mean: {data.shape}")
    top = np.mean(data[0:width, :])
    bottom = np.mean(data[ny - width : ny, :])
    left = np.mean(data[:, 0:width])
    right = np.mean(data[:, nx - width : nx])
    return float((top + bottom + left + right) / 4.0)


# 功能说明：从初始中心反复用极坐标重心校正位置，返回最终中心格点。
def pcc_iterate(
    data: np.ndarray,
    *,
    center_y: int,
    center_x: int,
    background: float,
    cross_radius: int = DEFAULT_CROSS_RADIUS_GRID,
    pcc_radius: int = DEFAULT_PCC_RADIUS_GRID,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> tuple[int, int]:
    """Exact equivalent of the uploaded integer-grid ``PCC`` iteration."""
    values = np.asarray(data, dtype=float)
    ny, nx = values.shape
    cross = int(cross_radius)
    radius = int(pcc_radius)
    if cross < 1:
        raise ValueError(f"PCC cross radius must be positive: {cross}")
    if radius < 1 or radius + 1 > cross:
        raise ValueError(f"PCC radius must satisfy 1 <= radius <= cross_radius-1: {radius}, {cross}")
    if ny < 2 * cross + 1 or nx < 2 * cross + 1:
        raise ValueError(
            f"center field {values.shape} is too small for PCC cross radius {cross}; "
            f"need at least {(2 * cross + 1, 2 * cross + 1)}"
        )

    cy = _clamp_int(int(center_y), cross, ny - cross - 1)
    cx = _clamp_int(int(center_x), cross, nx - cross - 1)
    for _ in range(int(max_iterations)):
        old_y, old_x = cy, cx
        cross_data = values[cy - cross : cy + cross + 1, cx - cross : cx + cross + 1]
        polar = pcc_bezier_interp(cross_data - float(background), cross, cross, radius)
        offset_x, offset_y = pcc_centroid(polar)
        cy = int(old_y + np.round(offset_y))
        cx = int(old_x + np.round(offset_x))
        cy = _clamp_int(cy, cross, ny - cross - 1)
        cx = _clamp_int(cx, cross, nx - cross - 1)
        if cy == old_y and cx == old_x:
            break
    return cy, cx


# 功能说明：把中心周围二维数据插值到 360 个方位和多个半径，返回极坐标数组。
def pcc_bezier_interp(data: np.ndarray, center_y: int, center_x: int, radius: int) -> np.ndarray:
    """Vectorized numerical equivalent of the uploaded ``BezierInterp``."""
    values = np.asarray(data, dtype=float)
    stock = np.zeros((360, radius + 1), dtype=float)
    stock[:, 0] = values[center_y, center_x]
    if radius == 0:
        return stock

    angle = np.deg2rad(np.arange(360, dtype=float))[:, np.newaxis]
    radial = np.arange(1, radius + 1, dtype=float)[np.newaxis, :]
    xproj = radial * np.cos(angle)
    yproj = radial * np.sin(angle)
    xfloor, yfloor = np.floor(xproj), np.floor(yproj)
    tx, ty = xproj - xfloor, yproj - yfloor

    x0 = center_x + xfloor.astype(int)
    x1 = center_x + np.ceil(xproj).astype(int)
    x2 = center_x + np.ceil(xproj).astype(int) + 1
    xm1 = center_x + xfloor.astype(int) - 1
    y0 = center_y + yfloor.astype(int)
    y1 = center_y + np.ceil(yproj).astype(int)
    y2 = center_y + np.ceil(yproj).astype(int) + 1
    ym1 = center_y + yfloor.astype(int) - 1

    # 功能说明：对指定行按 Bezier 公式插值，返回该行在各半径处的值。
    def interp_x(y_index):
        return (
            values[y_index, x0]
            + tx * (values[y_index, x1] - values[y_index, x0])
            + 0.25 * tx * (tx - 1.0)
            * (values[y_index, x2] - values[y_index, x1] - values[y_index, x0] + values[y_index, xm1])
        )

    b = interp_x(y0)
    c = interp_x(y1)
    d = interp_x(y2)
    a = interp_x(ym1)
    stock[:, 1:] = b + ty * (c - b) + 0.25 * ty * (ty - 1.0) * (a + d - b - c)
    return stock


# 功能说明：由 360 条径向剖面计算平均重心偏移，返回 x、y 偏移量。
def pcc_centroid(value: np.ndarray) -> tuple[float, float]:
    """Exact vectorized equivalent of the uploaded 180-profile ``centroid``."""
    polar = np.asarray(value, dtype=float)
    if polar.shape[0] != 360:
        raise ValueError(f"PCC polar data must contain 360 azimuths: {polar.shape}")
    radius = polar.shape[1] - 1
    profile = np.concatenate((polar[180:360, ::-1], polar[0:180, 1:]), axis=1)
    positions = np.arange(1, 2 * radius + 2, dtype=float)
    sums = np.sum(profile, axis=1)
    weighted = np.sum(profile * positions[np.newaxis, :], axis=1)
    profile_centers = np.divide(
        weighted,
        sums,
        out=np.zeros_like(weighted),
        where=np.abs(sums) >= 1.0e-12,
    )
    offsets = np.where(np.abs(sums) < 1.0e-12, 0.0, profile_centers - float(radius + 1))
    # The uploaded script hard-codes 45 because R_PCC=44; radius+1 is the
    # generalized identical expression.
    angles = np.deg2rad(np.arange(180, dtype=float))
    x_coord = offsets * np.cos(angles)
    y_coord = offsets * np.sin(angles)
    return float(np.sum(x_coord) / 180.0), float(np.sum(y_coord) / 180.0)


# 功能说明：将一个整数限制在上下界之间并返回限制后的值。
def _clamp_int(value: int, low: int, high: int) -> int:
    return low if value < low else high if value > high else value
