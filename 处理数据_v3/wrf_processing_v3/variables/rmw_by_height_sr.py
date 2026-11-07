"""风暴相对切向风方位平均最大处的半径（相对 slp_center），每个高度一个数。"""

from ._storm_relative import rmw_by_height


def process(ctx):
    rmw_by_height(ctx, layer=False)
