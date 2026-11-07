"""高级诊断的纯计算核心。"""

from __future__ import annotations
import numpy as np


def integrate_layers(values, heights):
    """按高度坐标以梯形法积分逐层二维诊断量。"""
    # 第一段：导入数组和高度坐标，确保轴零确实对应垂直层。
    layer_values = np.asarray(values, dtype=float)
    layer_heights = np.asarray(heights, dtype=float)
    if (
        layer_values.ndim < 1
        or layer_values.shape[0] != layer_heights.size
        or layer_heights.size < 2
    ):
        raise ValueError("层数与高度不匹配")
    # 第二段：高度坐标由注册表排序，显式检查单调性以避免负厚度积分。
    if np.any(np.diff(layer_heights) <= 0.0):
        raise ValueError("边界层高度必须严格递增")
    # 第三段：沿第零轴积分，结果保留原水平网格的形状。
    trapezoid = getattr(np, "trapezoid", np.trapz)
    return trapezoid(layer_values, x=layer_heights, axis=0)


def tilt_values(lower, upper, *, dx_km, dy_km):
    """由高层相对低层中心的位移计算风暴倾斜四个量。"""
    # 第一段：导入中心索引和网格间距，并转换为可重复的浮点计算。
    lower_y, lower_x = map(float, lower)
    upper_y, upper_x = map(float, upper)
    spacing_x_km = float(dx_km)
    spacing_y_km = float(dy_km)
    if spacing_x_km <= 0.0 or spacing_y_km <= 0.0:
        raise ValueError("dx_km 与 dy_km 必须为正数")
    # 第二段：读取高低层中心差异，将网格索引位移转换为物理距离。
    tilt_x_km = (upper_x - lower_x) * spacing_x_km
    tilt_y_km = (upper_y - lower_y) * spacing_y_km
    # 第三段：计算长度和顺时针为负、从正东起算的旧版方向角。
    return {
        "tilt_x": tilt_x_km,
        "tilt_y": tilt_y_km,
        "tilt_magnitude": float(np.hypot(tilt_x_km, tilt_y_km)),
        "tilt_angle": float(-np.degrees(np.arctan2(tilt_y_km, tilt_x_km))),
    }
