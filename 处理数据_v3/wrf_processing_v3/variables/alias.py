"""历史名称的兼容入口；别名与原变量共享计算结果。"""

# 1. 无需额外数值库，直接使用上下文接口。


def process(ctx):
    # 2. 注册表声明别名实际指向的变量。
    source = ctx.spec.options["source"]
    values = ctx.read(source)
    # 3. 以请求名称保存，保留来源说明。
    return ctx.save(ctx.task.variable, values, metadata={"alias_of": source})
