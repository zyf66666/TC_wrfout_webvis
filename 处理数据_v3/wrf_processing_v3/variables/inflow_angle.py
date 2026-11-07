import numpy as np
from ._helpers import wind_at_center, save


def process(ctx):
    # 导入 / 读取 / 计算保存
    save(
        ctx,
        np.degrees(np.arctan2(-wind_at_center(ctx, "vr"), wind_at_center(ctx, "vt"))),
        units="degree",
        derived_from=["ua", "va", "slp_center"],
        center_mode="slp",
    )
