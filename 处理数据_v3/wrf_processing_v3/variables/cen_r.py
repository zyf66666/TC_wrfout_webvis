"""cen_r：离心力项 v_t'^2/r（m s-2），v_t' 为风暴相对切向风，以 slp_center 为原点；中心 2 km 内为缺测。算法见 _agradient.py。"""

import numpy as np

from . import _agradient as A
from ._helpers import save


def process(ctx):
    vt, radius, motion, centre = A.storm_relative_vt(ctx)
    values = A.centrifugal_r(vt, radius)
    save(ctx, values.astype(np.float32), units="m s-2", formula="vt_storm_relative**2 / r",
         derived_from=["ua", "va", "slp_center", "center_geo"], **A.common_metadata(ctx, centre, motion))
