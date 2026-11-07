"""位温异常：目标域位温减去环境域同高度环带的半径加权均值。"""

# 1. 导入数值库和通用的环境环平均函数。
import numpy as np

from ._extra_math import environmental_anomaly


def process(ctx):
    # 2. 读取目标域 theta，以及注册表指定环境域的分层中心极坐标 theta。
    field = np.asarray(ctx.read("theta"), dtype=float)
    environment_field = np.asarray(ctx.read("theta1_polar"), dtype=float)
    config = ctx.spec.options
    source_metadata = getattr(ctx, "read_metadata", lambda name: {})(
        "theta1_polar"
    )
    resolution = source_metadata.get("radius_resolution_km", config["resolution_km"])
    if config["environment_domain"] == ctx.task.domain:
        raise ValueError("环境域必须与目标域不同")
    radii = np.arange(environment_field.shape[1]) * resolution

    # 3. 计算环境环平均并保存位温异常；环带端点包含在内，缺测不参与平均。
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
            "environment_theta_K": environment,
            "valid_fraction": coverage,
            "weighting": "radius; finite samples only; ring endpoints included",
        },
    )
