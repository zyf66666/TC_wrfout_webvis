"""风暴倾斜相对正东的顺时针角度。"""

from .tilt_x import calculate


def process(ctx):
    """复用中心位移计算，保存倾斜方向角。"""
    # 第一段：处理器依赖由注册表声明，具体高度在共享计算函数中读取。
    # 第二段：计算高层中心相对低层中心的完整倾斜向量。
    values = calculate(ctx)
    # 第三段：保存当前任务的方向角，单位是 degree。
    ctx.save(ctx.task.variable, values["tilt_angle"], metadata={"units": "degree"})
