"""把台风中心格点映射到当前时次的经纬度；适用于移动嵌套网格。"""

# 1. 导入数值库。
import numpy as np


def process(ctx):
    # 2. 读取中心与当前时次坐标，不能使用首时次网格替代移动网格。
    center_y, center_x = np.asarray(ctx.read("slp_center")).ravel()[:2]
    latitude = ctx.read("XLAT")
    longitude = ctx.read("XLONG")
    row, column = int(round(center_y)), int(round(center_x))

    # 3. 保存 [纬度, 经度]，后续距离与移动速度共享这个依赖。
    if latitude.shape != longitude.shape or not (
        0 <= row < latitude.shape[0] and 0 <= column < latitude.shape[1]
    ):
        raise ValueError("中心超出经纬度网格或经纬度形状不一致")
    return ctx.save(
        ctx.task.variable,
        np.array([latitude[row, column], longitude[row, column]]),
        metadata={"columns": ["latitude", "longitude"]},
    )
