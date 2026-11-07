from __future__ import annotations

from ..core.config import resolve_plot_processed_root

import re
from pathlib import Path
from typing import Any, Callable

import numpy as np
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch

from .draw_utils import (
    DEFAULT_DOMAIN_CONFIG,
    FIG_TYPE_NAMES,
    VARIABLE_TYPE_REGISTRY,
    angle_indices,
    format_range,
    get_dx_km,
    infer_variable_type,
    make_axis_minimum_2d,
    normalize_sequence,
    radius_indices_for_mode,
    radius_km_to_indices,
    resolve_selection_value,
    selected_radius_km,
    validate_variable_shape,
)
from .member_styles import member_display_name, resolve_experiment_color, scheme_linestyle
from .reflectivity_mean import uses_echo_mean, echo_totals, finish_echo_mean, echo_mean
from .center_lookup import load_center_grid
from ..core.registry import VARIABLE_REGISTRY, parse_cartesian_from_polar_variable_name, parse_fourier_variable_name
from ..services.processed_data import DataReference, ProcessedDataReader

CLASSIFICATION_CMAP = ListedColormap(
    [
        "#f0f0f0",
        "#ffff99",
        "#66c2a5",
        "#3288bd",
        "#fdae61",
        "#d73027",
    ]
)


# 功能说明：利用已有数值估计缺测点：一维按相邻位置插值，二维先线性插值再用最近点补齐；整片没有有效数据时直接报错。
def fill_nan_with_interpolation(arr: np.ndarray) -> np.ndarray:
    """Fill missing samples without turning an absent field into zeros."""
    values = np.ma.asarray(arr, dtype=float).filled(np.nan)
    valid = np.isfinite(values)
    if valid.all():
        return values
    if not valid.any():
        raise ValueError("Cannot interpolate a field with no finite samples (没有有效数据)")
    filled = values.copy()
    if values.ndim == 1:
        indices = np.arange(values.size)
        filled[~valid] = np.interp(indices[~valid], indices[valid], values[valid])
        return filled
    if values.ndim != 2:
        raise ValueError("Missing-value interpolation requires a 1D or 2D field")
    from scipy.interpolate import griddata
    from scipy.spatial import QhullError

    points = np.argwhere(valid)
    missing = np.argwhere(~valid)
    try:
        interpolated = griddata(points, values[valid], missing, method="linear")
    except QhullError:
        # Sparse/collinear samples cannot form a triangulation.
        interpolated = np.full(len(missing), np.nan)
    remaining = ~np.isfinite(interpolated)
    if remaining.any():
        interpolated[remaining] = griddata(points, values[valid], missing[remaining], method="nearest")
    filled[~valid] = interpolated
    return filled


# 功能说明：把新旧变量绘图配置统一成一张设置表，补上填色、缩放、平滑和选区默认值；云分类使用专门的颜色分级。
def normalize_variable_config(variable: str, spec: Any) -> dict[str, Any]:
    if isinstance(spec, dict):
        config = dict(spec)
        config.setdefault("enabled", True)
        config.setdefault("plot_type", "contourf")
        config.setdefault("extend", "both")
        config.setdefault("sigma", None)
        config.setdefault("scale_factor", 1.0)
        config.setdefault("times", None)
        config.setdefault("height", None)
        config.setdefault("angle", None)
        config.setdefault("radius", None)
        if str(config.get("plot_type", "contourf")).lower() == "contour":
            config["clabel"] = True
        return config

    if isinstance(spec, (list, tuple)):
        if len(spec) == 0:
            raise ValueError(f"{variable} 的变量配置为空")
        fill = bool(spec[0])
        if variable == "classification":
            return {
                "enabled": True,
                "plot_type": "contourf",
                "cmap": CLASSIFICATION_CMAP,
                "levels": [-0.5, 0.5, 1.5, 2.5, 3.5, 4.5, 5.5],
                "extend": "neither",
                "sigma": None,
                "scale_factor": 1.0,
                "times": None,
                "height": None,
                "angle": None,
                "radius": None,
            }
        return {
            "enabled": True,
            "plot_type": "contourf" if fill else "contour",
            "cmap": spec[1] if fill and len(spec) > 1 else None,
            "color": spec[1] if not fill and len(spec) > 1 else "k",
            "levels": spec[2] if len(spec) > 2 else None,
            "extend": spec[3] if len(spec) > 3 else "both",
            "sigma": spec[4] if len(spec) > 4 else None,
            "scale_factor": spec[5] if len(spec) > 5 else 1.0,
            "times": None,
            "height": None,
            "angle": None,
            "radius": None,
        }

    raise TypeError(f"{variable} 的变量配置必须是 dict/list/tuple")


# 功能说明：按试验、时间、高度、方向和半径读取变量，按图型取平均，再处理缺测、平滑和单位缩放，返回可以直接画图的数据与来源说明。
def prepare_variable_data(
    member_name: str,
    domain: int,
    variables: dict[str, Any],
    fig_type: int,
    *,
    data_root: str | Path = ".",
    domain_config: dict[int, dict[str, Any]] | None = None,
    selection: dict[str, Any] | None = None,
    polar_dir_style: str = "same",
    polar_radius_resolution_km: float | None = None,
    variable_type_registry: dict[str, str] | None = None,
    format_config: dict[str, Any] | None = None,
    dry_run: bool = False,
    allow_missing: bool = False,
    progress: Callable[[str], None] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if fig_type not in FIG_TYPE_NAMES:
        raise ValueError(f"不支持的 fig_type: {fig_type}")

    # 2026-11-01：时次、高度直接用实际值（processed_v3 按坐标读取），不再先换成“序号表”里的序号。
    domain_config = domain_config or DEFAULT_DOMAIN_CONFIG
    selection = selection or {}
    dx_km = get_dx_km(domain, domain_config)
    radius_resolution_km = polar_radius_resolution_km if polar_radius_resolution_km is not None else dx_km
    polar = fig_type != 0

    payloads: dict[str, dict[str, Any]] = {}
    summaries: list[dict[str, Any]] = []

    for variable, raw_spec in variables.items():
        config = normalize_variable_config(variable, raw_spec)
        if not config.get("enabled", True):
            continue

        times = normalize_sequence(resolve_selection_value(config, selection, "times"), "times")
        heights_value = resolve_selection_value(config, selection, "height")
        heights_m = normalize_sequence(heights_value, "height", allow_none=True)
        if fig_type == 5:
            angle = normalize_sequence(
                resolve_selection_value(config, selection, "angle"), "angle", allow_none=True
            ) or []
            radius_km = normalize_sequence(
                resolve_selection_value(config, selection, "radius"), "radius", allow_none=True
            ) or []
        else:
            angle = normalize_sequence(resolve_selection_value(config, selection, "angle"), "angle")
            radius_km = normalize_sequence(resolve_selection_value(config, selection, "radius"), "radius")
        radius_mode = config.get("radius_mode") or selection.get("radius_mode", "auto")

        radius_indices = radius_km_to_indices(radius_km, dx_km, radius_resolution_km)

        expected_type = (variable_type_registry or {}).get(variable) or VARIABLE_TYPE_REGISTRY.get(variable)
        if fig_type in (1, 8) and expected_type == "3d" and heights_m is not None:
            reducer = reduce_radius_height_files if fig_type == 1 else reduce_time_height_files
            reduced = reducer(
                data_root=data_root,
                member_name=member_name,
                domain=domain,
                variable=variable,
                times=times,
                heights_m=heights_m,
                angle=angle,
                radius_indices=radius_indices,
                radius_mode=radius_mode,
                radius_resolution_km=radius_resolution_km,
                polar_dir_style=polar_dir_style,
                progress=progress,
            )
            if reduced is not None:
                values, x, y, file_infos = reduced
                summary = {
                    "variable": variable,
                    "type": "3d",
                    "plot_type": config.get("plot_type"),
                    "times": times,
                    "height": heights_m,
                    "angle": angle,
                    "radius": radius_km,
                    "radius_index": radius_indices,
                    "radius_mode": radius_mode,
                    "files": file_infos,
                    "result_shape": tuple(values.shape),
                }
                values = apply_postprocess(variable, values, config, fig_type=fig_type)
                summary["result_shape"] = tuple(values.shape)
                summaries.append(summary)
                payloads[variable] = {
                    "variable": variable,
                    "member_name": member_name,
                    "config": config,
                    "type": "3d",
                    "values": values,
                    "x": x,
                    "y": y,
                    "times": times,
                    "height": heights_m,
                    "angle": angle,
                    "radius": radius_km,
                    "radius_mode": radius_mode,
                    "radius_index": radius_indices,
                    "files": file_infos,
                }
                print(
                    f"{variable}: shape={values.shape}, "
                    f"max={np.nanmax(values):.3f}, min={np.nanmin(values):.3f}"
                )
                continue

        stack, file_infos, variable_type = load_variable_stack(
            variable=variable,
            member_name=member_name,
            domain=domain,
            times=times,
            heights_m=heights_m,
            fig_type=fig_type,
            data_root=data_root,
            polar=polar,
            polar_dir_style=polar_dir_style,
            registry=variable_type_registry,
            dry_run=dry_run,
            allow_missing=allow_missing,
            progress=progress,
        )

        summary = {
            "variable": variable,
            "type": variable_type,
            "plot_type": config.get("plot_type"),
            "times": times,
            "height": heights_m,
            "angle": angle,
            "radius": radius_km,
            "radius_index": radius_indices,
            "radius_mode": radius_mode,
            "files": file_infos,
            "result_shape": None,
        }

        if stack is None:
            summaries.append(summary)
            continue

        loaded_times = [info["time"] for info in file_infos if info["exists"]]
        if len(loaded_times) != len(times):
            # ``allow_missing`` is used by overlaid time series where members
            # may finish at different forecast hours.  Keep the x coordinate
            # aligned with the files that were actually loaded.
            times = loaded_times
            summary["times"] = times

        result = reduce_variable(
            stack=stack,
            variable=variable,
            variable_type=variable_type,
            fig_type=fig_type,
            times=times,
            heights_m=heights_m,
            angle=angle,
            radius_indices=radius_indices,
            radius_mode=radius_mode,
            radius_resolution_km=radius_resolution_km,
            first_path=file_infos[0]["path"],
        )

        values = apply_postprocess(variable, result["values"], config, fig_type=fig_type)
        result["values"] = values
        summary["result_shape"] = tuple(values.shape)
        summaries.append(summary)

        payloads[variable] = {
            "variable": variable,
            "member_name": member_name,
            "config": config,
            "type": variable_type,
            "values": values,
            "x": result["x"],
            "y": result["y"],
            "times": times,
            "height": heights_m,
            "angle": angle,
            "radius": radius_km,
            "radius_mode": radius_mode,
            "radius_index": radius_indices,
            "files": file_infos,
        }

        print(
            f"{variable}: shape={values.shape}, "
            f"max={np.nanmax(values):.3f}, min={np.nanmin(values):.3f}"
        )

    apply_rmw_validity_filter(
        payloads,
        member_name=member_name,
        domain=domain,
        data_root=data_root,
        domain_config=domain_config,
        selection=selection,
        polar_dir_style=polar_dir_style,
        polar_radius_resolution_km=polar_radius_resolution_km,
        variable_type_registry=variable_type_registry,
        format_config=format_config,
    )
    apply_horizontal_km_coordinates(
        payloads,
        fig_type=fig_type,
        member_name=member_name,
        domain=domain,
        data_root=data_root,
        domain_config=domain_config,
        format_config=format_config,
    )
    return payloads, summaries


# 功能说明：开启弱涡旋过滤时，检查同一时刻的平均最大风速；低于阈值就把该时刻的最大风速半径设成绘图缺测，避免弱系统的半径干扰曲线。
def apply_rmw_validity_filter(
    payloads: dict[str, dict[str, Any]],
    *,
    member_name: str,
    domain: int,
    data_root: str | Path,
    domain_config: dict[int, dict[str, Any]],
    selection: dict[str, Any],
    polar_dir_style: str,
    polar_radius_resolution_km: float | None,
    variable_type_registry: dict[str, str] | None,
    format_config: dict[str, Any] | None,
) -> None:
    """Mask RMW values when the azimuthal-mean vortex is below a chosen strength."""
    fmt = (format_config or {}).get("time_series", {})
    if not _enabled_time_series_option(fmt.get("hide_weak_rmw", False)):
        return
    rmw_payload = payloads.get("rmw")
    if rmw_payload is None:
        return

    try:
        threshold = float(fmt.get("rmw_min_axis_mean_wind", 10.0))
    except (TypeError, ValueError):
        threshold = 10.0
    if not np.isfinite(threshold):
        return

    axis_payload = payloads.get("axis_mean_max_wind")
    if axis_payload is None:
        axis_payloads, _ = prepare_variable_data(
            member_name=member_name,
            domain=domain,
            variables={"axis_mean_max_wind": {"enabled": True, "plot_type": "line"}},
            fig_type=5,
            data_root=data_root,
            domain_config=domain_config,
            selection={**selection, "times": list(rmw_payload["times"])},
            polar_dir_style=polar_dir_style,
            polar_radius_resolution_km=polar_radius_resolution_km,
            variable_type_registry=variable_type_registry,
            format_config=format_config,
            allow_missing=True,
        )
        axis_payload = axis_payloads.get("axis_mean_max_wind")
    if axis_payload is None:
        return

    axis_by_time = {
        float(time_hour): float(value)
        for time_hour, value in zip(axis_payload["x"], axis_payload["values"], strict=True)
    }
    rmw_values = np.asarray(rmw_payload["values"], dtype=float).copy()
    for index, time_hour in enumerate(rmw_payload["x"]):
        if axis_by_time.get(float(time_hour), np.nan) < threshold:
            rmw_values[index] = np.nan
    rmw_payload["values"] = rmw_values


# 功能说明：把布尔值或 true、yes、on 等文字识别为开启状态，供时间序列选项判断使用。
def _enabled_time_series_option(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"})


# 功能说明：把水平图的格点编号换成距所选中心多少公里，并写回各图层坐标；气象变量本身的数值不变。
def apply_horizontal_km_coordinates(
    payloads: dict[str, dict[str, Any]],
    *,
    fig_type: int,
    member_name: str,
    domain: int,
    data_root: str | Path,
    domain_config: dict[int, dict[str, Any]] | None,
    format_config: dict[str, Any] | None,
) -> None:
    """Convert horizontal plot payloads from grid indices to center-relative km.

    Matplotlib contour plots need the data layer and the formatting layer to use
    the same coordinate system.  For horizontal WRF maps we want X/Y labels in km
    relative to the chosen center, not raw i/j grid indices.  When the format
    sets ``coordinate_system='center_km'``, this function rewrites each payload's
    x/y coordinates before contourf/contour/quiver are drawn:

        x_km = (x_index - center_x_index) * dx_km
        y_km = (y_index - center_y_index) * dy_km

    The array values are not changed; only their plotted coordinates are changed.
    """
    if fig_type != 0 or not payloads:
        return
    fmt = (format_config or {}).get("horizontal", {}) if isinstance(format_config, dict) else {}
    if fmt.get("coordinate_system") != "center_km":
        return

    first_payload = next(iter(payloads.values()))
    values = np.asarray(first_payload["values"])
    grid_shape = tuple(values.shape[-2:])
    times = first_payload.get("times") or [0]
    center_y, center_x = resolve_horizontal_center_grid(
        data_root=data_root,
        member_name=member_name,
        domain=domain,
        times=times,
        grid_shape=grid_shape,
        fmt=fmt,
    )
    config = (domain_config or DEFAULT_DOMAIN_CONFIG).get(domain, {})
    dx_km = float(config.get("dx_km") or 1.0)
    dy_km = float(config.get("dy_km") or dx_km)
    # y grows with the WRF south_north index.  Because XLAT increases with this
    # index in normal WRF output, positive Y means north of the chosen center.
    y = (np.arange(grid_shape[0], dtype=float) - float(center_y)) * dy_km
    x = (np.arange(grid_shape[1], dtype=float) - float(center_x)) * dx_km
    for payload in payloads.values():
        payload["x"] = x
        payload["y"] = y
        payload["center_grid"] = (float(center_y), float(center_x))
        payload["coordinate_system"] = "center_km"


# 功能说明：确定水平图参考中心的格点位置：默认网格中点，可读取中心文件，显式指定的中心编号优先级最高。
def resolve_horizontal_center_grid(
    *,
    data_root: str | Path,
    member_name: str,
    domain: int,
    times: list[Any],
    grid_shape: tuple[int, int],
    fmt: dict[str, Any],
) -> tuple[float, float]:
    """Return the center in raw WRF grid indices (y, x).

    The returned value is still an index location, not km.  It is used by
    ``apply_horizontal_km_coordinates`` to create center-relative km coordinates.
    """
    default_y = (grid_shape[0] - 1) / 2.0
    default_x = (grid_shape[1] - 1) / 2.0
    center_y, center_x = default_y, default_x
    if fmt.get("center_enabled", False):
        # 与 draw_format.resolve_center 共用同一查找逻辑：读 processed_v3 的
        # slp_center/layer_center；找不到时会发出警告。
        found = load_center_grid(
            data_root=data_root,
            member_name=member_name,
            domain=domain,
            time_hour=times[0] if times else 0,
            height_m=fmt.get("center_height_m"),
            center_path=fmt.get("center_path"),
        )
        if found is not None:
            center_y, center_x = found
    if fmt.get("center_index") is not None:
        values = np.atleast_1d(fmt["center_index"]).astype(float)
        if values.size == 1:
            center_y = center_x = float(values[0])
        else:
            center_y, center_x = float(values[0]), float(values[1])
    return center_y, center_x


# 功能说明：按时次读取某变量的已处理文件，支持逐高度文件，检查数据形状并记录来源；根据模式报告或允许跳过缺文件。
def load_variable_stack(
    *,
    variable: str,
    member_name: str,
    domain: int,
    times: list[Any],
    heights_m: list[Any] | None,
    fig_type: int,
    data_root: str | Path,
    polar: bool,
    polar_dir_style: str,
    registry: dict[str, str] | None,
    dry_run: bool,
    allow_missing: bool,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[np.ndarray] | None, list[dict[str, Any]], str | None]:
    arrays: list[np.ndarray] = []
    file_infos: list[dict[str, Any]] = []
    variable_type: str | None = None
    expected_type = (registry or {}).get(variable) or VARIABLE_TYPE_REGISTRY.get(variable)
    if expected_type is None:
        cartesian = parse_cartesian_from_polar_variable_name(variable)
        fourier = parse_fourier_variable_name(variable)
        if cartesian is not None and cartesian.base_name in VARIABLE_REGISTRY:
            expected_type = VARIABLE_REGISTRY[cartesian.base_name].dimension.value
        elif fourier is not None and fourier.base_name in VARIABLE_REGISTRY:
            expected_type = VARIABLE_REGISTRY[fourier.base_name].dimension.value

    for time_index, time in enumerate(times, start=1):
        file_count = len(heights_m) if expected_type == "3d" and heights_m is not None else 1
        file_label = "高度文件" if file_count > 1 else "数据文件"
        if progress is not None:
            progress(f"读取 {variable} {_time_label(time)}：{file_count} 个{file_label}（{time_index}/{len(times)}）")
        if expected_type == "3d" and heights_m is not None:
            per_height = load_per_height_stack(
                data_root=data_root,
                member_name=member_name,
                domain=domain,
                variable=variable,
                time=time,
                heights_m=heights_m,
                polar=polar,
                polar_dir_style=polar_dir_style,
            )
            if per_height is not None:
                array, info = per_height
                file_infos.append(info)
                variable_type = "3d"
                arrays.append(array)
                continue
            if polar and not dry_run:
                expected_paths = expected_per_height_paths(
                    data_root=data_root,
                    member_name=member_name,
                    domain=domain,
                    variable=variable,
                    time=time,
                    heights_m=heights_m,
                    polar_dir_style=polar_dir_style,
                )
                raise FileNotFoundError(
                    f"缺失 per-height 极坐标切片: {variable} {_time_label(time)}。"
                    f"需要 {len(heights_m)} 个高度都已完成，查找的文件:\n" + "\n".join(str(path) for path in expected_paths)
                )

        reader = _processed_data_reader(data_root)
        reference = _first_reference(
            reader=reader,
            member_name=member_name,
            domain=domain,
            variable=variable,
            time=time,
            height_m=None,
            polar=polar,
            polar_dir_style=polar_dir_style,
        )
        if reference is not None:
            array = _load_reference(reader, reference, mmap=True)
            info = {
                "time": time,
                "path": str(reference.path),
                "exists": True,
                "shape": tuple(array.shape),
                "candidates": [str(reference.path)],
                "backend": reference.backend,
            }
            file_infos.append(info)
            inferred = infer_variable_type(variable, registry, array.ndim)
            validate_variable_shape(variable, reference.path, tuple(array.shape), inferred, fig_type)
            if variable_type is None:
                variable_type = inferred
            elif variable_type != inferred:
                raise ValueError(f"{variable} 的类型不一致: {variable_type} vs {inferred}")
            arrays.append(array)
            continue

        candidates = _expected_v3_paths(reader, member_name, domain, variable, time, polar, polar_dir_style)
        file_infos.append(
            {
                "time": time,
                "path": str(candidates[0]),
                "exists": False,
                "shape": None,
                "candidates": [str(candidate) for candidate in candidates],
            }
        )
        if dry_run or allow_missing:
            continue
        candidate_text = "\n".join(str(candidate) for candidate in candidates)
        raise FileNotFoundError(
            f"缺失 processed_v3 切片: {variable} {_time_label(time)}\n查找的文件:\n{candidate_text}"
        )

    if not arrays:
        return None, file_infos, variable_type or infer_variable_type(variable, registry, 3)

    shapes = [tuple(array.shape) for array in arrays]
    if len(set(shapes)) != 1:
        same_nonradial_shape = len({shape[:-1] for shape in shapes}) == 1
        if not (polar and fig_type in (1, 2, 3, 4, 5, 8) and same_nonradial_shape):
            raise ValueError(f"{variable} 不同时间文件 shape 不一致: {shapes}")

    return arrays, file_infos, variable_type


# 功能说明：寻找同一时刻所选高度的全部文件，检查各层尺寸相同后叠成三维数据；找不到完整的一组时返回空值。
def load_per_height_stack(
    *,
    data_root: str | Path,
    member_name: str,
    domain: int,
    variable: str,
    time: Any,
    heights_m: list[Any],
    polar: bool,
    polar_dir_style: str,
) -> tuple[np.ndarray, dict[str, Any]] | None:
    reader = _processed_data_reader(data_root)
    for var_dir in _variable_candidates(variable, polar, polar_dir_style):
        references = [
            reader.resolve(
                member_name=member_name,
                domain=domain,
                variable=var_dir,
                time_hour=time,
                height_m=height,
            )
            for height in heights_m
        ]
        if all(reference is not None for reference in references):
            resolved = [reference for reference in references if reference is not None]
            arrays = [_load_reference(reader, reference, mmap=True) for reference in resolved]
            shapes = [tuple(array.shape) for array in arrays]
            if len(set(shapes)) != 1:
                raise ValueError(f"{variable} per-height files have inconsistent shapes: {shapes}")
            return (
                np.stack(arrays, axis=0),
                {
                    "time": time,
                    "path": str(resolved[0].path),
                    "exists": True,
                    "shape": (len(arrays),) + shapes[0],
                    "candidates": [str(reference.path) for reference in resolved],
                    "backends": [reference.backend for reference in resolved],
                },
            )
    return None


# 功能说明：为半径—高度图逐层读取数据，对所选时次和方向的有效点累计求平均，直接得到高度乘半径的二维表，减少同时占用的内存。
def reduce_radius_height_files(
    *,
    data_root: str | Path,
    member_name: str,
    domain: int,
    variable: str,
    times: list[Any],
    heights_m: list[Any],
    angle: list[Any],
    radius_indices: list[int],
    radius_mode: str,
    radius_resolution_km: float,
    polar_dir_style: str,
    progress: Callable[[str], None] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[dict[str, Any]]] | None:
    """Average each source height layer before building the radius-height field."""
    totals: np.ndarray | None = None
    counts: np.ndarray | None = None
    file_infos: list[dict[str, Any]] = []
    radius_x: np.ndarray | None = None

    for time_index, time in enumerate(times, start=1):
        if progress is not None:
            progress(f"读取 {variable} {_time_label(time)}：{len(heights_m)} 个高度文件（{time_index}/{len(times)}）")
        reference_group = _per_height_references(
            data_root=data_root,
            member_name=member_name,
            domain=domain,
            variable=variable,
            time=time,
            heights_m=heights_m,
            polar_dir_style=polar_dir_style,
        )
        if reference_group is None:
            return None
        reader, references = reference_group

        shapes: list[tuple[int, ...]] = []
        for height_index, reference in enumerate(references):
            # Radius-height selects many short row segments.  On the external
            # processed-data volume, a memory map turns those into thousands of
            # scattered page reads; loading this compact (~0.4 MB) source array
            # once gives the filesystem a sequential read instead.
            source = _load_reference(reader, reference, mmap=False)
            shape = tuple(source.shape)
            shapes.append(shape)
            if len(shape) != 2:
                raise ValueError(
                    f"{variable} per-height file must be 2d: {reference.path}, actual_shape={shape}"
                )
            az_idx = angle_indices(angle, shape[0])
            try:
                r_idx = radius_indices_for_mode(radius_indices, shape[1], radius_mode)
            except ValueError as exc:
                max_radius = (shape[1] - 1) * radius_resolution_km
                raise ValueError(f"{exc}。最大可用半径={max_radius:g} km") from exc
            if radius_x is None:
                radius_x = selected_radius_km(r_idx, radius_resolution_km)
                totals = np.zeros((len(heights_m), len(r_idx)), dtype=np.float64)
                counts = np.zeros((len(heights_m), len(r_idx)), dtype=np.int64)
            elif len(r_idx) != totals.shape[1]:
                raise ValueError(
                    f"{variable} different height files have inconsistent radius coverage: {reference.path}"
                )

            selected = np.asarray(source[np.ix_(az_idx, r_idx)], dtype=np.float64)
            if uses_echo_mean(variable, 1):
                layer_totals, layer_counts = echo_totals(selected, axis=0)
                totals[height_index] += layer_totals
                counts[height_index] += layer_counts
            else:
                totals[height_index] += np.nansum(selected, axis=0)
                counts[height_index] += np.sum(~np.isnan(selected), axis=0)

        if len(set(shapes)) != 1:
            raise ValueError(f"{variable} per-height files have inconsistent shapes: {shapes}")
        file_infos.append(
            {
                "time": time,
                "path": str(references[0].path),
                "exists": True,
                "shape": (len(references),) + shapes[0],
                "candidates": [str(reference.path) for reference in references],
                "backends": [reference.backend for reference in references],
            }
        )

    if totals is None or counts is None or radius_x is None:
        return None
    values = np.full(totals.shape, np.nan, dtype=np.float64)
    np.divide(totals, counts, out=values, where=counts > 0)
    if uses_echo_mean(variable, 1):
        values = finish_echo_mean(totals, counts)
    return values, radius_x, np.asarray(heights_m, dtype=float), file_infos


# 功能说明：逐时次、逐高度读取极坐标文件，对选定方位角与半径求等权平均，直接生成高度乘时间矩阵。
def reduce_time_height_files(
    *,
    data_root: str | Path,
    member_name: str,
    domain: int,
    variable: str,
    times: list[Any],
    heights_m: list[Any],
    angle: list[Any],
    radius_indices: list[int],
    radius_mode: str,
    radius_resolution_km: float,
    polar_dir_style: str,
    progress: Callable[[str], None] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[dict[str, Any]]] | None:
    values = np.full((len(heights_m), len(times)), np.nan, dtype=np.float64)
    file_infos: list[dict[str, Any]] = []

    for time_index, time in enumerate(times):
        if progress is not None:
            progress(
                f"读取 {variable} {_time_label(time)}：{len(heights_m)} 个高度文件"
                f"（{time_index + 1}/{len(times)}）"
            )
        reference_group = _per_height_references(
            data_root=data_root,
            member_name=member_name,
            domain=domain,
            variable=variable,
            time=time,
            heights_m=heights_m,
            polar_dir_style=polar_dir_style,
        )
        if reference_group is None:
            return None
        reader, references = reference_group

        shapes: list[tuple[int, ...]] = []
        for height_index, reference in enumerate(references):
            source = _load_reference(reader, reference, mmap=False)
            shape = tuple(source.shape)
            shapes.append(shape)
            if len(shape) != 2:
                raise ValueError(
                    f"{variable} per-height file must be 2d: {reference.path}, actual_shape={shape}"
                )
            az_idx = angle_indices(angle, shape[0])
            try:
                r_idx = radius_indices_for_mode(radius_indices, shape[1], radius_mode)
            except ValueError as exc:
                max_radius = (shape[1] - 1) * radius_resolution_km
                raise ValueError(f"{exc}。最大可用半径={max_radius:g} km") from exc
            selected = np.asarray(source[np.ix_(az_idx, r_idx)], dtype=np.float64)
            valid = np.isfinite(selected)
            if uses_echo_mean(variable, 8):
                values[height_index, time_index] = echo_mean(selected)
            elif valid.any():
                values[height_index, time_index] = float(np.sum(selected[valid]) / np.sum(valid))

        if len(set(shapes)) != 1:
            raise ValueError(f"{variable} per-height files have inconsistent shapes: {shapes}")
        file_infos.append(
            {
                "time": time,
                "path": str(references[0].path),
                "exists": True,
                "shape": (len(references),) + shapes[0],
                "candidates": [str(reference.path) for reference in references],
                "backends": [reference.backend for reference in references],
            }
        )

    return values, np.asarray(times, dtype=float), np.asarray(heights_m, dtype=float), file_infos


def _processed_data_reader(data_root: str | Path) -> ProcessedDataReader:
    """绘图只读 processed_v3；旧 v1/v2 根目录按 resolve_plot_processed_root 改指 v3。"""
    return ProcessedDataReader(resolve_plot_processed_root(data_root))


def _variable_candidates(variable: str, polar: bool, polar_dir_style: str) -> list[str]:
    if not polar:
        return [variable]
    style = polar_dir_style.lower()
    if style == "suffix":
        return [f"{variable}_polar"]
    if style == "auto":
        return [f"{variable}_polar", variable]
    return [variable]


def _first_reference(
    *,
    reader: ProcessedDataReader,
    member_name: str,
    domain: int,
    variable: str,
    time: Any,
    height_m: Any,
    polar: bool,
    polar_dir_style: str,
) -> DataReference | None:
    for candidate in _variable_candidates(variable, polar, polar_dir_style):
        reference = reader.resolve(
            member_name=member_name,
            domain=domain,
            variable=candidate,
            time_hour=time,
            height_m=height_m,
        )
        if reference is not None:
            return reference
    return None


def _per_height_references(
    *,
    data_root: str | Path,
    member_name: str,
    domain: int,
    variable: str,
    time: Any,
    heights_m: list[Any],
    polar_dir_style: str,
) -> tuple[ProcessedDataReader, list[DataReference]] | None:
    reader = _processed_data_reader(data_root)
    for candidate in _variable_candidates(variable, True, polar_dir_style):
        references = [
            reader.resolve(
                member_name=member_name,
                domain=domain,
                variable=candidate,
                time_hour=time,
                height_m=height,
            )
            for height in heights_m
        ]
        if all(reference is not None for reference in references):
            return reader, [reference for reference in references if reference is not None]
    return None


def _load_reference(
    reader: ProcessedDataReader,
    reference: DataReference,
    *,
    mmap: bool = False,
) -> np.ndarray:
    return reader.load(reference)


# 功能说明：列出某时刻各高度切片所在的 processed_v3 文件（同一变量所有高度在一个 .nc 里），供缺失提示使用。
def expected_per_height_paths(
    *,
    data_root: str | Path,
    member_name: str,
    domain: int,
    variable: str,
    time: Any,
    heights_m: list[Any],
    polar_dir_style: str,
) -> list[Path]:
    reader = _processed_data_reader(data_root)
    return _expected_v3_paths(reader, member_name, domain, variable, time, True, polar_dir_style)


# 功能说明：按变量名候选（极坐标时含 X_polar）列出对应的 processed_v3 文件路径。
def _expected_v3_paths(
    reader: ProcessedDataReader,
    member_name: str,
    domain: int,
    variable: str,
    time: Any,
    polar: bool,
    polar_dir_style: str,
) -> list[Path]:
    from 处理数据_v3.wrf_processing_v3.request import Task

    return [
        reader.expected_path(Task(member_name, int(domain), 0, candidate, None))
        for candidate in _variable_candidates(variable, polar, polar_dir_style)
    ]


# 功能说明：把时次写成进度和报错里用的文字，例如 36.0833 → “36h5min”。
def _time_label(time: int | float | str) -> str:
    try:
        total = round(float(time) * 60.0)
    except (TypeError, ValueError):
        return str(time)
    hours, minutes = divmod(abs(int(total)), 60)
    sign = "-" if total < 0 else ""
    return f"{sign}{hours}h{minutes}min" if minutes else f"{sign}{hours}h"


# 功能说明：保留图型需要的坐标，对其余选中的维度求忽略缺测的平均。例如半径—高度图平均时间和方向，时间曲线则每个时刻得到一个数。
def reduce_variable(
    *,
    stack: list[np.ndarray],
    variable: str,
    variable_type: str,
    fig_type: int,
    times: list[Any],
    heights_m: list[Any] | None,
    angle: list[Any],
    radius_indices: list[int],
    radius_mode: str,
    radius_resolution_km: float,
    first_path: str,
) -> dict[str, np.ndarray]:
    if fig_type == 8 and variable_type != "3d":
        raise ValueError(f"{variable} 不是三维变量，不能绘制 time_height")

    if variable_type == "1d":
        if fig_type != 5:
            raise ValueError(f"{variable} is a 1d variable and requires fig_type=5 time_series")
        values = np.asarray([float(np.nanmean(np.ravel(array))) for array in stack], dtype=float)
        x = np.asarray(times, dtype=float)
        return {"values": values, "x": x, "y": values}

    if variable_type == "3d":
        # 三维栈的第 0 维就是所选高度，顺序与 heights_m 一致（逐高度读取 v3 切片后叠起来）。
        per_file_shape = tuple(stack[0].shape)
        if not heights_m:
            raise ValueError(f"{variable} 是三维变量，需要选择高度")
        if per_file_shape[0] != len(heights_m):
            raise ValueError(
                f"三维数据的高度层数与所选高度不一致: variable={variable}, path={first_path}, "
                f"actual_shape={(len(stack),) + per_file_shape}, heights_m={list(heights_m)}"
            )
        height_indices = list(range(len(heights_m)))

        if fig_type == 0:
            selected = np.stack([array[height_indices, :, :] for array in stack], axis=0)
            values = np.nanmean(selected, axis=(0, 1))
            y = np.arange(values.shape[0])
            x = np.arange(values.shape[1])
            return {"values": values, "x": x, "y": y}

        azimuth_size = per_file_shape[1]
        radius_size = min(array.shape[2] for array in stack)
        az_idx = angle_indices(angle, azimuth_size)
        try:
            r_idx = radius_indices_for_mode(radius_indices, radius_size, radius_mode)
        except ValueError as exc:
            max_radius = (radius_size - 1) * radius_resolution_km
            raise ValueError(f"{exc}。最大可用半径={max_radius:g} km") from exc
        radius_x = selected_radius_km(r_idx, radius_resolution_km)
        height_y = np.asarray(heights_m, dtype=float)
        azimuth_x = make_monotonic_azimuth(az_idx)

        if fig_type == 1:
            selected = np.stack([array[np.ix_(height_indices, az_idx, r_idx)] for array in stack], axis=0)
            values = echo_mean(selected, axis=(0, 2)) if uses_echo_mean(variable, fig_type) else np.nanmean(selected, axis=(0, 2))
            return {"values": values, "x": radius_x, "y": height_y}

        if fig_type == 2:
            selected = np.stack([array[np.ix_(height_indices, az_idx, r_idx)] for array in stack], axis=0)
            values = echo_mean(selected, axis=(1, 2)) if uses_echo_mean(variable, fig_type) else np.nanmean(selected, axis=(1, 2))
            return {"values": values, "x": radius_x, "y": np.asarray(times, dtype=float)}

        if fig_type == 3:
            selected = np.stack([array[np.ix_(height_indices, az_idx, r_idx)] for array in stack], axis=0)
            values = np.nanmean(selected, axis=(1, 3))
            return {"values": values, "x": azimuth_x, "y": np.asarray(times, dtype=float)}

        if fig_type == 4:
            selected = np.stack([array[np.ix_(height_indices, az_idx, r_idx)] for array in stack], axis=0)
            values = np.nanmean(selected, axis=(0, 3))
            return {"values": values, "x": azimuth_x, "y": height_y}

        if fig_type == 5:
            # One scalar per time: average the selected height/azimuth/radius
            # samples together, without giving sparsely valid layers extra weight.
            values = np.asarray([
                (echo_mean(array[np.ix_(height_indices, az_idx, r_idx)])
                 if uses_echo_mean(variable, fig_type)
                 else np.nanmean(array[np.ix_(height_indices, az_idx, r_idx)], dtype=np.float64))
                for array in stack
            ])
            x = np.asarray(times, dtype=float)
            return {"values": values, "x": x, "y": values}

        if fig_type == 8:
            selected = np.stack([array[np.ix_(height_indices, az_idx, r_idx)] for array in stack], axis=0)
            values = (echo_mean(selected, axis=(2, 3)) if uses_echo_mean(variable, fig_type) else np.nanmean(selected, axis=(2, 3))).T
            return {"values": values, "x": np.asarray(times, dtype=float), "y": height_y}

    if variable_type == "2d":
        if fig_type == 0:
            selected = np.stack([array[:, :] for array in stack], axis=0)
            values = np.nanmean(selected, axis=0)
            y = np.arange(values.shape[0])
            x = np.arange(values.shape[1])
            return {"values": values, "x": x, "y": y}

        if fig_type in (1, 4):
            raise ValueError(
                f"{variable} 是二维变量，不能画 {FIG_TYPE_NAMES[fig_type]}。"
                f"path={first_path}, actual_shape={stack.shape}"
            )

        per_file_shape = tuple(stack[0].shape)
        azimuth_size = per_file_shape[0]
        radius_size = min(array.shape[1] for array in stack)
        az_idx = angle_indices(angle, azimuth_size)
        try:
            r_idx = radius_indices_for_mode(radius_indices, radius_size, radius_mode)
        except ValueError as exc:
            max_radius = (radius_size - 1) * radius_resolution_km
            raise ValueError(f"{exc}。最大可用半径={max_radius:g} km") from exc
        radius_x = selected_radius_km(r_idx, radius_resolution_km)
        azimuth_x = make_monotonic_azimuth(az_idx)

        if fig_type == 2:
            selected = np.stack([array[np.ix_(az_idx, r_idx)] for array in stack], axis=0)
            values = np.nanmean(selected, axis=1)
            return {"values": values, "x": radius_x, "y": np.asarray(times, dtype=float)}

        if fig_type == 3:
            selected = np.stack([array[np.ix_(az_idx, r_idx)] for array in stack], axis=0)
            values = np.nanmean(selected, axis=2)
            return {"values": values, "x": azimuth_x, "y": np.asarray(times, dtype=float)}

        if fig_type == 5:
            selected = np.stack([array[np.ix_(az_idx, r_idx)] for array in stack], axis=0)
            values = np.nanmean(selected, axis=(1, 2))
            x = np.asarray(times, dtype=float)
            return {"values": values, "x": x, "y": values}

    raise ValueError(f"未知变量类型或图类型: variable_type={variable_type}, fig_type={fig_type}")


# 功能说明：当所选方向跨过360度回到0度时，给后半段加360，使图上横坐标连续递增。
def make_monotonic_azimuth(indices: np.ndarray) -> np.ndarray:
    x = np.asarray(indices, dtype=float)
    if x.size < 2:
        return x
    adjusted = x.copy()
    for idx in range(1, adjusted.size):
        if adjusted[idx] < adjusted[idx - 1]:
            adjusted[idx:] += 360
            break
    return adjusted


# 功能说明：在数据副本上执行缺测处理、平滑和数值缩放；按选项保留空白、估计缺值或报错，不回写源数据文件。
def apply_postprocess(
    variable: str,
    values: np.ndarray,
    config: dict[str, Any],
    *,
    fig_type: int | None = None,
) -> np.ndarray:
    result = np.ma.asarray(values, dtype=float).filled(np.nan).copy()
    policy = config.get("missing_value_policy", "interpolate")
    # 线性Z平均后真正缺测的位置保留空白，不能用附近回波填补。
    if uses_echo_mean(variable, fig_type) and policy == "interpolate":
        policy = "mask"
    if policy not in {"interpolate", "mask", "error"}:
        raise ValueError(f"{variable}: unknown missing_value_policy: {policy}")
    missing = ~np.isfinite(result)
    if missing.any():
        if policy == "error":
            raise ValueError(f"{variable}: contains {int(missing.sum())} missing/non-finite values")
        if policy == "interpolate":
            result = fill_nan_with_interpolation(result)
        else:
            result[missing] = np.nan

    # 功能说明：调用指定的平滑方法更新当前结果；保留缺测模式下按有效数据权重平滑，再恢复原来的缺测空白。
    def smooth(filter_function, **options):
        nonlocal result
        if policy == "mask" and missing.any():
            valid = np.isfinite(result)
            numerator = filter_function(np.where(valid, result, 0.0), **options)
            weights = filter_function(valid.astype(float), **options)
            result = np.divide(numerator, weights, out=np.full_like(result, np.nan), where=weights > 0)
            result[missing] = np.nan
        else:
            result = filter_function(result, **options)

    if result.ndim == 2:
        from scipy.ndimage import gaussian_filter1d
        axis_options = (
            (("time_smooth_sigma", 1, "nearest"),)
            if fig_type == 8
            else (("angle_smooth_sigma", 1, "wrap"), ("time_smooth_sigma", 0, "nearest"))
        )
        for key, axis, mode in axis_options:
            sigma = config.get(key)
            if sigma is not None and float(sigma) > 0:
                smooth(gaussian_filter1d, sigma=float(sigma), axis=axis, mode=mode)
    sigma = config.get("sigma")
    if sigma is not None:
        from scipy.ndimage import gaussian_filter
        smooth(gaussian_filter, sigma=sigma)
    scale_factor = config.get("scale_factor", 1.0)
    if scale_factor is not None:
        result = result * float(scale_factor)
    return result


# 功能说明：绘图入口（只接受命名参数；2026-11-01 删除了旧式位置参数入口）。
def draw_fig(ax, **kwargs: Any):
    return _draw_fig_new(ax=ax, **kwargs)


# 功能说明：先准备绘图数据，再按图型画水平图、剖面图或时间曲线；预检查模式只准备数据，按需返回来源信息。
def _draw_fig_new(
    *,
    ax,
    member_name: str,
    domain: int,
    variables: dict[str, Any],
    fig_type: int,
    data_root: str | Path = ".",
    domain_config: dict[int, dict[str, Any]] | None = None,
    selection: dict[str, Any] | None = None,
    polar_dir_style: str = "same",
    polar_radius_resolution_km: float | None = None,
    variable_type_registry: dict[str, str] | None = None,
    double_azimuth: bool = False,
    format_config: dict[str, Any] | None = None,
    dry_run: bool = False,
    return_data: bool = False,
    progress: Callable[[str], None] | None = None,
):
    payloads, summaries = prepare_variable_data(
        member_name=member_name,
        domain=domain,
        variables=variables,
        fig_type=fig_type,
        data_root=data_root,
        domain_config=domain_config,
        selection=selection,
        polar_dir_style=polar_dir_style,
        polar_radius_resolution_km=polar_radius_resolution_km,
        variable_type_registry=variable_type_registry or VARIABLE_TYPE_REGISTRY,
        format_config=format_config,
        dry_run=dry_run,
        allow_missing=dry_run,
        progress=progress,
    )

    if dry_run:
        if return_data:
            return ax, None, None, payloads, summaries
        return ax, None, None

    if ax is None:
        raise ValueError("非 dry-run 模式必须提供 matplotlib Axes")

    if fig_type == 0:
        ax, cf, cr = horizontal_plot(ax, payloads)
    elif fig_type == 1:
        ax, cf, cr = radius_height_plot(ax, payloads)
    elif fig_type == 2:
        ax, cf, cr = time_radius_plot(ax, payloads)
    elif fig_type == 3:
        ax, cf, cr = time_azimuth_plot(ax, payloads, double_azimuth=double_azimuth)
    elif fig_type == 4:
        ax, cf, cr = height_azimuth_plot(ax, payloads, double_azimuth=double_azimuth)
    elif fig_type == 5:
        ax, cf, cr = time_series_plot(ax, payloads, format_config=format_config)
    elif fig_type == 8:
        ax, cf, cr = time_height_plot(ax, payloads)
    else:
        raise ValueError("不支持的 fig_type")

    if return_data:
        return ax, cf, cr, payloads, summaries
    return ax, cf, cr


# 功能说明：把准备好的多个变量依次叠画成水平分布图，返回填色与等值线对象供后续添加图例。
def horizontal_plot(ax, payloads: dict[str, dict[str, Any]]):
    cf, cr = None, None
    for variable, payload in payloads.items():
        cf, cr = plot_payload(ax, variable, payload, cf, cr)
    return ax, cf, cr


# 功能说明：把准备好的变量依次画在半径—高度坐标中，用于观察不同距离和高度上的结构。
def radius_height_plot(ax, payloads: dict[str, dict[str, Any]]):
    cf, cr = None, None
    for variable, payload in payloads.items():
        cf, cr = plot_payload(ax, variable, payload, cf, cr)
    return ax, cf, cr


# 功能说明：把准备好的变量依次画在时间—半径坐标中，用于观察不同半径上的数值如何随时间变化。
def time_radius_plot(ax, payloads: dict[str, dict[str, Any]]):
    cf, cr = None, None
    for variable, payload in payloads.items():
        cf, cr = plot_payload(ax, variable, payload, cf, cr)
    return ax, cf, cr


# 功能说明：绘制时间—方向分布，按需把方向重复显示两圈，方便观察跨越0度边界的结构。
def time_azimuth_plot(ax, payloads: dict[str, dict[str, Any]], double_azimuth: bool = False):
    cf, cr = None, None
    for variable, payload in payloads.items():
        payload = maybe_double_azimuth_payload(payload, double_azimuth)
        cf, cr = plot_payload(ax, variable, payload, cf, cr)
    return ax, cf, cr


# 功能说明：绘制高度—方向分布，按需重复一圈方向数据，便于观察不同高度上的方位差异。
def height_azimuth_plot(ax, payloads: dict[str, dict[str, Any]], double_azimuth: bool = False):
    cf, cr = None, None
    for variable, payload in payloads.items():
        payload = maybe_double_azimuth_payload(payload, double_azimuth)
        cf, cr = plot_payload(ax, variable, payload, cf, cr)
    return ax, cf, cr


# 功能说明：把各三维变量绘制在时间—高度坐标中，横轴为时次、纵轴为高度。
def time_height_plot(ax, payloads: dict[str, dict[str, Any]]):
    cf, cr = None, None
    for variable, payload in payloads.items():
        cf, cr = plot_payload(ax, variable, payload, cf, cr)
    return ax, cf, cr


# 功能说明：把各变量或试验画成随时间变化的曲线，按单位安排左右纵轴，可加散点和风切变箭头，并记录轴信息供格式设置使用。
def time_series_plot(ax, payloads: dict[str, dict[str, Any]], format_config: dict[str, Any] | None = None):
    fmt = (format_config or {}).get("time_series", format_config or {})
    dual_axis = fmt.get("dual_axis", "auto")
    arrow_cfg = normalize_time_series_arrow_config(fmt)
    member_rows = bool(arrow_cfg.get("member_rows", fmt.get("member_rows", False)))
    arrow_cfg["member_rows"] = member_rows
    axis_order: list[str] = []
    axis_specs: dict[str, dict[str, str]] = {}
    for variable, payload in payloads.items():
        if should_skip_time_series_line(variable, payload["config"], arrow_cfg, member_rows):
            continue
        spec = time_series_axis_spec(variable, payload["config"])
        if spec["key"] not in axis_specs:
            axis_specs[spec["key"]] = spec
            axis_order.append(spec["key"])

    use_dual_axis = len(axis_order) > 1 and dual_axis is not False
    axes_by_key = {axis_order[0]: ax} if axis_order else {}
    if use_dual_axis:
        axes_by_key[axis_order[1]] = ax.twinx()
        for extra_key in axis_order[2:]:
            axes_by_key[extra_key] = axes_by_key[axis_order[1]]
    else:
        axes_by_key = {key: ax for key in axis_order}

    handles = []
    axis_state = {"left": None, "right": None}
    for index, (variable, payload) in enumerate(payloads.items()):
        config = payload["config"]
        if should_skip_time_series_line(variable, config, arrow_cfg, member_rows):
            continue
        spec = time_series_axis_spec(variable, config)
        target_ax = axes_by_key.get(spec["key"], ax)
        x = np.asarray(payload["x"], dtype=float)
        y = normalize_time_series_values(variable, payload["values"], fmt, config)
        color = resolve_time_series_color(payload, payloads, config, fmt, format_config, index)
        label = time_series_label(variable, payload, payloads)
        line = target_ax.plot(
            x,
            y,
            color=color,
            linewidth=config.get("linewidth", 2.0),
            linestyle=resolve_time_series_linestyle(payload, config, fmt),
            marker=config.get("marker"),
            label=label,
        )[0]
        if bool(config.get("scatter_points", config.get("scatter", fmt.get("scatter_points", fmt.get("scatter", False))))):
            target_ax.scatter(
                x,
                y,
                color=line.get_color(),
                s=config.get("scatter_size", fmt.get("scatter_size", 24)),
                marker=config.get("scatter_marker", fmt.get("scatter_marker", "o")),
                zorder=config.get("scatter_zorder", line.get_zorder() + 1),
                label="_nolegend_",
            )
        handles.append(line)

        side = "right" if use_dual_axis and target_ax is not ax else "left"
        if axis_state[side] is None:
            axis_state[side] = {
                "axis": target_ax,
                "key": spec["key"],
                "label": spec["label"],
                "color": line.get_color(),
                "config": config,
            }

    ax._time_series_axis_state = axis_state
    quiver = draw_time_series_shear_arrows(ax, payloads, arrow_cfg)
    if len(handles) > 1:
        ax.legend(handles=handles)
    return ax, None, quiver


# 功能说明：根据变量单位决定它属于哪类纵轴，并取得用户指定或默认的纵轴标题。
def time_series_axis_spec(variable: str, config: dict[str, Any]) -> dict[str, str]:
    units = config.get("units") or config.get("unit")
    if units is None:
        units = infer_time_series_units(variable)
    key = normalize_time_series_unit(units or "")
    default_label = "Shear Angle" if key == "direction" else units or "Value"
    label = config.get("ylabel") or config.get("axis_label") or default_label
    return {"key": key, "label": str(label)}


# 功能说明：从变量名称推测时间曲线单位，例如风速为米每秒、气压为百帕、距离为公里；无法识别则用通用标签。
def infer_time_series_units(variable: str) -> str:
    name = variable.lower()
    if "direction" in name or "angle" in name:
        return "degree"
    if "wind" in name or name in {"ua", "va", "u", "v", "u10", "v10", "vr", "vt"}:
        return "m/s"
    if "pressure" in name or "slp" in name or "pmin" in name:
        return "hPa"
    if (
        name in {"rmw", "center_land_distance", "tilt_x", "tilt_y", "tilt_magnitude", "tilt_1_6", "tilt_x_1_6", "tilt_y_1_6"}
        or "distance" in name
        or re.fullmatch(r"tilt_(?:x_|y_)?\d+_\d+", name)
    ):
        return "km"
    return "Value"


# 功能说明：把同一种单位的不同写法统一，例如 mps 和 m/s 归为一类，让它们可共用纵轴。
def normalize_time_series_unit(units: str) -> str:
    text = str(units).strip().lower().replace(" ", "")
    if text in {"ms-1", "m/s", "mps"}:
        return "m/s"
    if text in {"hpa", "mb"}:
        return "hPa"
    if text in {"degree", "degrees", "deg"}:
        return "direction"
    return text or "value"


# 功能说明：对时间曲线按选项做平滑；角度变量使用专门的圆周平滑并转换到指定角度范围。
def normalize_time_series_values(variable: str, values: np.ndarray, fmt: dict[str, Any], config: dict[str, Any]) -> np.ndarray:
    result = np.asarray(values, dtype=float)
    if is_direction_variable(variable, config):
        smooth_sigma = config.get("angle_smooth_sigma", fmt.get("angle_smooth_sigma"))
        if smooth_sigma is not None and float(smooth_sigma) > 0:
            result = smooth_clockwise_angle(result, sigma=float(smooth_sigma))
        return wrap_direction_angle(result, config.get("angle_wrap", fmt.get("angle_wrap", "360")))
    smooth_sigma = config.get("time_smooth_sigma", fmt.get("time_smooth_sigma"))
    if smooth_sigma is not None and float(smooth_sigma) > 0 and result.size > 1:
        from scipy.ndimage import gaussian_filter1d

        result = gaussian_filter1d(result, sigma=float(smooth_sigma))
    return result


# 功能说明：先把角度变成单位圆上的横纵分量再平滑，最后转回角度，避免359度和1度被错误平均到180度附近。
def smooth_clockwise_angle(values: np.ndarray, *, sigma: float) -> np.ndarray:
    """Smooth clockwise angles through unit-vector components.

    The stored shear direction is ``-degrees(arctan2(v, u))``.  Converting it
    back to the mathematical angle before smoothing avoids false jumps at the
    -360/0 boundary.
    """
    from scipy.ndimage import gaussian_filter1d

    clockwise = np.asarray(values, dtype=float)
    theta_math = np.deg2rad(-clockwise)
    cos_smooth = gaussian_filter1d(np.cos(theta_math), sigma=sigma)
    sin_smooth = gaussian_filter1d(np.sin(theta_math), sigma=sigma)
    return -np.degrees(np.arctan2(sin_smooth, cos_smooth))


# 功能说明：把方向值统一到0至360度或负360至0度范围，也可保留原值；不支持的范围设置会报错。
def wrap_direction_angle(values: np.ndarray, mode: Any) -> np.ndarray:
    normalized_mode = str(mode).strip().lower().replace(" ", "")
    result = np.asarray(values, dtype=float)
    if normalized_mode in {"-360_0", "-360:0", "negative360", "clockwise"}:
        wrapped = np.mod(result, 360.0)
        wrapped[wrapped > 0.0] -= 360.0
        return wrapped
    if normalized_mode in {"360", "0_360", "0:360", "positive360"}:
        return np.mod(result, 360.0)
    if normalized_mode in {"none", "raw", "false"}:
        return result
    raise ValueError(f"unsupported direction angle_wrap: {mode}")


# 功能说明：根据单位或名称判断一个变量是否表示方向角，以便使用专门的角度显示方法。
def is_direction_variable(variable: str, config: dict[str, Any] | None = None) -> bool:
    config = config or {}
    units = config.get("units") or config.get("unit")
    if units is not None and normalize_time_series_unit(str(units)) == "direction":
        return True
    name = variable.lower()
    return "direction" in name or "angle" in name


# 功能说明：多个试验叠画时用试验名称作图例；单个试验时优先使用自定义标签，否则显示变量名。
def time_series_label(variable: str, payload: dict[str, Any], payloads: dict[str, dict[str, Any]]) -> str:
    config = payload["config"]
    base_variable = str(payload.get("variable", variable))
    member_name = payload.get("member_name")
    members = {item.get("member_name") for item in payloads.values() if item.get("member_name")}
    if member_name and len(members) > 1:
        return member_display_name(str(member_name))
    if config.get("label"):
        return str(config["label"])
    return base_variable


# 功能说明：优先采用变量指定颜色；按试验名使用预设配色，未识别的试验按顺序取色。
def resolve_time_series_color(
    payload: dict[str, Any],
    payloads: dict[str, dict[str, Any]],
    config: dict[str, Any],
    fmt: dict[str, Any],
    format_config: dict[str, Any] | None,
    index: int,
) -> str:
    """Use the experiment palette for both individual and overlaid time series."""
    explicit_color = config.get("color")
    if explicit_color is not None and str(explicit_color).strip():
        return str(explicit_color)

    member_name = str(payload.get("member_name") or "")
    if member_name:
        track_fmt = (format_config or {}).get("track", {})
        color_fmt = {**track_fmt, **fmt}
        experiment_color = resolve_experiment_color(member_name, index, color_fmt)
        if experiment_color is not None:
            return experiment_color
    return str(config.get("color") or f"C{index}")


# 功能说明：优先按试验名匹配线型，再用变量或默认线型，帮助用实线、虚线区分试验。
def resolve_time_series_linestyle(payload: dict[str, Any], config: dict[str, Any], fmt: dict[str, Any]) -> str:
    """Resolve explicit or member-name-based line styles for comparisons."""
    member = str(payload.get("member_name") or "")
    styles = fmt.get("member_linestyles", fmt.get("member_styles", {"sea": "-", "land": "--"}))
    if isinstance(styles, dict) and member in styles:
        return str(styles[member])
    by_scheme = scheme_linestyle(member, fmt)
    if by_scheme is not None:
        return by_scheme
    if isinstance(styles, dict):
        lowered = member.lower()
        for pattern, linestyle in styles.items():
            if str(pattern).lower() in lowered:
                return str(linestyle)
    if config.get("linestyle") is not None:
        return str(config["linestyle"])
    return str(fmt.get("default_linestyle", "-"))


# 功能说明：把时间曲线的箭头开关或详细参数转成统一设置表，明确是否开启。
def normalize_time_series_arrow_config(fmt: dict[str, Any]) -> dict[str, Any]:
    raw = fmt.get("shear_arrows", fmt.get("arrows", False))
    if raw is True:
        return {"enabled": True}
    if isinstance(raw, dict):
        config = dict(raw)
        config.setdefault("enabled", True)
        return config
    return {"enabled": False}


# 功能说明：判断某变量是否只用于箭头而无需单独画折线，避免同一风分量产生多余曲线。
def should_skip_time_series_line(variable: str, config: dict[str, Any], arrow_cfg: dict[str, Any], member_rows: bool) -> bool:
    if config.get("show_line") is False or config.get("line") is False:
        return True
    if member_rows and arrow_cfg.get("enabled", False) and variable in arrow_component_names(arrow_cfg):
        return not bool(config.get("show_line", False))
    return False


# 功能说明：取得组成风切变箭头的两个水平分量名称，未指定时使用200至850百帕风切变的东西、南北分量。
def arrow_component_names(arrow_cfg: dict[str, Any]) -> set[str]:
    return {
        str(arrow_cfg.get("u_variable", "shear_200_850_u")),
        str(arrow_cfg.get("v_variable", "shear_200_850_v")),
    }


# 功能说明：按试验配对两个风切变分量，在各时刻画箭头；可让每个试验占一行，并添加表示箭头长度含义的图例。
def draw_time_series_shear_arrows(ax, payloads: dict[str, dict[str, Any]], arrow_cfg: dict[str, Any]):
    if not arrow_cfg.get("enabled", False):
        return None
    u_name = str(arrow_cfg.get("u_variable", "shear_200_850_u"))
    v_name = str(arrow_cfg.get("v_variable", "shear_200_850_v"))
    stride = max(1, int(arrow_cfg.get("stride", 1)))
    member_rows = bool(arrow_cfg.get("member_rows", False))
    groups: dict[str, dict[str, Any]] = {}
    for variable, payload in payloads.items():
        base_variable = str(payload.get("variable", variable))
        if base_variable not in {u_name, v_name}:
            continue
        member = str(payload.get("member_name") or "")
        groups.setdefault(member, {})[base_variable] = payload

    quiver = None
    row_labels: list[str] = []
    for row_index, (member, group) in enumerate(groups.items(), start=1):
        if u_name not in group or v_name not in group:
            continue
        u_payload = group[u_name]
        v_payload = group[v_name]
        x = np.asarray(u_payload["x"], dtype=float)
        u = np.asarray(u_payload["values"], dtype=float)
        v = np.asarray(v_payload["values"], dtype=float)
        u_config = u_payload["config"]
        if member_rows:
            y = np.full_like(x, float(row_index), dtype=float)
            row_labels.append(member or f"member {row_index}")
        else:
            y = arrow_y_values(payloads, member, x, arrow_cfg)
        color = arrow_cfg.get("color") or u_config.get("color") or f"C{row_index - 1}"
        quiver = ax.quiver(
            x[::stride],
            y[::stride],
            u[::stride],
            v[::stride],
            color=color,
            angles=arrow_cfg.get("angles", "uv"),
            scale=resolve_time_series_arrow_scale(arrow_cfg, u_config),
            width=arrow_cfg.get("width", 0.002),
            headwidth=arrow_cfg.get("headwidth", 3),
            headlength=arrow_cfg.get("headlength", 5),
            headaxislength=arrow_cfg.get("headaxislength", 4),
            zorder=arrow_cfg.get("zorder", 10),
        )
    if member_rows and row_labels:
        ax._time_series_member_rows = row_labels
        ax.set_ylim(float(arrow_cfg.get("ymin", 0.5)), len(row_labels) + float(arrow_cfg.get("ymax_padding", 0.5)))
    if quiver is not None and arrow_cfg.get("key", True):
        key_cfg = arrow_cfg.get("key_config", {})
        ax.quiverkey(
            quiver,
            X=key_cfg.get("x", arrow_cfg.get("key_x", 0.9)),
            Y=key_cfg.get("y", arrow_cfg.get("key_y", 0.95)),
            U=key_cfg.get("u", arrow_cfg.get("key_u", 10)),
            label=key_cfg.get("label", arrow_cfg.get("key_label", "10 m/s shear")),
            labelpos=key_cfg.get("labelpos", "E"),
            coordinates=key_cfg.get("coordinates", "axes"),
        )
    return quiver


# 功能说明：按时间图设置、变量附加设置、变量设置的顺序确定箭头缩放参数；这里参数越小，箭头越长。
def resolve_time_series_arrow_scale(arrow_cfg: dict[str, Any], variable_config: dict[str, Any]) -> float | None:
    """Resolve arrow scale, preferring the time-series override.

    Matplotlib quiver uses an inverse scale: a smaller number draws longer
    arrows.  The generic QUIVER variable ``extra.scale`` remains available as
    a fallback so the parameter has the same effect in time-series plots.
    """
    if arrow_cfg.get("scale") is not None:
        return float(arrow_cfg["scale"])
    extra = variable_config.get("extra") or {}
    if isinstance(extra, dict) and extra.get("scale") is not None:
        return float(extra["scale"])
    if variable_config.get("scale") is not None:
        return float(variable_config["scale"])
    return 400.0


# 功能说明：确定时间图箭头放在哪个纵坐标：优先跟随同试验指定变量的曲线，没有时放在固定高度。
def arrow_y_values(payloads: dict[str, dict[str, Any]], member: str, x: np.ndarray, arrow_cfg: dict[str, Any]) -> np.ndarray:
    y_variable = arrow_cfg.get("y_variable", "shear_200_850_magnitude")
    for payload in payloads.values():
        if str(payload.get("variable")) == y_variable and str(payload.get("member_name") or "") == member:
            return np.asarray(payload["values"], dtype=float)
    return np.full_like(x, float(arrow_cfg.get("y", 0.0)), dtype=float)


# 功能说明：需要显示两圈方向时，复制一份数据并将其横坐标加360，接在第一圈之后。
def maybe_double_azimuth_payload(payload: dict[str, Any], double_azimuth: bool) -> dict[str, Any]:
    if not double_azimuth:
        return payload
    copied = dict(payload)
    values = np.asarray(payload["values"])
    x = np.asarray(payload["x"], dtype=float)
    copied["values"] = np.concatenate((values, values), axis=1)
    copied["x"] = np.concatenate((x, x + 360))
    return copied


# 功能说明：按变量设置绘制彩色填充或等值线并可标注数值；云分类使用类别颜色和专门图例。
def plot_payload(ax, variable: str, payload: dict[str, Any], cf, cr):
    config = payload["config"]
    values, x, y = make_axis_minimum_2d(payload["values"], payload["x"], payload["y"])
    plot_type = config.get("plot_type", "contourf").lower()
    levels = resolve_levels_for_data(variable, config.get("levels"), values)

    if variable == "classification":
        cf = ax.contourf(
            x,
            y,
            values,
            levels=config.get("levels", [-0.5, 0.5, 1.5, 2.5, 3.5, 4.5, 5.5]),
            cmap=config.get("cmap", CLASSIFICATION_CMAP),
            extend=config.get("extend", "neither"),
            origin="lower",
        )
        legend_elements = [
            Patch(facecolor="#f0f0f0", label="No rain"),
            Patch(facecolor="#ffff99", label="Other"),
            Patch(facecolor="#66c2a5", label="Stratiform"),
            Patch(facecolor="#3288bd", label="Shallow convective"),
            Patch(facecolor="#fdae61", label="Moderate convective"),
            Patch(facecolor="#d73027", label="Deep convective"),
        ]
        ax.legend(handles=legend_elements, loc=config.get("legend_loc", "lower right"))
        return cf, cr

    if plot_type == "contourf":
        kwargs = {
            "cmap": resolve_cmap(config.get("cmap")),
            "levels": levels,
            "extend": config.get("extend", "both"),
            "zorder": config.get("zorder", 1),
        }
        kwargs = {key: value for key, value in kwargs.items() if value is not None}
        cf = ax.contourf(x, y, values, **kwargs)
    elif plot_type == "contour":
        kwargs = {
            "colors": config.get("color", "k"),
            "levels": levels,
            "linewidths": config.get("linewidths"),
            "zorder": config.get("zorder", 2),
        }
        kwargs = {key: value for key, value in kwargs.items() if value is not None}
        cr = ax.contour(x, y, values, **kwargs)
        if config.get("clabel", True):
            ax.clabel(
                cr,
                inline=True,
                fontsize=config.get("clabel_fontsize", 14),
                fmt=config.get("clabel_fmt", "%.1f"),
                colors=config.get("color", "k"),
            )
    else:
        raise ValueError(f"{variable} 的 plot_type={plot_type} 不支持，只能是 contourf 或 contour")
    return cf, cr


# 功能说明：检查指定的颜色分级；若气压误用了与实际值完全不相交的分级，就按真实气压范围生成合适分级。
def resolve_levels_for_data(variable: str, levels: Any, values: np.ndarray) -> Any:
    if levels is None:
        return None
    try:
        level_array = np.asarray(levels, dtype=float)
    except Exception:
        return levels
    if level_array.size == 0:
        return None

    data_min = float(np.nanmin(values))
    data_max = float(np.nanmax(values))
    if not np.isfinite(data_min) or not np.isfinite(data_max):
        return levels

    overlaps = float(np.nanmax(level_array)) >= data_min and float(np.nanmin(level_array)) <= data_max
    if overlaps or variable != "pressure":
        return levels

    # Pressure is in hPa after wrf-python processing.  Users often copy dbz
    # levels such as 10|15|...|50, which do not intersect pressure values
    # around 600-1000 hPa.  Auto-recover to data-based hPa levels instead of
    # producing a solid-color plot or a Matplotlib contour error.
    step = 5.0 if (data_max - data_min) <= 80.0 else 10.0
    lower = np.floor(data_min / step) * step
    upper = np.ceil(data_max / step) * step
    if upper <= lower:
        upper = lower + step
    return np.arange(lower, upper + 0.5 * step, step).tolist()


# 功能说明：把颜色表名称转换成可用色表，支持额外的 cmaps 色表包；缺依赖或名称错误时给出明确提示。
def resolve_cmap(cmap):
    if cmap is None:
        return None
    if not isinstance(cmap, str):
        return cmap
    if cmap.startswith("cmaps."):
        cmap_name = cmap.split(".", 1)[1]
        registered = _registered_cmap(cmap_name)
        if registered is not None:
            return registered
        try:
            import cmaps
        except ImportError as exc:
            raise ImportError(
                f"色表 {cmap!r} 需要安装 cmaps：pip install cmaps"
            ) from exc
        except ValueError as exc:
            # Some cmaps/Matplotlib combinations try to register every cmap
            # again during import.  If the requested map is already present,
            # reuse it instead of failing with "already registered".
            registered = _registered_cmap(cmap_name)
            if registered is not None and "already registered" in str(exc).lower():
                return registered
            raise
        try:
            return getattr(cmaps, cmap_name)
        except AttributeError as exc:
            registered = _registered_cmap(cmap_name)
            if registered is not None:
                return registered
            raise ValueError(f"cmaps 中不存在色表 {cmap_name!r}") from exc
    return cmap


# 功能说明：查找绘图库已经有的色表，避免重复注册同名色表引发错误；不存在就返回空值。
def _registered_cmap(name: str):
    """Return an existing Matplotlib cmap without triggering registration."""
    try:
        import matplotlib

        registry = getattr(matplotlib, "colormaps", None)
        if registry is not None and name in registry:
            return registry[name]
        from matplotlib import cm

        return cm.get_cmap(name)
    except (KeyError, ValueError):
        return None


# 功能说明：从变量配置中筛出开启的变量，供后续读取和绘制。
def enabled_variables(variables: dict[str, Any]) -> dict[str, Any]:
    return {
        variable: spec
        for variable, spec in variables.items()
        if normalize_variable_config(variable, spec).get("enabled", True)
    }


# 功能说明：取得第一份待绘制数据的尺寸，例如有多少行和列；没有数据时返回空值。
def first_payload_shape(payloads: dict[str, dict[str, Any]]) -> tuple[int, ...] | None:
    if not payloads:
        return None
    first = next(iter(payloads.values()))
    return tuple(np.asarray(first["values"]).shape)


# 功能说明：把一个变量使用的时间、高度、半径范围和数据尺寸整理成一行便于查看的说明。
def describe_payload(payload: dict[str, Any]) -> str:
    return (
        f"{payload['variable']} "
        f"time={format_range(payload['times'], 'h')} "
        f"height={format_range(payload['height'] or [], 'm')} "
        f"radius={format_range(payload['radius'], 'km')} "
        f"shape={payload['values'].shape}"
    )
