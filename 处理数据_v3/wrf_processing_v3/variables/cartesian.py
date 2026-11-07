"""把极坐标变量插值回参考笛卡尔网格。"""

from ..common import polar_to_cartesian, grid_km
from ._helpers import center, save


def process(ctx):
    # 导入：逆极坐标插值。
    # 读取：spec source 的极坐标数组、reference 的网格形状、中心。
    spec = ctx.spec.options
    source = spec["source"]
    reference = spec["reference"]
    mode = spec.get("center", "slp_center")
    center_name = "layer_center" if mode in ("layer", "layer_center") else "slp_center"
    cy, cx = center(ctx, center_name)
    dx, dy = grid_km(ctx)
    metadata = getattr(ctx, "read_metadata", lambda _name: {})(source) or {}
    resolution = metadata.get(
        "radius_resolution_km",
        ctx.options.get(
            "radius_resolution_km", ctx.options.get("polar_radius_resolution_km", dx)
        ),
    )
    # 计算保存：极坐标半径以外明确填 NaN，避免边缘值被错误外推。
    values = polar_to_cartesian(
        ctx.read(source),
        center_y=cy,
        center_x=cx,
        output_shape=ctx.read(reference).shape,
        dx_km=dx,
        dy_km=dy,
        radius_resolution_km=resolution,
        fill_value=float("nan"),
    )
    save(
        ctx,
        values,
        units=spec.get("units", ""),
        source=source,
        reference=reference,
        center_mode=mode,
        radius_resolution_km=resolution,
        fill_value="NaN",
    )
