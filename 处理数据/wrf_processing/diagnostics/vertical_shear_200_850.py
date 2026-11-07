from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import numpy as np

from wrf_webvis.app.core.centers import load_center_yx
from wrf_webvis.app.core.paths import PathManager, metadata_path
from wrf_webvis.app.core.schemas import ProcessingRequest, VariableDimension
from ..processing_utils import atomic_save, build_processing_metadata, prepare_wrf_import, wrfout_read_error
from ..progress import ProgressCallback, emit


PRESSURE_LEVELS_HPA = [200.0, 850.0]
ALGORITHM_VERSION = "bezier_polar_annulus_v1"
DEFAULT_INNER_RADIUS_GRID = 150
DEFAULT_OUTER_RADIUS_GRID = 350
DEFAULT_MAX_RADIUS_GRID = 380

VARIABLE_U = "shear_200_850_u"
VARIABLE_V = "shear_200_850_v"
VARIABLE_MAGNITUDE = "shear_200_850_magnitude"
VARIABLE_DIRECTION = "shear_200_850_direction"
VARIABLES = {VARIABLE_U, VARIABLE_V, VARIABLE_MAGNITUDE, VARIABLE_DIRECTION}

_UNITS = {
    VARIABLE_U: "m s-1",
    VARIABLE_V: "m s-1",
    VARIABLE_MAGNITUDE: "m s-1",
    VARIABLE_DIRECTION: "degree",
}


# 功能说明：在中心周围的环带分别求高低气压层平均风，再用高层减低层得到风切变；保存东西、南北分量及强度、方向四个结果，返回请求项路径。
def process(
    *,
    request: ProcessingRequest,
    path_manager: PathManager,
    variable: str,
    source_file: Path,
    source_time_index: int,
    time_hour: int,
    dtype: str,
    domain_config: dict[str, Any],
    ensure_slp_center: Callable[..., Path],
    progress: ProgressCallback | None = None,
    **_: Any,
) -> str:
    if variable not in VARIABLES:
        raise ValueError(f"Unsupported 200-850 hPa shear variable: {variable}")
    out_path = path_manager.variable_path(
        member_name=request.member_name,
        domain=request.domain,
        variable=variable,
        time_hour=time_hour,
        dimension=VariableDimension.ONE_D,
    )
    if out_path.exists() and not request.force and shear_output_is_current(out_path, request.options):
        return str(out_path)

    emit(progress, f"计算 {request.member_name} {time_hour}h 200-850hPa 垂直风切变")
    ensure_slp_center(request, [time_hour], progress)
    values = calculate_shear_values(
        source_file=source_file,
        source_time_index=source_time_index,
        center_path=path_manager.center_path(request.member_name, request.domain, "slp"),
        time_hour=time_hour,
        domain_config=domain_config,
        request_options=request.options,
    )
    outputs = write_shear_outputs(
        request=request,
        path_manager=path_manager,
        values=values,
        source_file=source_file,
        source_time_index=source_time_index,
        time_hour=time_hour,
        dtype=dtype,
    )
    return str(outputs[variable])


# 功能说明：读取配置的高低气压层风（默认200与850百帕），在以格点数指定的环带内分别平均，再计算风差、大小和方向。
def calculate_shear_values(
    *,
    source_file: Path,
    source_time_index: int,
    center_path: Path,
    time_hour: int,
    domain_config: dict[str, Any],
    request_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    options = request_options or {}
    inner_radius_grid = int(
        options.get("shear_radius_in", options.get("shear_inner_radius_grid", DEFAULT_INNER_RADIUS_GRID))
    )
    outer_radius_grid = int(
        options.get("shear_radius_out", options.get("shear_outer_radius_grid", DEFAULT_OUTER_RADIUS_GRID))
    )
    radius_max_grid = int(options.get("shear_radius_max", DEFAULT_MAX_RADIUS_GRID))
    validate_shear_radii(inner_radius_grid, outer_radius_grid, radius_max_grid)
    lower_hpa = float(options.get("shear_lower_hpa", PRESSURE_LEVELS_HPA[1]))
    upper_hpa = float(options.get("shear_upper_hpa", PRESSURE_LEVELS_HPA[0]))
    levels = [upper_hpa, lower_hpa]

    center_y, center_x = load_center_yx(center_path, time_hour)
    u_levels, v_levels = read_pressure_level_winds(source_file, source_time_index, levels)

    center_y_index = int(round(float(center_y)))
    center_x_index = int(round(float(center_x)))
    u_means = [
        polar_annulus_mean(
            u_levels[idx],
            center_y=center_y_index,
            center_x=center_x_index,
            radius_in=inner_radius_grid,
            radius_out=outer_radius_grid,
            radius_max=radius_max_grid,
        )
        for idx in range(len(levels))
    ]
    v_means = [
        polar_annulus_mean(
            v_levels[idx],
            center_y=center_y_index,
            center_x=center_x_index,
            radius_in=inner_radius_grid,
            radius_out=outer_radius_grid,
            radius_max=radius_max_grid,
        )
        for idx in range(len(levels))
    ]

    shear_u = float(u_means[0] - u_means[-1])
    shear_v = float(v_means[0] - v_means[-1])
    magnitude = float(np.hypot(shear_u, shear_v))
    direction = float(-np.degrees(np.arctan2(shear_v, shear_u)))
    return {
        VARIABLE_U: shear_u,
        VARIABLE_V: shear_v,
        VARIABLE_MAGNITUDE: magnitude,
        VARIABLE_DIRECTION: direction,
        "center_y": float(center_y),
        "center_x": float(center_x),
        "inner_radius_grid": inner_radius_grid,
        "outer_radius_grid": outer_radius_grid,
        "radius_max_grid": radius_max_grid,
        "upper_hpa": upper_hpa,
        "lower_hpa": lower_hpa,
        "u_level_means": [float(value) for value in u_means],
        "v_level_means": [float(value) for value in v_means],
    }


# 功能说明：检查风切变计算的内外半径是否在允许范围且顺序正确。
def validate_shear_radii(radius_in: int, radius_out: int, radius_max: int) -> None:
    if radius_in < 0:
        raise ValueError(f"shear radius_in must be non-negative: {radius_in}")
    if radius_out <= radius_in:
        raise ValueError(f"shear radius_out must be greater than radius_in: {radius_in}, {radius_out}")
    if radius_max < radius_out:
        raise ValueError(f"shear radius_max must be at least radius_out: {radius_max} < {radius_out}")


# 功能说明：检查结果说明中的环带半径、最大采样半径、高低气压层和算法版本是否匹配当前设置，返回是否可复用。
def shear_output_is_current(path: Path, options: dict[str, Any] | None) -> bool:
    meta_path = metadata_path(path)
    if not meta_path.exists():
        return False
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    config = options or {}
    expected_radius_in = int(
        config.get("shear_radius_in", config.get("shear_inner_radius_grid", DEFAULT_INNER_RADIUS_GRID))
    )
    expected_radius_out = int(
        config.get("shear_radius_out", config.get("shear_outer_radius_grid", DEFAULT_OUTER_RADIUS_GRID))
    )
    expected_radius_max = int(config.get("shear_radius_max", DEFAULT_MAX_RADIUS_GRID))
    expected_upper_hpa = float(config.get("shear_upper_hpa", PRESSURE_LEVELS_HPA[0]))
    expected_lower_hpa = float(config.get("shear_lower_hpa", PRESSURE_LEVELS_HPA[1]))
    try:
        return (
            metadata.get("algorithm_version") == ALGORITHM_VERSION
            and int(metadata.get("inner_radius_grid", -1)) == expected_radius_in
            and int(metadata.get("outer_radius_grid", -1)) == expected_radius_out
            and int(metadata.get("radius_max_grid", -1)) == expected_radius_max
            and float(metadata.get("upper_hpa", np.nan)) == expected_upper_hpa
            and float(metadata.get("lower_hpa", np.nan)) == expected_lower_hpa
        )
    except (TypeError, ValueError):
        return False


# 功能说明：把二维风场以风暴中心为原点插值成 360 个方位，再平均指定内外半径之间的全部格点，返回环带平均风分量。
def polar_annulus_mean(
    data: np.ndarray,
    *,
    center_y: int,
    center_x: int,
    radius_in: int,
    radius_out: int,
    radius_max: int,
) -> float:
    """Bezier-interpolate to 360-degree polar coordinates and average an annulus.

    This follows the supplied inner-core shear script exactly: the outer radius
    is excluded by the ``radius_in:radius_out`` slice.
    """
    validate_shear_radii(radius_in, radius_out, radius_max)
    polar = bezier_interp(data, center_y=center_y, center_x=center_x, radius_max=radius_max)
    annulus = polar[:, radius_in:radius_out]
    if not np.isfinite(annulus).any():
        raise ValueError(
            f"Bezier polar annulus contains no finite values: radius={radius_in}:{radius_out}"
        )
    return float(np.nanmean(annulus))


# 功能说明：以给定中心为原点，对每个方位、每个整数半径从二维场做三次 Bezier 插值，返回 360 行的极坐标数值表。
def bezier_interp(
    data: np.ndarray,
    *,
    center_y: int,
    center_x: int,
    radius_max: int,
) -> np.ndarray:
    """Vectorized equivalent of the supplied 360-degree ``BezierInterp``."""
    values = np.asarray(data, dtype=float)
    if values.ndim != 2:
        raise ValueError(f"BezierInterp input must be 2D, got shape={values.shape}")
    if radius_max < 0:
        raise ValueError(f"BezierInterp radius_max must be non-negative: {radius_max}")
    cy = int(center_y)
    cx = int(center_x)
    if not (0 <= cy < values.shape[0] and 0 <= cx < values.shape[1]):
        raise ValueError(f"BezierInterp center is outside data: center=({cy}, {cx}), shape={values.shape}")

    stock = np.zeros((360, radius_max + 1), dtype=float)
    stock[:, 0] = values[cy, cx]
    if radius_max == 0:
        return stock

    angle = np.deg2rad(np.arange(360, dtype=float))[:, np.newaxis]
    radius = np.arange(1, radius_max + 1, dtype=float)[np.newaxis, :]
    xproj = radius * np.cos(angle)
    yproj = radius * np.sin(angle)
    xfloor = np.floor(xproj)
    yfloor = np.floor(yproj)
    tx = xproj - xfloor
    ty = yproj - yfloor

    x0 = cx + xfloor.astype(int)
    x1 = cx + np.ceil(xproj).astype(int)
    x2 = cx + np.ceil(xproj).astype(int) + 1
    x_minus_1 = cx + xfloor.astype(int) - 1
    y0 = cy + yfloor.astype(int)
    y1 = cy + np.ceil(yproj).astype(int)
    y2 = cy + np.ceil(yproj).astype(int) + 1
    y_minus_1 = cy + yfloor.astype(int) - 1

    if (
        min(int(x_minus_1.min()), int(y_minus_1.min())) < 0
        or int(x2.max()) >= values.shape[1]
        or int(y2.max()) >= values.shape[0]
    ):
        raise ValueError(
            "BezierInterp radius_max reaches outside the WRF grid; "
            f"center=({cy}, {cx}), radius_max={radius_max}, shape={values.shape}"
        )

    # 功能说明：接收一组 y 方向格点索引，沿 x 方向用相邻四个格点计算 Bezier 插值值，返回全部方位和半径的中间结果。
    def interpolate_x(y_index: np.ndarray) -> np.ndarray:
        base = values[y_index, x0]
        first = tx * (values[y_index, x1] - base)
        curve = 0.25 * tx * (tx - 1.0) * (
            values[y_index, x2]
            - values[y_index, x1]
            - base
            + values[y_index, x_minus_1]
        )
        return base + first + curve

    b_value = interpolate_x(y0)
    c_value = interpolate_x(y1)
    d_value = interpolate_x(y2)
    a_value = interpolate_x(y_minus_1)
    stock[:, 1:] = (
        b_value
        + ty * (c_value - b_value)
        + 0.25 * ty * (ty - 1.0) * (a_value + d_value - b_value - c_value)
    )
    return stock


# 功能说明：从 WRF 文件读取指定气压层的风速分量。
def read_pressure_level_winds(
    source_file: Path,
    source_time_index: int,
    pressure_levels_hpa: list[float],
) -> tuple[np.ndarray, np.ndarray]:
    prepare_wrf_import()
    from netCDF4 import Dataset
    from wrf import getvar, interplevel, to_np

    try:
        with Dataset(source_file, "r") as dataset:
            ua = getvar(dataset, "ua", timeidx=source_time_index)
            va = getvar(dataset, "va", timeidx=source_time_index)
            pressure = getvar(dataset, "pressure", timeidx=source_time_index)
            levels = np.asarray(pressure_levels_hpa, dtype=float)
            u_levels = np.asarray(to_np(interplevel(ua, pressure, levels)), dtype=float)
            v_levels = np.asarray(to_np(interplevel(va, pressure, levels)), dtype=float)
    except Exception as exc:  # noqa: BLE001
        action = f"read/interpolate 200-850 hPa shear source_time_index={source_time_index}"
        raise RuntimeError(wrfout_read_error(path=source_file, action=action, original=exc)) from exc

    if u_levels.ndim == 2:
        u_levels = u_levels[np.newaxis, :, :]
    if v_levels.ndim == 2:
        v_levels = v_levels[np.newaxis, :, :]
    if u_levels.shape[0] != len(pressure_levels_hpa) or v_levels.shape[0] != len(pressure_levels_hpa):
        raise ValueError(f"pressure-level wind shape does not match requested levels: u={u_levels.shape}, v={v_levels.shape}")
    return u_levels, v_levels


# 功能说明：把风切变数组及其说明保存到处理目录。
def write_shear_outputs(
    *,
    request: ProcessingRequest,
    path_manager: PathManager,
    values: dict[str, Any],
    source_file: Path,
    source_time_index: int,
    time_hour: int,
    dtype: str,
) -> dict[str, Path]:
    outputs: dict[str, Path] = {}
    for variable in VARIABLES:
        out_path = path_manager.variable_path(
            member_name=request.member_name,
            domain=request.domain,
            variable=variable,
            time_hour=time_hour,
            dimension=VariableDimension.ONE_D,
        )
        if out_path.exists() and not request.force and shear_output_is_current(out_path, request.options):
            outputs[variable] = out_path
            continue
        atomic_save(out_path, np.asarray([values[variable]], dtype=dtype))
        metadata = build_processing_metadata(request, variable, source_file, time_hour, None, source_time_index)
        metadata.update(
            {
                "derived_from": ["ua", "va", "pressure", "slp_center"],
                "pressure_levels_hpa": [values["upper_hpa"], values["lower_hpa"]],
                "upper_hpa": values["upper_hpa"],
                "lower_hpa": values["lower_hpa"],
                "inner_radius_grid": values["inner_radius_grid"],
                "outer_radius_grid": values["outer_radius_grid"],
                "radius_max_grid": values["radius_max_grid"],
                "center_y": values["center_y"],
                "center_x": values["center_x"],
                "u_level_means": values["u_level_means"],
                "v_level_means": values["v_level_means"],
                "units": _UNITS[variable],
                "radius_definition": "BezierInterp 360-degree polar mean over [radius_in, radius_out)",
                "interpolation_method": "BezierInterp",
                "algorithm_version": ALGORITHM_VERSION,
                "direction_definition": "-degrees(arctan2(v_200_minus_850, u_200_minus_850))",
            }
        )
        path_manager.write_metadata(out_path, metadata)
        outputs[variable] = out_path
    return outputs
