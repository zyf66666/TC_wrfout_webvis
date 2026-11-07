"""风暴相对切向（逆时针为正）风：减去风暴移速后相对 slp_center 分解。"""

from ._storm_relative import process_component


def process(ctx):
    process_component(ctx, "vt", layer=False)
