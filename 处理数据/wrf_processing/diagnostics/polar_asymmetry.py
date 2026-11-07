from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from wrf_webvis.app.core.paths import PathManager, metadata_path
from wrf_webvis.app.core.schemas import ProcessingRequest, SelectionSpec, VariableDimension
from ..polar_transformer import PolarTransformer
from ..progress import ProgressCallback, emit
from ..processing_utils import atomic_save, build_processing_metadata


ALGORITHM_VERSION = "fixed_geographic_quadrant_disk_v1"
PRECIPITATION_VARIABLE = "precipitation_asymmetry"
DBZ_VARIABLE = "dbz_asymmetry"
MDBZ_VARIABLE = "mdbz_asymmetry"
VARIABLES = {PRECIPITATION_VARIABLE, DBZ_VARIABLE, MDBZ_VARIABLE}
DEFAULT_RADIUS_KM = 200.0
DEFAULT_DBZ_HEIGHT_M = 3000.0
UNITS = "1"


@dataclass(frozen=True)
class _SourceSpec:
    polar_variable: str
    dimension: VariableDimension
    center_mode: str
    height_m: float | None = None


# 功能说明：准备降水或反射率的极坐标数据，计算固定地理方向四个象限各占全圆总量的比例，用偏离均分的程度表示非对称度并保存。
def process(
    *,
    request: ProcessingRequest,
    path_manager: PathManager,
    variable: str,
    time_hour: int,
    dtype: str,
    ensure_slp_center: Callable[..., Path],
    ensure_standard_data: Callable[..., None],
    ensure_layer_centers: Callable[..., list[Path]],
    progress: ProgressCallback | None = None,
    **_: Any,
) -> str:
    if variable not in VARIABLES:
        raise ValueError(f"Unsupported polar asymmetry diagnostic: {variable}")

    radius_km = _requested_radius_km(request.options)
    spec = _source_spec(variable, request.options)
    source_path, source_metadata = _ensure_source_polar(
        request=request,
        path_manager=path_manager,
        spec=spec,
        time_hour=time_hour,
        radius_km=radius_km,
        ensure_slp_center=ensure_slp_center,
        ensure_standard_data=ensure_standard_data,
        ensure_layer_centers=ensure_layer_centers,
        progress=progress,
    )
    resolution_km = _radius_resolution_km(source_path, source_metadata)
    out_path = path_manager.variable_path(
        member_name=request.member_name,
        domain=request.domain,
        variable=variable,
        time_hour=time_hour,
        dimension=VariableDimension.ONE_D,
    )
    if out_path.exists() and not request.force and output_is_current(
        out_path,
        variable=variable,
        source_path=source_path,
        radius_km=radius_km,
        radius_resolution_km=resolution_km,
        spec=spec,
    ):
        return str(out_path)

    emit(progress, f"计算 {request.member_name} {time_hour}h {variable}")
    values = np.asarray(np.load(source_path, mmap_mode="r"), dtype=float)
    value = calculate_fixed_quadrant_asymmetry(
        values,
        radius_resolution_km=resolution_km,
        radius_km=radius_km,
    )
    atomic_save(out_path, np.asarray([value], dtype=dtype))
    metadata = build_processing_metadata(request, variable, source_path, time_hour, None, None)
    metadata.update(
        {
            "algorithm_version": ALGORITHM_VERSION,
            "derived_from": [str(source_path)],
            "source_polar_variable": spec.polar_variable,
            "source_polar_center_mode": spec.center_mode,
            "center_mode": spec.center_mode,
            "radius_km": radius_km,
            "radius_resolution_km": resolution_km,
            "integration_domain": "disk",
            "quadrants_deg": {
                "down_shear": [135, 225],
                "left_shear": [225, 315],
                "up_shear": [315, 45],
                "right_shear": [45, 135],
            },
            "units": UNITS,
            "source_polar_mtime_ns": source_path.stat().st_mtime_ns,
        }
    )
    if spec.height_m is not None:
        metadata["center_height_m"] = float(spec.height_m)
    path_manager.write_metadata(out_path, metadata)
    return str(out_path)


# 功能说明：把负值和缺测先置0，按面积权重积分得到四个固定地理象限占全圆的比例，再计算它们偏离各占四分之一的程度；全圆总量非正时返回缺测。
def calculate_fixed_quadrant_asymmetry(
    polar_values: np.ndarray,
    *,
    radius_resolution_km: float,
    radius_km: float,
) -> float:
    """Return the unsmoothed four-fixed-geographic-quadrant asymmetry.

    The directions and integrations are intentionally the same as the supplied
    ``my_package.py`` implementation: polar angle 0 degrees points east, the
    four quadrants are fixed in that geographic frame, and the radial integral
    is weighted by radius over the full disk.
    """
    values = np.asarray(polar_values, dtype=float)
    if values.ndim != 2 or values.shape[0] < 360:
        raise ValueError(f"Polar asymmetry requires at least 360 angular samples, got shape={values.shape}")
    if not np.isfinite(radius_resolution_km) or radius_resolution_km <= 0:
        raise ValueError(f"Polar radial resolution must be positive, got {radius_resolution_km!r}")
    if not np.isfinite(radius_km) or radius_km <= 0:
        raise ValueError(f"Asymmetry radius must be positive, got {radius_km!r}")

    radii = np.arange(values.shape[1], dtype=float) * float(radius_resolution_km)
    selected = radii <= float(radius_km) + 1.0e-8
    if not selected.any() or radii[selected][-1] < float(radius_km) - 1.0e-8:
        available = float(radii[-1]) if radii.size else 0.0
        raise ValueError(
            f"Polar source only reaches {available:g} km, below requested asymmetry radius {float(radius_km):g} km"
        )

    # The reference routine clips negative RAINNCV/DBZ before integration.  It
    # also implicitly drops NaNs via its interpolation; replacing them with zero
    # makes the same physical choice explicit for cached polar fields.
    field = np.nan_to_num(values[:360, selected], nan=0.0, posinf=0.0, neginf=0.0)
    field[field < 0.0] = 0.0
    radial = radii[selected]
    azimuth_integral = _trapezoid(field * radial[np.newaxis, :], radial, axis=1)
    theta = np.deg2rad(np.arange(360, dtype=float))
    total = float(_trapezoid(azimuth_integral, theta))
    if not np.isfinite(total) or total <= 0.0:
        return float("nan")

    down_shear = _trapezoid(azimuth_integral[135:225], theta[135:225])
    left_shear = _trapezoid(azimuth_integral[225:315], theta[225:315])
    up_shear = _trapezoid(azimuth_integral[315:360], theta[315:360]) + _trapezoid(
        azimuth_integral[:45], theta[:45]
    )
    right_shear = _trapezoid(azimuth_integral[45:135], theta[45:135])
    fractions = np.asarray([down_shear, left_shear, up_shear, right_shear], dtype=float) / total
    return float(np.sqrt((4.0 / 3.0) * np.sum((fractions - 0.25) ** 2)))


# 功能说明：检查诊断输出是否仍匹配当前输入和参数。
def output_is_current(
    out_path: Path,
    *,
    variable: str,
    source_path: Path,
    radius_km: float,
    radius_resolution_km: float,
    spec: _SourceSpec,
) -> bool:
    meta_path = metadata_path(out_path)
    if not meta_path.exists():
        return False
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        if not (
            metadata.get("algorithm_version") == ALGORITHM_VERSION
            and metadata.get("variable") == variable
            and metadata.get("derived_from") == [str(source_path)]
            and metadata.get("source_polar_variable") == spec.polar_variable
            and metadata.get("center_mode") == spec.center_mode
            and float(metadata.get("radius_km")) == float(radius_km)
            and float(metadata.get("radius_resolution_km")) == float(radius_resolution_km)
            and int(metadata.get("source_polar_mtime_ns")) == source_path.stat().st_mtime_ns
        ):
            return False
        if spec.height_m is not None and float(metadata.get("center_height_m")) != float(spec.height_m):
            return False
        data = np.load(out_path, mmap_mode="r")
        return tuple(data.shape) == (1,)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


# 功能说明：检查指定非对称度输出是否存在，且元数据中的源文件、半径、中心方式和算法仍与本次请求一致。
def is_output_current(
    *,
    request: ProcessingRequest,
    path_manager: PathManager,
    variable: str,
    time_hour: int,
) -> bool:
    """Whether the scalar cache matches its polar source and current options."""
    if variable not in VARIABLES:
        return False
    spec = _source_spec(variable, request.options)
    source = path_manager.existing_variable(
        member_name=request.member_name,
        domain=request.domain,
        variable=spec.polar_variable,
        time_hour=time_hour,
        dimension=spec.dimension,
        height_m=spec.height_m,
    )
    out_path = path_manager.variable_path(
        member_name=request.member_name,
        domain=request.domain,
        variable=variable,
        time_hour=time_hour,
        dimension=VariableDimension.ONE_D,
    )
    if source is None or not out_path.exists():
        return False
    source_metadata = _read_metadata(source.path)
    radius_km = _requested_radius_km(request.options)
    if not _source_is_usable(source.path, source_metadata, spec, radius_km):
        return False
    return output_is_current(
        out_path,
        variable=variable,
        source_path=source.path,
        radius_km=radius_km,
        radius_resolution_km=_radius_resolution_km(source.path, source_metadata),
        spec=spec,
    )


# 功能说明：降雨非对称度使用地面中心的降雨极坐标场；反射率非对称度使用指定高度的层中心极坐标场，返回源变量及中心、高度设置。
def _source_spec(variable: str, options: dict[str, Any] | None) -> _SourceSpec:
    if variable == PRECIPITATION_VARIABLE:
        return _SourceSpec("RAINNCV_polar", VariableDimension.TWO_D, "slp")
    if variable == MDBZ_VARIABLE:
        return _SourceSpec("mdbz_polar", VariableDimension.TWO_D, "slp")
    if variable == DBZ_VARIABLE:
        return _SourceSpec(
            "dbz1_polar",
            VariableDimension.THREE_D,
            "layer",
            _requested_dbz_height_m(options),
        )
    raise ValueError(f"Unsupported polar asymmetry diagnostic: {variable}")


# 功能说明：先检查已有极坐标场的形状、中心方式和半径是否够用；不够用时准备中心与基础变量并转换，最后再次检查生成结果。
def _ensure_source_polar(
    *,
    request: ProcessingRequest,
    path_manager: PathManager,
    spec: _SourceSpec,
    time_hour: int,
    radius_km: float,
    ensure_slp_center: Callable[..., Path],
    ensure_standard_data: Callable[..., None],
    ensure_layer_centers: Callable[..., list[Path]],
    progress: ProgressCallback | None,
) -> tuple[Path, dict[str, Any]]:
    existing = path_manager.existing_variable(
        member_name=request.member_name,
        domain=request.domain,
        variable=spec.polar_variable,
        time_hour=time_hour,
        dimension=spec.dimension,
        height_m=spec.height_m,
    )
    if existing is not None:
        source_metadata = _read_metadata(existing.path)
        if _source_is_usable(existing.path, source_metadata, spec, radius_km):
            return existing.path, source_metadata

    transformer = PolarTransformer(request.processed_root)
    if spec.center_mode == "slp":
        ensure_slp_center(request, [time_hour], progress)
        ensure_standard_data(request, [spec.polar_variable.removesuffix("_polar")], None, progress)
        source_path = transformer.transform_2d(
            member_name=request.member_name,
            domain=request.domain,
            variable=spec.polar_variable.removesuffix("_polar"),
            time_hour=time_hour,
            center_mode="slp",
            radius_km=radius_km,
            radius_resolution_km=None,
            force=request.force,
        )
    else:
        assert spec.height_m is not None
        selected_height = SelectionSpec(values=[spec.height_m])
        ensure_layer_centers(request, [time_hour], [spec.height_m], progress)
        ensure_standard_data(request, ["dbz"], selected_height, progress)
        source_path = transformer.transform_3d_per_height(
            member_name=request.member_name,
            domain=request.domain,
            variable="dbz",
            time_hour=time_hour,
            heights_m=[spec.height_m],
            center_mode="layer",
            radius_km=radius_km,
            radius_resolution_km=None,
            force=request.force,
        )[0]

    source_metadata = _read_metadata(source_path)
    if not _source_is_usable(source_path, source_metadata, spec, radius_km):
        raise ValueError(
            f"Generated polar source does not meet {spec.polar_variable} requirements at {time_hour}h"
        )
    return source_path, source_metadata


# 功能说明：检查源极坐标文件的形状、元数据和半径范围是否足够使用。
def _source_is_usable(
    source_path: Path, metadata: dict[str, Any], spec: _SourceSpec, radius_km: float) -> bool:
    try:
        values = np.load(source_path, mmap_mode="r")
        shape = tuple(values.shape)
        del values
        if len(shape) != 2 or shape[0] < 360:
            return False
        if metadata.get("center_mode") not in (None, spec.center_mode):
            return False
        if spec.height_m is not None and metadata.get("height_m") not in (None, spec.height_m):
            return False
        resolution = _radius_resolution_km(source_path, metadata)
        return float((shape[1] - 1) * resolution) >= float(radius_km) - 1.0e-8
    except (OSError, TypeError, ValueError):
        return False


# 功能说明：读取极坐标数据文件的 JSON 元数据；读不到或 JSON 无效时返回空字典，供上游按缺失处理。
def _read_metadata(path: Path) -> dict[str, Any]:
    try:
        return json.loads(metadata_path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}


# 功能说明：从极坐标元数据计算每个半径格点代表的公里间隔。
def _radius_resolution_km(path: Path, metadata: dict[str, Any]) -> float:
    try:
        value = float(metadata.get("radius_resolution_km"))
        if np.isfinite(value) and value > 0.0:
            return value
    except (TypeError, ValueError):
        pass
    values = np.load(path, mmap_mode="r")
    columns = int(values.shape[1]) if values.ndim == 2 else 0
    del values
    try:
        radius = float(metadata.get("radius_km"))
        if columns > 1 and np.isfinite(radius) and radius > 0.0:
            return radius / float(columns - 1)
    except (TypeError, ValueError):
        pass
    return 1.0


# 功能说明：从选项读取用户要求的分析半径并转换为公里数。
def _requested_radius_km(options: dict[str, Any] | None) -> float:
    raw = (options or {}).get("asymmetry_radius_km", DEFAULT_RADIUS_KM)
    try:
        radius = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"asymmetry_radius_km must be a positive number, got {raw!r}") from exc
    if not np.isfinite(radius) or radius <= 0.0:
        raise ValueError(f"asymmetry_radius_km must be a positive number, got {radius!r}")
    return radius


# 功能说明：从选项读取雷达反射率分析所用的高度。
def _requested_dbz_height_m(options: dict[str, Any] | None) -> float:
    raw = (options or {}).get("dbz_asymmetry_height_m", DEFAULT_DBZ_HEIGHT_M)
    try:
        height = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"dbz_asymmetry_height_m must be a positive number, got {raw!r}") from exc
    if not np.isfinite(height) or height <= 0.0:
        raise ValueError(f"dbz_asymmetry_height_m must be a positive number, got {height!r}")
    return height


# 功能说明：沿指定坐标轴用梯形法积分，处理不等间距的采样点。
def _trapezoid(values: np.ndarray, coordinates: np.ndarray, axis: int | None = None) -> np.ndarray:
    function = getattr(np, "trapezoid", np.trapz)
    if axis is None:
        return function(values, x=coordinates)
    return function(values, x=coordinates, axis=axis)
