"""风暴相对径向水汽通量：ρ × QVAPOR × 风暴相对径向风（相对当前高度 layer_center，向外为正）。"""

from ._storm_relative import radial_moisture_flux


def process(ctx):
    radial_moisture_flux(ctx, layer=True)
