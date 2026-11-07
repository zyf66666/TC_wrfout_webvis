"""cor_r：科氏力项 f·v_t'（m s-2），v_t' 为风暴相对切向风，以 slp_center 为原点。算法见 _agradient.py。"""

import numpy as np

from . import _agradient as A
from ._helpers import save


def process(ctx):
    vt, radius, motion, centre = A.storm_relative_vt(ctx)
    values = A.coriolis_r(vt, A.coriolis_f(ctx))
    save(ctx, values.astype(np.float32), units="m s-2", formula="f * vt_storm_relative",
         derived_from=["ua", "va", "slp_center", "center_geo"], **A.common_metadata(ctx, centre, motion))
