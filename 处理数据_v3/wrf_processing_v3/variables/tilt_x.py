"""风暴倾斜的东西分量。"""

from ._advanced import tilt_values
from ..common import grid_km


def process(ctx):
    """读取高低层中心，计算东西位移，并保存当前变量。"""
    # 第一段：读取变量规格中的高低层配置，保持与注册依赖相同的坐标。
    lower_height_m = ctx.spec.options.get("lower_height", 2000.0)
    upper_height_m = ctx.spec.options.get("upper_height", 8000.0)
    # 第二段：从受限上下文读取两个 layer_center 依赖，并继承实际网格间距。
    lower_center = ctx.read("layer_center", height=lower_height_m)
    upper_center = ctx.read("layer_center", height=upper_height_m)
    dx_km, dy_km = grid_km(ctx)
    values = tilt_values(
        lower_center,
        upper_center,
        dx_km=dx_km,
        dy_km=dy_km,
    )
    # 第三段：只保存当前任务对应的东西分量，单位是 km。
    ctx.save(ctx.task.variable, values["tilt_x"], metadata={"units": "km"})


def calculate(ctx):
    """供其余三个倾斜变量复用同一套依赖读取和纯算法计算。"""
    # 第一段：取得注册表固定的高低层高度。
    lower_height_m = ctx.spec.options.get("lower_height", 2000.0)
    upper_height_m = ctx.spec.options.get("upper_height", 8000.0)
    # 第二段：通过 ProcessingContext 明确选择两个对应高度的层中心。
    lower_center = ctx.read("layer_center", height=lower_height_m)
    upper_center = ctx.read("layer_center", height=upper_height_m)
    # 第三段：将网格索引差转换为四个物理倾斜量并返回给调用者。
    dx_km, dy_km = grid_km(ctx)
    return tilt_values(
        lower_center,
        upper_center,
        dx_km=dx_km,
        dy_km=dy_km,
    )
