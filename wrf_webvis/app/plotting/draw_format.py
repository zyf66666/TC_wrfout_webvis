from __future__ import annotations

from pathlib import Path
from typing import Any
import warnings

import matplotlib.patches as patches
import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.colors import Normalize
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator

from ..core.tilt_track import tilt_track_components
from .center_lookup import load_center_grid
from ..services.data_manager import RMW_BY_HEIGHT_SOURCES, DataManager, radius_height_rmw_source, rmw_center_label
from .member_styles import member_display_name, resolve_experiment_color, scheme_linestyle, split_member_key
from ..services.data_manager import member_time_limit
from .draw_utils import (
    DEFAULT_DOMAIN_CONFIG,
    format_range,
    get_dx_km,
    start_x_to_xlim,
)
from .v3_values import processed_reader, read_scalar, read_scalar_series, read_slice

# 功能说明：给水平图设置以中心为参照的距离坐标、半径圆、标题及可选辅助标记，让底图和中心、风切变等叠加内容使用对应坐标。
def set_format0(
    ax,
    member_name: str,
    domain: int,
    times: list[int | float],
    heights_m: list[int | float] | None,
    draw_variables: dict[str, Any],
    *,
    data_root: str | Path = ".",
    domain_config: dict[int, dict[str, Any]] | None = None,
    grid_shape: tuple[int, int] | None = None,
    config: dict[str, Any] | None = None,
    fontsize: int | None = None,
) -> str:
    cfg = config or {}
    common = cfg.get("common", {})
    fmt = cfg.get("horizontal", cfg)
    fontname = common.get("fontname", "Times New Roman")
    fontsize = fontsize or common.get("fontsize", 18)
    dx_km = get_dx_km(domain, domain_config or DEFAULT_DOMAIN_CONFIG)
    dy_km = _get_dy_km(domain, domain_config or DEFAULT_DOMAIN_CONFIG, dx_km)
    # True means the horizontal data layer has already been plotted in km
    # relative to the selected center.  In that mode all overlays must also use
    # km coordinates: center=(0, 0), radius circle=radius_km, xlim=xlim_km.
    # False keeps compatibility with the older grid-index coordinate system.
    km_coordinates = fmt.get("coordinate_system") == "center_km"

    center_y_grid, center_x_grid = resolve_center(
        data_root=data_root,
        member_name=member_name,
        domain=domain,
        times=times,
        grid_shape=grid_shape,
        center_enabled=fmt.get("center_enabled", False),
        center_path=fmt.get("center_path"),
        center_height_m=fmt.get("center_height_m"),
        center_index=fmt.get("center_index"),
    )
    center_y = 0.0 if km_coordinates else center_y_grid
    center_x = 0.0 if km_coordinates else center_x_grid

    ax.set_aspect("equal", adjustable="box")

    for radius_km in fmt.get("radius_circles_km", []):
        radius_value = float(radius_km) if km_coordinates else float(radius_km) / dx_km
        ax.add_patch(
            patches.Circle(
                (center_x, center_y),
                radius=radius_value,
                edgecolor=fmt.get("radius_circle_color", "darkgreen"),
                linewidth=fmt.get("radius_circle_linewidth", 1),
                linestyle=fmt.get("radius_circle_linestyle", "--"),
                facecolor="none",
                zorder=fmt.get("radius_circle_zorder", 6),
            )
        )

    maybe_draw_rmw(ax, data_root, member_name, domain, times, center_x, center_y, dx_km, fmt)
    maybe_draw_land_distance(ax, data_root, member_name, domain, times, center_x, dx_km, fmt)
    maybe_draw_upper_center(ax, data_root, member_name, domain, times, fmt, center_y_grid, center_x_grid, dx_km, dy_km)
    maybe_draw_shear_arrow(ax, data_root, member_name, domain, times, center_x, center_y, fmt)
    maybe_draw_tilt_track(ax, data_root, member_name, domain, times, center_x, center_y, dx_km, dy_km, fmt)
    maybe_draw_cloud_classification(ax, data_root, member_name, domain, times, fmt)
    maybe_draw_direction_lines(ax, center_x, center_y, dx_km, fmt)
    maybe_draw_arcs(ax, center_x, center_y, dx_km, fmt)

    xlim_km = fmt.get("xlim_km", 200)
    tick_interval_km = fmt.get("tick_interval_km", 50)
    half = float(xlim_km) if km_coordinates else float(xlim_km) / dx_km
    ax.set_xlim(center_x - half, center_x + half)
    ax.set_ylim(center_y - half, center_y + half)

    tick_km = np.arange(-float(xlim_km), float(xlim_km) + 0.5 * tick_interval_km, tick_interval_km)
    tick_positions = center_x + tick_km if km_coordinates else center_x + tick_km / dx_km
    y_tick_positions = center_y + tick_km if km_coordinates else center_y + tick_km / dx_km
    ax.set_xticks(tick_positions)
    ax.set_yticks(y_tick_positions)
    ax.set_xticklabels([f"{value:g}" for value in tick_km], fontsize=fontsize, fontname=fontname)
    ax.set_yticklabels([f"{value:g}" for value in tick_km], fontsize=fontsize, fontname=fontname)
    ax.set_xlabel("X (km)", fontsize=fontsize, fontname=fontname)
    ax.set_ylabel("Y (km)", fontsize=fontsize, fontname=fontname)

    title = make_title(
        common.get("title"),
        member_name=member_name,
        times=times,
        heights_m=heights_m,
        angle=None,
        radius=None,
        variables=draw_variables,
    )
    ax.set_title(title, fontsize=fontsize, fontname=fontname)
    return title


# 功能说明：给半径—高度图设置显示范围和刻度，把内部以米存储的高度标成公里，并生成标题。
def set_format1(
    ax,
    member_name: str,
    domain: int,
    times: list[int | float],
    angle: list[int | float],
    draw_variables: dict[str, Any],
    *,
    data_root: str | Path = ".",
    heights_m: list[int | float] | None = None,
    config: dict[str, Any] | None = None,
    fontsize: int | None = None,
) -> str:
    cfg = config or {}
    common = cfg.get("common", {})
    fmt = cfg.get("radius_height", cfg)
    fontname = common.get("fontname", "Times New Roman")
    fontsize = fontsize or common.get("fontsize", 18)

    if fmt.get("xlim") is not None:
        ax.set_xlim(fmt["xlim"])
    if fmt.get("ylim") is not None:
        ax.set_ylim(fmt["ylim"])

    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    radius_interval = fmt.get("radius_tick_interval_km", 25)
    height_interval = fmt.get("height_tick_interval_m", 1000)
    xticks = np.arange(np.floor(xlim[0]), np.ceil(xlim[1]) + radius_interval, radius_interval)
    ax.set_xticks(xticks[(xticks >= xlim[0]) & (xticks <= xlim[1])])
    ax.set_xlim(xlim)
    yticks = np.arange(0, max(ylim[1], height_interval) + height_interval, height_interval)
    yticks = yticks[(yticks >= ylim[0]) & (yticks <= ylim[1])]
    ax.set_yticks(yticks)
    ax.set_xticklabels([f"{tick:g}" for tick in ax.get_xticks()], fontsize=fontsize, fontname=fontname)
    ax.set_yticklabels([f"{tick / 1000:g}" for tick in yticks], fontsize=fontsize, fontname=fontname)
    ax.set_xlabel("Radius (km)", fontsize=fontsize, fontname=fontname)
    ax.set_ylabel("Height (km)", fontsize=fontsize, fontname=fontname)

    if fmt.get("draw_rmw", False):
        draw_radius_height_rmw(
            ax, data_root=data_root, member_name=member_name, domain=domain,
            times=times, heights_m=heights_m or [], format_options=fmt,
        )

    title = make_title(
        common.get("title"),
        member_name=member_name,
        times=times,
        heights_m=None,
        angle=angle,
        radius=None,
        variables=draw_variables,
    )
    ax.set_title(title, fontsize=fontsize, fontname=fontname)
    return title


def draw_radius_height_rmw(
    ax,
    *,
    data_root: str | Path,
    member_name: str,
    domain: int,
    times: list[int | float],
    heights_m: list[int | float],
    format_options: dict[str, Any],
) -> None:
    source = radius_height_rmw_source(format_options)
    if not times or not heights_m:
        raise ValueError("绘制逐高度 RMW 需要时次和高度选择")
    reader = DataManager(data_root).processed_reader
    values = np.full(len(heights_m), np.nan, dtype=float)
    for index, height_m in enumerate(heights_m):
        samples = []
        for time_hour in times:
            reference = reader.resolve(
                member_name=member_name, domain=domain, variable=source,
                time_hour=time_hour, height_m=height_m,
            )
            if reference is None:
                raise FileNotFoundError(
                    f"缺少 {source} 数据：{member_name} {time_hour:g} h, {height_m:g} m"
                )
            sample = np.asarray(reader.load(reference), dtype=float).reshape(-1)
            if sample.size != 1:
                raise ValueError(f"{source} 应为逐高度标量: {reference.path}")
            if np.isfinite(sample[0]):
                samples.append(float(sample[0]))
        if samples:
            values[index] = float(np.mean(samples))
    if not np.isfinite(values).any():
        raise ValueError(f"{source} 在所选时次和高度均无有效值")
    sigma_km = float(format_options.get("rmw_smooth_sigma_km", 0) or 0)
    values = smooth_radius_height_rmw(values, heights_m, sigma_km=sigma_km)
    label = f"RMW ({rmw_center_label(source)})"
    if sigma_km > 0:
        label = f"{label}, smooth {sigma_km:g} km"
    ax.plot(
        values, heights_m, label=label,
        color=format_options.get("rmw_color", "magenta"),
        linestyle=format_options.get("rmw_linestyle", "-"),
        linewidth=float(format_options.get("rmw_linewidth", 2)),
        zorder=10,
    )


def smooth_radius_height_rmw(
    values: np.ndarray, heights_m: list[int | float] | np.ndarray, *, sigma_km: float,
) -> np.ndarray:
    """Smooth each contiguous valid height segment using physical height distance."""
    data = np.asarray(values, dtype=float)
    heights = np.asarray(heights_m, dtype=float)
    sigma = float(sigma_km)
    if data.ndim != 1 or heights.shape != data.shape or not np.isfinite(heights).all():
        raise ValueError("RMW 平滑需要等长的一维高度和数值")
    if not np.isfinite(sigma) or sigma < 0:
        raise ValueError("RMW 平滑 sigma 必须为非负有限值")
    result = data.copy()
    if sigma == 0:
        return result
    valid = np.isfinite(data)
    starts = np.flatnonzero(valid & ~np.r_[False, valid[:-1]])
    ends = np.flatnonzero(valid & ~np.r_[valid[1:], False]) + 1
    for start, end in zip(starts, ends):
        segment_heights = heights[start:end]
        distance = (segment_heights[:, None] - segment_heights[None, :]) / (sigma * 1000)
        weights = np.exp(-0.5 * distance ** 2)
        result[start:end] = weights @ data[start:end] / weights.sum(axis=1)
    return result


# 功能说明：设置时间—半径图的距离、小时刻度与标题，可叠加最大风速半径、34节风半径、距陆地距离和登陆时刻参考线。
def set_format2(
    ax,
    member_name: str,
    domain: int,
    times: list[int | float],
    heights_m: list[int | float] | None,
    angle: list[int | float],
    draw_variables: dict[str, Any],
    *,
    data_root: str | Path = ".",
    config: dict[str, Any] | None = None,
    fontsize: int | None = None,
) -> str:
    cfg = config or {}
    common = cfg.get("common", {})
    fmt = cfg.get("time_radius", cfg)
    fontname = common.get("fontname", "Times New Roman")
    fontsize = fontsize or common.get("fontsize", 18)

    if fmt.get("xlim") is not None:
        ax.set_xlim(fmt["xlim"])
    if fmt.get("ylim") is None:
        if len(times) == 1:
            ax.set_ylim(float(times[0]) - 0.5, float(times[0]) + 0.5)
        else:
            ax.set_ylim(min(times), max(times))
    else:
        ax.set_ylim(fmt["ylim"])

    radius_interval = fmt.get("radius_interval", fmt.get("x_interval", fmt.get("tick_interval_km")))
    if radius_interval:
        xlim = ax.get_xlim()
        interval = float(radius_interval)
        xticks = np.arange(np.ceil(xlim[0] / interval) * interval, np.floor(xlim[1] / interval) * interval + interval * 0.5, interval)
        ax.set_xticks(xticks)
        ax.set_xticklabels([f"{tick:g}" for tick in xticks], fontsize=fontsize, fontname=fontname)

    time_interval = fmt.get("time_interval", fmt.get("y_interval", 5))
    ylim = ax.get_ylim()
    yticks = np.arange(np.ceil(ylim[0]), np.floor(ylim[1]) + 1, time_interval)
    ax.set_yticks(yticks)
    ax.set_yticklabels([f"{tick:g}" for tick in yticks], fontsize=fontsize, fontname=fontname)
    ax.tick_params(axis="x", labelsize=fontsize)
    ax.set_xlabel("Radius (km)", fontsize=fontsize, fontname=fontname)
    ax.set_ylabel("Time (h)", fontsize=fontsize, fontname=fontname)

    maybe_draw_time_radius_radius_diagnostic(
        ax, data_root, member_name, domain, times, fmt,
        option="draw_rmw", variable="rmw", label="RMW", color="darkgreen", linestyle="-", linewidth=1.5,
    )
    maybe_draw_time_radius_radius_diagnostic(
        ax, data_root, member_name, domain, times, fmt,
        option="draw_r34", variable="r_34", label="R34", color="darkorange", linestyle="--", linewidth=1.5,
    )
    maybe_draw_time_radius_land_distance(ax, data_root, member_name, domain, times, fmt)
    maybe_draw_inset_wind(ax, fmt)
    maybe_draw_landfall_time(ax, fmt, member_name)

    title = make_title(
        common.get("title"),
        member_name=member_name,
        times=None,
        heights_m=heights_m,
        angle=angle,
        radius=None,
        variables=draw_variables,
    )
    ax.set_title(title, fontsize=fontsize, fontname=fontname)
    return title


# 功能说明：设置时间—方向图的方向标签、时间轴和标题，可加风切变方向、高层中心偏移方向和登陆时刻。
def set_format3(
    ax,
    member_name: str,
    domain: int,
    times: list[int | float],
    heights_m: list[int | float] | None,
    radius_km: list[int | float],
    draw_variables: dict[str, Any],
    *,
    data_root: str | Path = ".",
    config: dict[str, Any] | None = None,
    fontsize: int | None = None,
) -> str:
    cfg = config or {}
    common = cfg.get("common", {})
    fmt = cfg.get("time_azimuth", cfg)
    fontname = common.get("fontname", "Times New Roman")
    fontsize = fontsize or common.get("fontsize", 16)
    double_azimuth = bool(fmt.get("double_azimuth", False))

    setup_azimuth_axis(ax, double_azimuth, fmt.get("start_x"), fmt.get("xlim"), fontsize, fontname)
    setup_time_axis(ax, times, fmt.get("time_interval", 5), fmt.get("ylim"), fontsize, fontname)
    maybe_draw_time_azimuth_shear(ax, data_root, member_name, domain, times, fmt)
    maybe_draw_center_height_angle(ax, data_root, member_name, domain, times, fmt)
    maybe_draw_landfall_time(ax, fmt, member_name)

    ax.set_ylabel("Time (h)", fontsize=fontsize, fontname=fontname)
    title = make_title(
        common.get("title"),
        member_name=member_name,
        times=None,
        heights_m=heights_m,
        angle=None,
        radius=radius_km,
        variables=draw_variables,
    )
    ax.set_title(title, fontsize=fontsize, fontname=fontname)
    return title


# 功能说明：设置高度—方向图的范围、方向标签和公里高度标签，可画指定方向参考线。
def set_format4(
    ax,
    member_name: str,
    domain: int,
    times: list[int | float],
    heights_m: list[int | float] | None,
    radius_km: list[int | float],
    draw_variables: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    fontsize: int | None = None,
) -> str:
    cfg = config or {}
    common = cfg.get("common", {})
    fmt = cfg.get("height_azimuth", cfg)
    fontname = common.get("fontname", "Times New Roman")
    fontsize = fontsize or common.get("fontsize", 18)
    double_azimuth = bool(fmt.get("double_azimuth", False))

    setup_azimuth_axis(ax, double_azimuth, fmt.get("start_x"), fmt.get("xlim"), fontsize, fontname)
    if fmt.get("ylim") is not None:
        ax.set_ylim(fmt["ylim"])
    elif heights_m:
        ymin = float(min(heights_m))
        ymax = float(max(heights_m))
        if ymin == ymax:
            padding = 50.0 if ymin < 1000 else 125.0
            ymin -= padding
            ymax += padding
        ax.set_ylim(ymin, ymax)

    ylim = ax.get_ylim()
    yticks = np.arange(0, max(ylim[1], 1000) + 1000, 1000)
    yticks = yticks[(yticks >= ylim[0]) & (yticks <= ylim[1])]
    ax.set_yticks(yticks)
    ax.set_yticklabels([f"{tick / 1000:g}" for tick in yticks], fontsize=fontsize, fontname=fontname)
    ax.set_xlabel("Azimuth", fontsize=fontsize, fontname=fontname)
    ax.set_ylabel("Height (km)", fontsize=fontsize, fontname=fontname)

    directions = fmt.get("draw_azimuth_direction")
    if directions:
        if directions is True:
            directions = [0]
        for angle in np.atleast_1d(directions):
            ax.axvline(angle, color="k", linestyle="-", linewidth=1.5)
            if double_azimuth:
                ax.axvline(float(angle) + 360, color="k", linestyle="-", linewidth=1.5)

    title = make_title(
        common.get("title"),
        member_name=member_name,
        times=times,
        heights_m=None,
        angle=None,
        radius=radius_km,
        variables=draw_variables,
    )
    ax.set_title(title, fontsize=fontsize, fontname=fontname)
    return title


# 功能说明：设置时间曲线的时间范围、刻度、网格和标题，并让纵轴根据数值类型或试验分行模式显示合适标签。
def set_format5(
    ax,
    member_name: str,
    domain: int,
    times: list[int | float],
    draw_variables: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    fontsize: int | None = None,
) -> str:
    cfg = config or {}
    common = cfg.get("common", {})
    fmt = cfg.get("time_series", cfg)
    fontname = common.get("fontname", "Times New Roman")
    fontsize = fontsize or common.get("fontsize", 16)

    if fmt.get("xlim") is not None:
        ax.set_xlim(fmt["xlim"])
    elif times:
        if len(times) == 1:
            ax.set_xlim(float(times[0]) - 0.5, float(times[0]) + 0.5)
        else:
            ax.set_xlim(min(times), max(times))
    if fmt.get("ylim") is not None:
        ax.set_ylim(fmt["ylim"])

    time_interval = fmt.get("time_interval", fmt.get("x_interval"))
    if time_interval:
        xlim = ax.get_xlim()
        xticks = np.arange(np.ceil(xlim[0]), np.floor(xlim[1]) + 1, float(time_interval))
        ax.set_xticks(xticks)
        ax.set_xticklabels([f"{tick:g}" for tick in xticks], fontsize=fontsize, fontname=fontname)

    ax.tick_params(axis="both", labelsize=fontsize)
    ax.grid(bool(fmt.get("grid", True)), linestyle=fmt.get("grid_linestyle", "--"), alpha=fmt.get("grid_alpha", 0.35))
    ax.set_xlabel(fmt.get("xlabel", "Time (h)"), fontsize=fontsize, fontname=fontname)
    apply_time_series_yaxis_format(ax, fmt, fontsize, fontname)

    title = make_title(
        common.get("title"),
        member_name=member_name,
        times=times,
        heights_m=None,
        angle=None,
        radius=None,
        variables=draw_variables,
    )
    ax.set_title(title, fontsize=fontsize, fontname=fontname)
    return title


# 功能说明：设置时间—高度图的小时横轴与公里高度纵轴，并生成包含平均扇区和半径范围的标题。
def set_format8(
    ax,
    member_name: str,
    domain: int,
    times: list[int | float],
    heights_m: list[int | float] | None,
    angle: list[int | float],
    radius_km: list[int | float],
    draw_variables: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    fontsize: int | None = None,
) -> str:
    cfg = config or {}
    common = cfg.get("common", {})
    fmt = cfg.get("time_height", cfg)
    fontname = common.get("fontname", "Times New Roman")
    fontsize = fontsize or common.get("fontsize", 16)

    if fmt.get("xlim") is not None:
        ax.set_xlim(fmt["xlim"])
    elif times:
        if len(times) == 1:
            ax.set_xlim(float(times[0]) - 0.5, float(times[0]) + 0.5)
        else:
            ax.set_xlim(min(times), max(times))
    if fmt.get("ylim") is not None:
        ax.set_ylim(fmt["ylim"])
    elif heights_m:
        if len(heights_m) == 1:
            padding = 50.0 if float(heights_m[0]) < 1000 else 125.0
            ax.set_ylim(float(heights_m[0]) - padding, float(heights_m[0]) + padding)
        else:
            ax.set_ylim(min(heights_m), max(heights_m))

    time_interval = float(fmt.get("time_interval", 6))
    xlim = ax.get_xlim()
    xticks = np.arange(
        np.ceil(xlim[0] / time_interval) * time_interval,
        np.floor(xlim[1] / time_interval) * time_interval + time_interval * 0.5,
        time_interval,
    )
    ax.set_xticks(xticks)
    ax.set_xticklabels([f"{tick:g}" for tick in xticks], fontsize=fontsize, fontname=fontname)

    height_interval = float(fmt.get("height_tick_interval_m", 1000))
    ylim = ax.get_ylim()
    yticks = np.arange(
        np.ceil(ylim[0] / height_interval) * height_interval,
        np.floor(ylim[1] / height_interval) * height_interval + height_interval * 0.5,
        height_interval,
    )
    ax.set_yticks(yticks)
    ax.set_yticklabels([f"{tick / 1000:g}" for tick in yticks], fontsize=fontsize, fontname=fontname)
    ax.set_xlabel(fmt.get("xlabel", "Time (h)"), fontsize=fontsize, fontname=fontname)
    ax.set_ylabel(fmt.get("ylabel", "Height (km)"), fontsize=fontsize, fontname=fontname)

    title = make_title(
        common.get("title"),
        member_name=member_name,
        times=None,
        heights_m=None,
        angle=angle,
        radius=radius_km,
        variables=draw_variables,
    )
    ax.set_title(title, fontsize=fontsize, fontname=fontname)
    return title


# 功能说明：给时间图的左、右纵轴分别设置标题和颜色；方向轴用方向标签，试验分行模式则用试验名作刻度。
def apply_time_series_yaxis_format(ax, fmt: dict[str, Any], fontsize: int, fontname: str) -> None:
    member_rows = getattr(ax, "_time_series_member_rows", None)
    if member_rows:
        ticks = np.arange(1, len(member_rows) + 1)
        ax.set_yticks(ticks)
        ax.set_yticklabels(member_rows, fontsize=fontsize, fontname=fontname)
        ax.set_ylabel(fmt.get("ylabel", "Member"), fontsize=fontsize, fontname=fontname)
        return

    state = getattr(ax, "_time_series_axis_state", None)
    if not state:
        ax.set_ylabel(fmt.get("ylabel", "Value"), fontsize=fontsize, fontname=fontname)
        return

    left = state.get("left")
    right = state.get("right")
    if left:
        label = fmt.get("left_ylabel") or (fmt.get("ylabel") if not right else None) or left["label"]
        color = fmt.get("left_color") or (left["color"] if right else "black")
        left["axis"].set_ylabel(label, fontsize=fontsize, fontname=fontname, color=color)
        left["axis"].tick_params(axis="y", labelsize=fontsize, labelcolor=color)
        left["axis"].spines["left"].set_color(color)
        if left.get("key") == "direction":
            apply_direction_axis_ticks(left["axis"], fmt, fontsize, fontname, left.get("config"))
        else:
            apply_time_series_numeric_ticks(
                left["axis"], fmt, "left", fontsize, fontname,
                allow_generic=True, variable_config=left.get("config"),
            )
    if right:
        label = fmt.get("right_ylabel") or right["label"]
        color = fmt.get("right_color") or right["color"]
        right["axis"].set_ylabel(label, fontsize=fontsize, fontname=fontname, color=color)
        right["axis"].tick_params(axis="y", labelsize=fontsize, labelcolor=color)
        right["axis"].spines["right"].set_color(color)
        if right.get("key") == "direction":
            apply_direction_axis_ticks(right["axis"], fmt, fontsize, fontname, right.get("config"))
        else:
            apply_time_series_numeric_ticks(
                right["axis"], fmt, "right", fontsize, fontname,
                allow_generic=False, variable_config=right.get("config"),
            )


# 功能说明：按变量设置或左右轴设置安排纵轴范围与刻度，可指定刻度列表、间距或自动整数刻度。
def apply_time_series_numeric_ticks(
    axis,
    fmt: dict[str, Any],
    side: str,
    fontsize: int,
    fontname: str,
    *,
    allow_generic: bool,
    variable_config: dict[str, Any] | None = None,
) -> None:
    config = variable_config or {}

    # 功能说明：为当前纵轴取一个选项，优先用变量设置，其次用指定侧的设置，允许时再用通用设置。
    def option(name: str):
        if config.get(name) is not None:
            return config[name]
        side_key = f"{side}_{name}"
        if fmt.get(side_key) is not None:
            return fmt[side_key]
        return fmt.get(name) if allow_generic else None

    limits = option("ylim")
    if limits is not None:
        axis.set_ylim(limits)

    ticks = option("yticks")
    if ticks is not None:
        values = np.asarray(ticks, dtype=float)
        axis.set_yticks(values)
        axis.set_yticklabels([f"{value:g}" for value in values], fontsize=fontsize, fontname=fontname)
        if limits is None and values.size:
            axis.set_ylim(float(values[0]), float(values[-1]))
        return

    interval = option("ytick_interval")
    start = option("ytick_start")
    end = option("ytick_end")
    if interval is not None:
        interval = float(interval)
        if interval <= 0:
            raise ValueError("time_series ytick_interval must be positive")
        lower, upper = axis.get_ylim()
        start_value = float(start) if start is not None else np.ceil(lower / interval) * interval
        end_value = float(end) if end is not None else np.floor(upper / interval) * interval
        if end_value >= start_value:
            values = np.arange(start_value, end_value + interval * 0.5, interval)
            axis.set_yticks(values)
            axis.set_yticklabels([f"{value:g}" for value in values], fontsize=fontsize, fontname=fontname)
            if start is not None or end is not None:
                axis.set_ylim(float(start if start is not None else lower), float(end if end is not None else upper))
        return

    if bool(option("integer_yticks") if option("integer_yticks") is not None else True):
        axis.yaxis.set_major_locator(MaxNLocator(nbins=int(option("ytick_nbins") or 6), integer=True))


# 功能说明：把方向纵轴的角度刻度标为 US、RS、DS、LS 等标签，也支持用户自定义刻度文字和范围。
def apply_direction_axis_ticks(
    axis,
    fmt: dict[str, Any],
    fontsize: int,
    fontname: str,
    variable_config: dict[str, Any] | None = None,
) -> None:
    if fmt.get("direction_ticks", "auto") is False:
        return
    config = variable_config or {}
    ticks = config.get("direction_tick_values", fmt.get("direction_tick_values", [0, 90, 180, 270, 360]))
    labels = config.get("direction_tick_labels", fmt.get("direction_tick_labels", ["US", "RS", "DS", "LS", "US"]))
    axis.set_yticks(ticks)
    axis.set_yticklabels(labels, fontsize=fontsize, fontname=fontname)
    limits = config.get("ylim", config.get("direction_ylim", fmt.get("ylim")))
    if limits is None:
        axis.set_ylim(min(ticks), max(ticks))
    else:
        axis.set_ylim(limits)


# 功能说明：为图片添加说明颜色与数值关系的颜色条，以及说明箭头长度对应多大风速的参考箭头。
def set_cb_q(
    cf,
    q,
    fig,
    level_min: float | None = None,
    level_max: float | None = None,
    interval: float | None = None,
    title: str = "",
    l: float | None = None,
    b: float | None = None,
    w: float | None = None,
    h: float | None = None,
    orientation: str = "vertical",
    fontsize: int = 18,
    *,
    colorbar_config: dict[str, Any] | None = None,
    quiver_key_config: dict[str, Any] | None = None,
):
    cb_cfg = colorbar_config or {
        "enabled": cf is not None,
        "level_min": level_min,
        "level_max": level_max,
        "interval": interval,
        "title": title,
        "position": [
            0.94 if l is None else l,
            0.14 if b is None else b,
            0.012 if w is None else w,
            0.72 if h is None else h,
        ],
        "orientation": orientation,
        "fontsize": fontsize,
    }

    if cf is not None and cb_cfg.get("enabled", True):
        cbar_ax = fig.add_axes(cb_cfg.get("position", [0.94, 0.14, 0.012, 0.72]))
        cb = fig.colorbar(cf, cax=cbar_ax, orientation=cb_cfg.get("orientation", "vertical"))
        tick_min = cb_cfg.get("level_min")
        tick_max = cb_cfg.get("level_max")
        tick_interval = cb_cfg.get("interval")
        cb_fontsize = cb_cfg.get("fontsize", fontsize)
        if tick_min is not None and tick_max is not None and tick_interval is not None:
            ticks = resolve_colorbar_ticks(tick_min, tick_max, tick_interval)
            cb.set_ticks(ticks)
            cb.set_ticklabels([f"{tick:g}" for tick in ticks], fontsize=cb_fontsize, fontname="Times New Roman")
        cb.ax.tick_params(labelsize=cb_fontsize)
        cb.set_label(cb_cfg.get("title", title), fontdict={"family": "Times New Roman", "size": cb_fontsize})

    q_cfg = quiver_key_config or {}
    if q is not None and q_cfg.get("enabled", True):
        cb_position = cb_cfg.get("position", [0.94, 0.14, 0.012, 0.72])
        default_position = [cb_position[0] + cb_position[2] * 0.5, min(0.98, cb_position[1] + cb_position[3] + 0.06)]
        position = q_cfg.get("position", default_position)
        q.axes.quiverkey(
            q,
            position[0],
            position[1],
            q_cfg.get("U", 10),
            q_cfg.get("label", "10 m/s"),
            labelpos=q_cfg.get("labelpos", "E"),
            coordinates=q_cfg.get("coordinates", "figure"),
            fontproperties={"family": "Times New Roman", "size": q_cfg.get("fontsize", fontsize)},
        )


# 功能说明：按起止值和间隔生成颜色条刻度，标签太密时减少标签数量，保留原来的数值范围。
def resolve_colorbar_ticks(
    tick_min: float, tick_max: float, tick_interval: float, *, max_ticks: int = 11
) -> np.ndarray:
    """Return readable ticks while preserving the requested colorbar range."""
    if tick_interval <= 0 or tick_max <= tick_min:
        return np.asarray([tick_min, tick_max], dtype=float)
    ticks = np.arange(tick_min, tick_max + tick_interval * 0.001, tick_interval)
    if len(ticks) <= max_ticks:
        return ticks
    # The color normalization and contour levels remain unchanged; only labels
    # are thinned for legibility.
    return np.linspace(tick_min, tick_max, max_ticks)


# 功能说明：确定颜色条在画布中的位置，并给主图留出间隔，减少颜色条与图内容挤在一起的情况。
def colorbar_layout(
    config: dict[str, Any] | None, *, has_colorbar: bool, orientation: str = "vertical"
) -> dict[str, float]:
    """Return a colorbar position and the right edge reserved for plot axes."""
    cfg = config or {}
    position = cfg.get("position")
    if not isinstance(position, (list, tuple)) or len(position) != 4:
        position = [0.93, 0.14, 0.018, 0.72]
    left, bottom, width, height = (float(value) for value in position)
    # Keep a visible gutter between the last panel and the colorbar.
    axes_right = max(0.5, left - 0.025) if has_colorbar and cfg.get("enabled", True) else 0.97
    return {"left": left, "bottom": bottom, "width": width, "height": height, "axes_right": axes_right}


# 功能说明：确定图的参考中心位置：默认网格中点，可读取指定时刻的中心文件，最后允许显式中心编号覆盖。
def resolve_center(
    *,
    data_root: str | Path,
    member_name: str,
    domain: int,
    times: list[int | float],
    grid_shape: tuple[int, int] | None,
    center_enabled: bool,
    center_path: str | Path | None,
    center_height_m: int | float | None,
    center_index: Any,
) -> tuple[float, float]:
    if grid_shape is None:
        default_y = default_x = 0.0
    else:
        default_y = (grid_shape[0] - 1) / 2.0
        default_x = (grid_shape[1] - 1) / 2.0

    center_y, center_x = default_y, default_x
    if center_enabled:
        found = load_center_grid(
            data_root=data_root,
            member_name=member_name,
            domain=domain,
            time_hour=times[0] if times else 0,
            height_m=center_height_m,
            center_path=center_path,
        )
        if found is not None:
            center_y, center_x = found
        else:
            warnings.warn("中心已启用但未找到，使用 domain 网格中点作为参考中心")

    if center_index is not None:
        values = np.atleast_1d(center_index).astype(float)
        if values.size == 1:
            center_y = center_x = float(values[0])
        else:
            center_y, center_x = float(values[0]), float(values[1])

    return center_y, center_x


# 功能说明：取得南北方向格点间距，未配置或读取失败时采用东西方向的间距。
def _get_dy_km(domain: int, domain_config: dict[int, dict[str, Any]], dx_km: float) -> float:
    try:
        return float((domain_config or {}).get(domain, {}).get("dy_km") or dx_km)
    except Exception:
        return float(dx_km)


HORIZONTAL_RMW_SOURCES = ("rmw", *RMW_BY_HEIGHT_SOURCES)


# 功能说明：水平图 RMW 圈的数据源（2026-10-26）：rmw = 最低模式层（默认，含义不变）；
# rmw_by_height / rmw_by_layer_center = 指定高度 rmw_height_m 的 RMW（与时间—半径图同名选项一致）。
def horizontal_rmw_source(fmt: dict[str, Any]) -> str:
    source = str(fmt.get("rmw_source") or "rmw")
    if source not in HORIZONTAL_RMW_SOURCES:
        raise ValueError(f"horizontal.rmw_source 不支持: {source}")
    return source


# 功能说明：逐高度数据源返回所选高度（m）；最低模式层返回 None。
def horizontal_rmw_height_m(fmt: dict[str, Any]) -> float | None:
    if horizontal_rmw_source(fmt) == "rmw":
        return None
    return float(fmt.get("rmw_height_m", 1000))


# 功能说明：从 processed_v3（rmw.nc / rmw_by_height.nc / rmw_by_layer_center.nc）读取一个成员、一个时次的 RMW（km）；
# 没有该切片时返回 None。
def read_horizontal_rmw_km(data_root, member_name: str, domain: int, time_hour: float, fmt: dict[str, Any]) -> float | None:
    source = horizontal_rmw_source(fmt)
    try:
        reader = DataManager(data_root).processed_reader
        reference = reader.resolve(
            member_name=member_name, domain=int(domain), variable=source,
            time_hour=float(time_hour), height_m=horizontal_rmw_height_m(fmt),
        )
    except (OSError, ValueError, KeyError):
        reference = None
    if reference is None:
        return None
    sample = np.asarray(reader.load(reference), dtype=float).reshape(-1)
    return float(sample[0]) if sample.size else float("nan")


# 功能说明：按坐标系把 RMW（km）换成坐标轴单位后画圆；可选在子图左下角标出数值。
def _draw_rmw_circle(ax, value_km: float, center_x, center_y, dx_km, fmt, label: str = "") -> None:
    if fmt.get("coordinate_system") == "center_km":
        radius = value_km if fmt.get("rmw_units", "km") == "km" else value_km * dx_km
    else:
        radius = value_km / dx_km if fmt.get("rmw_units", "km") == "km" else value_km
    ax.add_patch(
        patches.Circle(
            (center_x, center_y),
            radius=radius,
            edgecolor=fmt.get("rmw_color", "darkgreen"),
            linewidth=fmt.get("rmw_linewidth", 3),
            linestyle=fmt.get("rmw_linestyle", "-"),
            facecolor="none",
            zorder=fmt.get("rmw_zorder", 8),
            label=label or None,
        )
    )
    if fmt.get("rmw_annotate", False):
        text = f"{label} = {value_km:.0f} km" if label else f"RMW = {value_km:.0f} km"
        ax.text(
            0.03, 0.03, text, transform=ax.transAxes, ha="left", va="bottom",
            fontsize=fmt.get("rmw_annotate_fontsize", 12), zorder=fmt.get("rmw_zorder", 8) + 1,
            bbox={"boxstyle": "round,pad=0.2", "fc": "white", "ec": "none", "alpha": 0.8},
        )


# 功能说明：开启时读取最大风速半径并以台风中心为圆心画圆；按当前坐标是公里还是格点调整半径。
# 2026-10-26：读 processed_v3（每个子图用自己的成员与时次）；2026-11-01 起不再退回旧版 rmw/<时次>.npy、rmw.txt，
# 只有显式给了 rmw_path 才读该文件。
def maybe_draw_rmw(ax, data_root, member_name, domain, times, center_x, center_y, dx_km, fmt):
    if not fmt.get("draw_rmw", False):
        return
    source = horizontal_rmw_source(fmt)
    height_m = horizontal_rmw_height_m(fmt)
    if source == "rmw":
        label = "RMW (lowest level)"
    else:
        label = f"RMW ({height_m / 1000:g} km, {rmw_center_label(source)})"
    time_hour = float(times[0]) if times else 0.0
    if not fmt.get("rmw_path"):
        value = read_horizontal_rmw_km(data_root, member_name, domain, time_hour, fmt)
        if value is not None:
            if np.isfinite(value):
                _draw_rmw_circle(ax, value, center_x, center_y, dx_km, fmt, label)
            else:
                warnings.warn(f"RMW 为缺测值：{member_name} {time_hour:g} h（{source}）")
            return
        warnings.warn(f"RMW 已启用但 processed_v3 中没有 {source}：{member_name} {time_hour:g} h" + ("" if source == "rmw" else f"，{height_m:g} m"))
        return
    path = Path(fmt["rmw_path"])
    if not path.exists():
        warnings.warn(f"RMW 已启用但文件不存在: {path}")
        return
    value = _land_distance_value_from_path(path, time_hour)
    if not np.isfinite(value):
        warnings.warn(f"RMW 无法读取当前时次: {path}")
        return
    _draw_rmw_circle(ax, value, center_x, center_y, dx_km, fmt, label)


# 功能说明：开启时读取中心到陆地的距离，在中心东侧或西侧画一条相应距离的竖直参考线。
def maybe_draw_land_distance(ax, data_root, member_name, domain, times, center_x, dx_km, fmt):
    if not fmt.get("draw_land_distance", False):
        return
    time_hour = float(times[0]) if times else 0.0
    if fmt.get("land_distance_path"):
        path = Path(fmt["land_distance_path"])
        if not path.exists():
            warnings.warn(f"land distance 已启用但文件不存在: {path}")
            return
        value = _land_distance_value_from_path(path, time_hour)
    else:
        value = read_scalar(processed_reader(data_root), member_name, domain, "center_land_distance", time_hour)
        if value is None:
            warnings.warn(f"land distance 已启用但 processed_v3 中没有 center_land_distance：{member_name} {time_hour:g} h")
            return
    if not np.isfinite(value):
        warnings.warn(f"land distance 为缺测值：{member_name} {time_hour:g} h")
        return
    if fmt.get("coordinate_system") == "center_km":
        offset = value if fmt.get("land_distance_units", "km") == "km" else value * dx_km
    else:
        offset = value / dx_km if fmt.get("land_distance_units", "km") == "km" else value
    side = fmt.get("land_distance_side", "west")
    x = center_x - offset if side == "west" else center_x + offset
    ax.axvline(
        x,
        color=fmt.get("land_distance_color", "black"),
        linestyle=fmt.get("land_distance_linestyle", "--"),
        linewidth=fmt.get("land_distance_linewidth", 2),
        zorder=fmt.get("land_distance_zorder", 9),
    )


# 功能说明：读取用户在格式里显式指定的数据文件（.npy 或文本表）。
def _load_explicit_table(path: Path) -> np.ndarray:
    if path.suffix == ".npy":
        return np.load(path, allow_pickle=True)
    return np.loadtxt(path)


# 功能说明：从距离或半径数据表中取指定时刻的单个数值，兼容带时间列、按行排列和单值文件。
def _land_distance_value_from_path(path: Path, time_hour: int | float) -> float:
    data = np.asarray(_load_explicit_table(path), dtype=float)
    if data.ndim >= 2 and data.shape[1] >= 2:
        matches = np.where(np.isclose(data[:, 0].astype(float), float(time_hour)))[0]
        if matches.size:
            return float(data[int(matches[0]), 1])
        return float("nan")
    row = int(time_hour)
    return float(data[row] if data.ndim > 0 and row < data.shape[0] else data.flat[0])


# 功能说明：读取指定高度的涡旋中心，把它转换到当前水平图坐标并画一个醒目标记，用于观察高低层中心偏移。
def maybe_draw_upper_center(ax, data_root, member_name, domain, times, fmt, ref_center_y=None, ref_center_x=None, dx_km=1.0, dy_km=1.0):
    height_m = fmt.get("draw_center_height_m")
    if not height_m:
        return
    found = load_center_grid(
        data_root=data_root,
        member_name=member_name,
        domain=domain,
        time_hour=times[0] if times else 0,
        height_m=height_m,
    )
    if found is None:
        return
    y, x = found
    # Upper-center files store raw grid indices.  Convert them to the same
    # center-relative km coordinates as the horizontal data before plotting.
    if fmt.get("coordinate_system") == "center_km" and ref_center_y is not None and ref_center_x is not None:
        x = (float(x) - float(ref_center_x)) * float(dx_km)
        y = (float(y) - float(ref_center_y)) * float(dy_km)
    ax.scatter(x, y, marker=fmt.get("center_marker", "o"), color=fmt.get("center_color", "red"), s=fmt.get("center_size", 120), zorder=12)


# 功能说明：开启时读取高低层风的差异，即垂直风切变，在中心位置画方向与强度箭头，并可加长度说明。
def maybe_draw_shear_arrow(ax, data_root, member_name, domain, times, center_x, center_y, fmt):
    if not fmt.get("inner_core_vertical_wind_shear", False):
        return
    # 2026-11-01：旧版 shear_total.npy 只在 processed_v2 里有，不再自动查找；需要时在格式里给 shear_path。
    path = Path(fmt["shear_path"]) if fmt.get("shear_path") else None
    if path is None or not path.exists():
        warnings.warn(f"内核垂直风切变已启用但没有可读文件（v3 中没有对应变量，需在格式里指定 shear_path）: {path}")
        return
    try:
        loaded = np.load(path, allow_pickle=True)
        if isinstance(loaded, np.ndarray) and loaded.dtype == object:
            loaded = loaded.item()
        row = int(times[0]) if times else 0
        shear_u, shear_v = extract_shear_components(loaded, member_name, row)
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"无法读取内核垂直风切变: {path}, {exc}")
        return
    q = ax.quiver(center_x, center_y, shear_u, shear_v, color=fmt.get("shear_color", "darkgreen"), scale=fmt.get("shear_scale", 50), width=0.005, zorder=13)
    if fmt.get("shear_key", True):
        ax.quiverkey(q, X=0.9, Y=0.95, U=fmt.get("shear_key_u", 5), label=fmt.get("shear_key_label", "5 m/s"), labelpos="E", coordinates="axes")


# 功能说明：从不同格式的风切变记录中提取东西和南北分量；若保存的是各层风，就用首末层相减得到差值。
def extract_shear_components(loaded, member_name: str, row: int) -> tuple[float, float]:
    record = loaded.get(member_name, loaded) if isinstance(loaded, dict) else loaded
    if isinstance(record, dict) and "u_interplevel_total" in record and "v_interplevel_total" in record:
        u = np.asarray(record["u_interplevel_total"])[row]
        v = np.asarray(record["v_interplevel_total"])[row]
        return float(u[0] - u[-1]), float(v[0] - v[-1])
    if isinstance(record, dict) and "u" in record and "v" in record:
        return float(np.asarray(record["u"])[row]), float(np.asarray(record["v"])[row])
    arr = np.asarray(record, dtype=float)
    if arr.ndim == 2 and arr.shape[1] >= 2:
        return float(arr[row, 0]), float(arr[row, 1])
    raise ValueError("不支持的 shear 文件结构")


# 功能说明：把高层中心相对低层中心的位移随时间连成轨迹，可平滑、按时间渐变着色，并标记起点、终点和选定时刻。
def maybe_draw_tilt_track(ax, data_root, member_name, domain, times, center_x, center_y, dx_km, dy_km, fmt, *, track=None, norm=None):
    """Overlay the upper-minus-lower vortex-center displacement through time."""
    if not fmt.get("draw_tilt_track", False):
        return
    try:
        if track is None:
            track = load_tilt_track(data_root, member_name, domain, fmt)
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"无法读取涡旋倾斜轨迹: {member_name} d0{domain}, {exc}")
        return
    if track["time"].size == 0:
        warnings.warn(f"涡旋倾斜轨迹没有可用时次: {member_name} d0{domain}")
        return

    tilt_x = track["tilt_x"].copy()
    tilt_y = track["tilt_y"].copy()
    sigma = float(fmt.get("tilt_track_smooth_sigma", 1.0) or 0.0)
    if sigma > 0 and tilt_x.size > 1:
        from scipy.ndimage import gaussian_filter1d

        tilt_x = gaussian_filter1d(tilt_x, sigma=sigma)
        tilt_y = gaussian_filter1d(tilt_y, sigma=sigma)

    if fmt.get("coordinate_system") == "center_km":
        x = center_x + tilt_x
        y = center_y + tilt_y
    else:
        x = center_x + tilt_x / float(dx_km)
        y = center_y + tilt_y / float(dy_km)

    time_values = track["time"]
    use_gradient = bool(fmt.get("tilt_track_time_gradient", True)) and x.size > 1
    artist = None
    if use_gradient:
        points = np.column_stack((x, y)).reshape(-1, 1, 2)
        segments = np.concatenate((points[:-1], points[1:]), axis=1)
        time_mid = 0.5 * (time_values[:-1] + time_values[1:])
        vmin = float(np.nanmin(time_values))
        vmax = float(np.nanmax(time_values))
        if np.isclose(vmin, vmax):
            vmax = vmin + 1.0
        artist = LineCollection(
            segments,
            cmap=fmt.get("tilt_track_cmap", "viridis"),
            norm=norm if norm is not None else Normalize(vmin=vmin, vmax=vmax),
            linestyles=fmt.get("tilt_track_linestyle", "-"),
            linewidths=float(fmt.get("tilt_track_linewidth", 3.0)),
            alpha=float(fmt.get("tilt_track_alpha", 1.0)),
            zorder=int(fmt.get("tilt_track_zorder", 16)),
        )
        artist.set_array(time_mid)
        ax.add_collection(artist)
    else:
        artist = ax.plot(
            x,
            y,
            color=fmt.get("tilt_track_color", "C3"),
            linewidth=float(fmt.get("tilt_track_linewidth", 3.0)),
            linestyle=fmt.get("tilt_track_linestyle", "-"),
            alpha=float(fmt.get("tilt_track_alpha", 1.0)),
            zorder=int(fmt.get("tilt_track_zorder", 16)),
            label=fmt.get("tilt_track_label", "Vortex tilt track"),
        )[0]

    marker_color = fmt.get("tilt_track_marker_color", fmt.get("tilt_track_color", "C3"))
    marker_edge = fmt.get("tilt_track_marker_edgecolor", "black")
    ax.scatter(
        x[0], y[0], marker=fmt.get("tilt_track_start_marker", "s"),
        s=float(fmt.get("tilt_track_start_size", 70)), color=marker_color,
        edgecolor=marker_edge, zorder=18,
    )
    ax.scatter(
        x[-1], y[-1], marker=fmt.get("tilt_track_end_marker", "*"),
        s=float(fmt.get("tilt_track_end_size", 140)), color=marker_color,
        edgecolor=marker_edge, zorder=18,
    )

    for mark_hour in fmt.get("tilt_track_mark_times", []):
        index = int(np.argmin(np.abs(time_values - float(mark_hour))))
        tolerance = float(fmt.get("tilt_track_mark_tolerance_h", np.inf))
        if abs(float(time_values[index]) - float(mark_hour)) > tolerance:
            continue
        ax.scatter(
            x[index], y[index], s=float(fmt.get("tilt_track_mark_size", 45)),
            facecolors="none", edgecolors=marker_color, linewidth=1.3, zorder=19,
        )
        if fmt.get("tilt_track_annotate_times", True):
            ax.annotate(
                f"{float(time_values[index]):g} h", (x[index], y[index]),
                xytext=tuple(fmt.get("tilt_track_text_offset", [5, 7])),
                textcoords="offset points", fontsize=fmt.get("tilt_track_fontsize", 10),
                color=marker_color, zorder=20,
            )

    if use_gradient and fmt.get("tilt_track_colorbar", False) and not getattr(ax, "_tilt_track_colorbar", False):
        colorbar = ax.figure.colorbar(
            artist,
            ax=ax,
            fraction=float(fmt.get("tilt_track_colorbar_fraction", 0.046)),
            pad=float(fmt.get("tilt_track_colorbar_pad", 0.04)),
        )
        colorbar.set_label(fmt.get("tilt_track_colorbar_label", "Time (h)"))
        ax._tilt_track_colorbar = True


# 功能说明：将多个试验的独立倾斜轨迹绘于同一相对距离坐标，共用时间色标并添加试验图例。
def draw_tilt_track_members(ax, data_root, members, domain, domain_config, config, member_time_limits):
    fmt = dict(config.get("horizontal", {}))
    fmt["coordinate_system"] = "center_km"
    tracks = []
    for member in members:
        track = load_tilt_track(data_root, member, domain, fmt)
        limit = member_time_limit(member_time_limits, member)
        if limit is not None:
            keep = track["time"] <= limit
            track = {key: values[keep] for key, values in track.items()}
        if not track["time"].size:
            raise ValueError(f"涡旋倾斜轨迹没有可用时次: {member} d0{domain}")
        tracks.append(track)
    times = np.unique(np.concatenate([track["time"] for track in tracks]))
    norm = Normalize(float(times.min()), float(times.max()) if times.max() > times.min() else float(times.min()) + 1)
    # 独立轨迹以低层中心为原点，不读取背景图的中心、RMW 等辅助数据。
    axes_fmt = {
        key: value for key, value in fmt.items()
        if key in ("xlim_km", "tick_interval_km", "radius_circles_km") or key.startswith("radius_circle_")
    }
    axes_fmt["coordinate_system"] = "center_km"
    set_format0(ax, ", ".join(member_display_name(member) for member in members), domain, times.tolist(), None, {},
                data_root=data_root, domain_config=domain_config,
                config={"common": config.get("common", {}), "horizontal": axes_fmt})
    handles = []
    styles = ["-", "--", "-.", ":"]
    for index, (member, track) in enumerate(zip(members, tracks)):
        member_fmt = dict(fmt)
        color = (fmt.get("tilt_track_member_colors") or {}).get(member, resolve_experiment_color(member, index, fmt) or f"C{index % 10}")
        style = (fmt.get("tilt_track_member_linestyles") or {}).get(
            member, scheme_linestyle(member, fmt) or styles[index % len(styles)])
        member_fmt.update(tilt_track_color=color, tilt_track_marker_color=color,
                          tilt_track_linestyle=style, tilt_track_label=member_display_name(member))
        maybe_draw_tilt_track(ax, data_root, member, domain, track["time"].tolist(),
                              0, 0, 1, 1, member_fmt, track=track, norm=norm)
        handles.append(Line2D([], [], color=color, linestyle=style, label=member_display_name(member),
                              linewidth=float(fmt.get("tilt_track_linewidth", 3))))
    ax.legend(handles=handles)


# 功能说明：读取东西、南北两个倾斜位移序列，保留它们共同拥有且符合时间选择的时刻，返回对应的二维位移。
def load_tilt_track(data_root, member_name: str, domain: int, fmt: dict[str, Any]) -> dict[str, np.ndarray]:
    reader = DataManager(data_root).processed_reader
    component_names = tilt_track_components(
        fmt, exists=lambda name: reader.variable_path(member_name, int(domain), name).is_file(),
    )
    # 按选定范围读对应分量；缺文件时直接报缺失，不改读其他范围。
    v3_dir = _tilt_v3_directory(data_root, member_name, domain, component_names)
    x_by_time = _load_scalar_series_netcdf(v3_dir / f"{component_names[0]}.nc")
    y_by_time = _load_scalar_series_netcdf(v3_dir / f"{component_names[1]}.nc")
    common_times = sorted(set(x_by_time).intersection(y_by_time))

    selected_times = fmt.get("tilt_track_times")
    if selected_times is not None:
        requested = np.asarray(selected_times if isinstance(selected_times, (list, tuple)) else [selected_times], dtype=float)
        common_times = [time for time in common_times if np.any(np.isclose(requested, time))]
    time_range = fmt.get("tilt_track_time_range")
    if time_range is not None:
        start, end = float(time_range[0]), float(time_range[1])
        common_times = [time for time in common_times if start <= time <= end]

    return {
        "time": np.asarray(common_times, dtype=float),
        "tilt_x": np.asarray([x_by_time[time] for time in common_times], dtype=float),
        "tilt_y": np.asarray([y_by_time[time] for time in common_times], dtype=float),
    }


# 功能说明：返回倾斜分量所在的 processed_v3 目录（旧 v1/v2 根目录改指 v3）；缺文件由读取时明确报错。
def _tilt_v3_directory(
    data_root, member_name: str, domain: int,
    component_names: tuple[str, str] = ("tilt_x", "tilt_y"),
) -> Path:
    # 倾斜分量都依赖层中心，与 tilt_x 在同一目录（非 base 中心方案在 centers/<方案>/ 下）。
    return DataManager(data_root).processed_reader.variable_path(member_name, int(domain), component_names[0]).parent


# 功能说明：读取 processed_v3 标量 NetCDF（time_minutes, data, completed），按小时返回已完成且有限的值。
def _load_scalar_series_netcdf(path: Path) -> dict[float, float]:
    import netCDF4

    result: dict[float, float] = {}
    if not path.is_file():
        raise FileNotFoundError(f"缺少 processed_v3 文件：{path}")
    with netCDF4.Dataset(path) as dataset:
        minutes = np.asarray(dataset.variables["time_minutes"][:], dtype=float)
        values = np.ma.filled(np.ma.asarray(dataset.variables["data"][:], dtype=float), np.nan)
        if "completed" in dataset.variables:
            completed = np.ma.filled(np.ma.asarray(dataset.variables["completed"][:]), 0).astype(bool)
        else:
            completed = np.ones(values.shape, dtype=bool)
    for minute, value, done in zip(minutes, values.reshape(-1), completed.reshape(-1)):
        if done and np.isfinite(value):
            result[float(minute) / 60.0] = float(value)
    return result


# 功能说明：开启时读取云分类格点，用不同符号标出层状云和不同强度对流类别，并按间隔抽取以免过密。
def maybe_draw_cloud_classification(ax, data_root, member_name, domain, times, fmt):
    if not fmt.get("cloud_classification", False):
        return
    time = float(times[0]) if times else 0.0
    data = read_slice(processed_reader(data_root), member_name, domain, "classification", time)
    if data is None:
        warnings.warn(f"云分类点已启用但 processed_v3 中没有 classification：{member_name} {time:g} h")
        return
    stride = int(fmt.get("cloud_classification_stride", 30))
    class_markers = [(2, "."), (3, "x"), (4, "+"), (5, "^")]
    for value, marker in class_markers:
        ys, xs = np.where(data == value)
        if ys.size:
            ax.scatter(xs[::stride], ys[::stride], marker=marker, color="k", s=15, zorder=14)


# 功能说明：按指定方向和起止距离，从参考中心向外画线段，用于标示研究扇区的方向边界。
def maybe_draw_direction_lines(ax, center_x, center_y, dx_km, fmt):
    angles = fmt.get("direction_angle")
    if angles is None:
        return
    angles = np.atleast_1d(angles).astype(float)
    starts = broadcast_values(fmt.get("direction_start", 0), angles.size)
    ends = broadcast_values(fmt.get("direction_end"), angles.size)
    if ends is None:
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        default_end = np.hypot(xlim[1] - xlim[0], ylim[1] - ylim[0]) * dx_km
        ends = np.full(angles.size, default_end)
    for angle, start_km, end_km in zip(angles, starts, ends):
        theta = np.radians(angle)
        scale = 1.0 if fmt.get("coordinate_system") == "center_km" else 1.0 / dx_km
        sx = center_x + start_km * scale * np.cos(theta)
        sy = center_y + start_km * scale * np.sin(theta)
        ex = center_x + end_km * scale * np.cos(theta)
        ey = center_y + end_km * scale * np.sin(theta)
        ax.plot([sx, ex], [sy, ey], color=fmt.get("direction_color", "black"), linewidth=2, zorder=15)


# 功能说明：按半径和起止角画圆弧；跨越0度的圆弧拆成两段，缺少必要参数时提示。
def maybe_draw_arcs(ax, center_x, center_y, dx_km, fmt):
    radius = fmt.get("arc_radius")
    start = fmt.get("arc_start_angle")
    end = fmt.get("arc_end_angle")
    if radius is None and start is None and end is None:
        return
    if radius is None or start is None or end is None:
        raise ValueError("arc_radius, arc_start_angle, arc_end_angle 必须同时设置")
    radii = np.atleast_1d(radius).astype(float)
    starts = broadcast_values(start, radii.size)
    ends = broadcast_values(end, radii.size)
    for radius_km, start_angle, end_angle in zip(radii, starts, ends):
        radius_grid = radius_km if fmt.get("coordinate_system") == "center_km" else radius_km / dx_km
        if end_angle >= start_angle:
            ax.add_patch(patches.Arc((center_x, center_y), 2 * radius_grid, 2 * radius_grid, theta1=start_angle, theta2=end_angle, color="black", linewidth=2, zorder=15))
        else:
            ax.add_patch(patches.Arc((center_x, center_y), 2 * radius_grid, 2 * radius_grid, theta1=start_angle, theta2=360, color="black", linewidth=2, zorder=15))
            ax.add_patch(patches.Arc((center_x, center_y), 2 * radius_grid, 2 * radius_grid, theta1=0, theta2=end_angle, color="black", linewidth=2, zorder=15))


# 功能说明：在时间—半径图叠加中心距陆地距离曲线（processed_v3 的 center_land_distance；显式给 land_distance_path 时读该文件）。
def maybe_draw_time_radius_land_distance(ax, data_root, member_name, domain, times, fmt):
    if not fmt.get("draw_land_distance", False):
        return
    configured_members = fmt.get("land_distance_members")
    if configured_members is not None and member_name not in configured_members:
        return
    if fmt.get("land_distance_path"):
        path = Path(fmt["land_distance_path"])
        if not path.exists():
            warnings.warn(f"land distance 已启用但文件不存在: {path}")
            return
        distance = np.asarray(_load_explicit_table(path), dtype=float).reshape(-1)
        ax.plot(distance, np.arange(distance.size), color="k", linestyle="--", linewidth=1.5, label="land distance")
        return
    distance_times, distance_values = read_scalar_series(
        processed_reader(data_root), member_name, domain, "center_land_distance", times
    )
    if not distance_values.size:
        warnings.warn(f"land distance 已启用但 processed_v3 中没有 center_land_distance：{member_name}")
        return
    ax.plot(distance_values, distance_times, color="k", linestyle="--", linewidth=1.5, label="land distance")


# 功能说明：读取各时刻的单值半径诊断并画参考曲线，可用于最大风速半径或34节风半径，并按配置平滑。
def maybe_draw_time_radius_radius_diagnostic(
    ax,
    data_root,
    member_name,
    domain,
    times,
    fmt,
    *,
    option: str,
    variable: str,
    label: str,
    color: str,
    linestyle: str,
    linewidth: float,
):
    """Draw a time-radius reference curve from a per-time scalar diagnostic."""
    if not fmt.get(option, False):
        return

    values: list[float] = []
    available_times: list[float] = []
    rmw_source = fmt.get("rmw_source") if variable == "rmw" else None
    if rmw_source not in (None, "", "rmw"):
        # 从逐高度 RMW（processed_v3）读取指定高度的逐时次值。
        rmw_source = str(rmw_source)
        if rmw_source not in RMW_BY_HEIGHT_SOURCES:
            raise ValueError(f"time_radius.rmw_source 不支持: {rmw_source}")
        rmw_height = float(fmt.get("rmw_height_m", 1000))
        reader = DataManager(data_root).processed_reader
        for time_hour in times:
            reference = reader.resolve(
                member_name=member_name, domain=domain, variable=rmw_source,
                time_hour=time_hour, height_m=rmw_height,
            )
            if reference is None:
                continue
            sample = np.asarray(reader.load(reference), dtype=float).reshape(-1)
            if sample.size and np.isfinite(sample[0]):
                values.append(float(sample[0]))
                available_times.append(float(time_hour))
        label = f"RMW ({rmw_height / 1000:g} km, {rmw_center_label(rmw_source)})"
    else:
        series_times, series_values = read_scalar_series(
            processed_reader(data_root), member_name, domain, variable, times
        )
        values.extend(float(value) for value in series_values)
        available_times.extend(float(value) for value in series_times)

    plot_values = np.asarray(values, dtype=float)
    smooth_sigma = float(fmt.get("overlay_smooth_sigma", 0) or 0)
    if smooth_sigma > 0 and plot_values.size > 1:
        from scipy.ndimage import gaussian_filter1d

        plot_values = gaussian_filter1d(plot_values, sigma=smooth_sigma, mode="nearest")

    if plot_values.size:
        ax.plot(
            plot_values,
            available_times,
            color=fmt.get(f"{variable}_color", color),
            linestyle=fmt.get(f"{variable}_linestyle", linestyle),
            linewidth=float(fmt.get(f"{variable}_linewidth", linewidth)),
            label=label,
        )


# 功能说明：读取本试验或通用的登陆小时，在以时间为纵轴的图上画水平参考线。
def maybe_draw_landfall_time(ax, fmt, member_name: str | None = None):
    """Draw a horizontal reference line at the configured landfall forecast hour."""
    landfall_times_h = fmt.get("landfall_times_h")
    if isinstance(landfall_times_h, dict) and member_name is not None:
        landfall_time_h = landfall_times_h.get(member_name)
    else:
        landfall_time_h = fmt.get("landfall_time_h")
    if landfall_time_h is None or landfall_time_h == "":
        return
    ax.axhline(
        float(landfall_time_h),
        color=fmt.get("landfall_line_color", "crimson"),
        linestyle=fmt.get("landfall_line_linestyle", "--"),
        linewidth=float(fmt.get("landfall_linewidth", 2)),
        zorder=fmt.get("landfall_line_zorder", 20),
    )


# 功能说明：在主图角落创建一个小坐标框，用配置给定的风分量画环境风示意箭头。
def maybe_draw_inset_wind(ax, fmt):
    inset = fmt.get("inset_wind", False)
    if not inset:
        return
    cfg = inset if isinstance(inset, dict) else {}
    inset_ax = ax.inset_axes(cfg.get("position", [0.8, 0.85, 0.15, 0.1]))
    inset_ax.quiver(0.0, 0.0, cfg.get("u", -10), cfg.get("v", 0), angles="xy", scale_units="xy", scale=cfg.get("scale", 2), color=cfg.get("color", "k"), width=0.02)
    inset_ax.set_xticks([])
    inset_ax.set_yticks([])
    inset_ax.set_xlim(cfg.get("xlim", [-6, 5]))
    inset_ax.set_ylim(cfg.get("ylim", [-2, 2]))


# 功能说明：开启时读取风切变方向序列，按文件格式转换角度并平滑，叠加到时间—方向图上。
def maybe_draw_time_azimuth_shear(ax, data_root, member_name, domain, times, fmt):
    if not fmt.get("inner_core_vertical_wind_shear", False):
        return
    path = Path(fmt["shear_path"]) if fmt.get("shear_path") else None
    if path is None or not path.exists():
        warnings.warn(f"内核垂直风切变已启用但没有可读文件（v3 中没有对应变量，需在格式里指定 shear_path）: {path}")
        return
    try:
        loaded = np.load(path, allow_pickle=True)
        if isinstance(loaded, np.ndarray) and loaded.dtype == object:
            loaded = loaded.item()
        record = loaded.get(member_name, loaded) if isinstance(loaded, dict) else loaded
        if isinstance(record, dict) and "angle" in record:
            angles = -np.asarray(record["angle"], dtype=float)[: len(times)]
            angles = np.mod(angles, 360)
        else:
            angles = np.asarray(record, dtype=float)[: len(times)]
        if len(angles) > 2:
            from scipy.ndimage import gaussian_filter1d
            angles = gaussian_filter1d(angles, sigma=1)
        ax.plot(angles, times, color="k", linestyle="-", linewidth=1.5, label="inner core vertical wind shear")
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"无法绘制内核垂直风切变: {path}, {exc}")


# 功能说明：读取两层中心位置，逐时计算高层相对参考层的偏移方向，平滑后叠加到时间—方向图上。
def maybe_draw_center_height_angle(ax, data_root, member_name, domain, times, fmt):
    height_m = fmt.get("center_height_m")
    if not height_m:
        return
    ref_m = fmt.get("center_reference_height_m", 2000)
    angles = []
    used_times = []
    missing = []
    for time in times:
        # 与水平图共用中心查找：读 processed_v3 的 layer_center。
        top = load_center_grid(
            data_root=data_root, member_name=member_name, domain=domain,
            time_hour=time, height_m=height_m, quiet=True,
        )
        ref = load_center_grid(
            data_root=data_root, member_name=member_name, domain=domain,
            time_hour=time, height_m=ref_m, quiet=True,
        )
        if top is None or ref is None:
            missing.append(time)
            continue
        dy = top[0] - ref[0]
        dx = top[1] - ref[1]
        angles.append(np.degrees(np.arctan2(dy, dx)) % 360)
        used_times.append(time)
    if missing:
        warnings.warn(
            f"{member_name} 缺少 {height_m:g} m 或 {ref_m:g} m 层中心的时次："
            + ", ".join(f"{float(value):g} h" for value in missing[:10])
            + (" …" if len(missing) > 10 else "")
        )
    if not angles:
        return
    values = np.asarray(angles)
    if values.size > 2:
        from scipy.ndimage import gaussian_filter1d
        values = gaussian_filter1d(values, sigma=1)
    ax.plot(values, used_times, color="red", linestyle="-", linewidth=1.5, label=f"center at {height_m:g}m")


# 功能说明：给方向横轴安排每90度的文字标签，支持显示一圈或两圈，并选择从哪个方向开始显示。
def setup_azimuth_axis(ax, double_azimuth, start_x, xlim, fontsize, fontname):
    if double_azimuth:
        ticks = np.arange(0, 721, 90)
        labels = ["US", "RS", "DS", "LS", "US", "RS", "DS", "LS", "US"]
    else:
        ticks = np.arange(0, 361, 90)
        labels = ["US", "RS", "DS", "LS", "US"]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, fontsize=fontsize, fontname=fontname)
    ax.set_xlim(xlim if xlim is not None else start_x_to_xlim(start_x, double_azimuth))


# 功能说明：设置时间纵轴的范围和小时间隔；只有一个时次时在其两侧稍留空间。
def setup_time_axis(ax, times, time_interval, ylim, fontsize, fontname):
    if ylim is None:
        if len(times) == 1:
            ylim = [float(times[0]) - 0.5, float(times[0]) + 0.5]
        else:
            ylim = [min(times), max(times)]
    ax.set_ylim(ylim)
    yticks = np.arange(np.ceil(ylim[0]), np.floor(ylim[1]) + 1, time_interval)
    ax.set_yticks(yticks)
    ax.set_yticklabels([f"{tick:g}" for tick in yticks], fontsize=fontsize, fontname=fontname)


# 功能说明：把一个通用数值复制成指定数量，方便多个方向或圆弧共用设置；已有列表长度不匹配则报错。
def broadcast_values(value, size):
    if value is None:
        return None
    arr = np.atleast_1d(value).astype(float)
    if arr.size == 1 and size > 1:
        return np.full(size, arr[0], dtype=float)
    if arr.size != size:
        raise ValueError("参数必须是标量或与目标数量一致")
    return arr


# 功能说明：把试验名称、时间、高度、方向、半径和变量填入标题模板；没有模板时自动拼成说明性标题。
def make_title(template, *, member_name, times, heights_m, angle, radius, variables):
    variable_text = " ".join(str(key) for key in variables.keys())
    experiment, center_scheme = split_member_key(str(member_name))
    fields = {
        "member": member_display_name(str(member_name)) if "@" in str(member_name) else member_name,
        "experiment": experiment,
        "center_scheme": center_scheme,
        "time": format_range(times or [], "h"),
        "height": format_range([f"{float(value) / 1000:g}" for value in (heights_m or [])], "km"),
        "angle": format_range(angle or [], "deg"),
        "radius": format_range(radius or [], "km"),
        "variables": variable_text,
    }
    if template:
        return template.format(**fields)
    parts = [fields["member"]]
    for key in ("time", "height", "angle", "radius"):
        if fields[key]:
            parts.append(fields[key])
    if variable_text:
        parts.append(variable_text)
    return " ".join(parts)
