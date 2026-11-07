from ..common import grid_km, moist_air_density, horizontal_convergence
from ._helpers import save


def process(ctx):
    # 导入 / 读取 / 计算保存
    rho = moist_air_density(ctx.read("pressure"), ctx.read("temp"), ctx.read("QVAPOR"))
    dx, dy = grid_km(ctx)
    save(
        ctx,
        horizontal_convergence(
            rho * ctx.read("ua"), rho * ctx.read("va"), dx_m=dx * 1000, dy_m=dy * 1000
        ),
        units="kg m-3 s-1",
        derived_from=["ua", "va", "pressure", "temp", "QVAPOR"],
    )
