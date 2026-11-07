"""Compatibility entrypoint for the original RH-only safety checker."""
from .layer_polar_safety import audit_layer_polar_times


# 功能说明：把请求临时改为 RH1 的按层中心极坐标变量，并复用半径边界审计来报告哪些时次和高度可安全转换。
def audit_rh1_polar_times(request, **kwargs):
    return audit_layer_polar_times(request.model_copy(update={"variables": ["rh1_polar"]}), **kwargs)
