"""湿空气密度。"""

import numpy as np
from ..common import fill_nan
from ._helpers import save


def process(ctx):
    # 导入：与旧 density.ipynb 相同的 MetPy 湿空气状态方程，延迟导入避免框架启动时加载单位系统。
    from metpy.calc import density as metpy_density
    from metpy.units import units

    # 读取：pressure、temp、QVAPOR 为同一高度层。
    p, t, q = (
        fill_nan(np.asarray(ctx.read(name), dtype=float))
        for name in ("pressure", "temp", "QVAPOR")
    )
    if p.shape != t.shape or p.shape != q.shape:
        raise ValueError("pressure、temp 和 QVAPOR 形状必须一致")
    values = (
        metpy_density(p * units.hPa, t * units.K, q * 1000.0 * units("g/kg"))
        .to("kg/m^3")
        .magnitude
    )
    # 计算保存。
    save(
        ctx,
        fill_nan(np.asarray(values, dtype=np.float32)).astype(np.float32, copy=False),
        units="kg m-3",
        formula="metpy.calc.density(pressure[hPa], temp[K], QVAPOR[g/kg])",
        derived_from=["pressure", "temp", "QVAPOR"],
    )
