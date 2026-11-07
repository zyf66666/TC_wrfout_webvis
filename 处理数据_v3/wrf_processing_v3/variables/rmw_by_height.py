"""Maximum azimuthal-mean tangential-wind radius at the requested height."""

import numpy as np

from ._helpers import save
from ._scalar import tangential_profile


def process(ctx):
    radii, means = tangential_profile(ctx)
    index = int(np.nanargmax(means))
    save(
        ctx,
        np.asarray([radii[index]]),
        units="km",
        derived_from=["ua", "va", "slp_center"],
        center_mode="slp_center",
        axis_mean_max_wind_m_s=float(means[index]),
    )
