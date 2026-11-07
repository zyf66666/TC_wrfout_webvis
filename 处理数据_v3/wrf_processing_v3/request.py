"""Minute-based immutable processing tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Task:
    experiment: str
    domain: int
    time_minutes: int
    variable: str
    height_m: int | str | None

    def __post_init__(self) -> None:
        if not isinstance(self.experiment, str) or not self.experiment:
            raise ValueError("experiment 不能为空")
        if any(part in self.experiment for part in ("/", "\\")):
            raise ValueError("experiment 不能包含路径分隔符")
        if not isinstance(self.variable, str) or not self.variable:
            raise ValueError("variable 不能为空")
        if any(part in self.variable for part in ("/", "\\")):
            raise ValueError("variable 不能包含路径分隔符")
        if not isinstance(self.domain, int) or isinstance(self.domain, bool) or self.domain <= 0:
            raise ValueError("domain 必须是正整数")
        if not isinstance(self.time_minutes, int) or isinstance(self.time_minutes, bool):
            raise ValueError("time_minutes 必须是整数分钟")
        if self.height_m not in (None, "lowest") and (
            not isinstance(self.height_m, int) or isinstance(self.height_m, bool)
        ):
            raise ValueError("height_m 必须是整数米、lowest 或 None")

    @property
    def time(self) -> int:
        """Compatibility alias used by existing variable handlers."""
        return self.time_minutes

    @property
    def height(self) -> int | str | None:
        """Compatibility alias used by existing variable handlers."""
        return self.height_m


@dataclass(frozen=True, slots=True)
class Request:
    experiments: tuple[str, ...]
    domains: tuple[int, ...]
    times_minutes: tuple[int, ...]
    heights_m: tuple[int | str | None, ...]
    variables: tuple[str, ...]
    source_root: Path
    output_root: Path | None = None
    force: bool = False
    force_dependencies: bool = False
    dry_run: bool = False
    options: dict[str, Any] = field(default_factory=dict)
    dtype: str = "float32"
    # 2026-11-02 中心方案（center_schemes.json 的编号）。None：沿用旧行为，中心参数直接取 options、结果写在 output_root；
    # "base"：同一位置，但中心参数必须与 base 方案一致；其他方案：依赖中心的变量写到 output_root/centers/<方案>/。
    center_scheme: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "experiments",
            "domains",
            "times_minutes",
            "heights_m",
            "variables",
        ):
            raw_values = getattr(self, name)
            if isinstance(raw_values, (str, bytes)):
                raise ValueError(f"{name} 必须是序列，单个值也需写成 (value,)")
            values = tuple(raw_values)
            if not values:
                raise ValueError(f"{name} 不能为空")
            object.__setattr__(self, name, values)
        object.__setattr__(self, "source_root", Path(self.source_root))
        object.__setattr__(
            self,
            "output_root",
            Path(self.output_root or "/Volumes/land_1/processed_v3"),
        )
        object.__setattr__(self, "options", dict(self.options))
        for value in self.times_minutes:
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError("times_minutes 必须使用整数分钟")
        for value in self.heights_m:
            if value not in (None, "lowest") and (
                not isinstance(value, int) or isinstance(value, bool)
            ):
                raise ValueError("heights_m 必须使用整数米、lowest 或 None")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in self.domains
        ):
            raise ValueError("domains 必须为正整数")
        for value in (*self.experiments, *self.variables):
            if not isinstance(value, str) or not value or any(
                part in value for part in ("/", "\\")
            ):
                raise ValueError("experiment 和 variable 必须是安全的非空名称")
        if self.center_scheme is not None:
            from .center_schemes import validate_scheme_id

            object.__setattr__(self, "center_scheme", validate_scheme_id(self.center_scheme))
        if self.dtype != "float32":
            raise ValueError("v3 data dtype 固定为 float32")
        if self.force_dependencies and not self.force:
            raise ValueError("force_dependencies 只能与 force=True 一起使用")

    @property
    def tasks(self) -> tuple[Task, ...]:
        return tuple(
            Task(experiment, domain, minute, variable, height)
            for experiment in self.experiments
            for domain in self.domains
            for minute in self.times_minutes
            for variable in self.variables
            for height in self.heights_m
        )

    @property
    def times(self) -> tuple[int, ...]:
        return self.times_minutes

    @property
    def heights(self) -> tuple[int | str | None, ...]:
        return self.heights_m


# 功能说明：按请求的中心方案并入中心参数（options），返回新请求；center_scheme 为 None 时原样返回。
def with_center_scheme(request: Request) -> Request:
    if request.center_scheme is None:
        return request
    from dataclasses import replace

    from .center_schemes import apply_center_scheme

    return replace(request, options=apply_center_scheme(request.options, request.center_scheme))


def storage_scheme_options(request: Request, registry: Any) -> dict[str, Any]:
    """构造 NetCDFStorage 时与中心方案有关的参数。"""
    return {"center_scheme": request.center_scheme, "center_dependent": getattr(registry, "center_dependent", None)}
