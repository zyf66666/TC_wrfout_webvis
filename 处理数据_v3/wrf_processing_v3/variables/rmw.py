import numpy as np
from ._scalar import tangential_profile
from ._helpers import save


def process(ctx):
    # 导入 / 读取 ua、va（lowest）和 slp_center / 计算保存
    radii, means = tangential_profile(ctx)
    save(
        ctx,
        np.array([radii[np.nanargmax(means)]]),
        units="km",
        derived_from=["ua", "va", "slp_center"],
        level="lowest",
    )
