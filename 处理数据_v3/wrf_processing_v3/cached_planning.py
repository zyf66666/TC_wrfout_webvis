"""先检查处理结果；仅在无需原始输入且时间原点已验证时省略源索引。"""
from __future__ import annotations

from datetime import datetime

from .dependency_planner import DependencyPlanner
from .storage import NetCDFStorage


class _UnknownOrigin(Exception):
    pass


class _PlanningStorage(NetCDFStorage):
    """只用于规划，不写文件；在已有文件的读锁内取得并校验时间原点。"""

    def __init__(self, request, registry=None):
        from .request import storage_scheme_options

        super().__init__(request.output_root, time_origin='',
                         lock_timeout_seconds=request.options.get('lock_timeout_seconds', 120.0),
                         **storage_scheme_options(request, registry))
        self.members = set()

    def _validate_identity(self, dataset, task, layout):
        origin = getattr(dataset, 'time_origin', None)
        try:
            datetime.strptime(origin, '%Y-%m-%d_%H:%M:%S')
        except (ValueError, TypeError):
            raise _UnknownOrigin from None
        if not self.time_origin:
            self.time_origin = origin
        # 保留 schema、成员、域、变量、layout 和跨文件 time_origin 校验。
        super()._validate_identity(dataset, task, layout)
        self.members.add((task.experiment, task.domain))


def cached_plan(request, registry):
    """返回 (storage, plan)，无法安全跳过源索引时返回 None。

    不缓存源目录的时间映射，也不把请求参数中的时间原点当成源验证结果。
    仍由同一个依赖规划器判断完成标记、强制重算及跨域/逐层依赖。
    """
    storage = _PlanningStorage(request, registry)
    try:
        plan = DependencyPlanner(registry, storage).plan(request)
    except _UnknownOrigin:
        return None
    if any(item.should_compute and item.spec.kind in {'native', 'getvar'} for item in plan):
        return None
    required_members = {(exp, domain) for exp in request.experiments for domain in request.domains}
    if not storage.time_origin or not required_members.issubset(storage.members):
        return None
    explicit_origin = request.options.get('time_origin')
    if explicit_origin and explicit_origin != storage.time_origin:
        raise ValueError('缓存 time_origin 与请求不一致')
    # 执行仍使用普通存储，确保写入和请求内缓存路径完全不变。
    return NetCDFStorage(request.output_root, time_origin=storage.time_origin,
                         lock_timeout_seconds=storage.lock_timeout_seconds,
                         center_scheme=storage.center_scheme, center_dependent=storage.center_dependent), plan
