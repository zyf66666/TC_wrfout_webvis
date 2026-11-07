"""笛卡尔坐标的风暴相对东西风：ua 减去风暴移速的东西分量。"""

from ._storm_relative import relative_component


def process(ctx):
    relative_component(ctx, "ua")
