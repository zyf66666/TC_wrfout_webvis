from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np

from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.schemas import ProcessingRequest
from ..land_distance import LandDistanceService
from ..processing_utils import resolve_wrfout_root
from ..progress import ProgressCallback, emit
from .common import land_distance_for_time, write_scalar_diagnostic


VARIABLE = "center_land_distance"
UNITS = "km"


# 功能说明：确保中心和陆地边界存在，计算指定时次台风中心到最近海岸的有符号距离并保存；全海洋试验写入约定值。
def process(
    *,
    request: ProcessingRequest,
    path_manager: PathManager,
    source_file: Path,
    source_time_index: int,
    time_hour: int | float,
    dtype: str,
    ensure_slp_center: Callable[..., Path],
    progress: ProgressCallback | None = None,
    **_: Any,
) -> str:
    emit(progress, f"计算 {request.member_name} {time_hour}h 台风中心到陆地距离")
    ensure_slp_center(request, [time_hour], progress)

    fallback_value = _all_ocean_fallback(request)
    if fallback_value is not None:
        emit(progress, f"{request.member_name} 为全海洋试验，center_land_distance={fallback_value:g} km")
        return _write_fallback(
            request=request,
            path_manager=path_manager,
            source_file=source_file,
            source_time_index=source_time_index,
            time_hour=time_hour,
            dtype=dtype,
            value=fallback_value,
            reason="member_pattern",
        )

    service = LandDistanceService(request.processed_root)
    boundary_wrfout = request.wrfout_file if request.wrfout_file and "_d01_" in str(request.wrfout_file) else None
    boundary_path = service.compute_land_boundary(
        member_name=request.member_name,
        wrfout_root=resolve_wrfout_root(request.member_name, request.wrfout_root),
        wrfout_file=boundary_wrfout,
        force=request.force,
    )

    # A valid all-ocean d01 produces an empty transition-point array. Handle
    # that case even when the experiment name does not match a configured
    # all-ocean pattern.
    boundary = np.asarray(np.load(boundary_path), dtype=float)
    if boundary.size == 0:
        fallback_value = float(request.options.get("all_ocean_land_distance_km", 9999.0))
        emit(progress, f"{request.member_name} 的陆地边界为空，center_land_distance={fallback_value:g} km")
        return _write_fallback(
            request=request,
            path_manager=path_manager,
            source_file=source_file,
            source_time_index=source_time_index,
            time_hour=time_hour,
            dtype=dtype,
            value=fallback_value,
            reason="empty_land_boundary",
        )

    distance_result = service.compute_distance(
        member_name=request.member_name,
        domain=request.domain,
        boundary_path=boundary_path,
        force=request.force,
    )
    try:
        value = land_distance_for_time(Path(distance_result["npy"]), time_hour)
    except KeyError:
        distance_result = service.compute_distance(
            member_name=request.member_name,
            domain=request.domain,
            boundary_path=boundary_path,
            force=True,
        )
        value = land_distance_for_time(Path(distance_result["npy"]), time_hour)

    return write_scalar_diagnostic(
        request=request,
        path_manager=path_manager,
        variable=VARIABLE,
        value=value,
        source_file=source_file,
        source_time_index=source_time_index,
        time_hour=time_hour,
        dtype=dtype,
        metadata_extra={"derived_from": [distance_result["npy"]], "land_boundary": str(boundary_path)},
        units=UNITS,
    )


# 功能说明：检查试验名称是否匹配配置的全海洋标记；匹配时返回约定的远海距离，默认9999公里，否则返回空值。
def _all_ocean_fallback(request: ProcessingRequest) -> float | None:
    patterns = request.options.get("all_ocean_member_patterns", ["_sea", "ocean"])
    if isinstance(patterns, str):
        patterns = [patterns]
    member = request.member_name.lower()
    if any(str(pattern).lower() in member for pattern in patterns):
        return float(request.options.get("all_ocean_land_distance_km", 9999.0))
    return None


# 功能说明：当试验被标为全海洋时，把约定的远海距离连同回退原因写成该时次的中心距陆地诊断文件。
def _write_fallback(
    *,
    request: ProcessingRequest,
    path_manager: PathManager,
    source_file: Path,
    source_time_index: int,
    time_hour: int | float,
    dtype: str,
    value: float,
    reason: str,
) -> str:
    return write_scalar_diagnostic(
        request=request,
        path_manager=path_manager,
        variable=VARIABLE,
        value=value,
        source_file=source_file,
        source_time_index=source_time_index,
        time_hour=time_hour,
        dtype=dtype,
        metadata_extra={
            "derived_from": [],
            "all_ocean_fallback": True,
            "all_ocean_reason": reason,
            "all_ocean_land_distance_km": value,
        },
        units=UNITS,
    )
