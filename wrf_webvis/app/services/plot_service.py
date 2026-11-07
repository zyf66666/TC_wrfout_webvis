from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..core.config import FIGURE_ROOT, legacy_processed_root_warning, resolve_plot_processed_root
from ..core.registry import DEFAULT_HEIGHT_LEVELS_M, VARIABLE_REGISTRY, resolve_variable_name
from ..core.schemas import FormatConfig, PlotRequest, PlotType, VariableConfig
from ..core.selection import expand_selection
from ..plotting.draw_main import render
from ..plotting.plot_spec import build_plot_spec, domain_config_dict
from ..plotting.plotly_3d import create_3d_plot, normalize_3d_heights
from ..plotting.research_analysis import (
    analysis_variable_configs,
    create_research_analysis_plot,
    normalize_research_request,
    missing_inputs,
    requested_analysis_type,
    required_paths,
)
from .data_manager import DataManager, request_member_names
from .plot_inputs import MissingProcessedDataError, check_additional_plot_inputs
from .processed_data import blank_missing_slices, blankable_missing
from .progress import ProgressCallback, emit, check_cancellation, job_concurrency_limiter, mark_running
from .custom_code import run_custom_code
from ..plotting.figure_axes import read_axes_sidecar
from ..plotting.reflectivity_mean import uses_echo_mean, METHOD as ECHO_MEAN_METHOD


class PlotService:
    # 功能说明：记录图片输出目录；之后生成的 PNG 或 HTML 都会写入这里。
    def __init__(self, figure_root: str | Path = FIGURE_ROOT) -> None:
        self.figure_root = Path(figure_root)

    # 功能说明：在真正画图前列出需要和缺少的已处理文件，并计算本次图片编号；不会生成图片。
    def dry_run(self, request: PlotRequest) -> dict[str, Any]:
        if request.fig_type == PlotType.CUSTOM_CODE:
            with job_concurrency_limiter.acquire():
                return run_custom_code(request, self.figure_root, self.plot_id(request), check_only=True)
        root_warnings = _use_processed_v3_root(request)
        self._apply_fig_defaults(request)
        analysis_type = requested_analysis_type(request)
        if analysis_type is not None:
            paths = required_paths(request, analysis_type)
            missing = missing_inputs(request, analysis_type)
            return {
                "dry_run": True,
                "plan": {
                    "domain_config": domain_config_dict(request).get(request.domain, {}),
                    "required_files": [{"path": str(path)} for path in paths],
                    "missing_files": [
                        {"path": str(item.path), "variable": item.variable, "time_hour": item.time_hour, "height_m": item.height_m}
                        for item in missing
                    ],
                    "warnings": root_warnings,
                },
                "plot_id": self.plot_id(request),
            }
        manager = DataManager.from_plot_request(request)
        plan = manager.plan_for_plot(request)
        _apply_detected_grid(request, plan)
        payload = plan.as_dict()
        payload["warnings"] = [*root_warnings, *payload.get("warnings", [])]
        additional = check_additional_plot_inputs(request)
        entries = additional.entries()
        payload["required_files"].extend(entries)
        payload["missing_files"].extend(entry for entry in entries if not entry["exists"])
        if missing_slices_mode(request) == "blank":
            fatal = [e for e in payload["missing_files"] if not blankable_missing(e.get("variable", ""), e.get("path", ""))]
            blank = [e for e in payload["missing_files"] if blankable_missing(e.get("variable", ""), e.get("path", ""))]
            payload["missing_files"] = fatal
            payload["blank_slices"] = blank
            if blank:
                payload["warnings"].append(_blank_warning(blank))
        return {"dry_run": True, "plan": payload, "plot_id": self.plot_id(request)}

    # 功能说明：让绘图任务最多同时运行 3 个，满额时排队并可取消；获得名额后开始实际画图并返回图片信息。
    def create_plot(self, request: PlotRequest, progress: ProgressCallback | None = None) -> dict[str, Any]:
        # API, dashboard and CLI callers all use the same capacity limit.
        with job_concurrency_limiter.acquire(
            on_wait=lambda: emit(progress, "已有 3 个任务在运行，当前任务排队等待"),
            cancel_check=lambda: check_cancellation(progress),
        ):
            mark_running(progress)
            return self._create_plot(request, progress)

    # 功能说明：检查所需文件，选择普通、三维或专题诊断绘图并保存结果；返回图片位置、编号及实际使用的数据文件。
    def _create_plot(self, request: PlotRequest, progress: ProgressCallback | None = None) -> dict[str, Any]:
        if request.fig_type == PlotType.CUSTOM_CODE:
            request.dry_run = False
            return run_custom_code(request, self.figure_root, self.plot_id(request), progress)
        root_warnings = _use_processed_v3_root(request)
        for message in root_warnings:
            emit(progress, message)
        self._apply_fig_defaults(request)
        request.dry_run = False
        analysis_type = requested_analysis_type(request)
        if analysis_type is not None:
            normalize_research_request(request)
            plot_id = self.plot_id(request)
            output_name = f"{plot_id}.png"
            image_path = self.figure_root / output_name
            self.figure_root.mkdir(parents=True, exist_ok=True)
            missing = missing_inputs(request, analysis_type)
            if missing:
                _raise_missing_processed_data(missing)
            emit(progress, f"生成可复现研究诊断图: {analysis_type}")
            analysis_result = create_research_analysis_plot(request, image_path, progress=progress)
            emit(progress, f"图片已生成: {image_path}")
            research_result = {
                "plot_id": plot_id,
                "image_path": str(image_path),
                "static_path": f"/static/figures/{output_name}",
                "metadata": {
                    "title": request.title,
                    "request_hash": plot_id,
                    **analysis_result["metadata"],
                },
                "used_files": analysis_result["used_files"],
                "processed_files": [],
                "warnings": list(root_warnings),
            }
            axes = read_axes_sidecar(image_path)
            if axes:
                research_result["axes"] = axes
            return research_result
        emit(progress, "检查绘图所需的预处理数据")
        manager = DataManager.from_plot_request(request)
        plan = manager.plan_for_plot(request, progress=progress)
        _apply_detected_grid(request, plan)
        for message in plan.warnings:
            if "网格间距" in message or "径向间隔" in message:
                emit(progress, message)
        additional = check_additional_plot_inputs(request)
        missing_items = [*plan.missing_files, *additional.missing_items]
        blank_mode = missing_slices_mode(request) == "blank"
        blank_warnings: list[str] = []
        if missing_items and blank_mode:
            # 2026-11-05 留空模式：文件存在、只缺部分时次/高度的切片画成空白；文件不存在或缺中心仍报错。
            fatal = [item for item in missing_items if not blankable_missing(item.variable, item.path)]
            if fatal:
                _raise_missing_processed_data(fatal)
            blank_warnings.append(_blank_warning(missing_items))
            emit(progress, blank_warnings[-1])
        elif missing_items:
            _raise_missing_processed_data(missing_items)
        else:
            emit(progress, "所需文件均已存在；不检查缓存元数据或有效性")

        plot_id = self.plot_id(request)
        output_name = f"{plot_id}.{request.output_format}"
        html_name: str | None = None
        three_d_result: dict[str, Any] = {}
        self.figure_root.mkdir(parents=True, exist_ok=True)
        emit(progress, "调用 Matplotlib 绘图")
        with blank_missing_slices(blank_mode):
            if int(request.fig_type) == 7:
                output_name = f"{plot_id}.png"
                html_name = f"{plot_id}.html"
                three_d_result = self._create_3d_plot(request, output_name, html_name, progress=progress)
            else:
                render(build_plot_spec(request, self.figure_root / output_name), progress=progress)
        image_path = self.figure_root / output_name
        emit(progress, f"图片已生成: {image_path}")
        result = {
            "plot_id": plot_id,
            "image_path": str(image_path),
            "static_path": f"/static/figures/{output_name}",
            "metadata": {
                "title": request.title,
                "domain_config": plan.as_dict()["domain_config"],
                "request_hash": plot_id,
                "reflectivity_averaging": [
                    {"variable": config.name, "method": ECHO_MEAN_METHOD,
                     "input": "all_finite_dbz", "negative_values": "retain",
                     "denominator": "finite_samples", "output_units": "dBZ", "missing": "mask"}
                    for config in request.variables
                    if config.enabled and uses_echo_mean(config.name, int(request.fig_type))
                ],
            },
            "used_files": [
                *[item["path"] for item in plan.as_dict()["required_files"]],
                *[str(path) for path in additional.required_files],
            ],
            "processed_files": [],
            "warnings": [*root_warnings, *plan.warnings, *blank_warnings],
        }
        axes = read_axes_sidecar(image_path)
        if axes:
            result["axes"] = axes
        if html_name is not None:
            result["html_path"] = str(self.figure_root / html_name)
            result["html_static_path"] = f"/static/figures/{html_name}"
            result["used_files"] = [*result["used_files"], *three_d_result.get("source_paths", [])]
        return result

    # 功能说明：根据图型补齐请求缺少的默认选择和格式设置。
    def _apply_fig_defaults(self, request: PlotRequest) -> None:
        fig_type = int(request.fig_type)
        if missing_slices_mode(request) == "blank":
            # 留空模式下缺的整段时次/高度不能用插值补上，否则会画出并不存在的数据。
            for config in [*request.variables, *request.quiver]:
                if config.missing_value_policy == "interpolate":
                    config.missing_value_policy = "mask"
        for config in request.variables:
            if config.enabled and uses_echo_mean(config.name, fig_type):
                config.extra.pop("echo_threshold_dbz", None)
                config.extra.pop("echo_threshold_operator", None)
                config.extra.pop("dbz_clip_min", None)
                config.extra.update(reflectivity_mean=ECHO_MEAN_METHOD,
                                    reflectivity_denominator="finite_samples")
                if config.missing_value_policy == "interpolate":
                    config.missing_value_policy = "mask"
        if fig_type != 6 and _is_empty_selection(request.time):
            request.time = _selection([0])
        if fig_type == 0:
            if _is_empty_selection(request.height):
                request.height = _selection([1000])
            request.angle = None
            request.radius = None
        elif fig_type == 1:
            if _is_empty_selection(request.height):
                levels = DEFAULT_HEIGHT_LEVELS_M
                ylim = _format_config(request.format).radius_height.get("ylim")
                if isinstance(ylim, (list, tuple)) and len(ylim) >= 2:
                    lower, upper = float(ylim[0]), float(ylim[1])
                    levels = [height for height in levels if lower <= height <= upper]
                    if not levels:
                        raise ValueError(f"radius-height 高度范围 {lower:g}–{upper:g} m 内没有默认高度层")
                request.height = _selection(levels)
            if _is_empty_selection(request.angle):
                request.angle = _selection([0, 360])
            if _is_empty_selection(request.radius):
                xlim = _format_config(request.format).radius_height.get("xlim")
                request.radius = _selection(list(xlim[:2]) if isinstance(xlim, (list, tuple)) and len(xlim) >= 2 else [0, 200])
        elif fig_type == 2:
            if _is_empty_selection(request.height):
                request.height = _selection([1000])
            if _is_empty_selection(request.angle):
                request.angle = _selection([0, 360])
            time_radius_xlim = _format_config(request.format).time_radius.get("xlim")
            if isinstance(time_radius_xlim, (list, tuple)) and len(time_radius_xlim) >= 2:
                request.radius = _selection(list(time_radius_xlim[:2]))
            elif _is_empty_selection(request.radius):
                request.radius = _selection([0, 200])
        elif fig_type == 3:
            if _is_empty_selection(request.height):
                request.height = _selection([1000])
            if _is_empty_selection(request.radius):
                request.radius = _selection([50])
            request.angle = _selection([0, 360])
        elif fig_type == 4:
            if _is_empty_selection(request.height):
                request.height = _selection(DEFAULT_HEIGHT_LEVELS_M)
            if _is_empty_selection(request.radius):
                request.radius = _selection([50])
            request.angle = _selection([0, 360])
        elif fig_type == 5:
            polar_time_series = any(
                config.enabled and config.use_polar is not False
                and (
                    VARIABLE_REGISTRY.get(resolve_variable_name(config.name).base_name) is None
                    or VARIABLE_REGISTRY[resolve_variable_name(config.name).base_name].dimension.value != "1d"
                )
                for config in [*request.variables, *request.quiver]
            )
            if polar_time_series:
                if _is_empty_selection(request.angle):
                    request.angle = _selection([0, 360])
                if _is_empty_selection(request.radius):
                    request.radius = _selection([0, 200])
            else:
                request.height = None
                request.angle = None
                request.radius = None
        elif fig_type == 6:
            request.height = None
            request.angle = None
            request.radius = None
        elif fig_type == 7:
            if _is_empty_selection(request.height):
                request.height = _selection([2000, 5000, 8000])
            else:
                request.height = _selection(normalize_3d_heights(expand_selection(request.height)))
            request.angle = None
            request.radius = None
        elif fig_type == 8:
            if any(config.enabled for config in request.quiver):
                raise ValueError("time-height 暂不支持 quiver 变量")
            if any(config.enabled and config.use_polar is False for config in request.variables):
                raise ValueError("time-height 必须使用极坐标变量数据")
            if _is_empty_selection(request.height):
                request.height = _selection(DEFAULT_HEIGHT_LEVELS_M)
            if _is_empty_selection(request.angle):
                request.angle = _selection([0, 360])
            if _is_empty_selection(request.radius):
                request.radius = _selection([0, 200])

    # 功能说明：从启用的变量、时次和高度中取三维图参数，生成 PNG 和可交互 HTML，并返回所用源文件信息。
    def _create_3d_plot(self, request: PlotRequest, output_name: str, html_name: str, progress: ProgressCallback | None = None) -> dict[str, Any]:
        variable_configs = [config for config in [_variable_config(item) for item in request.variables] if config.enabled]
        if not variable_configs:
            raise ValueError("3D plot requires one enabled variable")
        variable = resolve_variable_name(variable_configs[0].name).base_name
        times = expand_selection(request.time, default=[0])
        if not times:
            raise ValueError("3D plot requires one time")
        heights_m = normalize_3d_heights(expand_selection(request.height, default=[2000, 5000, 8000]))
        quiver_configs = [config for config in [_variable_config(item) for item in request.quiver] if config.enabled]
        quiver_names = [resolve_variable_name(config.name).base_name for config in quiver_configs]
        return create_3d_plot(
            data_root=request.processed_root or request.data_root,
            output_png=self.figure_root / output_name,
            output_html=self.figure_root / html_name,
            member_name=request_member_names(request)[0],
            domain=request.domain,
            variable=variable,
            time_hour=times[0],
            heights_m=heights_m,
            domain_config=domain_config_dict(request),
            format_config=_model_dump(_format_config(request.format)),
            quiver_variables=quiver_names,
            progress=progress,
        )

    # 功能说明：根据绘图请求内容生成稳定的图片编号。
    def plot_id(self, request: PlotRequest) -> str:
        payload = _jsonable(_model_dump(request))
        if request.fig_type != PlotType.CUSTOM_CODE:
            payload.pop("custom_code", None)
        if not payload.get("center_schemes") or payload.get("center_schemes") == ["base"]:
            payload.pop("center_schemes", None)  # 不比较中心方案时不参与编号，已有图的编号不变
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        return digest[:16]


# 功能说明：把 plan_for_plot 从数据识别出的网格间距和极坐标径向间隔写回请求（2026-11-04，见 grid_detection.py），
# 绘图规格和图片编号都按实际网格生成。
def _apply_detected_grid(request: PlotRequest, plan: Any) -> None:
    request.domain_config = plan.domain_config
    resolution = getattr(plan, "polar_radius_resolution_km", None)
    if resolution is not None:
        request.polar_radius_resolution_km = resolution


# 功能说明：绘图只读 processed_v3（2026-11-01）。把请求里的数据根目录换成实际读取的 v3 根目录，
# 旧 v1/v2 目录（看板旧图回填时常见）自动改指 v3，并返回提示；请求哈希也随之按 v3 目录计算。
def _use_processed_v3_root(request: PlotRequest) -> list[str]:
    source = request.processed_root or request.data_root
    warning = legacy_processed_root_warning(source)
    request.data_root = resolve_plot_processed_root(source)
    request.processed_root = None
    return [warning] if warning else []


# 功能说明：把变量配置字典统一转换成变量配置对象。
def _variable_config(value: VariableConfig | dict[str, Any]) -> VariableConfig:
    return value if isinstance(value, VariableConfig) else VariableConfig(**value)


# 功能说明：把格式配置对象或字典转换成普通字典。
def _format_config(value: FormatConfig | dict[str, Any]) -> FormatConfig:
    return value if isinstance(value, FormatConfig) else FormatConfig(**value)


# 功能说明：判断选择规则是否没有指定任何数值或范围。
def _is_empty_selection(spec: Any) -> bool:
    return not expand_selection(spec)


# 功能说明：按成员的结束时次限制要绘制的时次。
def _member_limited_times(
    request: PlotRequest,
    member_name: str,
    times: list[int | float],
) -> list[int | float]:
    from .data_manager import member_time_limit

    end_hour = member_time_limit((request.selection or {}).get("member_time_limits"), member_name)
    if end_hour is None:
        return times
    return [time_hour for time_hour in times if float(time_hour) <= end_hour]


# 功能说明：优先展开主选择；主选择为空时展开备用选择。
def _selection(values: list[float] | list[int]) -> Any:
    from ..core.schemas import SelectionSpec

    return SelectionSpec(values=list(values))


# 功能说明：把模型对象转换为普通字典，兼容新旧两种模型接口。
def _model_dump(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    if hasattr(model, "dict"):
        return model.dict()
    return dict(model)


# 功能说明：递归把路径、枚举和模型转换为 JSON 可保存的数据。
def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if hasattr(value, "dict"):
        return _jsonable(value.dict())
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


# 功能说明：读取缺切片处理方式（format.common.missing_slices）：error（默认，缺切片整张图报错）或 blank（留白）。
def missing_slices_mode(request: PlotRequest) -> str:
    common = getattr(request.format, "common", None) or {}
    mode = str(common.get("missing_slices") or "error")
    if mode not in {"error", "blank"}:
        raise ValueError(f"format.common.missing_slices 只能是 error 或 blank：{mode}")
    return mode


# 功能说明：留空模式的提示：缺多少个切片、涉及哪些变量与时次（最多列 8 个）。
def _blank_warning(items: list[Any]) -> str:
    def field(item: Any, name: str) -> Any:
        return item.get(name) if isinstance(item, dict) else getattr(item, name, None)

    parts = []
    for item in items[:8]:
        time_hour, height = field(item, "time_hour"), field(item, "height_m")
        label = f"{field(item, 'variable')} {float(time_hour):g} h" if time_hour is not None else str(field(item, "variable"))
        if height not in (None, ""):
            label += f" {height:g} m" if isinstance(height, (int, float)) else f" {height}"
        member = Path(str(field(item, "path"))).parts[-3] if field(item, "path") else ""
        parts.append(f"{member} {label}".strip())
    more = f" 等共 {len(items)} 个" if len(items) > 8 else f" 共 {len(items)} 个"
    return "缺切片留空：" + "；".join(parts) + more + "（未处理，图上留白）"


# 功能说明：整理缺失文件路径并抛出提示先预处理数据的异常。
def _raise_missing_processed_data(items: list[Any]) -> None:
    raise MissingProcessedDataError(items)
