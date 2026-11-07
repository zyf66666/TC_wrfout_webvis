"""边界层质量通量辐合的垂直积分。"""

from ._advanced import integrate_layers


def process(ctx):
    """读取已派生的质量辐合层，积分后保存当前边界层变量。"""
    # 第一段：从注册变量规格取得源变量和固定边界层高度。
    heights = ctx.spec.options["heights"]
    source_variable = ctx.spec.options["source"]
    # 第二段：仅按已声明的 mass_convergence 高度依赖读取，保留上游算法口径。
    layers = [ctx.read(source_variable, height=height) for height in heights]
    # 第三段：对层值进行梯形积分，并用统一上下文保存当前任务变量。
    ctx.save(
        ctx.task.variable,
        integrate_layers(layers, heights),
        metadata={
            "units": "kg m-2 s-1",
            "heights": [float(height) for height in heights],
            "source": source_variable,
        },
    )
