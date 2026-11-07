"""SLP 中心径向风。"""

from ._helpers import wind_at_center, save


def process(ctx):
    # 导入：共享投影算法。
    # 读取：ua、va 与 slp_center。
    values = wind_at_center(ctx, "vr")
    # 计算保存。
    save(
        ctx,
        values,
        units="m s-1",
        center_mode="slp",
        derived_from=["ua", "va", "slp_center"],
    )
