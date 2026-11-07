from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wrf_webvis.app.core.paths import PathManager, WrfoutDomainReader, metadata_path
from wrf_webvis.app.core.registry import (
    DEFAULT_DOMAIN_CONFIG,
    DEFAULT_HEIGHT_LEVELS_M,
    default_processing_heights,
    VARIABLE_REGISTRY,
    get_variable_item,
    parse_cartesian_from_polar_variable_name,
    parse_fourier_variable_name,
    resolve_variable_name,
)
from wrf_webvis.app.core.schemas import PlotRequest, ProcessingRequest, SelectionSpec, VariableConfig, VariableDimension
from wrf_webvis.app.core.selection import expand_selection
from .processing_utils import processed_file_is_current, resolve_wrfout_root


@dataclass
class RequiredFile:
    variable: str
    dimension: str
    time_hour: float
    height_m: float | None
    path: Path
    exists: bool
    layout: str | None = None


@dataclass
class ProcessingPlan:
    domain_config: Any
    required_files: list[RequiredFile]
    missing_files: list[RequiredFile]
    warnings: list[str]

    # 功能说明：把整个处理计划的域配置、所需文件、缺失文件和警告转换成网页可传输的字典。
    def as_dict(self) -> dict[str, Any]:
        return {
            "domain_config": _model_dump(self.domain_config),
            "required_files": [_required_file_dict(item) for item in self.required_files],
            "missing_files": [_required_file_dict(item) for item in self.missing_files],
            "warnings": self.warnings,
        }


class DataManager:
    # 功能说明：保存处理根目录并创建路径管理器，后续方法用它查找变量输出和元数据。
    def __init__(self, processed_root: str | Path) -> None:
        self.path_manager = PathManager(processed_root)

    # 功能说明：只从绘图请求的 processed_root 或 data_root 取得处理数据根目录，并据此创建数据管理器。
    @classmethod
    def from_plot_request(cls, request: PlotRequest) -> "DataManager":
        return cls(request.processed_root or request.data_root)

    # 功能说明：检查绘图所需文件并返回已有、缺失和警告清单。
    def plan_for_plot(self, request: PlotRequest) -> ProcessingPlan:
        warnings: list[str] = []
        required: list[RequiredFile] = []

        for member_name in request_member_names(request):
            for variable_config in [_variable_config(item) for item in [*request.variables, *request.quiver]]:
                if not variable_config.enabled:
                    continue
                required.extend(self._required_for_variable(request, member_name, variable_config, warnings))

        missing = [item for item in required if not item.exists]
        domain_config = request.domain_config or WrfoutDomainReader._fallback(request.domain, DEFAULT_DOMAIN_CONFIG)
        return ProcessingPlan(domain_config=domain_config, required_files=required, missing_files=missing, warnings=warnings)

    # 功能说明：按请求直接列出的变量、时次和高度规划输出文件状态；此处不展开派生变量依赖。
    def plan_for_processing(self, request: ProcessingRequest) -> ProcessingPlan:
        warnings: list[str] = []
        required: list[RequiredFile] = []
        times = expand_selection(request.times, default=[0])
        heights = expand_selection(request.heights, default=default_processing_heights(request.variables))
        for variable in request.variables:
            item = VARIABLE_REGISTRY.get(variable)
            if item is None:
                warnings.append(f"Variable is not registered and will be treated as 2d: {variable}")
                dimension = VariableDimension.TWO_D
            else:
                dimension = item.dimension
            for time_hour in times:
                if dimension == VariableDimension.THREE_D:
                    for height_m in heights:
                        required.append(
                            self._file_status(
                                request.member_name,
                                request.domain,
                                variable,
                                dimension,
                                time_hour,
                                height_m,
                                trust_existing=request.trust_existing,
                            )
                        )
                else:
                    required.append(
                        self._file_status(
                            request.member_name,
                            request.domain,
                            variable,
                            dimension,
                            time_hour,
                            None,
                            trust_existing=request.trust_existing,
                        )
                    )
        missing = [item for item in required if not item.exists]
        if missing:
            domain_config = WrfoutDomainReader.read_domain_config(
                domain=request.domain,
                wrfout_root=resolve_wrfout_root(request.member_name, request.wrfout_root),
                wrfout_file=request.wrfout_file,
                fallback=DEFAULT_DOMAIN_CONFIG,
            )
        else:
            domain_config = WrfoutDomainReader._fallback(request.domain, DEFAULT_DOMAIN_CONFIG)
        return ProcessingPlan(domain_config=domain_config, required_files=required, missing_files=missing, warnings=warnings)

    # 功能说明：根据图型、变量维度和极坐标设置，列出该图层实际会读取的一个变量文件，并记录未登记变量或缺高度警告。
    def _required_for_variable(
        self,
        request: PlotRequest,
        member_name: str,
        config: VariableConfig,
        warnings: list[str],
    ) -> list[RequiredFile]:
        cartesian = parse_cartesian_from_polar_variable_name(config.name)
        fourier = parse_fourier_variable_name(config.name)
        if cartesian is not None and cartesian.base_name in VARIABLE_REGISTRY:
            dimension = VARIABLE_REGISTRY[cartesian.base_name].dimension
        elif fourier is not None and fourier.base_name in VARIABLE_REGISTRY:
            dimension = VARIABLE_REGISTRY[fourier.base_name].dimension
        else:
            try:
                registry_item = get_variable_item(config.name)
                dimension = registry_item.dimension
            except KeyError:
                warnings.append(f"Variable is not registered and will be treated as 2d: {config.name}")
                dimension = VariableDimension.TWO_D

        times = member_limited_times(
            _selection(config.time, request.time, default=[0]),
            member_name,
            request.selection,
        )
        heights = _selection(config.height, request.height)
        if dimension == VariableDimension.THREE_D and not heights:
            warnings.append(f"3d variable has no height selection: {config.name}")
            return []

        required: list[RequiredFile] = []
        resolved = resolve_variable_name(config.name)
        if cartesian is not None:
            storage_name = config.name
        elif fourier is not None:
            storage_name = config.name
        elif (
            int(request.fig_type) in (1, 2, 3, 4, 5, 8)
        ) and dimension != VariableDimension.ONE_D and config.use_polar is not False:
            storage_name = resolved.polar_name
        else:
            storage_name = resolved.base_name
        for time_hour in times:
            if dimension == VariableDimension.THREE_D:
                for height_m in heights:
                    required.append(
                        self._file_status(
                            member_name,
                            request.domain,
                            storage_name,
                            dimension,
                            time_hour,
                            height_m,
                            trust_existing=True,
                        )
                    )
            else:
                required.append(
                    self._file_status(
                        member_name,
                        request.domain,
                        storage_name,
                        dimension,
                        time_hour,
                        None,
                        trust_existing=True,
                    )
                )
        return required

    # 功能说明：计算某成员、域、变量、时间和高度对应的输出路径，并标记文件是否存在。
    def _file_status(
        self,
        member_name: str,
        domain: int,
        variable: str,
        dimension: VariableDimension,
        time_hour: float,
        height_m: float | None,
        *,
        trust_existing: bool = False,
    ) -> RequiredFile:
        existing = self.path_manager.existing_variable(
            member_name=member_name,
            domain=domain,
            variable=variable,
            time_hour=time_hour,
            dimension=dimension,
            height_m=height_m,
        )
        if existing is not None:
            if not trust_existing and not processed_file_is_current(variable, existing.metadata):
                return RequiredFile(variable, dimension.value, time_hour, height_m, existing.path, False, existing.layout)
            return RequiredFile(variable, dimension.value, time_hour, height_m, existing.path, True, existing.layout)
        path = self.path_manager.variable_path(
            member_name=member_name,
            domain=domain,
            variable=variable,
            time_hour=time_hour,
            dimension=dimension,
            height_m=height_m,
        )
        return RequiredFile(variable, dimension.value, time_hour, height_m, path, False, None)

    # 功能说明：从已有处理文件的元数据恢复域网格配置，供绘图规划使用。
    def _domain_config_from_processed_metadata(self, required: list[RequiredFile], domain: int) -> Any | None:
        for item in required:
            if not item.exists:
                continue
            meta = metadata_path(item.path)
            if not meta.exists():
                continue
            try:
                payload = json.loads(meta.read_text(encoding="utf-8"))
                wrfout = payload.get("source_wrfout")
                if not wrfout:
                    continue
                config = WrfoutDomainReader.read_domain_config(domain=domain, wrfout_file=wrfout, fallback=DEFAULT_DOMAIN_CONFIG)
                if getattr(config, "source", "") == "wrfout":
                    return config
            except Exception:
                continue
        return None


# 功能说明：优先采用主选择；主选择为空时使用后备选择，并规范成统一选择对象。
def _selection(primary: SelectionSpec | None, fallback: SelectionSpec | None, *, default: list[float] | None = None) -> list[float]:
    values = expand_selection(primary)
    if values:
        return values
    return expand_selection(fallback, default=default)


# 功能说明：把请求时间限制在指定成员实际拥有的时间范围内。
def member_limited_times(
    times: list[float],
    member_name: str,
    selection: dict[str, Any] | None,
) -> list[float]:
    """Exclude requested forecast hours after this member's configured end."""
    limits = (selection or {}).get("member_time_limits")
    if not isinstance(limits, dict) or member_name not in limits:
        return times
    try:
        end_hour = float(limits[member_name])
    except (TypeError, ValueError):
        return times
    return [time_hour for time_hour in times if float(time_hour) <= end_hour]


# 功能说明：将变量配置值解析成变量维度、源变量等标准配置。
def _variable_config(value: VariableConfig | dict[str, Any]) -> VariableConfig:
    return value if isinstance(value, VariableConfig) else VariableConfig(**value)


# 功能说明：从请求中读取成员、域、变量和时间选择，返回数据规划使用的成员名称列表。
def request_member_names(request: PlotRequest) -> list[str]:
    names = _selection_members(getattr(request, "selection", None)) or request.member_names or [request.member_name]
    return [name for name in names if name]


# 功能说明：从选择对象取得成员列表，并处理单值、列表和默认成员情况。
def _selection_members(selection: Any) -> list[str]:
    if not selection:
        return []
    raw = selection.get("members") if isinstance(selection, dict) else getattr(selection, "members", None)
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.replace("|", ",").split(",")
    return [str(name).strip() for name in raw if str(name).strip()]


# 功能说明：把 RequiredFile 对象展开为包含路径、维度、时间和存在状态的字典。
def _required_file_dict(item: RequiredFile) -> dict[str, Any]:
    return {
        "variable": item.variable,
        "dimension": item.dimension,
        "time_hour": item.time_hour,
        "height_m": item.height_m,
        "path": str(item.path),
        "exists": item.exists,
        "layout": item.layout,
    }


# 功能说明：兼容不同版本的数据模型，把模型导出为普通字典。
def _model_dump(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    if hasattr(model, "dict"):
        return model.dict()
    return dict(model)
