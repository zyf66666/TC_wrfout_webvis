from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.schemas import ProcessingRequest
from ..progress import ProgressCallback, emit
from .common import read_lowest_model_wind, write_scalar_diagnostic


VARIABLE = "local_max_wind"
UNITS = "m s-1"


# 功能说明：读取最低模式层的东、北风分量，计算全域每个格点风速并保存其中最大值。
def process(
    *,
    request: ProcessingRequest,
    path_manager: PathManager,
    source_file: Path,
    source_time_index: int,
    time_hour: int,
    dtype: str,
    progress: ProgressCallback | None = None,
    **_: Any,
) -> str:
    emit(progress, f"计算 {request.member_name} {time_hour}h 最低模式层局地最大风速")
    ua, va = read_lowest_model_wind(source_file, source_time_index)
    value = float(np.nanmax(np.hypot(ua, va)))
    return write_scalar_diagnostic(
        request=request,
        path_manager=path_manager,
        variable=VARIABLE,
        value=value,
        source_file=source_file,
        source_time_index=source_time_index,
        time_hour=time_hour,
        dtype=dtype,
        metadata_extra={"derived_from": ["ua_lowest_model_level", "va_lowest_model_level"]},
        units=UNITS,
    )
