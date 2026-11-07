"""按时次批量准备原始数据并执行派生变量。"""

from __future__ import annotations

import importlib
from typing import Any

from .context import ProcessingContext
from .dependency_planner import DependencyPlanner
from .layer_polar_safety import audit_group, select_times, write_report


class Executor:
    def __init__(self, reader: Any, registry: Any, storage: Any):
        self.reader, self.registry, self.storage = reader, registry, storage

    def execute(self, planned: list[Any], request: Any, progress=None) -> dict[str, list[str]]:
        from .storage import NetCDFStorage
        from .execution_cache import SessionStorage

        if request.dry_run or not request.options.get("processing_cache", True) or not isinstance(self.storage, NetCDFStorage):
            return self._execute(planned, request, progress)
        original_storage = self.storage
        session = SessionStorage(
            original_storage,
            max_bytes=request.options.get("processing_cache_bytes", 256 * 1024 * 1024),
        )
        self.storage = session
        try:
            return self._execute(planned, request, progress)
        finally:
            session.clear()
            self.storage = original_storage

    def _execute(self, planned: list[Any], request: Any, progress=None) -> dict[str, list[str]]:
        pending_total = sum(item.should_compute for item in planned)
        summary = {
            "computed": [],
            "reused": [],
            "planned": [],
            "skipped": [],
            "reports": [],
        }
        for item in planned:
            summary["planned"].append(str(self.storage.path_for(item.task)))
            if not item.should_compute:
                summary["reused"].append(str(self.storage.path_for(item.task)))
        if request.dry_run:
            return summary

        native_kinds = {"native", "getvar"}
        source_tasks = [
            item
            for item in planned
            if item.should_compute and item.spec.kind in native_kinds
        ]
        if source_tasks:
            # 把原始任务整体交给读取器，它会按源文件、内部时次和字段合并。
            # 读取器逐字段保存并释放数组，因此不会把全请求的三维场堆在内存中。
            if progress is not None and isinstance(self.reader, self._wrf_reader_type()):
                self.reader.prepare(
                    [item.task for item in source_tasks], request, self.registry,
                    self.storage, progress=progress,
                )
            else:
                self.reader.prepare(
                    [item.task for item in source_tasks], request, self.registry, self.storage,
                )
            summary["computed"].extend(
                str(self.storage.path_for(item.task)) for item in source_tasks
            )
        policy = request.options.get("layer_polar_boundary_policy", "off")
        if policy not in {"off", "all_safe", "continuous"}:
            raise ValueError(
                "layer_polar_boundary_policy 必须是 off/all_safe/continuous"
            )
        pending = [
            item
            for item in planned
            if item.should_compute and item.spec.kind not in native_kinds
        ]
        completed = {
            item.task
            for item in planned
            if not item.should_compute or item.spec.kind in native_kinds
        }
        skipped = set()
        layer_groups = {}
        for item in pending:
            if item.spec.options.get("center") == "layer_center" and str(
                item.spec.handler
            ).endswith(".polar:process"):
                layer_groups.setdefault(self._group_key(item.task), []).append(item)
        audits = {}
        eligible = {}

        def dependencies(item):
            return [
                DependencyPlanner._normalize(
                    DependencyPlanner._dependency_task(item.task, dependency),
                    self.registry.resolve(dependency.name),
                )
                for dependency in item.spec.dependencies
            ]

        # 大部分任务仍顺着拓扑列表一次完成。按层安全检查须等待同组所有高度
        # 的源场和中心，暂缓该组；后续就绪任务可先完成，不会抢跑依赖。
        while pending:
            progressed = False
            for item in list(pending):
                required = dependencies(item)
                group_key = self._group_key(item.task)
                members = layer_groups.get(group_key, [])
                group_has_skipped_input = policy != "off" and any(
                    task in skipped
                    for member in members
                    for task in dependencies(member)
                )
                if group_has_skipped_input:
                    # 某层跨域输入被跳过时，其余层也不能绕过整时次安全策略。
                    audits[group_key] = {
                        "experiment": item.task.experiment,
                        "domain": item.task.domain,
                        "variable": item.task.variable,
                        "time": item.task.time,
                        "heights": [],
                        "all_safe": False,
                        "reason": "upstream_dependency_skipped",
                    }
                    eligible[group_key] = False
                    skipped.add(item.task)
                elif any(task in skipped for task in required):
                    skipped.add(item.task)
                elif not all(task in completed for task in required):
                    continue
                elif policy != "off" and group_key in layer_groups:
                    if group_key not in audits:
                        members = layer_groups[group_key]
                        if not all(
                            task in completed
                            for member in members
                            for task in dependencies(member)
                        ):
                            continue
                        # continuous 按物理时间判定，用户即使倒序给时间也不会恢复续算。
                        earlier = [
                            key
                            for key in layer_groups
                            if key[:3] == group_key[:3] and key[3] < group_key[3]
                        ]
                        if policy == "continuous" and any(
                            key not in audits for key in earlier
                        ):
                            continue
                        contexts = [
                            ProcessingContext(
                                member.task,
                                request,
                                member.spec,
                                self.storage,
                                self.registry,
                            )
                            for member in members
                        ]
                        audit = audit_group(contexts)
                        audits[group_key] = audit
                        eligible[group_key] = audit["all_safe"] and (
                            policy != "continuous"
                            or all(eligible[key] for key in earlier)
                        )
                    if not eligible[group_key]:
                        skipped.add(item.task)
                if item.task in skipped:
                    summary["skipped"].append(str(self.storage.path_for(item.task)))
                else:
                    self._run_derived(item, request)
                    completed.add(item.task)
                    summary["computed"].append(str(self.storage.path_for(item.task)))
                    if progress is not None:
                        progress(f"派生切片 {len(summary['computed'])}/{pending_total}: {item.task.variable} {item.task.time_minutes}min {item.task.height_m}")
                pending.remove(item)
                progressed = True
            if not progressed:
                names = ", ".join(
                    f"{item.task.variable}@{item.task.time_minutes}min"
                    for item in pending
                )
                raise RuntimeError(f"任务的依赖或安全审计未能就绪：{names}")
        report_groups = {}
        for key, audit in audits.items():
            report_groups.setdefault(key[:3], []).append(audit)
        for (experiment, domain, variable), rows in report_groups.items():
            path = write_report(
                request.output_root,
                experiment,
                domain,
                variable,
                rows,
                select_times(rows, policy),
            )
            summary["reports"].append(str(path))
        return summary

    @staticmethod
    def _wrf_reader_type():
        from .wrf_reader import WrfReader

        return WrfReader

    @staticmethod
    def _group_key(task):
        return task.experiment, task.domain, task.variable, task.time

    def _run_derived(self, item, request):
        begin_scope = getattr(self.storage, "begin_scope", None)
        if begin_scope is not None:
            begin_scope((item.task.experiment, item.task.domain, item.task.time_minutes))
        handler = self._handler(item.spec.handler)
        if handler is None:
            raise ValueError(f"派生变量 {item.task.variable} 没有处理器")
        if request.options.get("verbose", False):
            print(
                f"计算 {item.task.experiment} d{item.task.domain:02d} "
                f"{item.task.time_minutes}min {item.task.variable} "
                f"height={item.task.height_m}",
                flush=True,
            )
        try:
            handler(
                ProcessingContext(
                    item.task, request, item.spec, self.storage, self.registry
                )
            )
        except Exception as error:
            raise RuntimeError(f"计算失败：{item.task}；{error}") from error
        if not self.storage.exists(item.task):
            raise RuntimeError(f"处理器未保存预期结果：{item.task}")

    @staticmethod
    def _handler(handler: Any) -> Any:
        if not isinstance(handler, str):
            return handler
        module_name, separator, name = handler.replace(":", ".").rpartition(".")
        if not separator:
            raise ValueError(f"无效处理器路径：{handler}")
        return getattr(importlib.import_module(module_name), name)
