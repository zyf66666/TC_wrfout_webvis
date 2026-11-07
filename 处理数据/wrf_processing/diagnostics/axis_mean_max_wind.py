from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np

from wrf_webvis.app.core.centers import load_center_yx
from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.schemas import ProcessingRequest
from ..progress import ProgressCallback, emit
from ..radial_tangential_wind import radial_tangential_wind
from .common import azimuthal_mean_by_radius, read_lowest_model_wind, write_scalar_diagnostic


VARIABLE = "axis_mean_max_wind"
UNITS = "m s-1"


# 功能说明：将最低模式层风转换为相对中心的切向风，求每个半径的方位平均并保存其中最大风速。
def process(
    *,
    request: ProcessingRequest,
    path_manager: PathManager,
    source_file: Path,
    source_time_index: int,
    time_hour: int,
    dtype: str,
    domain_config: dict[str, Any],
    ensure_slp_center: Callable[..., Path],
    progress: ProgressCallback | None = None,
    **_: Any,
) -> str:
    emit(progress, f"计算 {request.member_name} {time_hour}h 轴平均最大切向风")
    ensure_slp_center(request, [time_hour], progress)
    center_path = path_manager.center_path(request.member_name, request.domain, "slp")
    center_y, center_x = load_center_yx(center_path, time_hour)
    dx_km = float(domain_config.get("dx_km") or 1.0)
    dy_km = float(domain_config.get("dy_km") or dx_km)
    ua, va = read_lowest_model_wind(source_file, source_time_index)
    vt = radial_tangential_wind(ua, va, center_y=center_y, center_x=center_x, dx_km=dx_km, dy_km=dy_km, component="vt")
    _radii, means = azimuthal_mean_by_radius(vt, center_y=center_y, center_x=center_x, dx_km=dx_km, dy_km=dy_km)
    value = float(means[int(np.nanargmax(means))])
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
            "derived_from": ["ua_lowest_model_level", "va_lowest_model_level"],
            "center_mode": "slp",
            "center_path": str(center_path),
        },
        units=UNITS,
    )
