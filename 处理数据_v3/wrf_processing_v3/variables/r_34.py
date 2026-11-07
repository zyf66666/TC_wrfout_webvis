import numpy as np
from ..common import outer_threshold_radius
from ._scalar import tangential_profile
from ._helpers import save


def process(ctx):
    # 导入 / 读取 ua、va（lowest）和 slp_center / 计算保存
    radii, means = tangential_profile(ctx)
    value = outer_threshold_radius(radii, means, 34 * 0.514444444)
    save(
        ctx,
        np.array([value]),
        units="km",
        derived_from=["ua", "va", "slp_center"],
        threshold_knots=34.0,
    )
