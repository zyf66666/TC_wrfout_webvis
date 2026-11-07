from ..common import grid_km, horizontal_convergence
from ._helpers import save


def process(ctx):
    # 导入 / 读取 ua、va / 计算保存
    dx, dy = grid_km(ctx)
    save(
        ctx,
        horizontal_convergence(
            ctx.read("ua"), ctx.read("va"), dx_m=dx * 1000, dy_m=dy * 1000
        ),
        units="s-1",
        formula="-(du/dx+dv/dy)",
        derived_from=["ua", "va"],
    )
