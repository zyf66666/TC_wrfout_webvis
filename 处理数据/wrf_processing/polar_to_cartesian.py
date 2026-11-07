from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from wrf_webvis.app.core.paths import PathManager, metadata_path
from wrf_webvis.app.core.registry import VARIABLE_REGISTRY, ResolvedCartesianFromPolarVariable, parse_cartesian_from_polar_variable_name
from wrf_webvis.app.core.schemas import ProcessingRequest, VariableDimension
from .processing_utils import atomic_save, build_processing_metadata


# 功能说明：把以风暴中心为圆心的极坐标数据重采样到规则笛卡尔网格。
def polar_to_cartesian_grid(
    data: np.ndarray,
    *,
    center_y: float,
    center_x: float,
    output_shape: tuple[int, int],
    dx_km: float,
    dy_km: float | None = None,
    radius_resolution_km: float | None = None,
    fill_value: float = 0.0,
) -> np.ndarray:
    values = np.asarray(data, dtype=float)
    if values.ndim != 2:
        raise ValueError(f"polar_to_cartesian_grid expects 2d (azimuth, radius), got {values.shape}")
    dy = float(dy_km if dy_km is not None else dx_km)
    dx = float(dx_km)
    resolution = float(radius_resolution_km if radius_resolution_km is not None else dx)

    yy, xx = np.indices(output_shape, dtype=float)
    y_km = (yy - float(center_y)) * dy
    x_km = (xx - float(center_x)) * dx
    radius_km = np.hypot(x_km, y_km)
    angle_deg = (np.degrees(np.arctan2(y_km, x_km)) + 360.0) % 360.0

    azimuth_count, radius_count = values.shape
    angle_coord = angle_deg / 360.0 * float(azimuth_count)
    radius_coord = radius_km / resolution
    outside = radius_coord > float(radius_count - 1)

    wrapped = np.concatenate((values, values[:1, :]), axis=0)
    try:
        from scipy.ndimage import map_coordinates

        coords = np.vstack([angle_coord.ravel(), radius_coord.ravel()])
        sampled = map_coordinates(wrapped, coords, order=1, mode="nearest").reshape(output_shape)
    except ImportError:
        angle_index = np.rint(angle_coord).astype(int) % azimuth_count
        radius_index = np.clip(np.rint(radius_coord).astype(int), 0, radius_count - 1)
        sampled = values[angle_index, radius_index]

    sampled[outside] = fill_value
    return sampled


# 功能说明：识别变量的基础维度，对二维变量处理一次、对三维变量逐高度处理，并返回写出的普通网格文件路径。
def process_polar_to_cartesian(
    *,
    request: ProcessingRequest,
    variable: str,
    time_hour: int,
    heights: list[float],
    dtype: str,
    domain_config: dict[str, Any],
) -> list[str]:
    parsed = parse_cartesian_from_polar_variable_name(variable)
    if parsed is None:
        raise ValueError(f"Not a polar-to-cartesian variable: {variable}")
    item = VARIABLE_REGISTRY.get(parsed.base_name)
    if item is None:
        raise ValueError(f"Base variable is not registered for polar-to-cartesian: {parsed.base_name}")

    path_manager = PathManager(request.processed_root)
    if item.dimension == VariableDimension.THREE_D:
        if not heights:
            raise ValueError(f"3d polar-to-cartesian processing requires heights: {variable}")
        return [
            _process_one_polar_to_cartesian_file(
                request=request,
                path_manager=path_manager,
                parsed=parsed,
                time_hour=time_hour,
                height_m=height_m,
                dimension=VariableDimension.THREE_D,
                dtype=dtype,
                domain_config=domain_config,
            )
            for height_m in heights
        ]
    return [
        _process_one_polar_to_cartesian_file(
            request=request,
            path_manager=path_manager,
            parsed=parsed,
            time_hour=time_hour,
            height_m=None,
            dimension=VariableDimension.TWO_D,
            dtype=dtype,
            domain_config=domain_config,
        )
    ]


# 功能说明：读取一个极坐标源场，取得中心和目标网格尺寸后插值回普通网格；已有目标文件存在且未强制重算时直接复用，不核对元数据。
def _process_one_polar_to_cartesian_file(
    *,
    request: ProcessingRequest,
    path_manager: PathManager,
    parsed: ResolvedCartesianFromPolarVariable,
    time_hour: int,
    height_m: float | None,
    dimension: VariableDimension,
    dtype: str,
    domain_config: dict[str, Any],
) -> str:
    source = path_manager.existing_variable(
        member_name=request.member_name,
        domain=request.domain,
        variable=parsed.source_name,
        time_hour=time_hour,
        dimension=dimension,
        height_m=height_m,
    )
    if source is None:
        height_text = "" if height_m is None else f" {height_m:g}m"
        raise FileNotFoundError(f"Missing polar source: {parsed.source_name} {time_hour}h{height_text}")

    out_path = path_manager.variable_path(
        member_name=request.member_name,
        domain=request.domain,
        variable=parsed.requested_name,
        time_hour=time_hour,
        dimension=dimension,
        height_m=height_m,
    )
    if out_path.exists() and not request.force:
        return str(out_path)

    source_metadata = _read_metadata(source.path)
    center_y, center_x = _center_from_metadata_or_file(
        path_manager=path_manager,
        parsed=parsed,
        request=request,
        time_hour=time_hour,
        height_m=height_m,
        metadata=source_metadata,
    )
    output_shape = _output_shape(source_metadata, domain_config)
    dx_km = float(source_metadata.get("dx_km") or domain_config.get("dx_km") or 1.0)
    dy_km = float(source_metadata.get("dy_km") or domain_config.get("dy_km") or dx_km)
    radius_resolution_km = float(source_metadata.get("radius_resolution_km") or dx_km)

    cartesian = polar_to_cartesian_grid(
        np.load(source.path, mmap_mode="r"),
        center_y=center_y,
        center_x=center_x,
        output_shape=output_shape,
        dx_km=dx_km,
        dy_km=dy_km,
        radius_resolution_km=radius_resolution_km,
    )
    atomic_save(out_path, cartesian.astype(dtype, copy=False))
    metadata = build_processing_metadata(
        request=request,
        variable=parsed.requested_name,
        source_file=source.path,
        time_hour=time_hour,
        height_m=height_m,
        source_time_index=None,
    )
    metadata.update(
        {
            "source": str(source.path),
            "source_variable": parsed.source_name,
            "base_variable": parsed.base_name,
            "center_mode": parsed.center_mode,
            "center_y": float(center_y),
            "center_x": float(center_x),
            "output_shape": list(output_shape),
            "dx_km": dx_km,
            "dy_km": dy_km,
            "radius_resolution_km": radius_resolution_km,
        }
    )
    path_manager.write_metadata(out_path, metadata)
    return str(out_path)


# 功能说明：读取某个极坐标数据文件旁的 JSON 元数据；文件不存在或内容损坏时返回空字典。
def _read_metadata(path: Path) -> dict[str, Any]:
    meta_path = metadata_path(path)
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


# 功能说明：优先从极坐标元数据取得中心，缺失时读取对应中心文件。
def _center_from_metadata_or_file(
    *,
    path_manager: PathManager,
    parsed: ResolvedCartesianFromPolarVariable,
    request: ProcessingRequest,
    time_hour: int,
    height_m: float | None,
    metadata: dict[str, Any],
) -> tuple[float, float]:
    if "center_y" in metadata and "center_x" in metadata:
        return float(metadata["center_y"]), float(metadata["center_x"])

    from wrf_webvis.app.core.centers import load_center_yx

    if parsed.center_mode == "layer" and height_m is not None:
        center_path = path_manager.layer_center_path(request.member_name, request.domain, height_m)
    else:
        center_path = path_manager.center_path(request.member_name, request.domain, "slp")
    if not center_path.exists():
        raise FileNotFoundError(f"Center file does not exist: {center_path}")
    return load_center_yx(center_path, time_hour)


# 功能说明：根据极坐标元数据和域配置确定笛卡尔输出网格的行列数。
def _output_shape(metadata: dict[str, Any], domain_config: dict[str, Any]) -> tuple[int, int]:
    source_shape = metadata.get("source_shape")
    if isinstance(source_shape, list) and len(source_shape) >= 2:
        return int(source_shape[-2]), int(source_shape[-1])
    ny = domain_config.get("ny") or domain_config.get("grid_number")
    nx = domain_config.get("nx") or domain_config.get("grid_number")
    if ny and nx:
        return int(ny), int(nx)
    raise ValueError("Cannot infer Cartesian output shape; missing polar source metadata and domain nx/ny")
