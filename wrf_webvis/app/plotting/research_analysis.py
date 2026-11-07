from __future__ import annotations

from .rendering import serialized_render, save_figure

import re
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from ..core.schemas import PlotLayerType, PlotRequest, SelectionSpec, VariableConfig, VariableDimension
from ..core.selection import expand_selection
from ..services.data_manager import DataManager, RequiredFile, request_member_names


ANALYSIS_TYPES = {"ls_direction_means", "timing_alignment_180_vs_270"}
DEFAULT_TIMES_H = [0, 3, 6, 9, 12]
DEFAULT_LS_SECTOR_DEG = [225.0, 315.0]
DEFAULT_RADIUS_KM = [20.0, 80.0]
ANALYSIS_VARIABLES = {
    "ls_direction_means": (
        ("mfc", "bl_mfc"),
        ("conv", "convergence"),
        ("qin", "qflux_r"),
        ("rain", "RAINNCV"),
    ),
    "timing_alignment_180_vs_270": (
        ("mfc", "bl_mfc"),
        ("conv", "convergence"),
        ("qin", "qflux_r"),
        ("w3", "wa"),
        ("rain", "RAINNCV"),
    ),
}


# 功能说明：为专题分析补上默认变量和统计方法说明，写回请求中，让保存的绘图方案能说明自己用了什么数据、怎么算。
def normalize_research_request(request: PlotRequest) -> None:
    """Make every saved research-analysis request self-describing for WebVis."""
    analysis_type = requested_analysis_type(request)
    if analysis_type is None:
        return
    if not request.variables:
        request.variables = _default_variable_configs(analysis_type)
    request.extra = dict(request.extra or {})
    request.extra["analysis_definition"] = _analysis_definition(request, analysis_type)


# 功能说明：列出专题图默认使用的水汽汇聚、空气汇聚、向内水汽输送和降雨等变量，设好高度、单位换算与图例；时序对比还加入3公里上升速度。
def _default_variable_configs(analysis_type: str) -> list[VariableConfig]:
    variables = [
        VariableConfig(
            name="bl_mfc", plot_type=PlotLayerType.LINE, use_polar=True, scale_factor=3600.0,
            units="mm h-1 equivalent", label="BL MFC", extra={"analysis_role": "mfc", "description": "boundary-layer moisture-flux convergence"},
        ),
        VariableConfig(
            name="convergence", plot_type=PlotLayerType.LINE, use_polar=True, height=SelectionSpec(values=[500]), scale_factor=1.0e4,
            units="10^-4 s^-1", label="500-m convergence", extra={"analysis_role": "conv", "description": "horizontal convergence"},
        ),
        VariableConfig(
            name="qflux_r", plot_type=PlotLayerType.LINE, use_polar=True, height=SelectionSpec(values=[500]), scale_factor=-1000.0,
            units="g m^-2 s^-1", label="500-m inward moisture flux", extra={"analysis_role": "qin", "description": "negative radial moisture flux is inward"},
        ),
    ]
    if analysis_type == "timing_alignment_180_vs_270":
        variables.append(
            VariableConfig(
                name="wa", plot_type=PlotLayerType.LINE, use_polar=True, height=SelectionSpec(values=[3000]), scale_factor=1.0,
                units="m s^-1", label="3-km ascent", extra={"analysis_role": "w3", "description": "vertical velocity at 3 km"},
            )
        )
    variables.append(
        VariableConfig(
            name="RAINNCV", plot_type=PlotLayerType.LINE, use_polar=True, scale_factor=1500.0,
            units="mm h^-1", label="Rain rate", extra={"analysis_role": "rain", "description": "model rain-rate diagnostic"},
        )
    )
    return variables


# 功能说明：把选中的方向扇区、半径范围、变量高度和平均方法整理成一份说明，随绘图请求保存。
def _analysis_definition(request: PlotRequest, analysis_type: str) -> dict[str, Any]:
    time_aggregation = "mean over all selected times" if analysis_type == "ls_direction_means" else "within-member min-max normalization; compare peak timing only"
    return {
        "name": analysis_type,
        "statistic": "area-weighted polar mean",
        "coordinate_system": "storm-centered polar grid",
        "ls_sector_deg": _range_option(request, "ls_sector_deg", DEFAULT_LS_SECTOR_DEG),
        "radius_km": _range_option(request, "radius_km", DEFAULT_RADIUS_KM),
        "time_aggregation": time_aggregation,
        "variables": [
            {
                "name": variable.name,
                "height_m": _height_for(variable),
                "scale_factor": variable.scale_factor,
                "units": variable.units,
                "label": variable.label,
                "description": (variable.extra or {}).get("description", ""),
            }
            for variable in request.variables
            if variable.enabled
        ],
    }


# 功能说明：从请求中挑出本专题支持且已开启的变量，并按分析角色归类；一个都没选时给出错误提示。
def _analysis_variables(request: PlotRequest, analysis_type: str) -> dict[str, VariableConfig]:
    normalize_research_request(request)
    if not request.variables:
        request.variables = _default_variable_configs(analysis_type)
    by_name = {variable.name: variable for variable in request.variables}
    variables = {
        role: by_name[name]
        for role, name in ANALYSIS_VARIABLES[analysis_type]
        if name in by_name and by_name[name].enabled
    }
    if not variables:
        raise ValueError(f"{analysis_type} requires at least one enabled diagnostic variable")
    return variables


# 功能说明：向其他模块提供本专题实际要用的变量清单，方便它们检查所需数据。
def analysis_variable_configs(request: PlotRequest, analysis_type: str) -> dict[str, VariableConfig]:
    """Return the enabled, supported inputs for a research analysis."""
    return _analysis_variables(request, analysis_type)


# 功能说明：读取一个变量指定的高度；没有高度就返回空值，指定多个高度则报错，因为此类专题要求每个变量只取一层。
def _height_for(variable: VariableConfig) -> int | None:
    if variable.height is None:
        return None
    values = [int(value) for value in expand_selection(variable.height, default=[])]
    if len(values) != 1:
        raise ValueError(f"{variable.name} must specify exactly one height for this research analysis")
    return values[0]


# 功能说明：查看请求是否指定了支持的专题分析名称；没有或名称不支持时返回空值。
def requested_analysis_type(request: PlotRequest) -> str | None:
    value = (request.extra or {}).get("analysis_type")
    return str(value) if value in ANALYSIS_TYPES else None


# 功能说明：按试验、时次、变量和高度列出专题图所需的 processed_v3 极坐标切片及其是否已完成，供绘图前检查。
def required_inputs(request: PlotRequest, analysis_type: str) -> list[RequiredFile]:
    normalize_research_request(request)
    members = _members_by_surface_and_direction(request, analysis_type)
    times = _times(request)
    manager = DataManager.from_plot_request(request)
    variables = _analysis_variables(request, analysis_type).values()
    items: list[RequiredFile] = []
    for member in members.values():
        for time_hour in times:
            for variable in variables:
                height_m = _height_for(variable)
                dimension = VariableDimension.THREE_D if height_m is not None else VariableDimension.TWO_D
                items.append(manager._file_status(
                    member, request.domain, _polar_name(variable.name), dimension, time_hour, height_m,
                ))
    return items


# 功能说明：专题图读取的 processed_v3 文件（去重，保持顺序）。
def required_paths(request: PlotRequest, analysis_type: str) -> list[Path]:
    return list(dict.fromkeys(item.path for item in required_inputs(request, analysis_type)))


# 功能说明：专题图缺失的 v3 切片（带变量、时次、高度，便于报错）。
def missing_inputs(request: PlotRequest, analysis_type: str) -> list[RequiredFile]:
    return [item for item in required_inputs(request, analysis_type) if not item.exists]


# 功能说明：检查专题数据文件齐全后，选择方向平均图或峰值时序对比图并保存；返回使用的文件及统计设置。
@serialized_render
def create_research_analysis_plot(request: PlotRequest, output_path: Path, *, progress=None) -> dict[str, Any]:
    analysis_type = requested_analysis_type(request)
    if analysis_type is None:
        raise ValueError("Unsupported research analysis type")
    normalize_research_request(request)
    paths = required_paths(request, analysis_type)
    missing = missing_inputs(request, analysis_type)
    if missing:
        sample = "; ".join(f"{item.variable} {item.time_hour:g} h @ {item.path}" for item in missing[:3])
        raise FileNotFoundError(f"Research analysis requires processed_v3 polar inputs; missing {len(missing)} slice(s): {sample}")

    if analysis_type == "ls_direction_means":
        _draw_ls_direction_means(request, output_path)
    else:
        _draw_timing_alignment(request, output_path)
    return {
        "used_files": [str(path) for path in paths],
        "metadata": {
            "analysis_type": analysis_type,
            "times_h": _times(request),
            "ls_sector_deg": _range_option(request, "ls_sector_deg", DEFAULT_LS_SECTOR_DEG),
            "radius_km": _range_option(request, "radius_km", DEFAULT_RADIUS_KM),
            "statistics": "area-weighted polar mean using radial-distance weights",
            "variables": [variable.name for variable in _analysis_variables(request, analysis_type).values()],
        },
    }


# 功能说明：把各试验在指定扇区和半径内的数值再对所选时次平均，画出海洋与陆地试验随环境风方向变化的对比并保存。
def _draw_ls_direction_means(request: PlotRequest, output_path: Path) -> None:
    members = _members_by_surface_and_direction(request, "ls_direction_means")
    directions = sorted({direction for surface, direction in members})
    values = {(surface, direction): _ls_series(request, member, "ls_direction_means") for (surface, direction), member in members.items()}
    configs = [
        ("mfc", "BL MFC (mm h$^{-1}$ equivalent)"),
        ("conv", "500-m convergence (10$^{-4}$ s$^{-1}$)"),
        ("qin", "500-m inward moisture flux (g m$^{-2}$ s$^{-1}$)"),
        ("rain", "Rain rate (mm h$^{-1}$)"),
    ]
    configs = [(key, ylabel) for key, ylabel in configs if key in next(iter(values.values()))]
    ncols = min(2, len(configs))
    nrows = int(np.ceil(len(configs) / ncols))
    default_size = [12.5 if ncols == 2 else 6.4, 4.5 * nrows]
    fig, axes = plt.subplots(nrows, ncols, figsize=_figsize(request, default_size), sharex=True, squeeze=False)
    try:
        for ax, (key, ylabel) in zip(axes.flat, configs):
            for surface, color, marker, linestyle in (("sea", "#2166ac", "o", "-"), ("land", "#b2182b", "s", "--")):
                means = [float(np.nanmean(values[(surface, direction)][key])) for direction in directions]
                ax.plot(directions, means, color=color, marker=marker, linestyle=linestyle, linewidth=2, markersize=6, label=surface.capitalize())
                if 180 in directions:
                    index = directions.index(180)
                    ax.scatter([180], [means[index]], s=105, facecolors="none", edgecolors=color, linewidths=2, zorder=5)
            ax.axvspan(168, 192, color="#fddbc7", alpha=0.45, zorder=0)
            ax.set_ylabel(ylabel)
            ax.grid(axis="y", alpha=0.25)
            ax.set_xticks(directions)
            ax.set_xticklabels([f"{direction}°" for direction in directions])
            ax.margins(x=0.03)
        for ax in axes.flat[len(configs):]:
            ax.set_visible(False)
        axes.flat[0].legend(frameon=False, ncol=2, loc="best")
        for ax in axes[-1, :]:
            if ax.get_visible():
                ax.set_xlabel("Low-level environmental-wind direction")
        ls_sector = _range_option(request, "ls_sector_deg", DEFAULT_LS_SECTOR_DEG)
        radius = _range_option(request, "radius_km", DEFAULT_RADIUS_KM)
        fig.suptitle(
            f"LS-sector means during 3–12 h ({ls_sector[0]:g}°–{ls_sector[1]:g}°, {radius[0]:g}–{radius[1]:g} km)\n"
            "Open circles/squares highlight the 180° experiment",
            fontsize=15,
            y=0.98,
        )
        fig.tight_layout(rect=[0, 0, 0.98, 0.94])
        save_figure(fig, output_path, dpi=request.dpi, bbox_inches="tight")
    finally:
        plt.close(fig)


# 功能说明：把海洋、陆地的180度与270度试验分别画成四个小图；各变量缩放到自身的0到1范围，便于比较峰值出现时间而非大小。
def _draw_timing_alignment(request: PlotRequest, output_path: Path) -> None:
    members = _members_by_surface_and_direction(request, "timing_alignment_180_vs_270")
    series = {(surface, direction): _ls_series(request, member, "timing_alignment_180_vs_270") for (surface, direction), member in members.items()}
    times = np.asarray(_times(request), dtype=float)
    fig, axes = plt.subplots(2, 2, figsize=_figsize(request, [12.5, 8.8]), sharex=True, sharey=True)
    try:
        styles = (
            ("mfc", "#7b3294", "BL MFC"),
            ("conv", "#008837", "500-m conv."),
            ("qin", "#2c7fb8", "500-m inward q flux"),
            ("w3", "#0571b0", "3-km ascent"),
            ("rain", "#ca0020", "Rain"),
        )
        for row, surface in enumerate(("sea", "land")):
            for col, direction in enumerate((180, 270)):
                ax = axes[row, col]
                for key, color, label in styles:
                    if key not in series[(surface, direction)]:
                        continue
                    values = series[(surface, direction)][key]
                    lo, hi = float(np.nanmin(values)), float(np.nanmax(values))
                    normalized = (values - lo) / (hi - lo) if hi > lo else np.zeros_like(values)
                    ax.plot(times, normalized, color=color, marker="o", linewidth=2.2, label=label)
                    ax.scatter([times[int(np.nanargmax(values))]], [1], s=35, color=color, zorder=4)
                ax.set_title(f"{surface.capitalize()} · {direction}°", fontsize=13)
                ax.set_xticks(times)
                ax.set_ylim(-0.05, 1.08)
                ax.grid(alpha=0.25)
                ax.axvline(9, color="0.45", linestyle=":", linewidth=0.8)
                ax.axvline(12, color="0.45", linestyle=":", linewidth=0.8)
                if col == 0:
                    ax.set_ylabel("Within-case normalized value")
                if row == 1:
                    ax.set_xlabel("Time (h)")
        axes[0, 0].legend(frameon=False, ncol=2, loc="upper left")
        fig.suptitle(
            "Timing alignment: 180° peaks near 9 h, while 270° dynamical forcing peaks at the 12-h boundary\n"
            "Normalization is within each variable and experiment; compare timing, not magnitude",
            fontsize=14,
            y=0.98,
        )
        fig.tight_layout(rect=[0, 0, 0.98, 0.92])
        save_figure(fig, output_path, dpi=request.dpi, bbox_inches="tight")
    finally:
        plt.close(fig)


# 功能说明：逐时读取一个试验的各变量，求指定扇区和半径内的面积加权平均，再乘单位换算系数，得到随时间变化的一串数。
def _ls_series(request: PlotRequest, member: str, analysis_type: str = "timing_alignment_180_vs_270") -> dict[str, np.ndarray]:
    variables = _analysis_variables(request, analysis_type)
    values = {key: [] for key in variables}
    sector = _range_option(request, "ls_sector_deg", DEFAULT_LS_SECTOR_DEG)
    radius = _range_option(request, "radius_km", DEFAULT_RADIUS_KM)
    for time_hour in _times(request):
        for key, variable in variables.items():
            values[key].append(_ls_mean(_load_variable(request, member, variable, time_hour), sector, radius) * variable.scale_factor)
    return {key: np.asarray(item, dtype=float) for key, item in values.items()}


# 功能说明：根据变量设置找到它的极坐标版本和指定高度，读取一个试验某时刻的数值。
def _load_variable(request: PlotRequest, member: str, variable: VariableConfig, time_hour: int) -> np.ndarray:
    return _load(request, member, _polar_name(variable.name), time_hour, _height_for(variable))


# 功能说明：给变量名补上表示极坐标数据的 _polar 后缀，已有后缀则保留。
def _polar_name(variable_name: str) -> str:
    return variable_name if variable_name.endswith("_polar") else f"{variable_name}_polar"


# 功能说明：优先使用请求里有效的图片宽高，否则返回专题图的默认大小。
def _figsize(request: PlotRequest, default: list[float]) -> tuple[float, float]:
    if request.figsize is not None and len(request.figsize) == 2 and all(float(value) > 0 for value in request.figsize):
        return float(request.figsize[0]), float(request.figsize[1])
    return float(default[0]), float(default[1])


# 功能说明：找到并读取指定试验、变量、时刻和高度的已处理数组；文件不存在就报出缺少的内容。
def _load(request: PlotRequest, member: str, variable: str, time_hour: int, height_m: int | None = None) -> np.ndarray:
    reader = DataManager.from_plot_request(request).processed_reader
    reference = reader.resolve(
        member_name=member, domain=request.domain, variable=variable,
        time_hour=time_hour, height_m=height_m,
    )
    if reference is None:
        raise FileNotFoundError(f"Missing {variable} for {member} at {time_hour} h")
    return np.asarray(reader.load(reference), dtype=float)


# 功能说明：选出给定角度扇区和半径范围，以半径为权重求平均，因为外圈同样角度对应的面积更大；没有有效值时返回缺测值。这里把半径格点序号当作公里。
def _ls_mean(values: np.ndarray, sector: list[float] | None = None, radius: list[float] | None = None) -> float:
    # The caller uses the defaults configured for this experiment.  Weighting
    # by radius converts a polar-grid average into an area-weighted annulus mean.
    sector = sector or DEFAULT_LS_SECTOR_DEG
    radius = radius or DEFAULT_RADIUS_KM
    angles = np.arange(values.shape[0], dtype=float) * 360.0 / values.shape[0]
    radii = np.arange(values.shape[1], dtype=float)
    angle_mask = (angles >= sector[0]) & (angles <= sector[1])
    radius_mask = (radii >= radius[0]) & (radii <= radius[1])
    selected = values[np.ix_(angle_mask, radius_mask)]
    weights = np.broadcast_to(radii[radius_mask], selected.shape)
    finite = np.isfinite(selected)
    if not np.any(finite):
        return float("nan")
    return float(np.sum(selected[finite] * weights[finite]) / np.sum(weights[finite]))


# 功能说明：从试验名称中识别海洋或陆地及风向，按专题要求检查试验是否齐全，并返回分类后的对应表。
def _members_by_surface_and_direction(request: PlotRequest, analysis_type: str) -> dict[tuple[str, int], str]:
    required = [(surface, direction) for surface in ("sea", "land") for direction in ((180, 270) if analysis_type == "timing_alignment_180_vs_270" else (0, 45, 90, 135, 180, 225, 270, 315))]
    found: dict[tuple[str, int], str] = {}
    for member in request_member_names(request):
        match = re.fullmatch(r"WRF_(\d+)_(sea|land)", member)
        if match:
            found[(match.group(2), int(match.group(1)))] = member
    missing = [f"WRF_{direction}_{surface}" for surface, direction in required if (surface, direction) not in found]
    if missing:
        raise ValueError(f"{analysis_type} requires members: {', '.join(missing)}")
    return {key: found[key] for key in required}


# 功能说明：把请求中的时间选择展开成小时列表；未指定有效列表时使用默认时次。
def _times(request: PlotRequest) -> list[int]:
    values = [int(value) for value in expand_selection(request.time, default=DEFAULT_TIMES_H)]
    return values or list(DEFAULT_TIMES_H)


# 功能说明：读取一个起止范围，转为数值并检查必须恰好有两个数且终点大于起点。
def _range_option(request: PlotRequest, name: str, default: list[float]) -> list[float]:
    raw = (request.extra or {}).get(name, default)
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise ValueError(f"{name} must contain exactly two values")
    lower, upper = float(raw[0]), float(raw[1])
    if upper <= lower:
        raise ValueError(f"{name} must be increasing")
    return [lower, upper]
