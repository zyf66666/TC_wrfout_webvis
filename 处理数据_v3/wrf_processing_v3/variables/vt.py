from ._helpers import wind_at_center, save


def process(ctx):
    # 导入 / 读取 / 计算保存
    save(
        ctx,
        wind_at_center(ctx, "vt"),
        units="m s-1",
        center_mode="slp",
        derived_from=["ua", "va", "slp_center"],
    )
