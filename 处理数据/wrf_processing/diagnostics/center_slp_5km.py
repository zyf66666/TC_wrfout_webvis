from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np

from wrf_webvis.app.core.centers import load_center_yx
from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.schemas import ProcessingRequest, VariableDimension
from ..progress import ProgressCallback, emit
from .common import mean_within_radius, write_scalar_diagnostic


VARIABLE = "center_slp_5km"
UNITS = "hPa"
RADIUS_KM = 5.0


# 功能说明：读取海平面气压和台风中心，计算中心周围 5 公里圆内的平均气压并保存。
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
    ensure_standard_data: Callable[..., None],
    progress: ProgressCallback | None = None,
    **_: Any,
) -> str:
    emit(progress, f"计算 {request.member_name} {time_hour}h 中心 5km SLP 均值")
    ensure_slp_center(request, [time_hour], progress)
    ensure_standard_data(request, ["slp"], None, progress)
    center_path = path_manager.center_path(request.member_name, request.domain, "slp")
    center_y, center_x = load_center_yx(center_path, time_hour)
    slp_path = path_manager.existing_variable(
        member_name=request.member_name,
        domain=request.domain,
        variable="slp",
        time_hour=time_hour,
        dimension=VariableDimension.TWO_D,
    )
    if slp_path is None:
        raise FileNotFoundError(f"Missing slp for center_slp_5km {time_hour}h")

    dx_km = float(domain_config.get("dx_km") or 1.0)
    dy_km = float(domain_config.get("dy_km") or dx_km)
    slp = np.asarray(np.load(slp_path.path, mmap_mode="r"), dtype=float)
    value = mean_within_radius(slp, center_y=center_y, center_x=center_x, radius_km=RADIUS_KM, dx_km=dx_km, dy_km=dy_km)
    return write_scalar_diagnostic(
        request=request,
        path_manager=path_manager,
        variable=VARIABLE,
        value=value,
        source_file=source_file,
        source_time_index=source_time_index,
        time_hour=time_hour,
        dtype=dtype,
        metadata_extra={"derived_from": [str(slp_path.path)], "radius_km": RADIUS_KM},
        units=UNITS,
    )
