"""风暴相对径向（向外为正）风：减去风暴移速后相对 本高度 layer_center 分解。"""

from ._storm_relative import process_component


def process(ctx):
    process_component(ctx, "vr", layer=True)
