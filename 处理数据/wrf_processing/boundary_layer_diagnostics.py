from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from wrf_webvis.app.core.centers import load_center_yx
from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.schemas import ProcessingRequest, VariableDimension
from .processing_utils import atomic_save, build_processing_metadata, fill_nan
from .radial_tangential_wind import radial_tangential_wind


RD_J_KG_K = 287.05
DERIVED_THREE_D_VARIABLES = {"convergence", "mass_convergence", "mfc", "qflux_r", "inflow_angle"}
BOUNDARY_LAYER_INTEGRAL_VARIABLES = {"bl_mass_convergence", "bl_mfc"}

_DEPENDENCIES = {
    "convergence": ["ua", "va"],
    "mass_convergence": ["ua", "va", "pressure", "temp", "QVAPOR"],
    "mfc": ["ua", "va", "pressure", "temp", "QVAPOR"],
    "qflux_r": ["ua", "va", "pressure", "temp", "QVAPOR"],
    "inflow_angle": ["ua", "va"],
}


# 功能说明：列出计算给定边界层变量前必须先准备的输入变量。
def dependencies_for(variable: str) -> list[str]:
    if variable in BOUNDARY_LAYER_INTEGRAL_VARIABLES:
        return ["mass_convergence" if variable == "bl_mass_convergence" else "mfc"]
    return list(_DEPENDENCIES[variable])


# 功能说明：判断某个变量是否依赖海平面气压最低点作为风暴中心。
def requires_slp_center(variable: str) -> bool:
    return variable in {"qflux_r", "inflow_angle"}


# 功能说明：从请求选项中取得边界层积分所用的高度范围。
def boundary_layer_heights(options: dict[str, Any] | None) -> list[float]:
    raw = (options or {}).get("boundary_layer_heights_m")
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        heights = sorted({float(value) for value in raw})
    else:
        lower = float((options or {}).get("boundary_layer_bottom_m", 100.0))
        upper = float((options or {}).get("boundary_layer_top_m", 1000.0))
        if upper <= lower:
            raise ValueError("boundary_layer_top_m must be greater than boundary_layer_bottom_m")
        heights = list(np.arange(lower, upper + 0.1, 100.0, dtype=float))
        if heights[-1] != upper:
            heights.append(upper)
    if len(heights) < 2:
        raise ValueError("at least two boundary-layer heights are required")
    return heights


# 功能说明：根据东西、南北风在相邻格点间的变化计算水平辐合；正值表示空气汇聚，负值表示向外散开。
def horizontal_convergence(
    u: np.ndarray,
    v: np.ndarray,
    *,
    dx_m: float,
    dy_m: float,
) -> np.ndarray:
    """Return horizontal convergence ``-(du/dx + dv/dy)`` in s^-1."""
    u_values = np.asarray(u, dtype=float)
    v_values = np.asarray(v, dtype=float)
    if u_values.shape != v_values.shape or u_values.ndim != 2:
        raise ValueError("u and v must be two-dimensional arrays with the same shape")
    if dx_m <= 0 or dy_m <= 0:
        raise ValueError("dx_m and dy_m must be positive")
    return -(np.gradient(u_values, float(dx_m), axis=1) + np.gradient(v_values, float(dy_m), axis=0))


# 功能说明：用气压、温度和水汽混合比估算湿空气密度。
def moist_air_density(pressure_hpa: np.ndarray, temperature_k: np.ndarray, qv: np.ndarray) -> np.ndarray:
    """Return moist-air density in kg m^-3 from hPa, K, and water-vapor mixing ratio."""
    pressure_pa = np.asarray(pressure_hpa, dtype=float) * 100.0
    temperature = np.asarray(temperature_k, dtype=float)
    mixing_ratio = np.asarray(qv, dtype=float)
    if pressure_pa.shape != temperature.shape or pressure_pa.shape != mixing_ratio.shape:
        raise ValueError("pressure_hpa, temperature_k, and qv must have the same shape")
    if np.any(temperature <= 0):
        raise ValueError("temperature_k must be positive")
    return pressure_pa / (RD_J_KG_K * temperature * (1.0 + 0.61 * mixing_ratio))


# 功能说明：计算密度加权风的辐合，反映空气质量在水平面上的聚集程度。
def mass_flux_convergence(
    density: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    *,
    dx_m: float,
    dy_m: float,
) -> np.ndarray:
    """Return ``-div(rho * V)`` in kg m^-3 s^-1."""
    rho = np.asarray(density, dtype=float)
    return horizontal_convergence(rho * np.asarray(u, dtype=float), rho * np.asarray(v, dtype=float), dx_m=dx_m, dy_m=dy_m)


# 功能说明：计算水汽随风输送后的辐合，反映水汽是否在区域内汇集。
def moisture_flux_convergence(
    density: np.ndarray,
    qv: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    *,
    dx_m: float,
    dy_m: float,
) -> np.ndarray:
    """Return ``-div(rho * qv * V)`` in kg m^-3 s^-1."""
    rho_qv = np.asarray(density, dtype=float) * np.asarray(qv, dtype=float)
    return horizontal_convergence(rho_qv * np.asarray(u, dtype=float), rho_qv * np.asarray(v, dtype=float), dx_m=dx_m, dy_m=dy_m)


# 功能说明：读取一个指定时刻各高度的已处理风、气压等数据，按变量选择计算辐合、水汽输送或入流角，并逐层保存；不在这里直接打开原始WRF文件。
def process_derived_3d_variable(
    *,
    request: ProcessingRequest,
    path_manager: PathManager,
    variable: str,
    time_hour: int | float,
    heights: list[float],
    dtype: str,
    domain_config: dict[str, Any],
) -> list[str]:
    if variable not in DERIVED_THREE_D_VARIABLES:
        raise ValueError(f"unsupported boundary-layer 3d diagnostic: {variable}")
    dx_m = float(domain_config.get("dx_km") or 1.0) * 1000.0
    dy_m = float(domain_config.get("dy_km") or domain_config.get("dx_km") or 1.0) * 1000.0
    center: tuple[float, float] | None = None
    if requires_slp_center(variable):
        center_path = path_manager.center_path(request.member_name, request.domain, "slp")
        center = load_center_yx(center_path, time_hour)

    outputs: list[str] = []
    for height_m in heights:
        output = path_manager.variable_path(
            member_name=request.member_name,
            domain=request.domain,
            variable=variable,
            time_hour=time_hour,
            dimension=VariableDimension.THREE_D,
            height_m=height_m,
        )
        if output.exists() and not request.force:
            outputs.append(str(output))
            continue
        data, source_paths, source_file, source_time_index = _load_dependencies(
            path_manager, request, variable, time_hour, height_m
        )
        u = data["ua"]
        v = data["va"]
        if variable == "convergence":
            values = horizontal_convergence(u, v, dx_m=dx_m, dy_m=dy_m)
            formula = "-(du_sr/dx + dv_sr/dy)"
            units = "s-1"
        elif variable in {"mass_convergence", "mfc", "qflux_r"}:
            density = moist_air_density(data["pressure"], data["temp"], data["QVAPOR"])
            if variable == "mass_convergence":
                values = mass_flux_convergence(density, u, v, dx_m=dx_m, dy_m=dy_m)
                formula = "-div(rho*V_sr)"
                units = "kg m-3 s-1"
            elif variable == "mfc":
                values = moisture_flux_convergence(density, data["QVAPOR"], u, v, dx_m=dx_m, dy_m=dy_m)
                formula = "-div(rho*qv*V_sr)"
                units = "kg m-3 s-1"
            else:
                if center is None:
                    raise RuntimeError(f"missing SLP center for {variable}")
                center_y, center_x = center
                component = "vr" if variable == "qflux_r" else "vt"
                radial_or_tangential = radial_tangential_wind(
                    u, v, center_y=center_y, center_x=center_x, dx_km=dx_m / 1000.0, dy_km=dy_m / 1000.0, component=component
                )
                if variable == "qflux_r":
                    values = density * data["QVAPOR"] * radial_or_tangential
                    formula = "rho*qv*V_r_sr"
                    units = "kg m-2 s-1"
                else:
                    raise RuntimeError(f"unsupported density-weighted diagnostic: {variable}")
        else:
            if center is None:
                raise RuntimeError(f"missing SLP center for {variable}")
            center_y, center_x = center
            radial = radial_tangential_wind(
                u, v, center_y=center_y, center_x=center_x, dx_km=dx_m / 1000.0, dy_km=dy_m / 1000.0, component="vr"
            )
            tangential = radial_tangential_wind(
                u, v, center_y=center_y, center_x=center_x, dx_km=dx_m / 1000.0, dy_km=dy_m / 1000.0, component="vt"
            )
            values = np.degrees(np.arctan2(-radial, tangential))
            formula = "atan2(-V_r_sr, V_t_sr)"
            units = "degree"
        atomic_save(output, fill_nan(values).astype(dtype, copy=False))
        metadata = build_processing_metadata(request, variable, source_file, time_hour, height_m, source_time_index)
        metadata.update(
            {
                "derived_from": [str(path) for path in source_paths],
                "formula": formula,
                "units": units,
                "wind_reference": "storm_relative",
                "grid_spacing_m": {"dx": dx_m, "dy": dy_m},
            }
        )
        if center is not None:
            metadata["center_mode"] = "slp"
        path_manager.write_metadata(output, metadata)
        outputs.append(str(output))
    return outputs


# 功能说明：读取指定时刻各高度的质量或水汽汇聚值，按层间厚度用梯形法累加，保存该时刻整层的二维总量。
def process_boundary_layer_integral(
    *,
    request: ProcessingRequest,
    path_manager: PathManager,
    variable: str,
    time_hour: int | float,
    heights: list[float],
    dtype: str,
) -> str:
    if variable not in BOUNDARY_LAYER_INTEGRAL_VARIABLES:
        raise ValueError(f"unsupported boundary-layer integral diagnostic: {variable}")
    source_variable = dependencies_for(variable)[0]
    source_paths: list[Path] = []
    layers: list[np.ndarray] = []
    source_file: Path | None = None
    source_time_index: int | None = None
    for height_m in heights:
        existing = path_manager.existing_variable(
            member_name=request.member_name,
            domain=request.domain,
            variable=source_variable,
            time_hour=time_hour,
            dimension=VariableDimension.THREE_D,
            height_m=height_m,
        )
        if existing is None:
            raise FileNotFoundError(f"missing {source_variable} dependency at {time_hour}h {height_m}m")
        source_paths.append(existing.path)
        layers.append(np.asarray(np.load(existing.path, mmap_mode="r"), dtype=float))
        if source_file is None:
            source_file, source_time_index = _source_details(existing.path)
    trapezoid = getattr(np, "trapezoid", np.trapz)
    values = trapezoid(np.stack(layers, axis=0), x=np.asarray(heights, dtype=float), axis=0)
    output = path_manager.variable_path(
        member_name=request.member_name,
        domain=request.domain,
        variable=variable,
        time_hour=time_hour,
        dimension=VariableDimension.TWO_D,
    )
    atomic_save(output, fill_nan(values).astype(dtype, copy=False))
    metadata = build_processing_metadata(request, variable, source_file or source_paths[0], time_hour, None, source_time_index)
    metadata.update(
        {
            "derived_from": [str(path) for path in source_paths],
            "formula": f"integral_{heights[0]:g}m^{heights[-1]:g}m({source_variable}) dz",
            "units": "kg m-2 s-1",
            "vertical_bounds_m": [float(heights[0]), float(heights[-1])],
            "vertical_levels_m": [float(height) for height in heights],
        }
    )
    path_manager.write_metadata(output, metadata)
    return str(output)


# 功能说明：读取某时刻某高度计算所需的已处理变量，缺少任何一项就提示；返回数据、各文件路径及首份数据记录的原始来源时次。
def _load_dependencies(
    path_manager: PathManager,
    request: ProcessingRequest,
    variable: str,
    time_hour: int | float,
    height_m: float,
) -> tuple[dict[str, np.ndarray], list[Path], Path, int | None]:
    values: dict[str, np.ndarray] = {}
    paths: list[Path] = []
    source_file: Path | None = None
    source_time_index: int | None = None
    for dependency in dependencies_for(variable):
        existing = path_manager.existing_variable(
            member_name=request.member_name,
            domain=request.domain,
            variable=dependency,
            time_hour=time_hour,
            dimension=VariableDimension.THREE_D,
            height_m=height_m,
        )
        if existing is None:
            raise FileNotFoundError(f"missing {dependency} dependency for {variable} at {time_hour}h {height_m}m")
        values[dependency] = np.asarray(np.load(existing.path, mmap_mode="r"), dtype=float)
        paths.append(existing.path)
        if source_file is None:
            source_file, source_time_index = _source_details(existing.path)
    return values, paths, source_file or paths[0], source_time_index


# 功能说明：从数据旁的说明文件取得原始WRF路径和文件内时次编号；读取失败时返回当前数据路径和空时次编号。
def _source_details(path: Path) -> tuple[Path, int | None]:
    metadata_path = path.with_suffix(path.suffix + ".json")
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        source = payload.get("source_wrfout")
        source_index = payload.get("source_time_index")
        return Path(source) if source else path, int(source_index) if source_index is not None else None
    except Exception:  # noqa: BLE001
        return path, None
