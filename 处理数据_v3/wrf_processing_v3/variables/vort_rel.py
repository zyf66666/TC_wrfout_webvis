"""等高面上模型网格垂直相对涡度，不含科氏参数。"""
import numpy as np
from ..common import grid_km
from ._helpers import save


def calculate(u, v, *, dx_m, dy_m):
    """以米为水平间距：内部二阶中心差分，边界二阶单边差分。"""
    u, v = np.asarray(u, dtype=float), np.asarray(v, dtype=float)
    if u.shape != v.shape or u.ndim != 2 or min(u.shape) < 3:
        raise ValueError("ua/va 必须是相同形状、至少 3×3 的二维场")
    if dx_m <= 0 or dy_m <= 0:
        raise ValueError("网格间距必须为正")
    return np.gradient(v, dx_m, axis=1, edge_order=2) - np.gradient(u, dy_m, axis=0, edge_order=2)


def process(ctx):
    dx, dy = grid_km(ctx)
    save(ctx, calculate(ctx.read("ua"), ctx.read("va"), dx_m=dx*1000, dy_m=dy*1000),
         units="s-1", formula="dv/dx-du/dy on constant-height grid; no Coriolis term",
         derived_from=["ua", "va"])
