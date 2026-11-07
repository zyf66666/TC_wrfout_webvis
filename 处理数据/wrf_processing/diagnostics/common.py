from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.schemas import ProcessingRequest, VariableDimension
from ..processing_utils import atomic_save, build_processing_metadata, fill_nan, prepare_wrf_import, wrfout_read_error


# 功能说明：读取 WRF 最低模式层的东西、南北风分量，并清理缺测值。
def read_lowest_model_wind(wrfout_file: str | Path, time_index: int) -> tuple[np.ndarray, np.ndarray]:
    """Read earth-relative wind at the lowest WRF model level."""
    prepare_wrf_import()
    from netCDF4 import Dataset
    from wrf import getvar, to_np

    try:
        with Dataset(wrfout_file, "r") as dataset:
            ua = np.asarray(to_np(getvar(dataset, "ua", timeidx=time_index)), dtype=float)
            va = np.asarray(to_np(getvar(dataset, "va", timeidx=time_index)), dtype=float)
    except Exception as exc:  # noqa: BLE001
        action = f"read lowest model wind source_time_index={time_index}"
        raise RuntimeError(wrfout_read_error(path=Path(wrfout_file), action=action, original=exc)) from exc
    if ua.ndim == 3:
        ua = ua[0, :, :]
    if va.ndim == 3:
        va = va[0, :, :]
    if ua.ndim != 2 or va.ndim != 2:
        raise ValueError(f"lowest model wind must be 2D after level selection, got ua={ua.shape}, va={va.shape}")
    return fill_nan(ua), fill_nan(va)


# 功能说明：以风暴中心为圆心，把二维场按半径分环平均，得到径向剖面。
def azimuthal_mean_by_radius(
    values: np.ndarray,
    *,
    center_y: float,
    center_x: float,
    dx_km: float,
    dy_km: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Average a 2D field over annular bins around the storm center."""
    data = np.asarray(values, dtype=float)
    yy, xx = np.indices(data.shape, dtype=float)
    radius_km = np.hypot((xx - float(center_x)) * float(dx_km or 1.0), (yy - float(center_y)) * float(dy_km or dx_km or 1.0))
    resolution = max(float(dx_km or 1.0), 1.0e-6)
    bins = np.rint(radius_km / resolution).astype(int)
    finite = np.isfinite(data)
    if not finite.any():
        raise ValueError("cannot compute azimuthal mean from all-NaN data")
    max_bin = int(np.nanmax(bins[finite]))
    sums = np.bincount(bins[finite].ravel(), weights=data[finite].ravel(), minlength=max_bin + 1)
    counts = np.bincount(bins[finite].ravel(), minlength=max_bin + 1)
    means = np.divide(sums, counts, out=np.full(max_bin + 1, np.nan, dtype=float), where=counts > 0)
    radii = np.arange(max_bin + 1, dtype=float) * resolution
    valid = np.isfinite(means)
    if not valid.any():
        raise ValueError("cannot compute azimuthal mean from empty radial bins")
    return radii[valid], means[valid]


# 功能说明：从最大平均风速向外寻找首次降到阈值的位置，并插值得到半径。
def outer_threshold_radius(
    *,
    radii_km: np.ndarray,
    azimuthal_mean_wind_mps: np.ndarray,
    threshold_mps: float,
) -> float:
    """Return the first outward, linearly interpolated wind-threshold radius.

    The search begins at the radius of maximum azimuthal-mean wind, so weak
    winds inside the eye cannot be selected as an outward wind radius.
    """
    radii = np.asarray(radii_km, dtype=float)
    winds = np.asarray(azimuthal_mean_wind_mps, dtype=float)
    if radii.ndim != 1 or winds.ndim != 1 or radii.size != winds.size:
        raise ValueError("radii_km and azimuthal_mean_wind_mps must be equal-length 1D arrays")
    if radii.size == 0 or not np.isfinite(winds).any():
        return float("nan")

    peak_index = int(np.nanargmax(winds))
    if winds[peak_index] < float(threshold_mps):
        return float("nan")

    for current_index in range(peak_index + 1, radii.size):
        previous_index = current_index - 1
        previous_wind = winds[previous_index]
        current_wind = winds[current_index]
        if not (np.isfinite(previous_wind) and np.isfinite(current_wind)):
            continue
        if current_wind > float(threshold_mps):
            continue
        if current_wind == previous_wind:
            return float(radii[current_index])
        fraction = (float(threshold_mps) - previous_wind) / (current_wind - previous_wind)
        return float(radii[previous_index] + fraction * (radii[current_index] - radii[previous_index]))
    return float("nan")


# 功能说明：求中心周围指定半径内忽略NaN的平均值；圆内没有格点时改取离中心最近且位于网格内的点值。
def mean_within_radius(
    values: np.ndarray,
    *,
    center_y: float,
    center_x: float,
    radius_km: float,
    dx_km: float,
    dy_km: float,
) -> float:
    data = np.asarray(values, dtype=float)
    yy, xx = np.indices(data.shape, dtype=float)
    distance = np.hypot((xx - float(center_x)) * float(dx_km or 1.0), (yy - float(center_y)) * float(dy_km or dx_km or 1.0))
    mask = distance <= float(radius_km)
    if not mask.any():
        cy = int(round(float(center_y)))
        cx = int(round(float(center_x)))
        cy = min(max(cy, 0), data.shape[0] - 1)
        cx = min(max(cx, 0), data.shape[1] - 1)
        return float(data[cy, cx])
    return float(np.nanmean(data[mask]))


# 功能说明：计算指定内外半径环带内的平均值。
def mean_within_annulus(
    values: np.ndarray,
    *,
    center_y: float,
    center_x: float,
    inner_radius_km: float,
    outer_radius_km: float,
    dx_km: float,
    dy_km: float,
) -> float:
    data = np.asarray(values, dtype=float)
    if outer_radius_km <= inner_radius_km:
        raise ValueError(f"outer_radius_km must be greater than inner_radius_km: {inner_radius_km}, {outer_radius_km}")
    yy, xx = np.indices(data.shape, dtype=float)
    distance = np.hypot((xx - float(center_x)) * float(dx_km or 1.0), (yy - float(center_y)) * float(dy_km or dx_km or 1.0))
    mask = (distance >= float(inner_radius_km)) & (distance < float(outer_radius_km))
    finite = mask & np.isfinite(data)
    if not finite.any():
        raise ValueError(
            f"cannot compute annulus mean: no finite values between {inner_radius_km:g} and {outer_radius_km:g} km"
        )
    return float(np.nanmean(data[finite]))


# 功能说明：以格点数量而非公里表示内外半径，选出环带里的有效数值求平均；环带中没有有效点就报错。
def mean_within_annulus_grid(
    values: np.ndarray,
    *,
    center_y: float,
    center_x: float,
    inner_radius_grid: float,
    outer_radius_grid: float,
) -> float:
    data = np.asarray(values, dtype=float)
    if outer_radius_grid <= inner_radius_grid:
        raise ValueError(
            f"outer_radius_grid must be greater than inner_radius_grid: {inner_radius_grid}, {outer_radius_grid}"
        )
    yy, xx = np.indices(data.shape, dtype=float)
    distance = np.hypot(xx - float(center_x), yy - float(center_y))
    mask = (distance >= float(inner_radius_grid)) & (distance < float(outer_radius_grid))
    finite = mask & np.isfinite(data)
    if not finite.any():
        raise ValueError(
            f"cannot compute annulus mean: no finite values between radius grid "
            f"{inner_radius_grid:g} and {outer_radius_grid:g}"
        )
    return float(np.nanmean(data[finite]))


# 功能说明：从已经保存的距离表中按小时查找并返回距陆地距离；这里只读取结果，不重新计算中心或海岸距离。
def land_distance_for_time(path: Path, time_hour: int | float) -> float:
    data = np.atleast_2d(np.load(path))
    matches = np.where(np.isclose(data[:, 0].astype(float), float(time_hour)))[0]
    if matches.size == 0:
        available = ", ".join(f"{value:g}" for value in data[:, 0].astype(float)[:10])
        raise KeyError(f"land distance has no time={float(time_hour):g}. Available times: {available}")
    return float(data[int(matches[0]), 1])


# 功能说明：把某时刻的一个诊断数值存成只含一项的 .npy 数组，并保存单位、来源和计算参数等说明，返回输出路径。
def write_scalar_diagnostic(
    *,
    request: ProcessingRequest,
    path_manager: PathManager,
    variable: str,
    value: float,
    source_file: Path,
    source_time_index: int,
    time_hour: int,
    dtype: str,
    metadata_extra: dict[str, Any],
    units: str,
) -> str:
    out_path = path_manager.variable_path(
        member_name=request.member_name,
        domain=request.domain,
        variable=variable,
        time_hour=time_hour,
        dimension=VariableDimension.ONE_D,
    )
    atomic_save(out_path, np.asarray([value], dtype=dtype))
    metadata = build_processing_metadata(request, variable, source_file, time_hour, None, source_time_index)
    metadata.update(metadata_extra)
    metadata["units"] = units
    path_manager.write_metadata(out_path, metadata)
    return str(out_path)
