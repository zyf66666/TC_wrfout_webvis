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


VARIABLE = "rmw"
UNITS = "km"


# 功能说明：读取最低模式层风，围绕海平面气压中心计算方位平均切向风，保存风速最大的半径（RMW）。
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
    emit(progress, f"计算 {request.member_name} {time_hour}h RMW")
    ensure_slp_center(request, [time_hour], progress)
    center_path = path_manager.center_path(request.member_name, request.domain, "slp")
    center_y, center_x = load_center_yx(center_path, time_hour)
    dx_km = float(domain_config.get("dx_km") or 1.0)
    dy_km = float(domain_config.get("dy_km") or dx_km)
    ua, va = read_lowest_model_wind(source_file, source_time_index)
    vt = radial_tangential_wind(ua, va, center_y=center_y, center_x=center_x, dx_km=dx_km, dy_km=dy_km, component="vt")
    radii, means = azimuthal_mean_by_radius(vt, center_y=center_y, center_x=center_x, dx_km=dx_km, dy_km=dy_km)
    value = float(radii[int(np.nanargmax(means))])
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
