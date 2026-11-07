"""Expand minute-native task dependencies and decide slice reuse."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .register import Dependency
from .request import Request, Task
from .storage import DEFAULT_HEIGHTS_M, NetCDFStorage
from .variables._center_options import layer_center_mode


@dataclass(frozen=True, slots=True)
class PlannedTask:
    task: Task
    spec: Any
    explicit: bool
    should_compute: bool


class DependencyPlanner:
    def __init__(self, registry: Any, storage: NetCDFStorage | None = None):
        self.registry = registry
        self.storage = storage

    def plan(self, request: Request) -> list[PlannedTask]:
        from .request import storage_scheme_options, with_center_scheme

        request = with_center_scheme(request)  # 中心方案参数并入 options（重复调用结果不变）

        storage = self.storage or NetCDFStorage(
            request.output_root,
            time_origin=str(request.options.get("time_origin", "START_DATE")),
            **storage_scheme_options(request, self.registry),
        )
        force_variables = self._force_variables(request)
        force_slices = self._force_slices(request)
        selective_force = bool(force_variables or force_slices)
        explicit = {
            self._normalize(task, self.registry.resolve(task.variable))
            for task in request.tasks
        }
        ordered: list[Task] = []
        specs: dict[Task, Any] = {}
        compute_by_task: dict[Task, bool] = {}
        visiting: set[Task] = set()
        visited: set[Task] = set()

        center_heights: dict[tuple[str, int, int], set[int]] = {}
        collecting: set[Task] = set()

        def center_key(task: Task) -> tuple[str, int, int]:
            return task.experiment, task.domain, task.time_minutes

        def collect(task: Task) -> None:
            spec = self.registry.resolve(task.variable)
            task = self._normalize(task, spec)
            if task in collecting:
                return
            collecting.add(task)
            if task.variable == "layer_center" and isinstance(task.height_m, int):
                center_heights.setdefault(center_key(task), set()).add(task.height_m)
            for dependency in spec.dependencies:
                collect(self._dependency_task(task, dependency))

        for task in request.tasks:
            collect(task)
        # Only explicit vertical mode expands this chain; independent centers
        # need pressure at the requested heights alone.
        # A sparse output request must not make a vertically continuous center
        # jump directly from (for example) 1000 m to 15000 m.  Complete the
        # standard center chain up to the highest requested center; only these
        # dependencies are added, not extra output-variable slices.
        for (experiment, domain, minute), heights in list(center_heights.items()):
            if layer_center_mode(request.options, domain) != "vertical":
                continue
            maximum = max(heights)
            for height in DEFAULT_HEIGHTS_M:
                if height <= maximum:
                    collect(
                        Task(
                            experiment,
                            domain,
                            minute,
                            "layer_center",
                            height,
                        )
                    )

        cached_tasks = storage.existing(collecting)

        def task_spec(task: Task, spec: Any) -> Any:
            if task.variable != "layer_center" or not isinstance(task.height_m, int):
                return spec
            if layer_center_mode(request.options, task.domain) != "vertical":
                return spec
            ordered_center_heights = sorted(center_heights[center_key(task)])
            index = ordered_center_heights.index(task.height_m)
            initial = (
                Dependency("slp_center", None)
                if index == 0
                else Dependency("layer_center", ordered_center_heights[index - 1])
            )
            options = {**spec.options, "algorithm_version": "bezier_pcc_vertical_v1"}
            return replace(
                spec,
                dependencies=tuple(spec.dependencies) + (initial,),
                options=options,
            )

        def visit(task: Task) -> None:
            spec = self.registry.resolve(task.variable)
            task = self._normalize(task, spec)
            spec = task_spec(task, spec)
            if task in visited:
                return
            if task in visiting:
                raise ValueError(f"检测到变量依赖循环：{task.variable}")
            visiting.add(task)
            specs[task] = spec
            dependencies: list[Task] = []
            cached = task in cached_tasks
            forced = (
                task.variable in force_variables
                or self._matches_force_slice(task, force_slices)
                or (
                    request.force
                    and (task in explicit or request.force_dependencies)
                )
            )
            if selective_force or not cached or forced:
                for dependency in spec.dependencies:
                    dependency_spec = self.registry.resolve(dependency.name)
                    dependency_task = self._normalize(
                        self._dependency_task(task, dependency), dependency_spec
                    )
                    visit(dependency_task)
                    dependencies.append(dependency_task)
            compute = forced or not cached or any(
                compute_by_task.get(dependency, False) for dependency in dependencies
            )
            visiting.remove(task)
            visited.add(task)
            ordered.append(task)
            compute_by_task[task] = compute

        for task in request.tasks:
            visit(task)
        return [
            PlannedTask(task, specs[task], task in explicit, compute_by_task[task])
            for task in ordered
        ]

    @staticmethod
    def _normalize(task: Task, spec: Any) -> Task:
        if spec.dimensions in {"surface", "scalar"}:
            return Task(
                task.experiment,
                task.domain,
                task.time_minutes,
                task.variable,
                None,
            )
        return task

    @staticmethod
    def _dependency_task(task: Task, dependency: Dependency) -> Task:
        height = dependency.height_m
        if height == "same":
            height = task.height_m
        domain = dependency.domain or task.domain
        minute = (
            dependency.time_minutes
            if dependency.time_minutes is not None
            else task.time_minutes + dependency.time_offset_minutes
        )
        return Task(task.experiment, domain, minute, dependency.name, height)

    @staticmethod
    def _force_variables(request: Request) -> frozenset[str]:
        configured = request.options.get("force_variables", ())
        if isinstance(configured, str) or not isinstance(
            configured, (list, tuple, set, frozenset)
        ):
            raise ValueError("force_variables 必须是变量名列表")
        if any(not isinstance(name, str) or not name for name in configured):
            raise ValueError("force_variables 只能包含非空变量名")
        return frozenset(configured)

    @staticmethod
    def _force_slices(request: Request) -> tuple[dict[str, Any], ...]:
        configured = request.options.get("force_slices", ())
        if not isinstance(configured, (list, tuple)) or any(
            not isinstance(item, dict) for item in configured
        ):
            raise ValueError("force_slices 必须是字典列表")
        return tuple(dict(item) for item in configured)

    @staticmethod
    def _matches_force_slice(task: Task, selectors: tuple[dict[str, Any], ...]) -> bool:
        coordinates = {
            "experiment": task.experiment,
            "domain": task.domain,
            "time_minutes": task.time_minutes,
            "variable": task.variable,
            "height_m": task.height_m,
        }
        allowed = set(coordinates)
        for selector in selectors:
            unknown = set(selector) - allowed
            if unknown:
                raise ValueError(f"force_slices 含未知字段：{sorted(unknown)}")
            if not selector.get("variable"):
                raise ValueError("force_slices 每项必须指定 variable")
            if all(coordinates[name] == value for name, value in selector.items()):
                return True
        return False
