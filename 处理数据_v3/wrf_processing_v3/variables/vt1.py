from ._helpers import wind_at_center, save


def process(ctx):
    # 导入 / 读取（本高度 layer_center）/ 计算保存
    save(
        ctx,
        wind_at_center(ctx, "vt", layer=True),
        units="m s-1",
        center_mode="layer",
        derived_from=["ua", "va", "layer_center"],
    )
