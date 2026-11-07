"""风暴相对入流角：减去风暴移速后相对 slp_center 分解，atan2(-vr, vt)。"""

from ._storm_relative import inflow_angle


def process(ctx):
    inflow_angle(ctx, layer=False)
