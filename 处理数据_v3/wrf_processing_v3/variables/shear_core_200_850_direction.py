"""内核区（目标域 slp_center 150–350 km 环带）200–850 hPa 风切变方向角。"""

import numpy as np

from ._shear import shear_values


def process(ctx):
    return ctx.save(
        ctx.task.variable,
        np.asarray([shear_values(ctx)["direction"]]),
        metadata=ctx.spec.options,
    )
