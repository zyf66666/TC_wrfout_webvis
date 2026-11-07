import numpy as np

from ..common import cartesian_to_polar, grid_km, polar_radius_km
from ._helpers import center, save


def process(ctx):
    # 导入 / 读取 spec.options.source 与指定中心 / 计算保存
    spec = ctx.spec.options
    source = spec["source"]
    mode = spec.get("center", "slp_center")
    center_name = "layer_center" if mode in ("layer", "layer_center") else "slp_center"
    cy, cx = center(ctx, center_name)
    dx, dy = grid_km(ctx)
    # 半径按“变量 > domain > 全局”取，见 common.polar_radius_km。
    radius = polar_radius_km(ctx.options, ctx.task.variable, ctx.task.domain)
    resolution = ctx.options.get(
        "radius_resolution_km", ctx.options.get("polar_radius_resolution_km")
    )
    data = ctx.read(source)
    values, meta = cartesian_to_polar(
        data,
        center_y=cy,
        center_x=cx,
        dx_km=dx,
        dy_km=dy,
        radius_km=radius,
        radius_resolution_km=resolution,
        coordinate_cache=getattr(ctx, "cached_geometry", None),
    )
    # 旧绘图元数据使用 radius 与 source_shape；保留新旧两套键以便路径管理器复用。
    meta["radius"] = meta["radius_km"]
    meta["source_shape"] = list(np.shape(data))
    save(
        ctx,
        values,
        units=spec.get("units", ""),
        source=source,
        center_mode=mode,
        **meta
    )
