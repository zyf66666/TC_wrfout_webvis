"""MetPy density and storm-centred ventilation diagnostics."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from metpy.calc import density as metpy_density
from metpy.units import units

from wrf_webvis.app.core.paths import PathManager, atomic_write_json
from wrf_webvis.app.core.schemas import ProcessingRequest, VariableDimension

from ..polar_transformer import PolarTransformer
from ..processing_utils import atomic_save, domain_config_dict, fill_nan
from ..progress import ProgressCallback, emit


VARIABLES = {
    "density",
    "density_polar",
    "density1_polar",
    "theta_e1_asy_polar",
    "vr1_asy_polar",
    "wa1_asy_polar",
    "vr_ventilation_polar",
    "w_ventilation_polar",
    "vr1_ventilation_polar",
    "w1_ventilation_polar",
}


# 功能说明：用 MetPy 根据气压、温度和水汽混合比计算湿空气密度。
def compute_density_metpy(pressure_hpa: np.ndarray, temperature_k: np.ndarray, qv_mixing_ratio: np.ndarray) -> np.ndarray:
    """Compute moist-air density with the quantity conversions in density.ipynb."""
    pressure = fill_nan(np.asarray(pressure_hpa, dtype=np.float64))
    temperature = fill_nan(np.asarray(temperature_k, dtype=np.float64))
    mixing_ratio = fill_nan(np.asarray(qv_mixing_ratio, dtype=np.float64))
    if pressure.shape != temperature.shape or pressure.shape != mixing_ratio.shape:
        raise ValueError("pressure, temp, and QVAPOR must have matching shapes for density")
    values = metpy_density(
        pressure * units.hPa,
        temperature * units.K,
        mixing_ratio * 1000.0 * units("g/kg"),
    ).to("kg/m^3").magnitude
    return fill_nan(np.asarray(values, dtype=np.float32)).astype(np.float32, copy=False)


# 功能说明：用密度、相当位温的周向偏差和风的周向偏差计算两种通风量；径向向外或垂直向上的格点分别置0，只保留向内与下沉部分。
def compute_ventilation_fields(
    density: np.ndarray,
    theta_e_asymmetry: np.ndarray,
    radial_wind: np.ndarray,
    radial_asymmetry: np.ndarray,
    vertical_wind: np.ndarray,
    vertical_asymmetry: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return inward-radial and subsidence ventilation without mutating inputs."""
    fields = [np.asarray(value, dtype=np.float32) for value in (
        density, theta_e_asymmetry, radial_wind, radial_asymmetry, vertical_wind, vertical_asymmetry
    )]
    if any(value.shape != fields[0].shape for value in fields[1:]):
        raise ValueError("all ventilation inputs must have matching polar-grid shapes")
    rho, theta_e_asy, vr, vr_asy, w, w_asy = fields
    return (
        (rho * theta_e_asy * np.where(vr > 0.0, 0.0, vr_asy)).astype(np.float32, copy=False),
        (rho * theta_e_asy * np.where(w > 0.0, 0.0, w_asy)).astype(np.float32, copy=False),
    )


# 功能说明：加载通风诊断所需变量，逐时间和高度计算并保存通风结果。
def calculate(request: ProcessingRequest, progress: ProgressCallback | None = None) -> list[Path]:
    """Write all density, layer-centre asymmetry, and ventilation products."""
    manager = PathManager(request.processed_root)
    transformer = PolarTransformer(request.processed_root)
    times = [int(value) for value in (request.times.values or [])]
    heights = [float(value) for value in (request.heights.values or [])]
    if not times or not heights:
        raise ValueError("通风计算需要非空 TIMES 和 HEIGHTS")
    domain_config = domain_config_dict(request)
    radius_km = request.options.get("polar_max_radius_km")
    radius_resolution_km = request.options.get("polar_radius_resolution_km")
    outputs: list[Path] = []
    for time_hour in times:
        emit(progress, f"计算 {request.member_name} {time_hour}h MetPy 密度")
        outputs.extend(_write_density(request, manager, time_hour, height_m) for height_m in heights)
        for variable in ("theta_e", "wa"):
            outputs.extend(transformer.transform_3d_per_height(
                member_name=request.member_name, domain=request.domain, variable=variable, time_hour=time_hour,
                heights_m=heights, center_mode="layer", radius_km=radius_km,
                radius_resolution_km=radius_resolution_km, domain_config=domain_config, force=request.force,
            ))
        for center_mode in ("slp", "layer"):
            density_outputs = transformer.transform_3d_per_height(
                member_name=request.member_name, domain=request.domain, variable="density", time_hour=time_hour,
                heights_m=heights, center_mode=center_mode, radius_km=radius_km,
                radius_resolution_km=radius_resolution_km, domain_config=domain_config, force=request.force,
            )
            _remove_per_file_metadata(density_outputs)
            write_variable_metadata(request, "density1_polar" if center_mode == "layer" else "density_polar",
                                    formula="polar interpolation of density", units="kg m-3", source_variables=["density"])
            outputs.extend(density_outputs)
        outputs.extend(transformer.transform_3d_per_height(
            member_name=request.member_name, domain=request.domain, variable="vr", source_variable="vr1",
            time_hour=time_hour, heights_m=heights, center_mode="layer", radius_km=radius_km,
            radius_resolution_km=radius_resolution_km, domain_config=domain_config, force=request.force,
        ))
        for variable in ("theta_e1_polar", "vr1_polar", "wa1_polar"):
            outputs.extend(_write_asymmetry(request, manager, variable, time_hour, height_m) for height_m in heights)
        for variable, source_variable in (("theta_e_asy_polar", "theta_e_polar"), ("vr_asy_polar", "vr_polar"), ("wa_asy_polar", "wa_polar")):
            paths = [_existing_path(manager, request, variable, time_hour, height_m) for height_m in heights]
            _remove_per_file_metadata([path for path in paths if path is not None])
            write_variable_metadata(request, variable, formula="field - mean(field, axis=azimuth)",
                                    units="same as source variable", source_variables=[source_variable])
        emit(progress, f"计算 {request.member_name} {time_hour}h 通风")
        for height_m in heights:
            outputs.extend(_write_ventilation(request, manager, time_hour, height_m, layer_center=False))
            outputs.extend(_write_ventilation(request, manager, time_hour, height_m, layer_center=True))
    return outputs


# 功能说明：把通风变量的单位、维度和来源等说明写入元数据文件。
def write_variable_metadata(request: ProcessingRequest, variable: str, *, formula: str, units: str, source_variables: list[str]) -> Path:
    path = PathManager(request.processed_root).domain_root(request.member_name, request.domain) / variable / "source_metadata.json"
    if not path.exists() or request.force:
        atomic_write_json(path, {"variable": variable, "domain": request.domain, "member_name": request.member_name,
            "formula": formula, "units": units, "source_variables": source_variables,
            "time_height_layout": "per-height files; one shared provenance record"})
    return path


# 功能说明：读取一个时次和高度的已处理变量数组；文件缺失时明确报出缺少哪项通风计算输入。
def _load(manager: PathManager, request: ProcessingRequest, variable: str, time_hour: int, height_m: float) -> tuple[np.ndarray, Path]:
    existing = manager.existing_variable(member_name=request.member_name, domain=request.domain, variable=variable,
        time_hour=time_hour, dimension=VariableDimension.THREE_D, height_m=height_m)
    if existing is None:
        raise FileNotFoundError(f"缺少通风计算依赖变量：{request.member_name} d0{request.domain} {variable} {time_hour}h {height_m:g}m")
    return np.asarray(np.load(existing.path, mmap_mode="r"), dtype=np.float32), existing.path


# 功能说明：查找指定时刻和高度的已处理变量，存在就返回实际路径，不存在则返回空值。
def _existing_path(manager: PathManager, request: ProcessingRequest, variable: str, time_hour: int, height_m: float) -> Path | None:
    existing = manager.existing_variable(member_name=request.member_name, domain=request.domain, variable=variable,
        time_hour=time_hour, dimension=VariableDimension.THREE_D, height_m=height_m)
    return None if existing is None else existing.path


# 功能说明：读取气压、温度和水汽，计算某时刻某高度的空气密度并保存普通网格文件；已有文件且未要求重算时直接复用，极坐标转换在其他步骤完成。
def _write_density(request: ProcessingRequest, manager: PathManager, time_hour: int, height_m: float) -> Path:
    output = manager.variable_path(member_name=request.member_name, domain=request.domain, variable="density",
        time_hour=time_hour, dimension=VariableDimension.THREE_D, height_m=height_m)
    if output.exists() and not request.force:
        return output
    pressure, _ = _load(manager, request, "pressure", time_hour, height_m)
    temperature, _ = _load(manager, request, "temp", time_hour, height_m)
    qv, _ = _load(manager, request, "QVAPOR", time_hour, height_m)
    atomic_save(output, compute_density_metpy(pressure, temperature, qv).astype(request.dtype, copy=False))
    write_variable_metadata(request, "density", formula="metpy.calc.density(pressure[hPa], temp[K], QVAPOR*1000[g/kg])",
                            units="kg m-3", source_variables=["pressure", "temp", "QVAPOR"])
    return output


# 功能说明：从每个方位的极坐标场减去该半径的方位平均值，保存只保留非对称部分的新极坐标文件。
def _write_asymmetry(request: ProcessingRequest, manager: PathManager, variable: str, time_hour: int, height_m: float) -> Path:
    source, _ = _load(manager, request, variable, time_hour, height_m)
    output_variable = f"{variable[:-len('_polar')]}_asy_polar"
    output = manager.variable_path(member_name=request.member_name, domain=request.domain, variable=output_variable,
        time_hour=time_hour, dimension=VariableDimension.THREE_D, height_m=height_m)
    if output.exists() and not request.force:
        return output
    atomic_save(output, (source - np.mean(source, axis=0, keepdims=True)).astype(request.dtype, copy=False))
    write_variable_metadata(request, output_variable, formula="field - mean(field, axis=azimuth)",
                            units="same as source variable", source_variables=[variable])
    return output


# 功能说明：读取同一中心方式的极坐标密度、相当位温偏差与风场，计算向内和下沉通风量；按是否强制重算分别保存这两份结果。
def _write_ventilation(request: ProcessingRequest, manager: PathManager, time_hour: int, height_m: float, *, layer_center: bool) -> list[Path]:
    suffix = "1" if layer_center else ""
    density, _ = _load(manager, request, f"density{suffix}_polar", time_hour, height_m)
    theta_e, _ = _load(manager, request, f"theta_e{suffix}_asy_polar", time_hour, height_m)
    vr, _ = _load(manager, request, f"vr{suffix}_polar", time_hour, height_m)
    vr_asy, _ = _load(manager, request, f"vr{suffix}_asy_polar", time_hour, height_m)
    wa, _ = _load(manager, request, f"wa{suffix}_polar", time_hour, height_m)
    wa_asy, _ = _load(manager, request, f"wa{suffix}_asy_polar", time_hour, height_m)
    radial, subsidence = compute_ventilation_fields(density, theta_e, vr, vr_asy, wa, wa_asy)
    outputs: list[Path] = []
    sources = (["density_polar", "theta_e_asy_polar", "vr_polar", "vr_asy_polar", "wa_polar", "wa_asy_polar"]
               if not layer_center else ["density1_polar", "theta_e1_asy_polar", "vr1_polar", "vr1_asy_polar", "wa1_polar", "wa1_asy_polar"])
    for variable, values, formula in ((f"vr{suffix}_ventilation_polar", radial, "rho*theta_e_asy*where(vr<=0, vr_asy, 0)"),
                                      (f"w{suffix}_ventilation_polar", subsidence, "rho*theta_e_asy*where(wa<=0, wa_asy, 0)")):
        output = manager.variable_path(member_name=request.member_name, domain=request.domain, variable=variable,
            time_hour=time_hour, dimension=VariableDimension.THREE_D, height_m=height_m)
        if not output.exists() or request.force:
            atomic_save(output, values.astype(request.dtype, copy=False))
            write_variable_metadata(request, variable, formula=formula, units="kg K m-2 s-1", source_variables=sources)
        outputs.append(output)
    return outputs


# 功能说明：删除通风结果旁重复的单文件元数据，避免旧元数据掩盖汇总元数据。
def _remove_per_file_metadata(paths: list[Path]) -> None:
    for path in paths:
        path.with_suffix(path.suffix + ".json").unlink(missing_ok=True)
