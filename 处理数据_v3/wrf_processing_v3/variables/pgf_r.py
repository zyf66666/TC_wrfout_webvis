"""pgf_r：气压梯度力径向分量 −(1/ρ)∂p/∂r（m s-2，向外为正），以 slp_center 为原点。算法见 _agradient.py。"""

import numpy as np

from . import _agradient as A
from ._helpers import save


def process(ctx):
    # 读取同一高度的 pressure（hPa）、density（kg m-3）与地面中心；等高面上求水平梯度再投影到径向。
    pressure = np.asarray(ctx.read("pressure"), dtype=float)
    density = np.asarray(ctx.read("density"), dtype=float)
    if pressure.shape != density.shape or pressure.ndim != 2:
        raise ValueError("pressure 与 density 必须为同形二维数组")
    radius, cos, sin, dx_m, dy_m, centre = A.geometry(ctx, pressure.shape)
    values = A.pressure_gradient_force_r(pressure, density, cos, sin, dx_m, dy_m)
    save(ctx, values.astype(np.float32), units="m s-2", formula="-(1/rho) dp/dr",
         derived_from=["pressure", "density", "slp_center"], **A.common_metadata(ctx, centre))
