from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.registry import VARIABLE_REGISTRY, ResolvedFourierVariable, parse_fourier_variable_name
from wrf_webvis.app.core.schemas import ProcessingRequest, VariableDimension
from .processing_utils import atomic_save, build_processing_metadata


# 功能说明：从极坐标场中去掉周向平均值，保留空间不对称部分。
def asymmetric_component(data: np.ndarray, *, azimuth_axis: int = 0) -> np.ndarray:
    values = _validated_polar_array(data)
    return values - np.mean(values, axis=azimuth_axis, keepdims=True)


# 功能说明：从周向序列提取指定波数的傅里叶波动分量。
def extract_wave(data: np.ndarray, wavenumber: int, *, azimuth_axis: int = 0) -> np.ndarray:
    values = _validated_polar_array(data)
    axis = azimuth_axis % values.ndim
    n_azimuth = values.shape[axis]
    if wavenumber < 0 or wavenumber >= n_azimuth:
        raise ValueError(f"wavenumber must be in [0, {n_azimuth - 1}], got {wavenumber}")

    coeffs = np.fft.fft(values, axis=axis)
    filtered = np.zeros_like(coeffs)
    selector = [slice(None)] * values.ndim
    selector[axis] = wavenumber
    filtered[tuple(selector)] = coeffs[tuple(selector)]
    if wavenumber != 0:
        selector[axis] = -wavenumber
        filtered[tuple(selector)] = coeffs[tuple(selector)]
    return np.real(np.fft.ifft(filtered, axis=axis))


# 功能说明：根据变量请求，返回去掉周向平均的非对称场，或者只保留指定波数的起伏；每次返回所选的一种结果。
def decompose_polar_field(data: np.ndarray, parsed: ResolvedFourierVariable, *, azimuth_axis: int = 0) -> np.ndarray:
    if parsed.component == "asymmetry":
        return asymmetric_component(data, azimuth_axis=azimuth_axis)
    if parsed.component == "wave" and parsed.wavenumber is not None:
        return extract_wave(data, parsed.wavenumber, azimuth_axis=azimuth_axis)
    raise ValueError(f"Unsupported Fourier component: {parsed.component}")


# 功能说明：按变量名确定要提取非对称部分还是某个波数，逐个目标高度读取极坐标数据并保存所选分量。
def process_fourier_decomposition(
    *,
    request: ProcessingRequest,
    variable: str,
    time_hour: int,
    heights: list[float],
    dtype: str,
) -> list[str]:
    parsed = parse_fourier_variable_name(variable)
    if parsed is None:
        raise ValueError(f"Not a Fourier decomposition variable: {variable}")
    item = VARIABLE_REGISTRY.get(parsed.base_name)
    if item is None:
        raise ValueError(f"Base variable is not registered for Fourier decomposition: {parsed.base_name}")

    path_manager = PathManager(request.processed_root)
    if item.dimension == VariableDimension.THREE_D:
        if not heights:
            raise ValueError(f"3d Fourier decomposition requires heights: {variable}")
        return [
            _process_one_fourier_file(
                request=request,
                path_manager=path_manager,
                parsed=parsed,
                time_hour=time_hour,
                height_m=height_m,
                dimension=VariableDimension.THREE_D,
                dtype=dtype,
            )
            for height_m in heights
        ]

    return [
        _process_one_fourier_file(
            request=request,
            path_manager=path_manager,
            parsed=parsed,
            time_hour=time_hour,
            height_m=None,
            dimension=VariableDimension.TWO_D,
            dtype=dtype,
        )
    ]


# 功能说明：读取一份极坐标数据，计算请求的非对称部分或指定波数，写出结果和来源说明；已有结果且未要求重算时直接复用。
def _process_one_fourier_file(
    *,
    request: ProcessingRequest,
    path_manager: PathManager,
    parsed: ResolvedFourierVariable,
    time_hour: int,
    height_m: float | None,
    dimension: VariableDimension,
    dtype: str,
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
        raise FileNotFoundError(f"Missing Fourier source: {parsed.source_name} {time_hour}h{height_text}")

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

    source_data = np.load(source.path, mmap_mode="r")
    result = decompose_polar_field(source_data, parsed, azimuth_axis=0)
    atomic_save(out_path, result.astype(dtype, copy=False))
    metadata = build_processing_metadata(
        request=request,
        variable=parsed.requested_name,
        source_file=source.path,
        time_hour=time_hour,
        height_m=height_m,
        source_time_index=None,
    )
    metadata.update(_fourier_metadata(parsed, source.path))
    path_manager.write_metadata(out_path, metadata)
    return str(out_path)


# 功能说明：根据傅里叶分解参数和源文件信息生成结果元数据。
def _fourier_metadata(parsed: ResolvedFourierVariable, source_path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source": str(source_path),
        "source_variable": parsed.source_name,
        "base_variable": parsed.base_name,
        "fourier_component": parsed.component,
        "azimuth_axis": 0,
    }
    if parsed.wavenumber is not None:
        payload["wavenumber"] = parsed.wavenumber
    return payload


# 功能说明：把输入转为数值数组，要求至少有两个维度且不含 NaN 缺测值；不满足时拒绝做傅里叶分解。
def _validated_polar_array(data: np.ndarray) -> np.ndarray:
    values = np.asarray(data, dtype=float)
    if values.ndim < 2:
        raise ValueError(f"Fourier decomposition expects at least 2 dimensions, got {values.shape}")
    if np.isnan(values).any():
        raise ValueError("Fourier decomposition source contains NaN values")
    return values
