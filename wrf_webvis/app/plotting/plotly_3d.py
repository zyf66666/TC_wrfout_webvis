from __future__ import annotations

from .rendering import serialized_render, save_figure

from pathlib import Path
from typing import Any
import warnings

import numpy as np
import plotly.graph_objects as go

from .center_lookup import load_center_grid


DBZ_COLORSCALE = [
    [0.0, "#c7d2ea"],
    [0.125, "#86a8e7"],
    [0.25, "#5f66e8"],
    [0.375, "#09989a"],
    [0.5, "#5bff00"],
    [0.625, "#b8f01d"],
    [0.75, "#f6b300"],
    [0.875, "#ff7a00"],
    [1.0, "#ff2a00"],
]


# 功能说明：读取指定高度的数据，把各高度画成可旋转的彩色水平层，可加涡旋中心连线和风箭头；保存交互网页及静态预览图。
@serialized_render
def create_3d_plot(
    *,
    data_root: str | Path,
    output_png: str | Path,
    output_html: str | Path,
    member_name: str,
    domain: int,
    variable: str,
    time_hour: int | float,
    heights_m: list[float],
    domain_config: dict[int, dict[str, Any]] | None,
    format_config: dict[str, Any] | None,
    quiver_variables: list[str] | None = None,
    progress=None,
) -> dict[str, Any]:
    fmt = (format_config or {}).get("three_d", {})
    config = _domain_config(domain_config, domain)
    dx_km = float(config.get("dx_km") or 1.0)
    dy_km = float(config.get("dy_km") or dx_km)
    radius_km = float(fmt.get("radius_km", fmt.get("rmax_km", 300)))

    data, height_levels, source_paths = load_3d_variable(
        data_root=data_root,
        member_name=member_name,
        domain=domain,
        variable=variable,
        time_hour=time_hour,
        heights_m=heights_m,
    )
    height_indices = heights_to_indices(heights_m, height_levels)
    center_y, center_x = load_surface_center(data_root, member_name, domain, time_hour, data.shape[-2:])
    x_km = (np.arange(data.shape[-1], dtype=float) - center_x) * dx_km
    y_km = (np.arange(data.shape[-2], dtype=float) - center_y) * dy_km
    x_mask = np.abs(x_km) <= radius_km
    y_mask = np.abs(y_km) <= radius_km
    X, Y = np.meshgrid(x_km[x_mask], y_km[y_mask])
    surface_stride = max(int(fmt.get("surface_stride", 1)), 1)
    X_surface = X[::surface_stride, ::surface_stride]
    Y_surface = Y[::surface_stride, ::surface_stride]

    fig = go.Figure()
    threshold = float(fmt.get("transparent_below", 0 if variable.lower() == "dbz" else -np.inf))
    cmin = float(fmt.get("cmin", threshold if np.isfinite(threshold) else np.nanmin(data)))
    cmax = float(fmt.get("cmax", 55 if variable.lower() == "dbz" else np.nanmax(data)))
    opacity = float(fmt.get("surface_opacity", 0.8))
    colorscale = fmt.get("colorscale") or DBZ_COLORSCALE

    for idx, height_index in enumerate(height_indices):
        height_km = float(height_levels[height_index] / 1000.0)
        values = np.asarray(data[height_index], dtype=float)[np.ix_(y_mask, x_mask)][::surface_stride, ::surface_stride]
        visible = np.isfinite(values) & (values >= threshold)
        surface_values = np.where(visible, values, np.nan)
        fig.add_trace(
            go.Surface(
                x=X_surface,
                y=Y_surface,
                # Mask geometry too: missing surface colors alone leave a solid plane.
                z=np.where(visible, height_km, np.nan),
                surfacecolor=surface_values,
                connectgaps=False,
                colorscale=colorscale,
                cmin=cmin,
                cmax=cmax,
                opacity=opacity,
                showscale=idx == 0,
                name=f"{variable} {height_km:g} km",
                colorbar={"title": fmt.get("colorbar_title", variable)},
            )
        )

    center_trace = None
    if fmt.get("draw_layer_centers", True):
        center_trace = add_layer_center_trace(
            fig=fig,
            data_root=data_root,
            member_name=member_name,
            domain=domain,
            time_hour=time_hour,
            surface_center=(center_y, center_x),
            dx_km=dx_km,
            dy_km=dy_km,
            center_heights_km=fmt.get("center_heights_km"),
        )

    cone_trace = None
    if fmt.get("draw_arrows", False):
        cone_trace = add_arrow_trace(
            fig=fig,
            data_root=data_root,
            member_name=member_name,
            domain=domain,
            time_hour=time_hour,
            heights_m=heights_m,
            height_levels=height_levels,
            x_km=x_km,
            y_km=y_km,
            x_mask=x_mask,
            y_mask=y_mask,
            center_y=center_y,
            center_x=center_x,
            dx_km=dx_km,
            dy_km=dy_km,
            fmt=fmt,
            quiver_variables=quiver_variables,
        )

    width = int(fmt.get("width", 900))
    height = int(fmt.get("height", 800))
    zlim = fmt.get("zlim_km", [0, max(10.0, max(heights_m) / 1000.0)])
    fig.update_layout(
        scene={
            "xaxis": {"title": "X (km)", "range": _limit(radius_km, fmt.get("xlim_km"))},
            "yaxis": {"title": "Y (km)", "range": _limit(radius_km, fmt.get("ylim_km"))},
            "zaxis": {"title": "Height (km)", "range": zlim},
            "aspectmode": fmt.get("aspectmode", "cube"),
        },
        title=fmt.get("title", f"{member_name} {time_hour:g}h {variable}"),
        width=width,
        height=height,
        margin={"l": 0, "r": 0, "t": 45, "b": 0},
    )

    output_html = Path(output_html)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(output_html, include_plotlyjs="cdn", full_html=True)
    output_png = Path(output_png)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    try:
        fig.write_image(output_png)
    except Exception:
        write_preview_png(
            output_png=output_png,
            variable=variable,
            surfaces=[(float(height_levels[index] / 1000.0), np.asarray(data[index], dtype=float)[np.ix_(y_mask, x_mask)]) for index in height_indices],
            X=X,
            Y=Y,
            threshold=threshold,
            cmin=cmin,
            cmax=cmax,
            radius_km=radius_km,
            zlim=zlim,
            center_trace=center_trace,
            cone_trace=cone_trace,
            width=width,
            height=height,
        )

    return {
        "html_path": str(output_html),
        "image_path": str(output_png),
        "source_paths": [str(path) for path in source_paths],
        "variable": variable,
        "time_hour": time_hour,
        "heights_m": [float(value) for value in heights_m],
    }


# 功能说明：逐高度读取 processed_v3 切片并叠成三维数组，同时返回高度列表和来源文件。
def load_3d_variable(
    *,
    data_root: str | Path,
    member_name: str,
    domain: int,
    variable: str,
    time_hour: int | float,
    heights_m: list[float],
) -> tuple[np.ndarray, np.ndarray, list[Path]]:
    # 与数据预检共用 v3 切片读取器，只读取已经完成的指定时次和高度（2026-11-01 起不再读旧版 .npy）。
    from ..services.data_manager import DataManager

    reader = DataManager(data_root).processed_reader
    arrays = []
    paths = []
    for height_m in heights_m:
        reference = reader.resolve(
            member_name=member_name, domain=domain, variable=variable,
            time_hour=time_hour, height_m=height_m,
        )
        if reference is None:
            raise FileNotFoundError(
                f"missing completed 3D slice: {member_name} d{domain:02d} "
                f"{variable} {time_hour:g}h {height_m:g}m"
            )
        arrays.append(np.asarray(reader.load(reference), dtype=float))
        paths.append(reference.path)
    return np.stack(arrays), np.asarray(heights_m, dtype=float), list(dict.fromkeys(paths))


# 功能说明：读取指定时刻的地面中心位置；没有中心文件时用数据网格中点作为三维图参考中心。
def load_surface_center(
    data_root: str | Path,
    member_name: str,
    domain: int,
    time_hour: int | float,
    grid_shape: tuple[int, int],
) -> tuple[float, float]:
    # 与水平图共用中心查找：读 processed_v3 的 slp_center。
    found = load_center_grid(data_root=data_root, member_name=member_name, domain=domain, time_hour=time_hour)
    if found is None:
        warnings.warn("三维图未找到地面中心，使用网格中点作为参考中心")
        return (grid_shape[0] - 1) / 2.0, (grid_shape[1] - 1) / 2.0
    return found


# 功能说明：读取各高度的涡旋中心，换算成相对地面中心的公里位移，连成一条三维折线展示涡旋随高度的倾斜。
def add_layer_center_trace(
    *,
    fig: go.Figure,
    data_root: str | Path,
    member_name: str,
    domain: int,
    time_hour: int | float,
    surface_center: tuple[float, float],
    dx_km: float,
    dy_km: float,
    center_heights_km: Any,
) -> dict[str, np.ndarray] | None:
    heights_km = np.asarray(center_heights_km if center_heights_km is not None else np.arange(1, 10, 1), dtype=float)
    points = [[0.0, 0.0, 0.0]]
    missing: list[float] = []
    for height_km in heights_km:
        found = load_center_grid(
            data_root=data_root,
            member_name=member_name,
            domain=domain,
            time_hour=time_hour,
            height_m=int(round(float(height_km) * 1000)),
            quiet=True,
        )
        if found is None:
            missing.append(float(height_km))
            continue
        center_y, center_x = found
        points.append([(center_x - surface_center[1]) * dx_km, (center_y - surface_center[0]) * dy_km, float(height_km)])
    if missing:
        warnings.warn(
            f"三维图缺少 {member_name} {float(time_hour):g} h 这些高度的层中心："
            + ", ".join(f"{value:g} km" for value in missing)
        )
    if len(points) <= 1:
        return None
    arr = np.asarray(points, dtype=float)
    fig.add_trace(
        go.Scatter3d(
            x=arr[:, 0],
            y=arr[:, 1],
            z=arr[:, 2],
            mode="lines+markers",
            line={"color": "black", "width": 5},
            marker={"size": 5, "color": "black"},
            name="layer centers",
        )
    )
    return {"x": arr[:, 0], "y": arr[:, 1], "z": arr[:, 2]}


# 功能说明：读取三个风分量，按间距抽样并筛选上升速度范围，放大垂直分量后添加三维锥形箭头，返回箭头位置和数值。
def add_arrow_trace(
    *,
    fig: go.Figure,
    data_root: str | Path,
    member_name: str,
    domain: int,
    time_hour: int | float,
    heights_m: list[float],
    height_levels: np.ndarray,
    x_km: np.ndarray,
    y_km: np.ndarray,
    x_mask: np.ndarray,
    y_mask: np.ndarray,
    center_y: float,
    center_x: float,
    dx_km: float,
    dy_km: float,
    fmt: dict[str, Any],
    quiver_variables: list[str] | None,
) -> dict[str, np.ndarray] | None:
    names = arrow_variable_names(fmt, quiver_variables)
    try:
        u, _u_levels, _ = load_3d_variable(data_root=data_root, member_name=member_name, domain=domain, variable=names[0], time_hour=time_hour, heights_m=heights_m)
        v, _v_levels, _ = load_3d_variable(data_root=data_root, member_name=member_name, domain=domain, variable=names[1], time_hour=time_hour, heights_m=heights_m)
        w, _w_levels, _ = load_3d_variable(data_root=data_root, member_name=member_name, domain=domain, variable=names[2], time_hour=time_hour, heights_m=heights_m)
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"Could not draw 3D arrows: {exc}")
        return None

    height_indices = heights_to_indices(heights_m, height_levels)
    interval = max(int(fmt.get("arrow_interval", 12)), 1)
    threshold = float(fmt.get("arrow_w_min", fmt.get("vertical_velocity_min", 1.0)))
    w_max = fmt.get("arrow_w_max")
    w_max_value = float(w_max) if w_max is not None and str(w_max).strip() != "" else None
    vertical_scale = float(fmt.get("arrow_vertical_scale", 20.0))
    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    zs: list[np.ndarray] = []
    us: list[np.ndarray] = []
    vs: list[np.ndarray] = []
    ws: list[np.ndarray] = []
    x_sub = x_km[x_mask][::interval]
    y_sub = y_km[y_mask][::interval]
    Xs, Ys = np.meshgrid(x_sub, y_sub)
    y_indices = np.where(y_mask)[0][::interval]
    x_indices = np.where(x_mask)[0][::interval]
    for height_index in height_indices:
        wk = np.asarray(w[height_index], dtype=float)[np.ix_(y_indices, x_indices)]
        mask = np.isfinite(wk) & (wk >= threshold)
        if w_max_value is not None:
            mask &= wk <= w_max_value
        if not mask.any():
            continue
        xs.append(Xs[mask])
        ys.append(Ys[mask])
        zs.append(np.full(mask.sum(), float(height_levels[height_index] / 1000.0)))
        us.append(np.asarray(u[height_index], dtype=float)[np.ix_(y_indices, x_indices)][mask])
        vs.append(np.asarray(v[height_index], dtype=float)[np.ix_(y_indices, x_indices)][mask])
        ws.append(wk[mask] * vertical_scale)
    if not xs:
        return None
    payload = {"x": np.concatenate(xs), "y": np.concatenate(ys), "z": np.concatenate(zs), "u": np.concatenate(us), "v": np.concatenate(vs), "w": np.concatenate(ws)}
    fig.add_trace(
        go.Cone(
            x=payload["x"],
            y=payload["y"],
            z=payload["z"],
            u=payload["u"],
            v=payload["v"],
            w=payload["w"],
            sizemode="absolute",
            sizeref=float(fmt.get("arrow_sizeref", 25)),
            anchor="tail",
            colorscale=fmt.get("arrow_colorscale", "Blues"),
            showscale=False,
            name="vertical wind arrows",
        )
    )
    return payload


# 功能说明：从三维设置或箭头变量列表中选取三个风分量名称，未指定时默认使用 ua、va、wa。
def arrow_variable_names(fmt: dict[str, Any], quiver_variables: list[str] | None) -> list[str]:
    if fmt.get("arrow_variables"):
        values = fmt["arrow_variables"]
        if isinstance(values, str):
            values = [item.strip() for item in values.replace("|", ",").split(",") if item.strip()]
        if len(values) >= 3:
            return list(values[:3])
    names = list(quiver_variables or [])
    if len(names) >= 3:
        return names[:3]
    return ["ua", "va", "wa"]


# 功能说明：为每个请求高度找到数据中距离最近的高度层位置，供三维绘图取值。
def heights_to_indices(heights_m: list[float], levels_m: np.ndarray) -> list[int]:
    return [int(np.argmin(np.abs(levels_m - float(height_m)))) for height_m in heights_m]


# 功能说明：把高度统一为米：此处约定绝对值不超过50的输入按公里换算，未填时选2、5、8公里。
def normalize_3d_heights(values: list[float] | None) -> list[float]:
    raw = values or [2000.0, 5000.0, 8000.0]
    return [float(value) * 1000.0 if abs(float(value)) <= 50 else float(value) for value in raw]


# 功能说明：查找指定模拟区域的网格间距，兼容数字和文字形式的区域编号；找不到时默认1公里。
def _domain_config(domain_config: dict[int, dict[str, Any]] | None, domain: int) -> dict[str, Any]:
    if not domain_config:
        return {"dx_km": 1.0, "dy_km": 1.0}
    return domain_config.get(domain) or domain_config.get(str(domain)) or {"dx_km": 1.0, "dy_km": 1.0}


# 功能说明：把坐标范围整理成下限和上限；单个数表示以零为中心向两侧延伸的距离。
def _limit(radius_km: float, value: Any) -> list[float]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return [float(value[0]), float(value[1])]
    half = radius_km if value is None else float(value)
    return [-half, half]


# 功能说明：交互绘图库无法导出静态图片时，用 Matplotlib 重画各高度彩色层、中心线和箭头并保存一张三维预览。
@serialized_render
def write_preview_png(
    *,
    output_png: Path,
    variable: str,
    surfaces: list[tuple[float, np.ndarray]],
    X: np.ndarray,
    Y: np.ndarray,
    threshold: float,
    cmin: float,
    cmax: float,
    radius_km: float,
    zlim: list[float],
    center_trace: dict[str, np.ndarray] | None,
    cone_trace: dict[str, np.ndarray] | None,
    width: int,
    height: int,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import cm, colors

    fig = plt.figure(figsize=(max(width / 120.0, 4), max(height / 120.0, 4)))
    try:
        ax = fig.add_subplot(111, projection="3d")
        norm = colors.Normalize(vmin=cmin, vmax=cmax)
        cmap = cm.get_cmap("turbo")
        preview_stride = max(int(max(X.shape) / 220), 1)
        Xp = X[::preview_stride, ::preview_stride]
        Yp = Y[::preview_stride, ::preview_stride]
        for height_km, values in surfaces:
            shown = np.where(values < threshold, np.nan, values)[::preview_stride, ::preview_stride]
            facecolors = cmap(norm(np.nan_to_num(shown, nan=cmin)))
            facecolors[..., -1] = np.where(np.isfinite(shown), 0.75, 0.0)
            ax.plot_surface(Xp, Yp, np.full_like(shown, height_km), facecolors=facecolors, rstride=1, cstride=1, linewidth=0, shade=False)
        if center_trace is not None:
            ax.plot(center_trace["x"], center_trace["y"], center_trace["z"], color="black", marker="o", linewidth=2)
        if cone_trace is not None:
            stride = max(int(len(cone_trace["x"]) / 250), 1)
            ax.quiver(
                cone_trace["x"][::stride],
                cone_trace["y"][::stride],
                cone_trace["z"][::stride],
                cone_trace["u"][::stride],
                cone_trace["v"][::stride],
                cone_trace["w"][::stride],
                length=0.015,
                normalize=True,
                color="tab:blue",
                alpha=0.55,
            )
        ax.set_xlim(-radius_km, radius_km)
        ax.set_ylim(-radius_km, radius_km)
        ax.set_zlim(float(zlim[0]), float(zlim[1]))
        ax.set_xlabel("X (km)")
        ax.set_ylabel("Y (km)")
        ax.set_zlabel("Height (km)")
        ax.set_title(f"3D {variable}")
        fig.tight_layout()
        save_figure(fig, output_png, dpi=140)
    finally:
        plt.close(fig)
