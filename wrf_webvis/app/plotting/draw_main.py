from __future__ import annotations

from ..core.config import absolute_path, normalize_path_options

from .rendering import serialized_render, save_figure

import os
from pathlib import Path
from typing import Any

_cache_root = Path(os.environ.get("TMPDIR", "/tmp")) / "wrf_plot_matplotlib_cache"
(_cache_root / "matplotlib").mkdir(parents=True, exist_ok=True)
(_cache_root / "xdg").mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_cache_root / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(_cache_root / "xdg"))

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

from .draw_format import colorbar_layout, draw_tilt_track_members, set_cb_q, set_format0, set_format1, set_format2, set_format3, set_format4, set_format5, set_format8
from .draw_quiver import draw_quiver, enabled_quiver_variables
from .draw_utils import (
    FIG_TYPE_NAMES,
    validate_domain,
)
from .plot_spec import PlotSpec
from .draw_variables import draw_fig, enabled_variables, normalize_variable_config, prepare_variable_data, time_series_plot
from .track_plot import draw_track_plot


# 功能说明：按绘图规格安排试验和小图，读取已处理数据，绘制变量与风箭头，设置坐标、图例并保存图片；结束或出错都会关闭图形释放资源。
# 2026-11-01：取代旧的 run_from_config(config)。规格由 plot_spec.build_plot_spec(PlotRequest) 生成，测试也可直接构造 PlotSpec。
@serialized_render
def render(spec: PlotSpec, *, progress=None) -> None:
    data_root = absolute_path(spec.data_root)
    output_path = absolute_path(spec.output_path)
    selection = dict(spec.selection)
    members = resolve_members_from_config(spec.members, selection)
    if not members:
        raise ValueError("MEMBERS 不能为空")

    domain = int(spec.domain)
    domain_config = spec.domain_config
    validate_domain(domain, domain_config)

    figure = dict(spec.figure)
    fig_type = int(figure.get("fig_type", 0))
    if fig_type not in FIG_TYPE_NAMES:
        raise ValueError(f"不支持的 FIGURE['fig_type']: {fig_type}")

    variables = enabled_variables(spec.variables)
    quiver_cfg = dict(spec.quiver or {"enabled": False, "variables": {}})
    quiver_variables = enabled_quiver_variables(quiver_cfg.get("variables", {})) if quiver_cfg.get("enabled", False) else {}
    if fig_type == 8 and quiver_variables:
        raise ValueError("time-height 暂不支持 quiver 变量")
    if fig_type == 8 and any(spec.get("use_polar") is False for spec in variables.values()):
        raise ValueError("time-height 必须使用极坐标变量数据")
    format_config = normalize_path_options(spec.format)
    tilt_track_enabled = fig_type == 0 and (format_config.get("horizontal") or {}).get("draw_tilt_track", False)
    if not variables and not quiver_variables and fig_type != 6 and not tilt_track_enabled:
        raise ValueError("没有启用任何 VARIABLES 或 QUIVER 变量")

    if fig_type == 0:
        format_config = dict(format_config or {})
        format_config["horizontal"] = dict(format_config.get("horizontal", {}))
        # Horizontal maps should use real km coordinates by default.  This keeps
        # contour/quiver data and overlays such as radius circles centered on the
        # same point instead of mixing grid-index data with km tick labels.
        format_config["horizontal"].setdefault("coordinate_system", "center_km")
    time_series_variables = variables
    if fig_type == 5 and quiver_variables:
        format_config = enable_time_series_quiver_arrows(format_config)
        time_series_variables = {**variables, **time_series_quiver_as_variables(quiver_variables)}
    colorbar_config = infer_colorbar_config(spec.colorbar, variables)
    quiver_key_config = spec.quiver_key
    polar_dir_style = spec.polar_dir_style
    polar_radius_resolution_km = spec.polar_radius_resolution_km
    variable_registry = spec.variable_type_registry
    output_dir = output_path.parent
    panels = build_panels(members, selection, figure)
    overlay_members = should_overlay_time_series_members(fig_type, figure, format_config, members)
    overlay_track_members = fig_type == 6
    overlay_tilt_members = tilt_track_enabled and format_config["horizontal"].get("tilt_track_overlay_members", False)
    if overlay_tilt_members and (variables or quiver_variables):
        raise ValueError("倾斜轨迹多试验叠加用于独立轨迹图，请取消所有变量和风箭头")
    plot_panels = (
        [{"member_name": members[0], "selection": dict(selection), "member_names": members}]
        if overlay_members or overlay_track_members or overlay_tilt_members
        else panels
    )

    row = int(figure.get("row", 1))
    col = int(figure.get("col", 1))
    figsize = resolve_figure_layout(figure, row, col, format_config, fig_type)
    fig, axes = plt.subplots(row, col, figsize=figsize)
    try:
        axes = np.atleast_1d(axes).flatten()
        if len(plot_panels) > len(axes):
            raise ValueError(f"panel 数量不足: panels={len(panels)}, row*col={len(axes)}")

        last_cf = None
        last_q = None
        last_payloads: dict[str, Any] = {}
        last_quiver_payloads: dict[str, Any] = {}

        for idx, panel in enumerate(plot_panels):
            member_name = panel["member_name"]
            panel_selection = selection_for_member_time_limit(panel["selection"], member_name, format_config, fig_type)
            ax = axes[idx]
            double_azimuth = resolve_double_azimuth(format_config, fig_type)

            if overlay_tilt_members:
                draw_tilt_track_members(
                    ax, data_root, panel["member_names"], domain, domain_config,
                    format_config, selection.get("member_time_limits", {}),
                )
                continue

            if fig_type == 6:
                ax, cf, cr, payloads, summaries = draw_track_plot(
                    ax=ax,
                    member_names=panel["member_names"],
                    domain=domain,
                    data_root=data_root,
                    selection=panel_selection,
                    domain_config=domain_config,
                    format_config=format_config,
                )
                last_cf = cf if cf is not None else last_cf
                last_payloads = payloads
            elif time_series_variables and overlay_members:
                ax, cf, cr, payloads, summaries = draw_overlay_member_time_series(
                    ax=ax,
                    member_names=panel["member_names"],
                    domain=domain,
                    variables=time_series_variables,
                    data_root=data_root,
                    domain_config=domain_config,
                    selection=panel_selection,
                    polar_dir_style=polar_dir_style,
                    polar_radius_resolution_km=polar_radius_resolution_km,
                    variable_registry=variable_registry,
                    format_config=format_config,
                )
                last_cf = cf if cf is not None else last_cf
                last_q = cr if cr is not None else last_q
                last_payloads = payloads
            elif time_series_variables:
                ax, cf, cr, payloads, summaries = draw_fig(
                    ax=ax,
                    member_name=member_name,
                    domain=domain,
                    variables=time_series_variables,
                    fig_type=fig_type,
                    data_root=data_root,
                    domain_config=domain_config,
                    selection=panel_selection,
                    polar_dir_style=polar_dir_style,
                    polar_radius_resolution_km=polar_radius_resolution_km,
                    variable_type_registry=variable_registry,
                    double_azimuth=double_azimuth,
                    format_config=format_config,
                    return_data=True,
                    progress=progress,
                )
                last_cf = cf if cf is not None else last_cf
                last_payloads = payloads
            else:
                payloads = {}

            if quiver_variables and fig_type not in (5, 6):
                ax, q, q_payloads, q_summaries = draw_quiver(
                    ax=ax,
                    member_name=member_name,
                    domain=domain,
                    variables_quiver=quiver_variables,
                    fig_type=fig_type,
                    data_root=data_root,
                    domain_config=domain_config,
                    selection=panel_selection,
                    polar_dir_style=polar_dir_style,
                    polar_radius_resolution_km=polar_radius_resolution_km,
                    variable_type_registry=variable_registry,
                    format_config=format_config,
                    headwidth=quiver_cfg.get("headwidth", 3),
                    headlength=quiver_cfg.get("headlength", 5),
                    stream=quiver_cfg.get("stream", False),
                    double_azimuth=double_azimuth,
                    return_data=True,
                    progress=progress,
                )
                last_q = q if q is not None else last_q
                last_quiver_payloads = q_payloads

            if fig_type != 6:
                format_axes(
                    ax=ax,
                    member_name=member_name,
                    domain=domain,
                    fig_type=fig_type,
                    data_root=data_root,
                    domain_config=domain_config,
                    format_config=format_config,
                    variables_all={**variables, **quiver_variables},
                    payloads=payloads or last_quiver_payloads,
                )

        for extra_ax in axes[len(plot_panels):]:
            extra_ax.set_visible(False)

        if progress is not None:
            progress('数据绘制完成，正在调整布局和图例')
        layout = colorbar_layout(colorbar_config, has_colorbar=last_cf is not None)
        if last_cf is not None and colorbar_config.get("enabled", True) and "position" not in colorbar_config:
            colorbar_config["position"] = [layout["left"], layout["bottom"], layout["width"], layout["height"]]
        fig.tight_layout(rect=(0.03, 0.06, layout["axes_right"], 0.94))
        set_cb_q(last_cf, last_q, fig, colorbar_config=colorbar_config, quiver_key_config=quiver_key_config)

        output_dir.mkdir(parents=True, exist_ok=True)
        if figure.get("save", True):
            if progress is not None:
                progress(f'正在保存图片: {output_path}')
            save_figure(fig, output_path, dpi=figure.get("dpi", 150 * 2), bbox_inches="tight")
            print(f"Saved figure: {output_path}")
        if figure.get("show", False):
            plt.show()
    finally:
        plt.close(fig)


# 功能说明：从图片设置或对应图型设置中取得单个小图的宽高，均未填写时使用默认值。
def resolve_figsize_per_panel(figure: dict[str, Any], format_config: dict[str, Any], fig_type: int) -> tuple[float, float]:
    # Dashboard/API requests have historically stored the size of one panel in
    # ``figsize``.  Preserve that meaning when laying out a multi-panel figure.
    figsize = figure.get("figsize", figure.get("figsize_per_panel"))
    if figsize is None:
        fmt_name = FIG_TYPE_NAMES.get(fig_type)
        figsize = (format_config.get(fmt_name, {}) if fmt_name else {}).get("figsize")
    if figsize is None:
        figsize = (9, 8)
    if len(figsize) != 2:
        raise ValueError("figsize 必须是 [width, height]")
    return float(figsize[0]), float(figsize[1])


# 功能说明：用每个小图的宽高乘以列数和行数，计算整张画布需要多大。
def resolve_figure_layout(
    figure: dict[str, Any], row: int, col: int, format_config: dict[str, Any], fig_type: int
) -> tuple[float, float]:
    """Resolve the complete canvas from the requested per-panel dimensions."""
    panel = resolve_figsize_per_panel(figure, format_config, fig_type)
    return col * panel[0], row * panel[1]


# 功能说明：复制格式设置并打开时间序列图的箭头显示选项，保留其他已设置的箭头参数。
def enable_time_series_quiver_arrows(format_config: dict[str, Any]) -> dict[str, Any]:
    config = dict(format_config or {})
    time_series = dict(config.get("time_series", {}))
    arrows = time_series.get("shear_arrows")
    if isinstance(arrows, dict):
        arrows = dict(arrows)
        arrows.setdefault("enabled", True)
    else:
        arrows = {"enabled": True}
    time_series["shear_arrows"] = arrows
    config["time_series"] = time_series
    return config


# 功能说明：把箭头分量整理成时间序列可以读取的变量配置，并标记不单独画折线，让它们只用于组成箭头。
def time_series_quiver_as_variables(quiver_variables: dict[str, Any]) -> dict[str, Any]:
    converted: dict[str, Any] = {}
    for variable, config in quiver_variables.items():
        item = dict(config)
        item["show_line"] = False
        item["plot_type"] = "line"
        converted[variable] = item
    return converted


# 功能说明：优先采用选择区指定的试验列表，没有时才使用总配置中的试验列表。
def resolve_members_from_config(members_config: Any, selection: dict[str, Any]) -> list[str]:
    members = normalize_member_list(selection.get("members"))
    if members:
        return members
    return normalize_member_list(members_config)


# 功能说明：把逗号或竖线分隔的试验名、或已有列表整理为去掉空白和空项的名称列表。
def normalize_member_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.replace("|", ",").split(",")
    return [str(name).strip() for name in value if str(name).strip()]


# 功能说明：检查当前是否是多个试验的时间序列图，以及用户是否要求把这些试验叠放在同一张图上。
def should_overlay_time_series_members(fig_type: int, figure: dict[str, Any], format_config: dict[str, Any], members: list[str]) -> bool:
    if fig_type != 5 or len(members) <= 1:
        return False
    fmt = format_config.get("time_series", {})
    return bool(fmt.get("overlay_members", figure.get("overlay_members", False)))


# 功能说明：逐个读取试验的时间序列，给数据加上所属试验名后合并绘制，便于在同一坐标轴比较不同试验。
def draw_overlay_member_time_series(
    *,
    ax,
    member_names: list[str],
    domain: int,
    variables: dict[str, Any],
    data_root: str | Path,
    domain_config: dict[int, dict[str, Any]],
    selection: dict[str, Any],
    polar_dir_style: str,
    polar_radius_resolution_km: float | None,
    variable_registry: dict[str, str],
    format_config: dict[str, Any],
):
    merged_payloads: dict[str, dict[str, Any]] = {}
    all_summaries: list[dict[str, Any]] = []
    for member_name in member_names:
        payloads, summaries = prepare_variable_data(
            member_name=member_name,
            domain=domain,
            variables=variables,
            fig_type=5,
            data_root=data_root,
            domain_config=domain_config,
            selection=selection_for_member_time_limit(selection, member_name, format_config),
            polar_dir_style=polar_dir_style,
            polar_radius_resolution_km=polar_radius_resolution_km,
            variable_type_registry=variable_registry,
            format_config=format_config,
            allow_missing=True,
        )
        all_summaries.extend(summaries)
        for variable, payload in payloads.items():
            copied = dict(payload)
            copied["config"] = dict(payload["config"])
            copied["member_name"] = member_name
            copied.setdefault("variable", variable)
            merged_payloads[f"{member_name}:{variable}"] = copied

    ax, cf, cr = time_series_plot(ax, merged_payloads, format_config=format_config)
    return ax, cf, cr, merged_payloads, all_summaries


# 功能说明：按试验配置的结束小时裁剪时间选择，避免为已经结束的试验继续寻找后续时刻的数据。
def selection_for_member_time_limit(
    selection: dict[str, Any],
    member_name: str,
    format_config: dict[str, Any] | None = None,
    fig_type: int | None = None,
) -> dict[str, Any]:
    """Restrict a member to its configured final forecast hour."""
    member_selection = dict(selection)
    limits = selection.get("member_time_limits")
    if not isinstance(limits, dict):
        section = {2: "time_radius", 3: "time_azimuth", 8: "time_height"}.get(fig_type, "time_series")
        limits = (format_config or {}).get(section, {}).get("member_end_hours")
    from ..services.data_manager import member_time_limit

    limit = member_time_limit(limits, member_name)
    if limit is None:
        return member_selection

    times = selection.get("times")
    if times is None:
        return member_selection
    member_selection["times"] = [time_hour for time_hour in as_list(times, "SELECTION['times']") if float(time_hour) <= limit]
    return member_selection


# 功能说明：按用户设置把试验、时间、高度组合成小图清单，也接受逐个自定义的小图方案。
def build_panels(members: list[str], selection: dict[str, Any], figure: dict[str, Any]) -> list[dict[str, Any]]:
    custom_panels = figure.get("panels")
    if custom_panels is not None:
        panels: list[dict[str, Any]] = []
        for idx, panel in enumerate(custom_panels):
            panel_config = dict(panel)
            member_name = panel_config.pop("member_name", panel_config.pop("member", members[0]))
            panel_selection = dict(selection)
            panel_selection.update(panel_config)
            panels.append({"member_name": member_name, "selection": panel_selection})
        return panels

    panel_by = figure.get("panel_by", "member")
    panels = []

    if panel_by in (None, "member"):
        return [{"member_name": member_name, "selection": dict(selection)} for member_name in members]

    if panel_by == "time":
        for member_name in members:
            for time in as_list(selection.get("times"), "SELECTION['times']"):
                panel_selection = dict(selection)
                panel_selection["times"] = [time]
                panels.append({"member_name": member_name, "selection": panel_selection})
        return panels

    if panel_by == "height":
        for member_name in members:
            for height in as_list(selection.get("height"), "SELECTION['height']"):
                panel_selection = dict(selection)
                panel_selection["height"] = [height]
                panels.append({"member_name": member_name, "selection": panel_selection})
        return panels

    if panel_by == "time_height":
        for member_name in members:
            for time in as_list(selection.get("times"), "SELECTION['times']"):
                for height in as_list(selection.get("height"), "SELECTION['height']"):
                    panel_selection = dict(selection)
                    panel_selection["times"] = [time]
                    panel_selection["height"] = [height]
                    panels.append({"member_name": member_name, "selection": panel_selection})
        return panels

    raise ValueError("FIGURE['panel_by'] 只能是 'member', 'time', 'height', 'time_height'，或使用 FIGURE['panels']")


# 功能说明：把单个选择值、范围或数组统一变成列表；未提供必要选择时直接提示。
def as_list(value: Any, name: str) -> list[Any]:
    if value is None:
        raise ValueError(f"{name} 不能为空")
    if isinstance(value, range):
        return list(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


# 功能说明：从第一个填色变量的分级和名称推导颜色条的范围、间隔及标题，补充用户留为自动的设置。
def infer_colorbar_config(raw_config: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
    config = dict(raw_config or {})
    if not config.get("enabled", True):
        return config

    source_variable = None
    source_config: dict[str, Any] | None = None
    for variable, spec in variables.items():
        variable_config = normalize_variable_config(variable, spec)
        if not variable_config.get("enabled", True):
            continue
        if variable_config.get("plot_type", "contourf").lower() != "contourf":
            continue
        source_variable = variable
        source_config = variable_config
        break

    if source_config is None:
        return config

    levels = source_config.get("levels")
    levels_array = np.asarray(levels, dtype=float) if levels is not None else np.asarray([])
    if source_variable == "pressure" and levels_array.size > 0 and np.nanmax(levels_array) < 200:
        levels_array = np.asarray([])
    has_levels = levels_array.size > 0

    if is_auto_value(config.get("level_min")) and has_levels:
        config["level_min"] = float(levels_array[0])
    if is_auto_value(config.get("interval")) and levels_array.size >= 2:
        diffs = np.diff(levels_array)
        config["interval"] = float(diffs[0])
    if is_auto_value(config.get("level_max")) and has_levels:
        interval = config.get("interval")
        if interval is None or is_auto_value(interval):
            interval = 1.0
        config["level_max"] = float(levels_array[-1]) + abs(float(interval)) * 0.001
    if is_auto_value(config.get("title")):
        config["title"] = source_config.get("colorbar_title") or source_config.get("title") or source_variable

    return config


# 功能说明：判断某设置是否留空或写为 auto，从而决定是否需要自动补值。
def is_auto_value(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.lower() == "auto")


# 功能说明：检查时间—方向图或高度—方向图是否要求把一圈360度重复显示为两圈。
def resolve_double_azimuth(format_config: dict[str, Any], fig_type: int) -> bool:
    if fig_type == 3:
        return bool(format_config.get("time_azimuth", {}).get("double_azimuth", False))
    if fig_type == 4:
        return bool(format_config.get("height_azimuth", {}).get("double_azimuth", False))
    return False


# 功能说明：从已准备的数据中提取时间、高度等信息，按图型调用对应的坐标、标题和辅助标记设置函数。
def format_axes(
    *,
    ax,
    member_name: str,
    domain: int,
    fig_type: int,
    data_root: str | Path,
    domain_config: dict[int, dict[str, Any]],
    format_config: dict[str, Any],
    variables_all: dict[str, Any],
    payloads: dict[str, dict[str, Any]],
) -> None:
    first_payload = next(iter(payloads.values()), None) if payloads else None
    times = first_payload.get("times") if first_payload else []
    heights_m = first_payload.get("height") if first_payload else None
    angle = first_payload.get("angle") if first_payload else []
    radius = first_payload.get("radius") if first_payload else []
    grid_shape = None
    if first_payload is not None and fig_type == 0:
        values = np.asarray(first_payload["values"])
        grid_shape = tuple(values.shape[-2:])

    if fig_type == 0:
        set_format0(
            ax,
            member_name,
            domain,
            times,
            heights_m,
            variables_all,
            data_root=data_root,
            domain_config=domain_config,
            grid_shape=grid_shape,
            config=format_config,
        )
    elif fig_type == 1:
        set_format1(
            ax, member_name, domain, times, angle, variables_all,
            data_root=data_root, heights_m=heights_m, config=format_config,
        )
    elif fig_type == 2:
        set_format2(ax, member_name, domain, times, heights_m, angle, variables_all, data_root=data_root, config=format_config)
    elif fig_type == 3:
        set_format3(ax, member_name, domain, times, heights_m, radius, variables_all, data_root=data_root, config=format_config)
    elif fig_type == 4:
        set_format4(ax, member_name, domain, times, heights_m, radius, variables_all, config=format_config)
    elif fig_type == 5:
        set_format5(ax, member_name, domain, times, variables_all, config=format_config)
    elif fig_type == 8:
        set_format8(
            ax,
            member_name,
            domain,
            times,
            heights_m,
            angle,
            radius,
            variables_all,
            config=format_config,
        )

