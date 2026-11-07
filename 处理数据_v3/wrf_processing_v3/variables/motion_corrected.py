"""可选风暴移动订正；最低模式层诊断仍保留原版地球相对风口径。"""

# 1. 导入库。
import numpy as np


def process(ctx):
    # 2. 读取地球相对风及中心移动速度。
    source = ctx.spec.options["source"]
    values = np.asarray(ctx.read(source), dtype=float)
    motion = ctx.read("storm_motion")
    # 3. 指定高度风减移动速度；RMW等最低模式层诊断不订正。
    corrected = (
        values
        if ctx.task.height == "lowest"
        else values - motion[ctx.spec.options["component"]]
    )
    return ctx.save(
        ctx.task.variable,
        corrected,
        metadata={"motion_corrected": ctx.task.height != "lowest"},
    )
