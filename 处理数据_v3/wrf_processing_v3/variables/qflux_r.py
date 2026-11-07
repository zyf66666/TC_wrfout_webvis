from ..common import moist_air_density
from ._helpers import wind_at_center, save


def process(ctx):
    # 导入 / 读取 / 计算保存
    rho = moist_air_density(ctx.read("pressure"), ctx.read("temp"), ctx.read("QVAPOR"))
    save(
        ctx,
        rho * ctx.read("QVAPOR") * wind_at_center(ctx, "vr"),
        units="kg m-2 s-1",
        derived_from=["ua", "va", "pressure", "temp", "QVAPOR", "slp_center"],
        center_mode="slp",
    )
