import numpy as np
from ._helpers import lowest, save


def process(ctx):
    # 导入 / 读取 ua、va（lowest） / 计算保存
    save(
        ctx,
        np.array([np.nanmax(np.hypot(lowest(ctx, "ua"), lowest(ctx, "va")))]),
        units="m s-1",
        derived_from=["ua", "va"],
        level="lowest",
    )
