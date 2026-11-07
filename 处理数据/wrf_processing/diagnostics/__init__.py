from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.schemas import ProcessingRequest, VariableDimension
from ..progress import ProgressCallback
from . import (
    axis_mean_max_wind,
    center_land_distance,
    center_slp_5km,
    local_max_wind,
    polar_asymmetry,
    r_34,
    rmw,
    vertical_shear_200_850,
    vortex_tilt,
)


ONE_D_DIAGNOSTIC_VARIABLES = {
    local_max_wind.VARIABLE,
    axis_mean_max_wind.VARIABLE,
    r_34.VARIABLE,
    rmw.VARIABLE,
    center_slp_5km.VARIABLE,
    center_land_distance.VARIABLE,
    *vertical_shear_200_850.VARIABLES,
    *vortex_tilt.VARIABLES,
    *polar_asymmetry.VARIABLES,
}

# These diagnostics inspect their metadata before accepting an existing file.
# The processing manager must call them even when the target .npy is present.
SELF_VALIDATING_DIAGNOSTIC_VARIABLES = {
    *vertical_shear_200_850.VARIABLES,
    *vortex_tilt.VARIABLES,
    *polar_asymmetry.VARIABLES,
}

POLAR_ASYMMETRY_VARIABLES = polar_asymmetry.VARIABLES

_PROCESSORS = {
    local_max_wind.VARIABLE: local_max_wind.process,
    axis_mean_max_wind.VARIABLE: axis_mean_max_wind.process,
    r_34.VARIABLE: r_34.process,
    rmw.VARIABLE: rmw.process,
    center_slp_5km.VARIABLE: center_slp_5km.process,
    center_land_distance.VARIABLE: center_land_distance.process,
    **{variable: vertical_shear_200_850.process for variable in vertical_shear_200_850.VARIABLES},
    **{variable: vortex_tilt.process for variable in vortex_tilt.VARIABLES},
    **{variable: polar_asymmetry.process for variable in polar_asymmetry.VARIABLES},
}


# 功能说明：按请求运行一维时间序列诊断，并写出诊断结果。
def process_1d_diagnostic(
    *,
    request: ProcessingRequest,
    path_manager: PathManager,
    variable: str,
    source_file: Path | None,
    source_time_index: int | None,
    time_hour: int,
    dtype: str,
    domain_config: dict[str, Any],
    ensure_slp_center: Callable[..., Path],
    ensure_standard_data: Callable[..., None],
    ensure_layer_centers: Callable[..., list[Path]],
    progress: ProgressCallback | None = None,
) -> str:
    out_path = path_manager.variable_path(
        member_name=request.member_name,
        domain=request.domain,
        variable=variable,
        time_hour=time_hour,
        dimension=VariableDimension.ONE_D,
    )
    if out_path.exists() and not request.force and variable not in SELF_VALIDATING_DIAGNOSTIC_VARIABLES:
        return str(out_path)

    try:
        processor = _PROCESSORS[variable]
    except KeyError as exc:
        raise ValueError(f"Unsupported 1d diagnostic variable: {variable}") from exc
    return processor(
        request=request,
        path_manager=path_manager,
        variable=variable,
        source_file=source_file,
        source_time_index=source_time_index,
        time_hour=time_hour,
        dtype=dtype,
        domain_config=domain_config,
        ensure_slp_center=ensure_slp_center,
        ensure_standard_data=ensure_standard_data,
        ensure_layer_centers=ensure_layer_centers,
        progress=progress,
    )
