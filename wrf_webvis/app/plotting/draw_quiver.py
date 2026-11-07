from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .draw_utils import (
    VARIABLE_TYPE_REGISTRY,
)
from .draw_variables import prepare_variable_data


# 功能说明：把新旧格式的风箭头设置整理为统一形式，并补上箭头比例、间距、颜色等默认值。
def normalize_quiver_config(variable: str, spec: Any) -> dict[str, Any]:
    if isinstance(spec, dict):
        config = dict(spec)
        config.setdefault("enabled", True)
        config.setdefault("scale", None)
        config.setdefault("interval", 1)
        config.setdefault("color", "k")
        config.setdefault("scale_factor", 1.0)
        config.setdefault("times", None)
        config.setdefault("height", None)
        config.setdefault("angle", None)
        config.setdefault("radius", None)
        return config

    if isinstance(spec, (list, tuple)):
        return {
            "enabled": True,
            "scale": spec[0] if len(spec) > 0 else None,
            "interval": spec[1] if len(spec) > 1 else 1,
            "color": spec[2] if len(spec) > 2 else "k",
            "scale_factor": spec[3] if len(spec) > 3 else 1.0,
            "times": None,
            "height": None,
            "angle": None,
            "radius": None,
        }

    raise TypeError(f"{variable} 的 quiver 配置必须是 dict/list/tuple")


# 功能说明：风箭头绘制入口（只接受命名参数；2026-11-01 删除了旧式位置参数入口）。
def draw_quiver(ax, **kwargs: Any):
    return _draw_quiver_new(ax=ax, **kwargs)


# 功能说明：读取选中的两个或多个风分量，检查输入并画箭头；预检查模式只返回输入信息，正式模式要求已有画布。
def _draw_quiver_new(
    *,
    ax,
    member_name: str,
    domain: int,
    variables_quiver: dict[str, Any],
    fig_type: int,
    data_root: str | Path = ".",
    domain_config: dict[int, dict[str, Any]] | None = None,
    selection: dict[str, Any] | None = None,
    polar_dir_style: str = "same",
    polar_radius_resolution_km: float | None = None,
    variable_type_registry: dict[str, str] | None = None,
    format_config: dict[str, Any] | None = None,
    headwidth: float = 3,
    headlength: float = 5,
    stream: bool | dict[str, Any] | list[Any] | tuple[Any, ...] = False,
    double_azimuth: bool = False,
    dry_run: bool = False,
    return_data: bool = False,
    progress=None,
):
    quiver_variables = {
        variable: normalize_quiver_config(variable, spec)
        for variable, spec in variables_quiver.items()
        if normalize_quiver_config(variable, spec).get("enabled", True)
    }

    payloads, summaries = prepare_variable_data(
        member_name=member_name,
        domain=domain,
        variables=quiver_variables,
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
            return ax, None, payloads, summaries
        return ax, None

    if ax is None:
        raise ValueError("非 dry-run 模式必须提供 matplotlib Axes")
    if len(payloads) < 2:
        raise ValueError("画 quiver 至少需要两个变量，例如 ua 和 va")

    q = quiver_plot(
        ax,
        payloads,
        headwidth=headwidth,
        headlength=headlength,
        stream=stream,
        double_azimuth=double_azimuth,
    )
    if return_data:
        return ax, q, payloads, summaries
    return ax, q


# 功能说明：取前两个分量作为箭头的横向和纵向分量，按间距抽取格点以免过密，然后绘制箭头或表示流动路径的流线。
def quiver_plot(
    ax,
    payloads: dict[str, dict[str, Any]],
    *,
    headwidth: float,
    headlength: float,
    stream: bool | dict[str, Any] | list[Any] | tuple[Any, ...],
    double_azimuth: bool,
):
    names = list(payloads)
    u_payload = payloads[names[0]]
    v_payload = payloads[names[1]]

    u_values = np.asarray(u_payload["values"], dtype=float)
    v_values = np.asarray(v_payload["values"], dtype=float)
    x = np.asarray(u_payload["x"], dtype=float)
    y = np.asarray(u_payload["y"], dtype=float)

    if double_azimuth:
        u_values = np.concatenate((u_values, u_values), axis=1)
        v_values = np.concatenate((v_values, v_values), axis=1)
        x = np.concatenate((x, x + 360))

    if u_values.shape != v_values.shape:
        raise ValueError(
            f"quiver 两个变量 shape 不一致: {names[0]}={u_values.shape}, {names[1]}={v_values.shape}"
        )

    x_interval, y_interval = resolve_intervals(u_payload["config"], v_payload["config"])
    x_selected = x[::x_interval]
    y_selected = y[::y_interval]
    u_selected = u_values[::y_interval, ::x_interval]
    v_selected = v_values[::y_interval, ::x_interval]

    if stream:
        stream_config = normalize_stream_config(stream, u_payload["config"])
        ax.streamplot(
            x,
            y,
            u_values,
            v_values,
            linewidth=stream_config["linewidth"],
            color=stream_config["color"],
            density=stream_config["density"],
            zorder=stream_config["zorder"],
        )
        return None

    X, Y = np.meshgrid(x_selected, y_selected)
    q = ax.quiver(
        X,
        Y,
        u_selected,
        v_selected,
        scale=u_payload["config"].get("scale"),
        color=u_payload["config"].get("color", "k"),
        zorder=u_payload["config"].get("zorder", 5),
        headwidth=headwidth,
        headlength=headlength,
    )
    return q


# 功能说明：决定横向、纵向每隔几个格点画一个箭头，最小间隔为1，防止出现无效步长。
def resolve_intervals(u_config: dict[str, Any], v_config: dict[str, Any]) -> tuple[int, int]:
    u_interval = u_config.get("interval", 1)
    v_interval = v_config.get("interval", u_interval)
    if isinstance(u_interval, (list, tuple)):
        x_interval = int(u_interval[0])
        y_interval = int(u_interval[1] if len(u_interval) > 1 else u_interval[0])
    else:
        x_interval = int(u_interval)
        y_interval = int(v_interval[1] if isinstance(v_interval, (list, tuple)) and len(v_interval) > 1 else v_interval)
    return max(x_interval, 1), max(y_interval, 1)


# 功能说明：把流线开关或详细配置整理成线宽、颜色、密度与叠放顺序设置。
def normalize_stream_config(stream: bool | dict[str, Any] | list[Any] | tuple[Any, ...], variable_config: dict[str, Any]) -> dict[str, Any]:
    if isinstance(stream, dict):
        return {
            "linewidth": stream.get("linewidth", 1.5),
            "color": stream.get("color", variable_config.get("color", "k")),
            "density": stream.get("density", 1.0),
            "zorder": stream.get("zorder", 5),
        }
    if isinstance(stream, (list, tuple)):
        return {
            "linewidth": stream[0] if len(stream) > 0 else 1.5,
            "color": stream[1] if len(stream) > 1 else variable_config.get("color", "k"),
            "density": stream[2] if len(stream) > 2 else 1.0,
            "zorder": stream[3] if len(stream) > 3 else 5,
        }
    return {
        "linewidth": 1.5,
        "color": variable_config.get("color", "k"),
        "density": 1.0,
        "zorder": 5,
    }


# 功能说明：从箭头变量清单中筛出已开启的项，关闭的变量不进入绘图。
def enabled_quiver_variables(variables_quiver: dict[str, Any]) -> dict[str, Any]:
    return {
        variable: spec
        for variable, spec in variables_quiver.items()
        if normalize_quiver_config(variable, spec).get("enabled", True)
    }
