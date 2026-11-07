"""Minute-native view of the scientific registry bundled with v3."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import _scientific_registry


@dataclass(frozen=True)
class Dependency:
    name: str
    height_m: Any = "same"
    domain: int | None = None
    time_offset_minutes: int = 0
    time_minutes: int | None = None

    @property
    def height(self) -> Any:
        return self.height_m


@dataclass(frozen=True)
class VariableSpec:
    name: str
    kind: str
    units: str
    dimensions: str = "height"
    dependencies: tuple[Dependency, ...] = ()
    handler: Any = None
    wrf_name: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    storage_layout: str = "cartesian_3d"


_REGISTRY_PACKAGE = "wrf_processing_v3"


def _package_handler(handler: Any) -> Any:
    """Point registry handler paths at the package actually imported.

    The scientific registry spells handlers as ``wrf_processing_v3.variables...``.
    That only imports when ``处理数据_v3`` itself is on ``sys.path`` (the CLI);
    the web app imports the package as ``处理数据_v3.wrf_processing_v3``.
    """
    if not isinstance(handler, str) or __package__ in (None, "", _REGISTRY_PACKAGE):
        return handler
    if handler == _REGISTRY_PACKAGE or handler.startswith((f"{_REGISTRY_PACKAGE}.", f"{_REGISTRY_PACKAGE}:")):
        return __package__ + handler[len(_REGISTRY_PACKAGE):]
    return handler


# 定中心的两个“根”变量：地面中心与逐层中心。依赖它们（逐级往下）的变量都随中心方案而变。
CENTER_ROOT_VARIABLES = frozenset({"slp_center", "layer_center"})


class Registry:
    def __init__(self, scientific_registry: Any):
        self._scientific_registry = scientific_registry
        self._cache: dict[str, VariableSpec] = {}
        self._center_dependent: dict[str, bool] = {}

    # 功能说明：判断变量是否依赖中心（本身是 slp_center/layer_center，或任一依赖逐级依赖它们）。
    # 2026-11-02：中心方案按此决定变量存在共用目录还是 centers/<方案>/ 目录；按依赖图判断，不靠变量名猜。
    def center_dependent(self, name: str) -> bool:
        cached = self._center_dependent.get(name)
        if cached is not None:
            return cached
        spec = self.resolve(name)
        if spec.name in CENTER_ROOT_VARIABLES:
            result = True
        else:
            self._center_dependent[name] = False  # 防止异常的循环依赖导致无限递归
            result = any(self.center_dependent(dependency.name) for dependency in spec.dependencies)
        self._center_dependent[name] = result
        self._center_dependent[spec.name] = result
        return result

    def resolve(self, name: str) -> VariableSpec:
        if name not in self._cache:
            self._cache[name] = self._translate(self._scientific_registry.resolve(name))
        return self._cache[name]

    def _translate(self, spec: Any) -> VariableSpec:
        dependencies = tuple(self._dependency(item) for item in spec.dependencies)
        handler = _package_handler(spec.handler)
        options = dict(spec.options or {})
        if spec.name == "storm_motion" and "times" in options:
            options["times"] = [
                self._hour_to_minute(value, "storm_motion times")
                for value in options["times"]
            ]
        return VariableSpec(
            name=spec.name,
            kind=spec.kind,
            units=spec.units,
            dimensions=spec.dimensions,
            dependencies=dependencies,
            handler=handler,
            wrf_name=spec.wrf_name,
            options=options,
            storage_layout=self._storage_layout(spec.name, spec.dimensions),
        )

    def _dependency(self, dependency: Any) -> Dependency:
        absolute = getattr(dependency, "time", None)
        return Dependency(
            name=dependency.name,
            height_m=self._height(getattr(dependency, "height", "same")),
            domain=dependency.domain,
            time_offset_minutes=self._hour_to_minute(
                getattr(dependency, "time_offset", 0.0), "dependency offset"
            ),
            time_minutes=(
                None
                if absolute is None
                else self._hour_to_minute(absolute, "dependency time")
            ),
        )

    @staticmethod
    def _height(value: Any) -> Any:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            rounded = round(float(value))
            if abs(float(value) - rounded) > 1e-8:
                raise ValueError(f"v3 高度必须为整数米：{value}")
            return int(rounded)
        return value

    @staticmethod
    def _hour_to_minute(value: Any, label: str) -> int:
        minute = round(float(value) * 60.0)
        if abs(float(value) * 60.0 - minute) > 1e-7:
            raise ValueError(f"{label} 不能精确表示为整数分钟：{value}")
        return int(minute)

    @staticmethod
    def _storage_layout(name: str, dimensions: str) -> str:
        if name in {"slp_center", "center", "center_geo", "storm_motion", "motion"}:
            return "vector"
        if name in {"layer_center", "height_center", "pressure_center"}:
            return "height_vector"
        if name.endswith("_polar"):
            return "polar_3d" if dimensions == "height" else "polar_2d"
        if dimensions == "scalar":
            return "scalar"
        if dimensions == "height_scalar":
            return "height_scalar"
        if dimensions == "surface":
            return "cartesian_2d"
        return "cartesian_3d"


def build_registry(options: dict[str, Any] | None = None, times_minutes=()) -> Registry:
    minute_values = tuple(int(value) for value in times_minutes)
    hours = tuple(value / 60.0 for value in minute_values)
    return Registry(_scientific_registry.build_registry(options or {}, times=hours))
