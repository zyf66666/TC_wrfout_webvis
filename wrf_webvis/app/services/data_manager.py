from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from 处理数据_v3.wrf_processing_v3.request import Task

from ..core.config import legacy_processed_root_warning, resolve_plot_processed_root
from ..core.paths import WrfoutDomainReader
from ..core.registry import (
    DEFAULT_DOMAIN_CONFIG,
    VARIABLE_REGISTRY,
    get_variable_item,
    parse_cartesian_from_polar_variable_name,
    parse_fourier_variable_name,
    resolve_variable_name,
)
from ..core.schemas import DomainConfig, PlotRequest, SelectionSpec, VariableConfig, VariableDimension
from ..core.selection import expand_selection
from .progress import ProgressCallback, check_cancellation, emit
from .processed_data import DataReference, ProcessedDataReader

# 逐高度 RMW 数据源（2026-11-04 加风暴相对版本 *_sr：网页处理 post 数据集里逐高度 RMW 只有 _sr 名字）。
RMW_BY_HEIGHT_SOURCES = ("rmw_by_height", "rmw_by_layer_center", "rmw_by_height_sr", "rmw_by_layer_center_sr")
RADIUS_HEIGHT_RMW_SOURCES = set(RMW_BY_HEIGHT_SOURCES)


# 功能说明：逐高度 RMW 图例里的中心说明，例如 rmw_by_height_sr → "SLP center, storm-relative"。
def rmw_center_label(source: str) -> str:
    center = "SLP center" if str(source).startswith("rmw_by_height") else "layer center"
    return f"{center}, storm-relative" if str(source).endswith("_sr") else center


def radius_height_rmw_source(format_options: dict[str, Any]) -> str:
    source = str(format_options.get("rmw_source") or "rmw_by_height")
    if source not in RADIUS_HEIGHT_RMW_SOURCES:
        raise ValueError(f"未知 radius-height RMW 口径: {source}")
    return source


@dataclass
class RequiredFile:
    variable: str
    dimension: str
    time_hour: float
    height_m: float | None
    path: Path
    exists: bool
    layout: str | None = None
    backend: str | None = None
    reference: DataReference | None = None


@dataclass
class ProcessingPlan:
    domain_config: Any
    required_files: list[RequiredFile]
    missing_files: list[RequiredFile]
    warnings: list[str]
    # 2026-11-04：极坐标文件 radius_km 坐标的间隔（km）；本次不画极坐标数据时为请求里填的值或 None。
    polar_radius_resolution_km: float | None = None

    # 功能说明：把处理计划中的对象转换为网页可传输的普通字典。
    def as_dict(self) -> dict[str, Any]:
        return {
            "domain_config": _model_dump(self.domain_config),
            "polar_radius_resolution_km": self.polar_radius_resolution_km,
            "required_files": [_required_file_dict(item) for item in self.required_files],
            "missing_files": [_required_file_dict(item) for item in self.missing_files],
            "warnings": self.warnings,
        }


class DataManager:
    # 功能说明：确定绘图读取的 processed_v3 根目录并创建切片读取器（2026-11-01 起只读 v3；旧 v1/v2 根目录自动改指 v3）。
    def __init__(
        self,
        processed_root: str | Path,
        *,
        processed_v3_root: str | Path | None = None,
    ) -> None:
        source = processed_v3_root if processed_v3_root is not None else processed_root
        self.processed_root = resolve_plot_processed_root(source)
        self.root_warning = legacy_processed_root_warning(source)
        self.processed_reader = ProcessedDataReader(self.processed_root)

    # 功能说明：从绘图请求中的数据根目录创建数据管理器。
    @classmethod
    def from_plot_request(cls, request: PlotRequest) -> "DataManager":
        return cls(
            request.processed_root or request.data_root,
            processed_v3_root=getattr(request, "processed_v3_root", None),
        )

    # 功能说明：逐成员和变量列出绘图所需文件、缺失文件及警告。
    def plan_for_plot(self, request: PlotRequest, progress: ProgressCallback | None = None) -> ProcessingPlan:
        warnings: list[str] = []
        required: list[RequiredFile] = []

        for member_name in request_member_names(request):
            for variable_config in [_variable_config(item) for item in [*request.variables, *request.quiver]]:
                if not variable_config.enabled:
                    continue
                required.extend(self._required_for_variable(request, member_name, variable_config, warnings, progress))

        if int(request.fig_type) == 1 and request.format.radius_height.get("draw_rmw", False):
            source = radius_height_rmw_source(request.format.radius_height)
            heights = expand_selection(request.height)
            times = expand_selection(request.time, default=[0])
            for member_name in request_member_names(request):
                for time_hour in member_limited_times(times, member_name, request.selection):
                    for height_m in heights:
                        required.append(self._file_status(
                            member_name, request.domain, source, VariableDimension.THREE_D,
                            time_hour, height_m, trust_existing=True,
                        ))

        missing = [item for item in required if not item.exists]
        if self.root_warning:
            warnings.append(self.root_warning)
        # 2026-11-04：网格间距和极坐标径向间隔从本次要画的数据里自动识别（见 grid_detection.py），
        # 不再一律按默认表的 d03 = 1 km。请求显式给了 domain_config 时仍用请求的，只在与数据不符时警告。
        from .grid_detection import detect_plot_grid

        emit(progress, "识别网格间距")
        fallback = DEFAULT_DOMAIN_CONFIG.get(int(request.domain), {})
        detected, polar_resolution, grid_warnings = detect_plot_grid(
            request, request_member_names(request), required, self.processed_reader, fallback,
        )
        warnings.extend(grid_warnings)
        if request.domain_config is not None:
            domain_config = request.domain_config
            given = _model_dump(domain_config)
            if (
                detected.get("source") != "fallback" and given.get("dx_km") and detected.get("dx_km")
                and abs(float(given["dx_km"]) - float(detected["dx_km"])) > 1.0e-4 * float(detected["dx_km"])
            ):
                warnings.append(
                    f"请求指定的网格间距 dx={float(given['dx_km']):g} km 与数据的 {float(detected['dx_km']):g} km 不一致，按请求指定的绘图"
                )
        else:
            domain_config = DomainConfig(**detected)
        return ProcessingPlan(
            domain_config=domain_config, required_files=required, missing_files=missing, warnings=warnings,
            polar_radius_resolution_km=polar_resolution,
        )

    # 功能说明：推断一个变量在各时次和高度需要的文件，并返回其状态列表。
    def _required_for_variable(
        self,
        request: PlotRequest,
        member_name: str,
        config: VariableConfig,
        warnings: list[str],
        progress: ProgressCallback | None = None,
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
        total = len(times) * (len(heights) if dimension == VariableDimension.THREE_D else 1)
        self.processed_reader.prefetch([
            Task(
                member_name, request.domain,
                self.processed_reader._minute(time_hour), storage_name,
                self.processed_reader._height(height_m),
            )
            for time_hour in times
            for height_m in (heights if dimension == VariableDimension.THREE_D else [None])
        ])
        emit(progress, f"检查 {member_name}/{storage_name}：0/{total} 个文件")
        def report_file():
            check_cancellation(progress)
            count = len(required)
            if count % 25 == 0 or count == total:
                emit(progress, f"检查 {member_name}/{storage_name}：{count}/{total} 个文件")

        for time_hour in times:
            check_cancellation(progress)
            if dimension == VariableDimension.THREE_D:
                for height_m in heights:
                    check_cancellation(progress)
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
                    report_file()
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
                report_file()
        return required

    # 功能说明：检查一个 v3 切片是否已完成，返回带存在状态的记录。
    def _file_status(
        self,
        member_name: str,
        domain: int,
        variable: str,
        dimension: VariableDimension,
        time_hour: float,
        height_m: float | None,
        *,
        trust_existing: bool = True,
    ) -> RequiredFile:
        reference = self.processed_reader.resolve(
            member_name=member_name,
            domain=domain,
            variable=variable,
            time_hour=time_hour,
            height_m=height_m,
        )
        if reference is not None:
            return RequiredFile(
                variable,
                dimension.value,
                time_hour,
                height_m,
                reference.path,
                True,
                reference.backend,
                reference.backend,
                reference,
            )
        path = self.processed_reader.expected_path(
            Task(member_name, int(domain), self.processed_reader._minute(time_hour), variable,
                 self.processed_reader._height(height_m))
        )
        return RequiredFile(variable, dimension.value, time_hour, height_m, path, False, None, "netcdf4")


# 功能说明：优先展开主选择；主选择为空时展开备用选择。
def _selection(primary: SelectionSpec | None, fallback: SelectionSpec | None, *, default: list[float] | None = None) -> list[float]:
    values = expand_selection(primary)
    if values:
        return values
    return expand_selection(fallback, default=default)


# 功能说明：按成员配置的结束时次过滤请求时次。
def member_limited_times(
    times: list[float],
    member_name: str,
    selection: dict[str, Any] | None,
) -> list[float]:
    """Exclude requested forecast hours after this member's configured end."""
    end_hour = member_time_limit((selection or {}).get("member_time_limits"), member_name)
    if end_hour is None:
        return times
    return [time_hour for time_hour in times if float(time_hour) <= end_hour]


# 功能说明：取成员的结束时次；成员键“试验@中心方案”找不到时按试验名查（结束时次只与试验有关）。
def member_time_limit(limits: Any, member_name: str) -> float | None:
    if not isinstance(limits, dict):
        return None
    key = member_name if member_name in limits else str(member_name).split("@", 1)[0]
    if key not in limits:
        return None
    try:
        return float(limits[key])
    except (TypeError, ValueError):
        return None


# 功能说明：把变量配置字典统一转换成变量配置对象。
def _variable_config(value: VariableConfig | dict[str, Any]) -> VariableConfig:
    return value if isinstance(value, VariableConfig) else VariableConfig(**value)


# 功能说明：从选择条件或请求默认值取得成员名称列表。
def request_member_names(request: PlotRequest) -> list[str]:
    names = _selection_members(getattr(request, "selection", None)) or request.member_names or [request.member_name]
    names = [name for name in names if name]
    # 2026-11-02 中心方案对比：每个试验按所选方案展开成“试验@方案”（base 不加后缀）；已写了 @ 的成员保持不变。
    schemes = [str(item).strip() for item in (getattr(request, "center_schemes", None) or []) if str(item).strip()]
    if not schemes or schemes == ["base"]:
        return names
    from 处理数据_v3.wrf_processing_v3.center_schemes import member_key

    expanded: list[str] = []
    for name in names:
        candidates = [name] if "@" in name else [member_key(name, scheme) for scheme in schemes]
        expanded.extend(candidate for candidate in candidates if candidate not in expanded)
    return expanded


# 功能说明：从选择配置读取、分割并清理成员名称。
def _selection_members(selection: Any) -> list[str]:
    if not selection:
        return []
    raw = selection.get("members") if isinstance(selection, dict) else getattr(selection, "members", None)
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.replace("|", ",").split(",")
    return [str(name).strip() for name in raw if str(name).strip()]


# 功能说明：把所需文件记录转换为网页可序列化的字典。
def _required_file_dict(item: RequiredFile) -> dict[str, Any]:
    return {
        "variable": item.variable,
        "dimension": item.dimension,
        "time_hour": item.time_hour,
        "height_m": item.height_m,
        "path": str(item.path),
        "exists": item.exists,
        "layout": item.layout,
        "backend": item.backend,
    }


# 功能说明：把模型对象转换为普通字典，兼容新旧两种模型接口。
def _model_dump(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    if hasattr(model, "dict"):
        return model.dict()
    return dict(model)
