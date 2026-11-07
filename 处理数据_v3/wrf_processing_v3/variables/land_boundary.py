"""沿用旧理想试验口径：相邻列陆海属性不同的两侧格点作为海岸线。"""

# 1. 导入数值库。
import numpy as np


def process(ctx):
    # 2. 读取d01指定参考时次陆海掩膜及经纬度。
    land = ctx.read("XLAND")
    latitude = ctx.read("XLAT")
    longitude = ctx.read("XLONG")
    if land.shape != latitude.shape or land.shape != longitude.shape:
        raise ValueError("陆海掩膜与经纬度形状不一致")
    # 3. 计算边界点；全海洋/全陆地返回空表，由下游处理。
    boundary = np.zeros(land.shape, dtype=bool)
    boundary[:, 1:] |= land[:, 1:] != land[:, :-1]
    boundary[:, :-1] |= land[:, 1:] != land[:, :-1]
    points = np.column_stack([latitude[boundary], longitude[boundary]])
    return ctx.save(
        ctx.task.variable, points, metadata={"columns": ["latitude", "longitude"]}
    )
