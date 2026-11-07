import numpy as np
from ._scalar import tangential_profile
from ._helpers import save


def process(ctx):
    # 导入 / 读取 ua、va（lowest）和 slp_center / 计算保存
    _, means = tangential_profile(ctx)
    save(
        ctx,
        np.array([np.nanmax(means)]),
        units="m s-1",
        derived_from=["ua", "va", "slp_center"],
        level="lowest",
    )
