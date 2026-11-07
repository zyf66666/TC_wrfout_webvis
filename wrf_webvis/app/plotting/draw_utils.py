from __future__ import annotations


import warnings
from pathlib import Path
from typing import Any, Iterable

import numpy as np


DEFAULT_DOMAIN_CONFIG = {
    1: {"dx_km": 25.0, "grid_number": 201},
    2: {"dx_km": 5.0, "grid_number": 405},
    3: {"dx_km": 1.0, "grid_number": 805},
}


VARIABLE_TYPE_REGISTRY = {
    "dbz": "3d",
    "mdbz": "2d",
    "temp": "3d",
    "theta": "3d",
    "theta_anomaly": "3d",
    "theta_e": "3d",
    "theta_e_anomaly": "3d",
    "saturation_thetae": "3d",
    "QVAPOR": "3d",
    "rh": "3d",
    "rh1": "3d",
    "rh_diff": "3d",
    "div": "3d",
    "wa": "3d",
    "pressure": "3d",
    "vr_ventilation": "3d",
    "w_ventilation": "3d",
    "vr_ventilation1": "3d",
    "w_ventilation1": "3d",
    "ua": "3d",
    "va": "3d",
    "u": "3d",
    "v": "3d",
    "w": "3d",
    "vr": "3d",
    "vt": "3d",
    "ua1": "3d",
    "va1": "3d",
    "wa1": "3d",
    "vr1": "3d",
    "vt1": "3d",
    "ua1_asy": "3d",
    "va1_asy": "3d",
    "wa1_asy": "3d",
    "vr1_asy": "3d",
    "vt1_asy": "3d",
    "wa_asy": "3d",
    "vr_asy": "3d",
    "vt_asy": "3d",
    "H_DIABATIC": "3d",
    "H_DIABATIC1": "3d",
    "pv": "3d",
    "rvor": "3d",
    "agf": "3d",
    "agf_vt": "3d",
    "agf_grident": "3d",
    "agf_f": "3d",
    "density": "3d",
    "RAINNCV": "2d",
    "RAINNC": "2d",
    "saturation_dispersion": "2d",
    "cape": "2d",
    "cin": "2d",
    "LH": "2d",
    "HFX": "2d",
    "RAINNCV_diff": "2d",
    "saturation_dispersion_diff": "2d",
    "surface_heat_flux": "2d",
    "U10": "2d",
    "V10": "2d",
    "wind_speed_10": "2d",
    "classification": "2d",
    "local_max_wind": "1d",
    "axis_mean_max_wind": "1d",
    "rmw": "1d",
    "center_slp_5km": "1d",
    "center_land_distance": "1d",
}


QUIVER_VARIABLES = {
    "u",
    "v",
    "w",
    "ua",
    "va",
    "wa",
    "vr",
    "vt",
    "ua1",
    "va1",
    "wa1",
    "vr1",
    "vt1",
    "ua1_asy",
    "va1_asy",
    "wa1_asy",
    "vr1_asy",
    "vt1_asy",
    "wa_asy",
    "vr_asy",
    "vt_asy",
    "ua1_asy1w",
    "va1_asy1w",
    "wa1_asy1w",
    "vr1_asy1w",
    "vt1_asy1w",
    "ua1_asy2w",
    "va1_asy2w",
    "wa1_asy2w",
    "vr1_asy2w",
    "vt1_asy2w",
    "ua1_asy3w",
    "va1_asy3w",
    "wa1_asy3w",
    "vr1_asy3w",
    "vt1_asy3w",
}


FIG_TYPE_NAMES = {
    0: "horizontal",
    1: "radius_height",
    2: "time_radius",
    3: "time_azimuth",
    4: "height_azimuth",
    5: "time_series",
    6: "track",
    7: "three_d",
    8: "time_height",
}


AZIMUTH_STARTS = {
    "US": 0,
    "RS": 90,
    "DS": 180,
    "LS": 270,
}


# 功能说明：把模拟区域编号转成数据目录名称，例如3变成 d03。
def domain_dir(domain: int) -> str:
    return f"d0{int(domain)}"


# 功能说明：检查所选模拟区域是否在配置表里；存在则返回该区域的网格设置，否则列出可用编号。
def validate_domain(domain: int, domain_config: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    config = domain_config or DEFAULT_DOMAIN_CONFIG
    if domain not in config:
        available = ", ".join(str(key) for key in sorted(config))
        raise ValueError(f"domain={domain} 不在 DOMAIN_CONFIG 中。可用 domain: {available}")
    return config[domain]


# 功能说明：取得指定模拟区域东西方向相邻格点的距离，单位为公里。
def get_dx_km(domain: int, domain_config: dict[int, dict[str, Any]] | None = None) -> float:
    return float(validate_domain(domain, domain_config)["dx_km"])


# 功能说明：取得指定模拟区域配置的格点数量。
def get_grid_number(domain: int, domain_config: dict[int, dict[str, Any]] | None = None) -> int:
    return int(validate_domain(domain, domain_config)["grid_number"])


# 功能说明：把单个值、范围或数组统一整理成列表，减少后续处理不同输入形式的麻烦；按设置允许或拒绝空值。
def normalize_sequence(value: Any, name: str, allow_none: bool = False) -> list[Any] | None:
    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{name} 不能为 None")
    if isinstance(value, range):
        return list(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


# 功能说明：把以公里表示的半径除以格点间距并取整数，得到数据中的位置编号；拒绝负半径和非正间距。
def radius_km_to_indices(
    radius_km: Iterable[int | float],
    dx_km: float,
    radius_resolution_km: float | None = None,
) -> list[int]:
    resolution = float(radius_resolution_km) if radius_resolution_km is not None else float(dx_km)
    if resolution <= 0:
        raise ValueError("radius resolution 必须为正数")
    radii = np.asarray(list(radius_km), dtype=float)
    if np.any(radii < 0):
        raise ValueError("radius 不能为负数，单位为 km")
    return (radii / resolution).astype(int).tolist()


# 功能说明：决定某变量使用自己的时间、高度等设置还是全图统一设置；强制统一高度时优先使用全局高度。
def resolve_selection_value(
    variable_config: dict[str, Any],
    selection: dict[str, Any],
    key: str,
) -> Any:
    if key == "height" and selection.get("use_global_height_for_all_variables", False):
        return selection.get("height")
    value = variable_config.get(key)
    if value is not None:
        return value
    return selection.get(key)


# 功能说明：先查变量登记表判断数据是一维、二维还是三维，未登记时按实际数组层数推断并提醒。
def infer_variable_type(variable: str, registry: dict[str, str] | None, per_file_ndim: int) -> str:
    merged = dict(VARIABLE_TYPE_REGISTRY)
    if registry:
        merged.update(registry)
    if variable in merged:
        return merged[variable]
    if per_file_ndim == 3:
        warnings.warn(f"{variable} 不在 VARIABLE_TYPE_REGISTRY 中，已按 3d 变量推断")
        return "3d"
    if per_file_ndim == 2:
        warnings.warn(f"{variable} 不在 VARIABLE_TYPE_REGISTRY 中，已按 2d 变量推断")
        return "2d"
    if per_file_ndim in (0, 1):
        warnings.warn(f"{variable} is not in VARIABLE_TYPE_REGISTRY and was inferred as 1d")
        return "1d"
    raise ValueError(f"{variable} 不在 VARIABLE_TYPE_REGISTRY 中，且无法根据 ndim={per_file_ndim} 推断类型")


# 功能说明：检查文件中的数据层数是否符合变量类型，并检查极坐标三维数据是否有足够的方向格点；不符时报错。
def validate_variable_shape(variable: str, path: Path, shape: tuple[int, ...], variable_type: str, fig_type: int) -> None:
    expected_ndim = {"3d": 3, "2d": 2, "1d": 1}.get(variable_type)
    if expected_ndim is None:
        raise ValueError(f"unknown variable type: {variable_type}")
    actual_ndim = 1 if variable_type == "1d" and len(shape) == 0 else len(shape)
    if actual_ndim != expected_ndim:
        raise ValueError(
            f"变量维度和 fig_type 不匹配: variable={variable}, fig_type={fig_type}, "
            f"path={path}, actual_shape={shape}, expected_per_file_ndim={expected_ndim} ({variable_type})"
        )
    if fig_type != 0 and len(shape) == 3 and shape[1] < 2:
        raise ValueError(
            f"极坐标变量方位维过短: variable={variable}, path={path}, actual_shape={shape}, "
            "预期形状为 (height, azimuth, radius)"
        )


# 功能说明：把方向选择转成数据中的位置列表，支持单个方向、连续扇区以及跨越0度的扇区；这里直接按角度整数选格点。
def angle_indices(angle: Iterable[int | float], azimuth_size: int) -> np.ndarray:
    values = np.asarray(list(angle), dtype=float)
    if values.size == 0:
        raise ValueError("angle 不能为空，单位为 degree")
    if azimuth_size <= 0:
        raise ValueError("azimuth_size 必须为正数")
    if values.size == 1:
        return np.asarray([int(values[0]) % azimuth_size], dtype=int)
    if values.size == 2:
        start = int(values[0]) % azimuth_size
        raw_end = float(values[1])
        if float(values[0]) == 0 and raw_end == 360:
            return np.arange(azimuth_size, dtype=int)
        end = int(raw_end) % azimuth_size
        if end >= start:
            return np.arange(start, end + 1, dtype=int)
        return np.concatenate((np.arange(start, azimuth_size, dtype=int), np.arange(0, end + 1, dtype=int)))
    return np.asarray([int(value) % azimuth_size for value in values], dtype=int)


# 功能说明：把半径位置解释为单点、起止范围或逐项列表，并检查所选位置没有超出文件范围。
def radius_indices_for_mode(radius_indices: Iterable[int], radius_size: int, radius_mode: str = "auto") -> np.ndarray:
    indices = np.asarray(list(radius_indices), dtype=int)
    if indices.size == 0:
        raise ValueError("radius 不能为空，单位为 km")
    if radius_size <= 0:
        raise ValueError("radius_size 必须为正数")

    mode = radius_mode
    if mode == "auto":
        if indices.size == 1:
            mode = "single"
        elif indices.size == 2:
            mode = "range"
        else:
            mode = "list"

    if mode == "single":
        selected = np.asarray([indices[0]], dtype=int)
    elif mode == "range":
        if indices.size != 2:
            raise ValueError("radius_mode='range' 时，radius 必须是 [start, end]")
        start, end = int(indices[0]), int(indices[1])
        if end < start:
            raise ValueError("radius range 的结束半径必须大于或等于起始半径")
        selected = np.arange(start, end + 1, dtype=int)
    elif mode == "list":
        selected = indices
    else:
        raise ValueError("radius_mode 只能是 'auto', 'single', 'range', 或 'list'")

    if np.any(selected < 0) or np.any(selected >= radius_size):
        max_index = radius_size - 1
        raise ValueError(f"radius 超出数据范围。radius 维长度={radius_size}, 最大可用 radius_index={max_index}")
    return selected


# 功能说明：将半径位置编号乘以每格公里数，恢复绘图横坐标上的实际距离。
def selected_radius_km(indices: Iterable[int], resolution_km: float) -> np.ndarray:
    return np.asarray(list(indices), dtype=float) * float(resolution_km)


# 功能说明：当二维数据只有一行或一列时复制它并略微展开坐标，让至少需要两行两列的等值线工具可以画图。
def make_axis_minimum_2d(z: np.ndarray, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    arr = np.asarray(z)
    x_arr = np.asarray(x)
    y_arr = np.asarray(y)

    if arr.ndim != 2:
        raise ValueError(f"contour 数据必须是二维，actual_shape={arr.shape}")

    if arr.shape[0] == 1:
        y0 = float(y_arr[0]) if y_arr.size else 0.0
        dy = 1.0
        y_arr = np.asarray([y0 - dy / 2.0, y0 + dy / 2.0])
        arr = np.vstack((arr[0, :], arr[0, :]))
    if arr.shape[1] == 1:
        x0 = float(x_arr[0]) if x_arr.size else 0.0
        dx = 1.0
        x_arr = np.asarray([x0 - dx / 2.0, x0 + dx / 2.0])
        arr = np.column_stack((arr[:, 0], arr[:, 0]))

    return arr, x_arr, y_arr


# 功能说明：检查已开启的辅助图层是否有对应文件；缺少时发出提醒并返回不能绘制。
def maybe_warn_missing_auxiliary(enabled: bool, path: Path | None, description: str) -> bool:
    if not enabled:
        return False
    if path is None or not path.exists():
        warnings.warn(f"{description} 已启用但文件不存在: {path}")
        return False
    return True


# 功能说明：把一串选择值简写为单值或首尾范围，并附上单位，方便用于标题。
def format_range(values: Iterable[Any], suffix: str = "") -> str:
    seq = list(values)
    if not seq:
        return ""
    if len(seq) == 1:
        return f"{seq[0]}{suffix}"
    return f"{seq[0]}-{seq[-1]}{suffix}"


# 功能说明：根据 US、RS、DS、LS 等方向标签决定方位角横轴从哪里开始；也支持显示两圈方向。
def start_x_to_xlim(start_x: str | None, double_azimuth: bool) -> list[int]:
    full = 720 if double_azimuth else 360
    if not start_x:
        return [0, full]
    start = AZIMUTH_STARTS.get(str(start_x).upper())
    if start is None:
        return [0, full]
    return [start, start + 360]
