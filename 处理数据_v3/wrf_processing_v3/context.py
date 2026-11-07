"""派生变量处理器可见的受限读写上下文。"""

from __future__ import annotations

from typing import Any

import numpy as np

from .dependency_planner import DependencyPlanner
from .request import Request, Task
from .storage import Storage


_UNSET = object()


class ProcessingContext:
    def __init__(
        self, task: Task, request: Request, spec: Any, storage: Storage, registry: Any
    ):
        self.task, self.request, self.spec, self.storage, self.registry = (
            task,
            request,
            spec,
            storage,
            registry,
        )
        self._dependencies = tuple(getattr(spec, "dependencies", ()))
        self.options, self.option_sources = self._resolved_options()

    def _resolved_options(self) -> tuple[dict[str, Any], dict[str, str]]:
        defaults = dict(getattr(self.spec, "options", {}) or {})
        options = dict(defaults)
        sources = {key: "spec" for key in defaults}
        options.update(self.request.options)
        sources.update({key: "request" for key in self.request.options})
        spacing = self._dependency_grid_spacing()
        configured = options.get("domain_config", {})
        has_domain_config = isinstance(configured, dict) and (
            self.task.domain in configured or str(self.task.domain) in configured
        )
        if spacing is not None and not has_domain_config:
            domain_config = dict(configured) if isinstance(configured, dict) else {}
            domain_config[self.task.domain] = spacing
            options["domain_config"] = domain_config
            sources["domain_config"] = "dependency_metadata"
        return options, sources

    def _dependency_grid_spacing(self) -> dict[str, Any] | None:
        """从已读原始依赖继承网格间距，不用错误的全局默认值。"""
        for dependency in self._dependencies:
            spec = self.registry.resolve(dependency.name)
            task = DependencyPlanner._normalize(
                DependencyPlanner._dependency_task(self.task, dependency), spec
            )
            if task.domain != self.task.domain:
                continue
            spacing = self.storage.load_metadata(task).get("grid_spacing")
            if (
                isinstance(spacing, dict)
                and spacing.get("dx_km")
                and spacing.get("dy_km")
            ):
                return dict(spacing)
        return None

    def read(
        self,
        variable: str,
        units: str | None = None,
        *,
        height: Any = _UNSET,
        domain: Any = _UNSET,
        time: Any = _UNSET,
    ) -> np.ndarray:
        """读取已声明依赖；同名依赖须以最终坐标选择。"""
        spec, task = self._select_dependency(
            variable, height=height, domain=domain, time=time
        )
        actual_units = getattr(spec, "units", None)
        if units is not None and actual_units != units:
            raise ValueError(
                f"变量 {variable} 单位不匹配：期望 {units}，实际 {actual_units}"
            )
        return self.storage.load(task)

    def read_metadata(
        self,
        variable: str,
        *,
        height: Any = _UNSET,
        domain: Any = _UNSET,
        time: Any = _UNSET,
    ) -> dict[str, Any]:
        """读取已声明依赖的 JSON 元数据，选择规则与 :meth:`read` 一致。"""
        _, task = self._select_dependency(
            variable, height=height, domain=domain, time=time
        )
        return self.storage.load_metadata(task)

    def read_shape(self, variable: str, *, height: Any = _UNSET,
                   domain: Any = _UNSET, time: Any = _UNSET) -> tuple[int, ...]:
        """与read相同的依赖/坐标约束，只查询切片尺寸。"""
        _, task = self._select_dependency(variable, height=height, domain=domain, time=time)
        shape = getattr(self.storage, "shape", None)
        return shape(task) if shape is not None else np.shape(self.storage.load(task))

    def cached_geometry(self, key, factory):
        """共享仅依赖完整数值参数的几何坐标，不缓存变量数值。"""
        memoize = getattr(self.storage, "cached_geometry", None)
        return memoize(key, factory) if memoize is not None else factory()

    def _select_dependency(
        self, variable: str, *, height: Any, domain: Any, time: Any
    ) -> tuple[Any, Task]:
        candidates = [item for item in self._dependencies if item.name == variable]
        if not candidates:
            raise ValueError(f"变量 {variable} 未声明为依赖")
        spec = self.registry.resolve(variable)
        mapped = []
        for dependency in candidates:
            task = DependencyPlanner._normalize(
                DependencyPlanner._dependency_task(self.task, dependency), spec
            )
            if height is not _UNSET and task.height != height:
                continue
            if domain is not _UNSET and task.domain != domain:
                continue
            if time is not _UNSET and task.time != time:
                continue
            mapped.append(task)
        if not mapped:
            raise ValueError(f"变量 {variable} 没有匹配所选坐标的已声明依赖")
        if len(mapped) > 1:
            raise ValueError(
                f"变量 {variable} 的已声明依赖存在歧义，请指定 height、domain 或 time"
            )
        task = mapped[0]
        return spec, task

    def cached_calculation(self, key, factory):
        """同请求、同依赖切片/文件版本共享纯计算；普通存储保持原路径。"""
        memoize = getattr(self.storage, "cached_calculation", None)
        if memoize is None:
            return factory()
        tasks = tuple(
            DependencyPlanner._normalize(
                DependencyPlanner._dependency_task(self.task, dependency),
                self.registry.resolve(dependency.name),
            )
            for dependency in self._dependencies
        )
        return memoize(key, tasks, factory)

    def save(
        self, variable: str, array: np.ndarray, metadata: dict[str, Any] | None = None
    ):
        if variable != self.task.variable:
            raise ValueError("上下文只能保存当前任务变量")
        result = {
            "units": getattr(self.spec, "units", None),
            "options": self.options,
            "option_sources": self.option_sources,
            # 原始用户参数用于严格缓存；resolved_options 可含读取到的网格信息。
            "request_options": self.request.options,
            "source_root": str(self.request.source_root.resolve()),
            "resolved_options": self.options,
            "dtype": self.request.dtype,
            "algorithm_version": (getattr(self.spec, "options", {}) or {}).get(
                "algorithm_version"
            ),
            "dependency_fingerprints": self._dependency_fingerprints(),
        }
        spacing = self._dependency_grid_spacing()
        if spacing is not None:
            result["grid_spacing"] = spacing
        result.update(metadata or {})
        return self.storage.save(
            self.task, array, self.spec, result, dtype=self.request.dtype
        )

    def _dependency_fingerprints(self) -> dict[str, Any]:
        fingerprints = {}
        for dependency in self._dependencies:
            dependency_spec = self.registry.resolve(dependency.name)
            task = DependencyPlanner._normalize(
                DependencyPlanner._dependency_task(self.task, dependency),
                dependency_spec,
            )
            fingerprints[str(task)] = self.storage.fingerprint(task)
        return fingerprints
