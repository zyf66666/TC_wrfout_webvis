"""处理器的 Context 适配与标量诊断工具。"""

from __future__ import annotations
import numpy as np
from .. import common


def center(ctx, name="slp_center"):
    value = np.asarray(ctx.read(name), float).ravel()
    if value.size < 2:
        raise ValueError(f"{name} 必须保存 [y, x]")
    return float(value[-2]), float(value[-1])


def save(ctx, values, *, units, **metadata):
    """保存当前任务，并优先使用注册表定义的单位。"""
    resolved_units = units or getattr(ctx.spec, "units", "")
    ctx.save(
        ctx.task.variable,
        np.asarray(values),
        metadata={"units": resolved_units, **metadata},
    )


def lowest(ctx, name):
    # 读取层任务由依赖规划器映射；此处保持 ctx.read('ua') 的统一接口。
    return np.asarray(ctx.read(name), float)


def wind_at_center(ctx, component, *, layer=False):
    cy, cx = center(ctx, "layer_center" if layer else "slp_center")
    dx, dy = common.grid_km(ctx)
    return common.radial_tangential_wind(
        ctx.read("ua"),
        ctx.read("va"),
        center_y=cy,
        center_x=cx,
        dx_km=dx,
        dy_km=dy,
        component=component,
    )
