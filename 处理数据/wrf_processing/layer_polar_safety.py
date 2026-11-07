"""Check cached variable grids and layer centres before fixed-radius interpolation."""

from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np

from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.schemas import ProcessingRequest, VariableDimension
from wrf_webvis.app.core.registry import VARIABLE_REGISTRY, resolve_variable_name
from .radial_tangential_wind import RADIAL_TANGENTIAL_VARIABLES
from wrf_webvis.app.core.selection import expand_selection


# 功能说明：把层极坐标变量名映射到它对应的源变量。
def layer_polar_source(variable: str) -> str | None:
    """Resolve direct layer-centred polar fields, excluding SLP and diagnostics."""
    if not variable.endswith("_polar"):
        return None
    resolved = resolve_variable_name(variable[:-len("_polar")])
    item = VARIABLE_REGISTRY.get(resolved.base_name)
    if resolved.center_mode != "layer" or item is None or item.dimension != VariableDimension.THREE_D:
        return None
    return resolved.requested_name if resolved.requested_name in RADIAL_TANGENTIAL_VARIABLES else resolved.base_name


# 功能说明：针对一个按层中心的三维极坐标变量，逐时次和高度审计请求半径是否越过网格边界，并返回安全时段报告。
def audit_layer_polar_times(
    request: ProcessingRequest,
    *,
    domain_config: dict[int, dict[str, Any]],
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    variable = request.variables[0]
    source_variable = layer_polar_source(variable)
    if source_variable is None:
        raise ValueError(f"不是已注册的按层中心三维极坐标变量：{variable}")
    radius = float(request.options["polar_max_radius_km"])
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError("按层极坐标安全检查要求 polar_max_radius_km 为有限正数")
    times = sorted(set(expand_selection(request.times)))
    heights = sorted(set(expand_selection(request.heights)))
    if not times or not heights:
        raise ValueError("按层极坐标安全检查需要明确的时间和高度")
    config = domain_config[request.domain]
    dx = float(config.get("dx_km") or 1)
    dy = float(config.get("dy_km") or dx)
    if not all(math.isfinite(v) and v > 0 for v in (dx, dy)):
        raise ValueError("网格间距必须为有限正数")
    paths = PathManager(request.processed_root)
    centers = {}
    for height in heights:
        path = paths.layer_center_path(request.member_name, request.domain, height)
        table = np.atleast_2d(np.loadtxt(path))
        if table.shape[1] < 2:
            raise ValueError(f"中心文件格式错误：{path}")
        centers[height] = table

    per_layer, per_time, safe_times, unsafe_times = [], [], [], []
    continuous_end = None
    interrupted = False
    for index, time in enumerate(times):
        layers = []
        for height in heights:
            table = centers[height]
            if table.shape[1] >= 3:
                matches = np.flatnonzero(np.isclose(table[:, 0], time))
                if not matches.size:
                    raise ValueError(f"{request.member_name} {height:g}m 中心缺少 {time:g}h；请关闭仅检查模式补算")
                y, x = table[matches[0], 1:3]
            else:
                if time < 0 or int(time) != time or int(time) >= len(table):
                    raise ValueError(f"{request.member_name} {height:g}m 中心缺少 {time:g}h")
                y, x = table[int(time), :2]
            source = paths.variable_path(member_name=request.member_name, domain=request.domain,
                                         variable=source_variable, time_hour=time, height_m=height, dimension="3d")
            field = np.load(source, mmap_mode="r")
            shape = field.shape
            del field
            if len(shape) != 2:
                raise ValueError(f"{source_variable} 层必须是二维网格：{source}: {shape}")
            ny, nx = shape
            finite_center = bool(np.isfinite([y, x]).all())
            distance = float(min(y * dy, (ny - 1 - y) * dy, x * dx, (nx - 1 - x) * dx)) if finite_center else None
            safe = finite_center and distance >= radius - 1e-8
            row = {"time_h": time, "height_m": height, "center_y": float(y) if finite_center else None,
                   "center_x": float(x) if finite_center else None, "source_shape": list(shape),
                   "safe_radius_km": distance, "safe": safe}
            layers.append(row)
            per_layer.append(row)
        limiting = min(layers, key=lambda row: row["safe_radius_km"] if row["safe_radius_km"] is not None else -math.inf)
        safe = all(row["safe"] for row in layers)
        (safe_times if safe else unsafe_times).append(time)
        interrupted = interrupted or not safe
        if not interrupted:
            continuous_end = time
        per_time.append({"time_h": time, "safe": safe, "safe_radius_km": limiting["safe_radius_km"],
                         "limiting_height_m": limiting["height_m"]})
        if progress and (index % 10 == 0 or index == len(times) - 1):
            progress(f"检查 {request.member_name} {variable} 边界 {time:g}h ({index + 1}/{len(times)})")
    return {"variable": variable, "source_variable": source_variable, "member": request.member_name, "domain": request.domain, "radius_km": radius,
            "dx_km": dx, "dy_km": dy, "heights_m": heights, "requested_times": times,
            "safe_times": safe_times, "unsafe_times": unsafe_times, "continuous_end": continuous_end,
            "last_safe_time": safe_times[-1] if safe_times else None,
            "per_time": per_time, "per_layer": per_layer}
