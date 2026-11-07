"""相当位温异常：目标域减去环境域同高度环带的半径加权均值。"""

# 1. 导入数值库和明确保留旧版口径的环境平均函数。
import numpy as np
from ._extra_math import environmental_anomaly


def process(ctx):
    # 2. 读取：域选择由注册表依赖声明控制，模块不自行定位文件。
    field = np.asarray(ctx.read("theta_e"), dtype=float)
    environment_field = ctx.read("theta_e1_polar")
    config = ctx.spec.options
    source_metadata = getattr(ctx, "read_metadata", lambda name: {})("theta_e1_polar")
    resolution = source_metadata.get("radius_resolution_km", config["resolution_km"])
    if config["environment_domain"] == ctx.task.domain:
        raise ValueError("环境域必须与目标域不同")
    radii = np.arange(environment_field.shape[1]) * resolution

    # 3. 计算并保存：环带端点包含在内，缺测不参与平均。
    _, environment, coverage = environmental_anomaly(
        environment_field,
        radii,
        config["ring_km"],
    )
    anomaly = np.where(np.isfinite(field), field - environment, np.nan)
    return ctx.save(
        ctx.task.variable,
        anomaly,
        metadata={
            **config,
            "environment_theta_e_K": environment,
            "valid_fraction": coverage,
            "weighting": "radius; finite samples only; ring endpoints included",
        },
    )
