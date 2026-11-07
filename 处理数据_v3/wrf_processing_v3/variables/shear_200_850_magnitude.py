"""200–850 hPa 环境风切变强度。"""

import numpy as np

from ._shear import shear_values


def process(ctx):
    return ctx.save(
        ctx.task.variable,
        np.asarray([shear_values(ctx)["magnitude"]]),
        metadata=ctx.spec.options,
    )
