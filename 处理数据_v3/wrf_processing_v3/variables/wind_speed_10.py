import numpy as np
from ._helpers import save


def process(ctx):
    # 导入 / 读取 U10、V10 / 计算保存
    save(
        ctx,
        np.hypot(ctx.read("U10"), ctx.read("V10")),
        units="m s-1",
        derived_from=["U10", "V10"],
    )
