"""中心周围5km圆盘内平均海平面气压，单位hPa。"""

# 1. 导入库和网格间距接口。
import numpy as np
from ..common import grid_km


def process(ctx):
    # 2. 读取已准备的SLP及中心。
    pressure = np.asarray(ctx.read("slp"), dtype=float)
    center_y, center_x = ctx.read("slp_center")
    dx_km, dy_km = grid_km(ctx)
    # 3. 按物理半径筛选格点并求均值。
    rows, columns = np.indices(pressure.shape)
    radius = np.hypot((columns - center_x) * dx_km, (rows - center_y) * dy_km)
    selected = pressure[radius <= 5.0]
    if not np.isfinite(selected).any():
        raise ValueError("中心5km范围内没有有效气压")
    return ctx.save(
        ctx.task.variable, np.array([np.nanmean(selected)]), metadata={"radius_km": 5.0}
    )
