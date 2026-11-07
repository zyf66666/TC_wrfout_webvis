"""motf_r：匀速平移坐标系修正项 f·c_t（m s-2，二维地面场，各高度相同），c_t 为风暴移速的当地切向分量。算法见 _agradient.py。"""

import numpy as np

from . import _agradient as A
from ._helpers import save


def process(ctx):
    # slp 只用来取当前网格形状（移动嵌套每个时次网格位置不同，但形状相同）。
    shape = np.asarray(ctx.read("slp")).shape
    motion = A.storm_motion(ctx)
    radius, cos, sin, _, _, centre = A.geometry(ctx, shape)
    values = A.motion_force_r(motion[0], motion[1], cos, sin, A.coriolis_f(ctx))
    save(ctx, values.astype(np.float32), units="m s-2", formula="f * c_t",
         derived_from=["slp_center", "center_geo"], **A.common_metadata(ctx, centre, motion))
