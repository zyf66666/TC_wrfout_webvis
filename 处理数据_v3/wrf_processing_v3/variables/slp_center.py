import numpy as np
from ..common import grid_km
from ._helpers import save
from ._center_options import CENTER_ALGORITHMS, slp_center_algorithm


def process(ctx):
    # 导入：独立的中心定位算法（登记表 _center_options.CENTER_ALGORITHMS，如 v1 Bezier PCC、v2 平滑首猜 + 浮点 PCC）。
    # 读取：SLP 与 domain_config、中心算法请求选项（来自中心方案）。
    data = np.asarray(ctx.read("slp"), dtype=float)
    dx, dy = grid_km(ctx)
    algorithm, algorithm_options = slp_center_algorithm(ctx.options, ctx.task.domain)
    y, x, extra = CENTER_ALGORITHMS[algorithm].locate(data, dx_km=dx, dy_km=dy, **algorithm_options)
    iy = min(max(int(round(y)), 0), data.shape[0] - 1)
    ix = min(max(int(round(x)), 0), data.shape[1] - 1)
    value = float(data[iy, ix])
    # 计算保存：中心数组稳定保存为整数 [y, x]，气压与该取整格点对应；
    # v2 的浮点中心、首猜和迭代信息写在切片元数据里。
    save(
        ctx,
        np.array([iy, ix]),
        units="grid_index",
        pressure_hpa=value,
        algorithm=algorithm,
        **algorithm_options,
        **extra,
        derived_from=["slp"],
    )
