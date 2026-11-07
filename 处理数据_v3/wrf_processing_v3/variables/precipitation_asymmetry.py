"""precipitation_asymmetry：固定地理四象限的面积加权非对称度。"""

# 1. 导入已有纯算法，保持原积分与缺测值口径。
import numpy as np
from ._extra_math import calculate_fixed_quadrant_asymmetry


def process(ctx):
    # 2. 读取对应的极坐标依赖（固定高度由注册表指定）。
    field = ctx.read("RAINNCV_polar")
    config = ctx.spec.options
    source_metadata = ctx.read_metadata("RAINNCV_polar")
    resolution = source_metadata.get("radius_resolution_km", config["resolution_km"])
    # 3. 计算并保存：无量纲标量按长度为1的数组保存。
    result = calculate_fixed_quadrant_asymmetry(
        field,
        radius_resolution_km=resolution,
        radius_km=config["radius_km"],
    )
    return ctx.save(ctx.task.variable, np.asarray([result]), metadata=config)
