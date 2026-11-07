from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..core.registry import VARIABLE_REGISTRY
from ..core.schemas import PlotRequest, VariableDimension
from ..core.selection import expand_selection
from ..core.tilt_track import tilt_track_components
from .data_manager import RMW_BY_HEIGHT_SOURCES, DataManager, RequiredFile, member_limited_times, request_member_names


@dataclass(frozen=True)
class PlotInputCheck:
    """格式叠加和特殊图型需要的额外输入。2026-11-01 起都是 processed_v3 切片（按 completed 判断），
    只有用户在格式里显式给的文件路径才按“文件是否存在”判断。"""

    items: list[RequiredFile] = field(default_factory=list)

    @property
    def required_files(self) -> list[Path]:
        return _unique_paths(item.path for item in self.items)

    @property
    def missing_items(self) -> list[RequiredFile]:
        """缺失的切片（带变量、时次、高度），报错时用它给出具体位置。"""
        return [item for item in self.items if not item.exists]

    @property
    def missing_files(self) -> list[Path]:
        return _unique_paths(item.path for item in self.missing_items)

    # 功能说明：给网页预检用的条目（路径、是否可用、变量、时次、高度）。
    def entries(self) -> list[dict[str, Any]]:
        return [
            {
                "path": str(item.path),
                "exists": bool(item.exists),
                "variable": item.variable,
                "time_hour": item.time_hour,
                "height_m": item.height_m,
                "kind": "plot_dependency",
            }
            for item in self.items
        ]


class MissingProcessedDataError(FileNotFoundError):
    # 功能说明：显示缺失的时次、高度和实际数据路径，避免同一个 NetCDF 文件中的缺层被误报为文件不存在。
    def __init__(self, items: Iterable[Any]) -> None:
        entries = list(items)
        self.missing_files = _unique_paths(Path(getattr(item, "path", item)) for item in entries)
        details = []
        for item in entries:
            path = Path(getattr(item, "path", item))
            if getattr(item, "time_hour", None) is not None:
                height = getattr(item, "height_m", None)
                location = f"{item.time_hour:g} h"
                if height is not None:
                    location += f", {height:g} m" if isinstance(height, (int, float)) else f", {height}"
                details.append(f"- {item.variable}（{location}）：{path}")
            else:
                details.append(f"- {path}")
        super().__init__(
            "缺少绘图所需的预处理数据，请检查所选数据根目录、时次和高度。\n"
            f"缺失数据切片（{len(entries)} 个）：\n" + "\n".join(details)
        )


# 功能说明：检查特殊图型和格式额外需要的数据，返回所需切片及其是否已完成。
def check_additional_plot_inputs(request: PlotRequest) -> PlotInputCheck:
    """List non-layer inputs used by formats and special plot types.

    This is deliberately an availability-only check of processed_v3 slices.
    The web application never opens wrfout files and never compares cache
    metadata with processing options or source timestamps.
    """

    manager = DataManager.from_plot_request(request)
    members = request_member_names(request)
    times = expand_selection(request.time, default=[] if int(request.fig_type) == 6 else [0])
    required: list[RequiredFile] = []
    format_payload = _model_dump(request.format)

    if int(request.fig_type) == 0:
        horizontal = format_payload.get("horizontal") or {}
        for member in members:
            member_times = member_limited_times(times, member, request.selection)
            if horizontal.get("center_enabled", False):
                configured = horizontal.get("center_path")
                center_height = horizontal.get("center_height_m")
                if configured:
                    required.append(_explicit_file(configured))
                else:
                    variable = "slp_center" if center_height is None else "layer_center"
                    required.extend(_slices(manager, member, request.domain, variable, member_times,
                                            None if center_height is None else [center_height]))
            if horizontal.get("draw_rmw", False) and not horizontal.get("rmw_path"):
                from ..plotting.draw_format import horizontal_rmw_height_m, horizontal_rmw_source

                height = horizontal_rmw_height_m(horizontal)
                required.extend(_slices(manager, member, request.domain, horizontal_rmw_source(horizontal),
                                        member_times, None if height is None else [height]))
            if horizontal.get("draw_land_distance", False) and not horizontal.get("land_distance_path"):
                required.extend(_slices(manager, member, request.domain, "center_land_distance", member_times))
            if horizontal.get("draw_tilt_track", False):
                # 预检与轨迹读取使用同一范围映射，缺文件时不改读默认 2–8 km（轨迹按整条时间序列读取）。
                reader = manager.processed_reader
                required.extend(
                    _variable_file(reader.variable_path(member, request.domain, name), name)
                    for name in tilt_track_components(
                        horizontal,
                        exists=lambda name: reader.variable_path(member, request.domain, name).is_file(),
                    )
                )

    if int(request.fig_type) == 2:
        time_radius = format_payload.get("time_radius") or {}
        if time_radius.get("draw_land_distance", False) and not time_radius.get("land_distance_path"):
            for member in members:
                member_times = member_limited_times(times, member, request.selection)
                required.extend(_slices(manager, member, request.domain, "center_land_distance", member_times))
        rmw_source = _time_radius_rmw_source(time_radius)
        if time_radius.get("draw_rmw", False) and rmw_source is not None:
            rmw_height = float(time_radius.get("rmw_height_m", 1000))
            for member in members:
                member_times = member_limited_times(times, member, request.selection)
                required.extend(_slices(manager, member, request.domain, rmw_source, member_times, [rmw_height]))
        for option, variable in (("draw_rmw", "rmw"), ("draw_r34", "r_34")):
            if option == "draw_rmw" and rmw_source is not None:
                continue
            if time_radius.get(option, False):
                for member in members:
                    member_times = member_limited_times(times, member, request.selection)
                    required.extend(_slices(manager, member, request.domain, variable, member_times))

    if int(request.fig_type) == 6:
        track = format_payload.get("track") or {}
        for member in members:
            # 与 track_plot.load_center_track 一致：读 processed_v3 的 slp_center 时间序列。
            v3_center = manager.processed_reader.variable_path(member, request.domain, "slp_center")
            required.append(_variable_file(v3_center, "slp_center"))
            if (
                track.get("draw_land_boundary", True)
                and not track.get("land_boundary_path")
                and track.get("land_boundary_x_km") is None
            ):
                required.extend(_slices(manager, member, 1, "land_boundary",
                                        [float(track.get("land_boundary_time", 0.0))]))
        if track.get("draw_rainnc_background", False) and not track.get("rainnc_path"):
            rain_member = str(track.get("rainnc_member") or (members[0] if members else ""))
            rain_domain = int(track.get("rainnc_domain", 1))
            rain_time = float(track.get("rainnc_time_hour", 120.0))
            rain_variable = str(track.get("rainnc_variable", "RAINNC"))
            for variable in (rain_variable, "XLAT", "XLONG"):
                required.extend(_slices(manager, rain_member, rain_domain, variable, [rain_time]))

    if int(request.fig_type) == 7:
        three_d = format_payload.get("three_d") or {}
        if three_d.get("draw_layer_centers", True):
            heights_km = three_d.get("center_heights_km") or list(range(1, 10))
            for member in members:
                # 与 3D 中心绘制共用切片解析器，接受已经完成的 v3 中心。
                member_times = member_limited_times(times or [0], member, request.selection)
                required.extend(_slices(manager, member, request.domain, "layer_center", member_times,
                                        [float(value) * 1000.0 for value in heights_km]))
        if three_d.get("draw_arrows", False) and not any(_enabled(item) for item in request.quiver):
            heights = expand_selection(request.height, default=[2000, 5000, 8000])
            for member in members:
                member_times = member_limited_times(times or [0], member, request.selection)
                for variable in ("ua", "va", "wa"):
                    required.extend(_slices(manager, member, request.domain, variable, member_times, heights))

    return PlotInputCheck(items=_unique_items(required))


# 功能说明：时间—半径图 RMW 线的数据源：None 表示用最低模式层的逐时次 rmw 标量。
def _time_radius_rmw_source(time_radius: dict[str, Any]) -> str | None:
    source = time_radius.get("rmw_source")
    if source in (None, "", "rmw"):
        return None
    source = str(source)
    if source not in RMW_BY_HEIGHT_SOURCES:
        raise ValueError(f"time_radius.rmw_source 不支持: {source}")
    return source


# 功能说明：列出给定变量在各时次（三维变量还要各高度）的 processed_v3 切片及是否已完成。
def _slices(
    manager: DataManager,
    member: str,
    domain: int,
    variable: str,
    times: Iterable[int | float],
    heights: Iterable[int | float] | None = None,
) -> list[RequiredFile]:
    item = VARIABLE_REGISTRY.get(variable)
    heights = list(heights) if heights is not None else None
    dimension = item.dimension if item is not None else (
        VariableDimension.THREE_D if heights else VariableDimension.TWO_D
    )
    # 给了高度（含逐高度标量 rmw_by_height、layer_center）就逐高度检查；否则按单个切片检查。
    levels: list[Any] = list(heights) if heights else [None]
    result: list[RequiredFile] = []
    for time_hour in times:
        for height_m in levels:
            result.append(manager._file_status(member, domain, variable, dimension, float(time_hour), height_m))
    return result


# 功能说明：按整份 v3 文件是否存在登记的输入（轨迹类按整条时间序列读取）。
def _variable_file(path: Path, variable: str) -> RequiredFile:
    path = Path(path)
    return RequiredFile(variable, "file", None, None, path, path.is_file(), None, "netcdf4")


# 功能说明：用户在格式里显式指定的文件，只检查文件是否存在。
def _explicit_file(path: str | Path) -> RequiredFile:
    path = Path(path)
    return RequiredFile(path.name, "file", None, None, path, path.exists(), None, "file")


# 功能说明：按（路径、变量、时次、高度）去重，保持顺序。
def _unique_items(items: Iterable[RequiredFile]) -> list[RequiredFile]:
    result: list[RequiredFile] = []
    seen: set[tuple[Any, ...]] = set()
    for item in items:
        key = (Path(item.path), item.variable, item.time_hour, item.height_m)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


# 功能说明：保留原顺序去除重复路径。
def _unique_paths(values: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for value in values:
        path = Path(value)
        if path not in seen:
            result.append(path)
            seen.add(path)
    return result


# 功能说明：把模型对象转换为普通字典，兼容新旧两种模型接口。
def _model_dump(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    if hasattr(model, "dict"):
        return model.dict()
    return dict(model or {})


# 功能说明：读取配置对象或字典的启用开关。
def _enabled(value: Any) -> bool:
    return bool(value.get("enabled", True)) if isinstance(value, dict) else bool(getattr(value, "enabled", True))
