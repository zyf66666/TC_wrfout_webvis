"""w1_ventilation_polar：保留向内或下沉部分的通风量，单位 kg K m-2 s-1。"""

# 1. 导入库：使用 float32 与原通风诊断保持一致。
import numpy as np


def process(ctx):
    # 2. 读取：四个依赖均为相同中心和相同极坐标网格。
    density = np.asarray(ctx.read("density1_polar"), dtype=np.float32)
    theta_anomaly = np.asarray(ctx.read("theta_e1_asy_polar"), dtype=np.float32)
    wind = np.asarray(ctx.read("wa1_polar"), dtype=np.float32)
    wind_anomaly = np.asarray(ctx.read("wa1_asy_polar"), dtype=np.float32)
    if any(
        field.shape != density.shape for field in (theta_anomaly, wind, wind_anomaly)
    ):
        raise ValueError("通风计算的输入形状必须一致")

    # 3. 计算并保存：原风速 > 0 的格点置零；其余保留风速非对称分量。
    ventilation = density * theta_anomaly * np.where(wind > 0.0, 0.0, wind_anomaly)
    source_metadata = ctx.read_metadata("density1_polar")
    geometry = {
        key: source_metadata[key]
        for key in (
            "radius_km", "radius_resolution_km", "source_shape",
            "center_mode", "angle_count",
        )
        if key in source_metadata
    }
    return ctx.save(
        ctx.task.variable,
        ventilation,
        metadata={
            **geometry,
            "formula": "rho * theta_e_asymmetry * inward_or_downward_wind_asymmetry"
        },
    )
