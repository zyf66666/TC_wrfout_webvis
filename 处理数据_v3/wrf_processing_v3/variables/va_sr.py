"""笛卡尔坐标的风暴相对南北风：va 减去风暴移速的南北分量。"""

from ._storm_relative import relative_component


def process(ctx):
    relative_component(ctx, "va")
