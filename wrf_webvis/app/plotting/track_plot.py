from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import warnings

import numpy as np

from ..core.paths import metadata_path
from .center_lookup import load_v3_center_series
from .v3_values import processed_reader, read_scalar_series, read_slice
from .draw_variables import resolve_cmap
from .member_styles import member_display_name, resolve_experiment_color, scheme_linestyle, split_member_key


EARTH_RADIUS_KM = 6371.0


# 功能说明：读取多个试验的台风中心路径，按设置添加降雨背景、海陆边界和时间标记，再画出对比轨迹并返回绘图对象与数据。
def draw_track_plot(
    *,
    ax,
    member_names: list[str],
    domain: int,
    data_root: str | Path,
    selection: dict[str, Any],
    domain_config: dict[int, dict[str, Any]] | None = None,
    format_config: dict[str, Any] | None = None,
):
    cfg = format_config or {}
    common = cfg.get("common", {})
    fmt = cfg.get("track", {})
    times = _selection_times(selection)
    track_payloads: dict[str, dict[str, Any]] = {}
    for index, member_name in enumerate(member_names):
        try:
            payload = load_center_track(
                data_root=data_root,
                member_name=member_name,
                domain=domain,
                times=times,
                domain_config=domain_config,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"Could not read track for {member_name}: {exc}")
            continue

        payload = _truncate_track_at_member_end(payload, member_name, fmt)
        track_payloads[member_name] = payload

    background_artist = draw_track_rainnc_background(
        ax=ax,
        data_root=data_root,
        member_names=member_names,
        track_payloads=track_payloads,
        fmt=fmt,
    )

    land_drawn = False
    for index, member_name in enumerate(member_names):
        payload = track_payloads.get(member_name)
        if payload is None:
            continue
        color = resolve_track_color(member_name, index, fmt)
        if not land_drawn or fmt.get("land_per_member", False):
            land_drawn = draw_land_distribution(
                ax=ax,
                data_root=data_root,
                member_name=member_name,
                domain=domain,
                track=payload,
                color=fmt.get("land_color", "black"),
                fmt=fmt,
            ) or land_drawn
        ax.plot(
            payload["x"],
            payload["y"],
            color=color,
            linewidth=fmt.get("track_linewidth", 2),
            linestyle=resolve_track_linestyle(member_name, fmt),
            marker=fmt.get("track_marker", "o"),
            markersize=fmt.get("track_markersize", 4),
            label=fmt.get("label_template", "{member}").format(
                member=member_display_name(member_name),
                experiment=split_member_key(member_name)[0], center_scheme=split_member_key(member_name)[1],
            ),
            zorder=fmt.get("track_zorder", 5),
        )
        time_values = np.asarray(payload["times"], dtype=float)
        marker_interval_hours = float(fmt.get("marker_interval_hours", 0) or 0)
        if marker_interval_hours > 0:
            interval_markers = np.isclose(np.mod(time_values, marker_interval_hours), 0.0)
            ax.scatter(
                np.asarray(payload["x"], dtype=float)[interval_markers],
                np.asarray(payload["y"], dtype=float)[interval_markers],
                s=float(fmt.get("interval_marker_size", 32)),
                facecolors="white",
                edgecolors=color,
                linewidths=1.1,
                zorder=fmt.get("track_zorder", 5) + 1,
            )
        annotate_time = bool(fmt.get("annotate_time", False))
        annotate_final_time = bool(fmt.get("annotate_final_time", True))
        if annotate_time or annotate_final_time:
            marked = np.zeros(time_values.shape, dtype=bool)
            if annotate_time:
                interval_hours = float(fmt.get("annotation_interval_hours", 24) or 0)
                if interval_hours > 0:
                    marked = np.isclose(np.mod(time_values, interval_hours), 0.0)
                else:
                    marked = np.ones(time_values.shape, dtype=bool)
            if annotate_final_time and marked.size:
                marked[-1] = True
            marked_times = time_values[marked]
            marked_x = np.asarray(payload["x"], dtype=float)[marked]
            marked_y = np.asarray(payload["y"], dtype=float)[marked]
            ax.scatter(
                marked_x,
                marked_y,
                s=float(fmt.get("annotation_marker_size", 38)),
                facecolors="white",
                edgecolors=color,
                linewidths=1.2,
                zorder=fmt.get("track_zorder", 5) + 1,
            )
            for time_hour, x_value, y_value in zip(marked_times, marked_x, marked_y):
                ax.text(x_value, y_value, f"{time_hour:g}", fontsize=fmt.get("annotation_fontsize", 8), color=color)

    apply_track_format(ax, track_payloads, common, fmt)
    colorbar_artist = background_artist if fmt.get("rainnc_colorbar", True) else None
    return ax, colorbar_artist, None, track_payloads, []


# 功能说明：根据某个试验指定的结束小时，同时裁剪轨迹的时间、位置和经纬度，保持各项一一对应。
def _truncate_track_at_member_end(payload: dict[str, Any], member_name: str, fmt: dict[str, Any]) -> dict[str, Any]:
    configured_ends = fmt.get("member_end_hours")
    if not isinstance(configured_ends, dict) or member_name not in configured_ends:
        return payload
    try:
        end_hour = float(configured_ends[member_name])
    except (TypeError, ValueError):
        return payload

    time_values = np.asarray(payload.get("times"), dtype=float)
    selected = np.isfinite(time_values) & (time_values <= end_hour + 1e-8)
    if selected.size == 0 or selected.all():
        return payload

    truncated = dict(payload)
    for key in ("times", "x", "y", "lat", "lon"):
        values = payload.get(key)
        if values is None:
            continue
        array = np.asarray(values)
        if array.ndim and array.shape[0] == selected.size:
            truncated[key] = array[selected]
    return truncated


# 功能说明：开启背景选项时读取指定试验的累计降雨及经纬度网格，换成相对路径起点的公里坐标后绘制彩色降雨底图。
def draw_track_rainnc_background(
    *,
    ax,
    data_root: str | Path,
    member_names: list[str],
    track_payloads: dict[str, dict[str, Any]],
    fmt: dict[str, Any],
):
    if not fmt.get("draw_rainnc_background", False):
        return None
    member_name = str(fmt.get("rainnc_member") or (member_names[0] if member_names else ""))
    track = track_payloads.get(member_name)
    if track is None:
        warnings.warn(f"RAINNC 背景试验没有可用路径中心: {member_name}")
        return None
    if track.get("coordinate_source") != "lat_lon":
        warnings.warn("RAINNC 背景需要中心文件包含 lat/lon，当前路径只能使用格点坐标")
        return None

    rain_domain = int(fmt.get("rainnc_domain", 1))
    rain_time = float(fmt.get("rainnc_time_hour", 120.0))
    variable = str(fmt.get("rainnc_variable", "RAINNC"))
    custom_path = fmt.get("rainnc_path")
    try:
        if custom_path:
            # 显式指定的离线降水文件（.npy）沿用旁边说明文件里的网格经纬度。
            rain_path = Path(custom_path)
            if not rain_path.is_file():
                warnings.warn(f"找不到路径图降水背景文件: {rain_path}")
                return None
            rain = np.asarray(np.load(rain_path), dtype=float)
            lat, lon = load_processed_lat_lon_grid(rain_path)
        else:
            # 2026-11-01：读 processed_v3 同一 domain、同一时次的 RAINNC 与 XLAT/XLONG。
            reader = processed_reader(data_root)
            rain = read_slice(reader, member_name, rain_domain, variable, rain_time)
            lat = read_slice(reader, member_name, rain_domain, "XLAT", rain_time)
            lon = read_slice(reader, member_name, rain_domain, "XLONG", rain_time)
            if rain is None or lat is None or lon is None:
                missing = [name for name, value in ((variable, rain), ("XLAT", lat), ("XLONG", lon)) if value is None]
                warnings.warn(
                    f"找不到路径图降水背景: member={member_name}, d0{rain_domain}, time={rain_time:g}h，"
                    f"processed_v3 缺少 {', '.join(missing)}"
                )
                return None
        if rain.ndim != 2:
            raise ValueError(f"RAINNC 背景必须是二维场，当前 shape={rain.shape}")
        if lat.shape != rain.shape or lon.shape != rain.shape:
            raise ValueError(f"RAINNC 与 XLAT/XLONG shape 不一致: rain={rain.shape}, lat={lat.shape}, lon={lon.shape}")
        reference = track["reference"]
        x_km, y_km = lonlat_to_km(reference["lon"], reference["lat"], lon, lat)
        values = rain * float(fmt.get("rainnc_scale_factor", 1.0))
        levels = np.asarray(
            fmt.get("rainnc_levels", [1, 5, 10, 20, 40, 60, 80, 100, 150, 200, 300]),
            dtype=float,
        )
        if levels.ndim != 1 or levels.size < 2 or np.any(np.diff(levels) <= 0):
            raise ValueError("rainnc_levels 必须是至少两个严格递增的数值")
        artist = ax.contourf(
            x_km,
            y_km,
            values,
            levels=levels,
            cmap=resolve_cmap(fmt.get("rainnc_cmap", "turbo")),
            extend=fmt.get("rainnc_extend", "max"),
            alpha=float(fmt.get("rainnc_alpha", 0.85)),
            zorder=float(fmt.get("rainnc_zorder", 0)),
        )
        ax._track_background_extent = (
            float(np.nanmin(x_km)), float(np.nanmax(x_km)),
            float(np.nanmin(y_km)), float(np.nanmax(y_km)),
        )
        return artist
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"无法绘制 d0{rain_domain} {rain_time:g}h {variable} 背景: {exc}")
        return None


# 功能说明：（只用于显式指定的 rainnc_path）读取数据文件旁的说明信息，再寻找离线保存的纬度和经度网格。
def load_processed_lat_lon_grid(data_path: Path) -> tuple[np.ndarray, np.ndarray]:
    meta_path = metadata_path(data_path)
    metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return load_processed_grid_coordinates(data_path, metadata)


# 功能说明：从说明信息指定目录或默认 grid 目录读取经纬度数组；缺文件时明确报出路径。
def load_processed_grid_coordinates(
    data_path: Path,
    metadata: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray]:
    data_path = Path(data_path)
    domain_root = data_path.parents[1]
    configured = metadata.get("processed_grid_root")
    grid_root = Path(configured) if configured else domain_root / "grid"
    lat_path = grid_root / "lat.npy"
    lon_path = grid_root / "lon.npy"
    missing = [path for path in (lat_path, lon_path) if not path.exists()]
    if missing:
        details = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"路径图缺少预处理网格坐标: {details}")
    return np.asarray(np.load(lat_path), dtype=float), np.asarray(np.load(lon_path), dtype=float)


# 功能说明：按试验统一配色选择路径颜色，没有匹配时使用绘图库的顺序颜色。
def resolve_track_color(member_name: str, index: int, fmt: dict[str, Any]) -> str:
    return resolve_experiment_color(member_name, index, fmt) or f"C{index}"


# 功能说明：按完整试验名或名称片段挑选线型，默认用实线和虚线区分海洋与陆地试验。
def resolve_track_linestyle(member_name: str, fmt: dict[str, Any]) -> str:
    styles = fmt.get("member_linestyles", {"sea": "-", "land": "--"})
    if isinstance(styles, dict) and member_name in styles:
        return str(styles[member_name])
    by_scheme = scheme_linestyle(member_name, fmt)
    if by_scheme is not None:
        return by_scheme
    if isinstance(styles, dict):
        lower_name = member_name.lower()
        for key, value in styles.items():
            if str(key).lower() in lower_name:
                return str(value)
    return str(fmt.get("track_linestyle", "-"))


# 功能说明：读取并筛选台风中心表，尝试用离线坐标补齐经纬度，再换算成相对起点的公里位移；经纬度不全时退回格点位移。
def load_center_track(
    *,
    data_root: str | Path,
    member_name: str,
    domain: int,
    times: list[float] | None,
    domain_config: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # 读 processed_v3 的 slp_center（经纬度来自 center_geo，缺时用同时次 XLAT/XLONG），与水平图的中心一致。
    # 2026-11-01 起不再退回旧的 center.txt。
    series = load_v3_center_series(data_root=data_root, member_name=member_name, domain=domain, times=times)
    if series is None:
        raise FileNotFoundError(f"processed_v3 中没有 {member_name} d{int(domain):02d} 的 slp_center，无法画路径")
    path = series["path"]
    time_values = series["times"]
    y_grid = series["y"]
    x_grid = series["x"]
    lat = series["lat"]
    lon = series["lon"]

    # 2026-10-26 Claude：成员积分结束后（或 center_geo 缺时次）个别时次经纬度为 NaN 时，
    # 只丢掉这些时次，不要整条路径退回格点坐标（移动嵌套下格点坐标几乎不动，路径会缩在原点）。
    if lat is not None and lon is not None:
        finite = np.isfinite(lat) & np.isfinite(lon)
        if 2 <= int(finite.sum()) < finite.size:
            time_values = np.asarray(time_values, dtype=float)[finite]
            y_grid = np.asarray(y_grid, dtype=float)[finite]
            x_grid = np.asarray(x_grid, dtype=float)[finite]
            lat = np.asarray(lat, dtype=float)[finite]
            lon = np.asarray(lon, dtype=float)[finite]

    if lat is not None and lon is not None and np.isfinite(lat).all() and np.isfinite(lon).all():
        x_km, y_km = lonlat_to_km(lon[0], lat[0], lon, lat)
        coordinate_source = "lat_lon"
        reference = {"lat": float(lat[0]), "lon": float(lon[0])}
    else:
        config = (domain_config or {}).get(domain, {})
        dx_km = float(config.get("dx_km") or 1.0)
        dy_km = float(config.get("dy_km") or dx_km)
        x_km = (x_grid - x_grid[0]) * dx_km
        y_km = (y_grid - y_grid[0]) * dy_km
        coordinate_source = "grid"
        reference = {"y": float(y_grid[0]), "x": float(x_grid[0])}

    return {
        "path": path,
        "times": time_values,
        "x": np.asarray(x_km, dtype=float),
        "y": np.asarray(y_km, dtype=float),
        "lat": lat,
        "lon": lon,
        "coordinate_source": coordinate_source,
        "reference": reference,
    }


# 功能说明：按配置绘制固定竖直海陆边界，或从离线边界经纬度读取；没有边界数据时尝试用中心距陆地距离推算参考线。
def draw_land_distribution(
    *,
    ax,
    data_root: str | Path,
    member_name: str,
    domain: int,
    track: dict[str, Any],
    color: str,
    fmt: dict[str, Any],
) -> bool:
    if fmt.get("draw_land_boundary", True) is False:
        return False

    fixed_x = fmt.get("land_boundary_x_km")
    if fixed_x is not None:
        return draw_vertical_land_boundary(ax, float(fixed_x), color=color, fmt=fmt)

    if track.get("coordinate_source") == "lat_lon":
        points = load_land_boundary_points(data_root, member_name, fmt)
        if points is not None:
            if points.ndim == 2 and points.shape[1] >= 2 and points.size:
                reference = track["reference"]
                x_km, y_km = lonlat_to_km(reference["lon"], reference["lat"], points[:, 1], points[:, 0])
                max_abs = fmt.get("land_extent_km")
                if max_abs is not None:
                    mask = (np.abs(x_km) <= float(max_abs)) & (np.abs(y_km) <= float(max_abs))
                    x_km = x_km[mask]
                    y_km = y_km[mask]
                if x_km.size:
                    if fmt.get("straight_land_boundary", True):
                        return draw_vertical_land_boundary(
                            ax,
                            float(np.nanmedian(x_km)),
                            color=color,
                            fmt=fmt,
                        )
                    order = np.argsort(y_km if np.ptp(y_km) >= np.ptp(x_km) else x_km)
                    line = ax.plot(
                        x_km[order],
                        y_km[order],
                        color=fmt.get("land_color", color),
                        linestyle=fmt.get("land_linestyle", "--"),
                        linewidth=fmt.get("land_linewidth", 1.5),
                        alpha=fmt.get("land_alpha", 0.75),
                        zorder=fmt.get("land_zorder", 2),
                    )[0]
                    line.set_gid("land_boundary")
                    return True

    distance = load_land_distance_series(data_root, member_name, domain, track["times"])
    if distance is None:
        return False
    boundary_x = track["x"] - distance
    if fmt.get("straight_land_boundary", True):
        return draw_vertical_land_boundary(
            ax,
            float(np.nanmedian(boundary_x)),
            color=color,
            fmt=fmt,
        )
    line = ax.plot(
        boundary_x,
        track["y"],
        color=fmt.get("land_color", color),
        linestyle=fmt.get("land_linestyle", "--"),
        linewidth=fmt.get("land_linewidth", 1.5),
        alpha=fmt.get("land_alpha", 0.75),
        zorder=fmt.get("land_zorder", 2),
    )[0]
    line.set_gid("land_boundary")
    return True


# 功能说明：读取海岸线点 [纬度, 经度]：显式 land_boundary_path（.npy）优先，否则读 processed_v3 d01 的 land_boundary
# （参考时次 land_boundary_time，默认 0 h，与处理端一致）。没有时返回 None。
def load_land_boundary_points(data_root: str | Path, member_name: str, fmt: dict[str, Any]) -> np.ndarray | None:
    if fmt.get("land_boundary_path"):
        path = Path(fmt["land_boundary_path"])
        return np.asarray(np.load(path), dtype=float) if path.exists() else None
    values = read_slice(
        processed_reader(data_root), member_name, 1, "land_boundary",
        float(fmt.get("land_boundary_time", 0.0)),
    )
    return None if values is None else np.asarray(values, dtype=float)


# 功能说明：在指定东西方向公里位置画一条竖直海陆边界，并给它加识别标记供坐标范围计算使用。
def draw_vertical_land_boundary(ax, x_km: float, *, color: str, fmt: dict[str, Any]) -> bool:
    if not np.isfinite(x_km):
        return False
    line = ax.axvline(
        x=x_km,
        color=fmt.get("land_color", color),
        linestyle=fmt.get("land_linestyle", "--"),
        linewidth=fmt.get("land_linewidth", 1.5),
        alpha=fmt.get("land_alpha", 0.75),
        zorder=fmt.get("land_zorder", 2),
    )
    line.set_gid("land_boundary")
    return True


# 功能说明：读取与轨迹时次对应的中心距陆地距离（processed_v3 的 center_land_distance）；有任一时次缺失就返回 None。
def load_land_distance_series(
    data_root: str | Path,
    member_name: str,
    domain: int,
    times: np.ndarray,
) -> np.ndarray | None:
    requested = [float(value) for value in times]
    found_times, values = read_scalar_series(
        processed_reader(data_root), member_name, domain, "center_land_distance", requested
    )
    if len(found_times) != len(requested):
        return None
    return values


# 功能说明：以给定经纬度为参考点，把其他点的经纬度差近似换算成东西、南北方向的公里距离。
def lonlat_to_km(lon0: float, lat0: float, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    lat_mid = np.deg2rad((float(lat0) + lat) * 0.5)
    east = np.deg2rad(lon - float(lon0)) * EARTH_RADIUS_KM * np.cos(lat_mid)
    north = np.deg2rad(lat - float(lat0)) * EARTH_RADIUS_KM
    return east, north


# 功能说明：给路径图设置等比例坐标、原点参考线、标题和图例，并根据轨迹、边界与背景范围安排显示区域。
def apply_track_format(ax, payloads: dict[str, dict[str, Any]], common: dict[str, Any], fmt: dict[str, Any]) -> None:
    ax.set_aspect("equal", adjustable="box")
    ax.grid(bool(fmt.get("grid", True)), linestyle=fmt.get("grid_linestyle", "--"), alpha=fmt.get("grid_alpha", 0.35))
    ax.axhline(0, color=fmt.get("origin_color", "0.75"), linewidth=0.8, zorder=1)
    ax.axvline(0, color=fmt.get("origin_color", "0.75"), linewidth=0.8, zorder=1)
    ax.set_xlabel(fmt.get("xlabel", "West-East distance (km)"))
    ax.set_ylabel(fmt.get("ylabel", "South-North distance (km)"))
    ax.set_title(_track_title(common, fmt))

    if payloads:
        all_x = np.concatenate([payload["x"] for payload in payloads.values()])
        all_y = np.concatenate([payload["y"] for payload in payloads.values()])
        boundary_x = [
            np.asarray(line.get_xdata(), dtype=float)
            for line in ax.lines
            if line.get_gid() == "land_boundary"
        ]
        if boundary_x:
            all_x = np.concatenate([all_x, *boundary_x])
        background_extent = getattr(ax, "_track_background_extent", None)
        if background_extent is not None:
            all_x = np.concatenate([all_x, np.asarray(background_extent[:2], dtype=float)])
            all_y = np.concatenate([all_y, np.asarray(background_extent[2:], dtype=float)])
        _apply_axis_limits(ax, all_x, all_y, fmt)
    _apply_ticks(ax, fmt)
    if fmt.get("legend", True):
        ax.legend(loc=fmt.get("legend_loc", "best"))


# 功能说明：采用用户写好的固定标题；没有可用固定标题时显示默认的台风路径标题。
def _track_title(common: dict[str, Any], fmt: dict[str, Any]) -> str:
    title = fmt.get("title") or common.get("title")
    if title and "{" not in str(title):
        return str(title)
    return "Typhoon track"


# 功能说明：优先使用指定的路径图范围，否则依据有效轨迹位置和留白计算以原点为中心的范围。
def _apply_axis_limits(ax, x_values: np.ndarray, y_values: np.ndarray, fmt: dict[str, Any]) -> None:
    xlim = fmt.get("xlim_km")
    ylim = fmt.get("ylim_km")
    if xlim is not None:
        ax.set_xlim(_limit_from_value(xlim))
    if ylim is not None:
        ax.set_ylim(_limit_from_value(ylim))
    if xlim is None or ylim is None:
        finite_x = x_values[np.isfinite(x_values)]
        finite_y = y_values[np.isfinite(y_values)]
        if finite_x.size and finite_y.size:
            span = max(float(np.nanmax(np.abs(finite_x))), float(np.nanmax(np.abs(finite_y))), 10.0)
            pad = float(fmt.get("padding_km", max(20.0, span * 0.12)))
            half = span + pad
            if xlim is None:
                ax.set_xlim(-half, half)
            if ylim is None:
                ax.set_ylim(-half, half)


# 功能说明：把路径图范围设置转成两个端点；只给一个数时表示负该数到正该数。
def _limit_from_value(value: Any) -> tuple[float, float]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return float(value[0]), float(value[1])
    half = float(value)
    return -half, half


# 功能说明：按指定的公里间隔给路径图横纵轴放置刻度，未指定间隔则保持原来的刻度。
def _apply_ticks(ax, fmt: dict[str, Any]) -> None:
    interval = fmt.get("tick_interval_km")
    if not interval:
        return
    step = float(interval)
    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    ax.set_xticks(np.arange(np.ceil(xlim[0] / step) * step, np.floor(xlim[1] / step) * step + step * 0.5, step))
    ax.set_yticks(np.arange(np.ceil(ylim[0] / step) * step, np.floor(ylim[1] / step) * step + step * 0.5, step))


# 功能说明：把选择区的一个或多个时间统一变成数值列表；未指定则返回空值，表示读取时不按时次筛选。
def _selection_times(selection: dict[str, Any]) -> list[float] | None:
    raw = selection.get("times")
    if raw is None:
        return None
    if isinstance(raw, np.ndarray):
        raw = raw.tolist()
    if isinstance(raw, (list, tuple)):
        return [float(value) for value in raw]
    return [float(raw)]
