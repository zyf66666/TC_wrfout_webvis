"""RMW at each requested height relative to that height's pressure center."""

import numpy as np

from ._helpers import save
from ._scalar import tangential_profile


def process(ctx):
    radii, means = tangential_profile(ctx, layer=True)
    index = int(np.nanargmax(means))
    save(
        ctx,
        np.asarray([radii[index]]),
        units="km",
        derived_from=["ua", "va", "layer_center"],
        center_mode="layer_center",
        axis_mean_max_wind_m_s=float(means[index]),
    )
