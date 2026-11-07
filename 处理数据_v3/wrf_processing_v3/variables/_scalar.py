import numpy as np
from .. import common
from ._helpers import center, save, wind_at_center


def tangential_profile(ctx, *, layer=False):
    dx, dy = common.grid_km(ctx)
    def compute():
        cy, cx = center(ctx, "layer_center" if layer else "slp_center")
        vt = wind_at_center(ctx, "vt", layer=layer)
        return common.azimuthal_mean(vt, center_y=cy, center_x=cx, dx_km=dx, dy_km=dy)
    memoize = getattr(ctx, "cached_calculation", None)
    if memoize is None:
        return compute()
    return memoize(("tangential_profile", layer, dx, dy), compute)
