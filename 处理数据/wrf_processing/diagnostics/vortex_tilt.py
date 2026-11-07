from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import numpy as np

from wrf_webvis.app.core.centers import load_center_yx
from wrf_webvis.app.core.paths import PathManager, metadata_path
from wrf_webvis.app.core.schemas import ProcessingRequest, VariableDimension
from ..processing_utils import atomic_save, build_processing_metadata
from ..progress import ProgressCallback, emit


DEFAULT_LOWER_HEIGHT_M = 2000.0
DEFAULT_UPPER_HEIGHT_M = 8000.0
ALGORITHM_VERSION = "layer_center_displacement_v1"

VARIABLE_X = "tilt_x"
VARIABLE_Y = "tilt_y"
VARIABLE_MAGNITUDE = "tilt_magnitude"
VARIABLE_ANGLE = "tilt_angle"
VARIABLES = {VARIABLE_X, VARIABLE_Y, VARIABLE_MAGNITUDE, VARIABLE_ANGLE}

_UNITS = {
    VARIABLE_X: "km",
    VARIABLE_Y: "km",
    VARIABLE_MAGNITUDE: "km",
    VARIABLE_ANGLE: "degree",
}


# 功能说明：确保高低两层中心可用，计算高层相对低层的位移、距离和方向，检查并保存四种倾斜结果，返回本次请求指标的路径。
def process(
    *,
    request: ProcessingRequest,
    path_manager: PathManager,
    variable: str,
    source_file: Path,
    source_time_index: int,
    time_hour: int | float,
    dtype: str,
    domain_config: dict[str, Any],
    ensure_layer_centers: Callable[..., list[Path]],
    progress: ProgressCallback | None = None,
    **_: Any,
) -> str:
    if variable not in VARIABLES:
        raise ValueError(f"Unsupported vortex-tilt variable: {variable}")
    out_path = path_manager.variable_path(
        member_name=request.member_name,
        domain=request.domain,
        variable=variable,
        time_hour=time_hour,
        dimension=VariableDimension.ONE_D,
    )
    if out_path.exists() and not request.force and tilt_output_is_current(out_path, request.options):
        return str(out_path)

    lower_height_m, upper_height_m = tilt_heights(request.options)
    emit(
        progress,
        f"计算 {request.member_name} {float(time_hour):g}h 涡旋倾斜 "
        f"({lower_height_m:g}–{upper_height_m:g} m)",
    )
    ensure_layer_centers(
        request,
        [time_hour],
        [lower_height_m, upper_height_m],
        progress,
    )
    values = calculate_tilt_values(
        lower_center_path=path_manager.layer_center_path(request.member_name, request.domain, lower_height_m),
        upper_center_path=path_manager.layer_center_path(request.member_name, request.domain, upper_height_m),
        time_hour=time_hour,
        domain_config=domain_config,
        lower_height_m=lower_height_m,
        upper_height_m=upper_height_m,
    )
    outputs = write_tilt_outputs(
        request=request,
        path_manager=path_manager,
        values=values,
        source_file=source_file,
        source_time_index=source_time_index,
        time_hour=time_hour,
        dtype=dtype,
    )
    return str(outputs[variable])


# 功能说明：从选项确定需要比较的低层和高层中心高度。
def tilt_heights(options: dict[str, Any] | None) -> tuple[float, float]:
    config = options or {}
    lower = float(config.get("tilt_lower_height_m", DEFAULT_LOWER_HEIGHT_M))
    upper = float(config.get("tilt_upper_height_m", DEFAULT_UPPER_HEIGHT_M))
    if lower < 0:
        raise ValueError(f"tilt_lower_height_m must be non-negative: {lower}")
    if upper <= lower:
        raise ValueError(
            f"tilt_upper_height_m must be greater than tilt_lower_height_m: {upper} <= {lower}"
        )
    return lower, upper


# 功能说明：比较不同高度的风暴中心位置，计算风暴涡旋的倾斜量。
def calculate_tilt_values(
    *,
    lower_center_path: Path,
    upper_center_path: Path,
    time_hour: int | float,
    domain_config: dict[str, Any],
    lower_height_m: float,
    upper_height_m: float,
) -> dict[str, float]:
    lower_y, lower_x = load_center_yx(lower_center_path, time_hour)
    upper_y, upper_x = load_center_yx(upper_center_path, time_hour)
    dx_km = float(domain_config.get("dx_km") or 1.0)
    dy_km = float(domain_config.get("dy_km") or dx_km)

    tilt_x = float((upper_x - lower_x) * dx_km)
    tilt_y = float((upper_y - lower_y) * dy_km)
    magnitude = float(np.hypot(tilt_x, tilt_y))
    # 与风切变方向保持一致：从低层中心指向高层中心，顺时针为负角度。
    angle = float(-np.degrees(np.arctan2(tilt_y, tilt_x)))
    return {
        VARIABLE_X: tilt_x,
        VARIABLE_Y: tilt_y,
        VARIABLE_MAGNITUDE: magnitude,
        VARIABLE_ANGLE: angle,
        "lower_center_y": float(lower_y),
        "lower_center_x": float(lower_x),
        "upper_center_y": float(upper_y),
        "upper_center_x": float(upper_x),
        "lower_height_m": float(lower_height_m),
        "upper_height_m": float(upper_height_m),
        "dx_km": dx_km,
        "dy_km": dy_km,
    }


# 功能说明：检查已有结果说明中的算法版本与高低层高度是否匹配；此处不检查中心文件内容是否变化。
def tilt_output_is_current(path: Path, options: dict[str, Any] | None) -> bool:
    meta_path = metadata_path(path)
    if not meta_path.exists():
        return False
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        lower, upper = tilt_heights(options)
        return (
            metadata.get("algorithm_version") == ALGORITHM_VERSION
            and float(metadata.get("lower_height_m", np.nan)) == lower
            and float(metadata.get("upper_height_m", np.nan)) == upper
        )
    except (OSError, TypeError, ValueError):
        return False


# 功能说明：分别把某时刻的东西位移、南北位移、位移长度和方向保存为四个单值文件及说明；符合复用条件的文件保留。
def write_tilt_outputs(
    *,
    request: ProcessingRequest,
    path_manager: PathManager,
    values: dict[str, float],
    source_file: Path,
    source_time_index: int,
    time_hour: int | float,
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
        if out_path.exists() and not request.force and tilt_output_is_current(out_path, request.options):
            outputs[variable] = out_path
            continue
        atomic_save(out_path, np.asarray([values[variable]], dtype=dtype))
        metadata = build_processing_metadata(
            request,
            variable,
            source_file,
            time_hour,
            None,
            source_time_index,
        )
        metadata.update(
            {
                "derived_from": ["pressure", "layer_center"],
                "lower_height_m": values["lower_height_m"],
                "upper_height_m": values["upper_height_m"],
                "lower_center_y": values["lower_center_y"],
                "lower_center_x": values["lower_center_x"],
                "upper_center_y": values["upper_center_y"],
                "upper_center_x": values["upper_center_x"],
                "dx_km": values["dx_km"],
                "dy_km": values["dy_km"],
                "units": _UNITS[variable],
                "algorithm_version": ALGORITHM_VERSION,
                "vector_definition": "upper-level center minus lower-level center",
                "angle_definition": "-degrees(arctan2(tilt_y, tilt_x)); clockwise from east",
            }
        )
        path_manager.write_metadata(out_path, metadata)
        outputs[variable] = out_path
    return outputs
