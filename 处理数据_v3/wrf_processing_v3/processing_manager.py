"""Public processing entry point for the v3 storage pipeline."""

from __future__ import annotations

from typing import Any

from .cache_compatibility import check_cache_compatibility
from .dependency_planner import DependencyPlanner
from .source_index import SourceIndex
from .storage import NetCDFStorage


class ProcessingManager:
    def __init__(self, registry: Any = None, reader: Any = None):
        self.registry = registry
        self.reader = reader

    def process(self, request: Any, progress=None) -> dict[str, list[str]]:
        from .request import storage_scheme_options, with_center_scheme

        # 2026-11-02：先按中心方案并入中心参数（center_scheme 为 None 时不变）。
        request = with_center_scheme(request)
        verbose = bool(request.options.get("verbose", False))
        progress_callback = progress

        def report(message: str) -> None:
            if verbose:
                print(message, flush=True)
            if progress_callback is not None:
                progress_callback(message)

        registry = self.registry
        if registry is None:
            from .register import build_registry

            registry = build_registry(
                request.options, times_minutes=request.times_minutes
            )
        reader = self.reader
        if reader is None:
            from .cached_planning import cached_plan

            cached = cached_plan(request, registry)
            if cached is not None:
                storage, plan = cached
                check_cache_compatibility(plan, storage, request.options)
                self._prepare_scheme(request, plan)
                report(f"缓存时间原点已验证：{storage.time_origin}；无需原始输入，跳过 WRF 源索引扫描")
                from .executor import Executor

                return Executor(None, registry, storage).execute(plan, request, progress=progress_callback)
        if reader is None:
            from .wrf_reader import WrfReader

            report("  1/3 扫描 WRF 源文件并建立时间索引...")
            index = SourceIndex(request)
            origins = {
                index.time_origin(experiment, domain)
                for experiment in request.experiments
                for domain in request.domains
            }
            if len(origins) != 1:
                raise ValueError(f"请求域的 START_DATE 不一致：{sorted(origins)}")
            time_origin = origins.pop()
            reader = WrfReader(index=index)
            report(f"  1/3 源文件索引完成，START_DATE={time_origin}")
        else:
            time_origin = request.options.get("time_origin")
            if not time_origin:
                raise ValueError("注入自定义 reader 时必须在 options 中提供 time_origin")
        storage = NetCDFStorage(
            request.output_root,
            time_origin=str(time_origin),
            lock_timeout_seconds=float(
                request.options.get("lock_timeout_seconds", 120.0)
            ),
            **storage_scheme_options(request, registry),
        )
        report("  2/3 建立依赖计划并批量检查已有切片...")
        plan = DependencyPlanner(registry, storage).plan(request)
        check_cache_compatibility(plan, storage, request.options)
        self._prepare_scheme(request, plan)
        pending = sum(item.should_compute for item in plan)
        report(f"  2/3 依赖计划完成：{len(plan)} 个节点，{pending} 个待计算")
        from .executor import Executor

        report("  3/3 开始执行..." if not request.dry_run else "  3/3 dry-run 完成")
        return Executor(reader, registry, storage).execute(plan, request, progress=progress_callback)


    @staticmethod
    def _prepare_scheme(request: Any, plan: Any) -> None:
        """非 base 方案实际计算前，在 centers/<方案>/ 写入（或核对）参数快照 scheme.json。"""
        scheme = getattr(request, "center_scheme", None)
        if scheme in (None, "base") or request.dry_run or not any(item.should_compute for item in plan):
            return
        from .center_schemes import ensure_scheme_manifest

        ensure_scheme_manifest(request.output_root, scheme)
