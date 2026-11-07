"""绘图规格：网页出图请求（PlotRequest）到绘图核心的唯一入口（2026-11-01）。

以前网页请求先被 ``PlotService._to_legacy_config`` 拼成全大写字段的临时对象（DATA_ROOT、VARIABLES、
HEIGHT_LEVELS_M……，来自早年 plot_namelist.py 命令行的格式），再由 ``run_from_config`` 用
``get_config_value(config, "XXX", 默认值)`` 一项项取出。现在改为：

    spec = build_plot_spec(request, output_path)   # 本文件：请求 → 绘图规格
    render(spec, progress=...)                      # draw_main：按规格画图并保存

``PlotSpec`` 的字段就是绘图核心真正用到的全部输入；测试可以直接构造它。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import DEFAULT_PLOT_PROCESSED_ROOT, FIGURE_ROOT, absolute_path
from ..core.registry import VARIABLE_REGISTRY, plotting_variable_type_registry, resolve_variable_name
from ..core.schemas import FormatConfig, PlotRequest, VariableConfig
from ..core.selection import expand_selection
from .draw_utils import DEFAULT_DOMAIN_CONFIG, VARIABLE_TYPE_REGISTRY


@dataclass
class PlotSpec:
    """一张图的全部绘图输入。变量和格式仍是绘图核心使用的普通字典。"""

    data_root: Path = field(default_factory=lambda: Path(DEFAULT_PLOT_PROCESSED_ROOT))
    output_path: Path = field(default_factory=lambda: Path(FIGURE_ROOT) / "plot.png")
    members: list[str] = field(default_factory=list)
    domain: int = 3
    domain_config: dict[int, dict[str, Any]] = field(default_factory=lambda: dict(DEFAULT_DOMAIN_CONFIG))
    # 画布：fig_type、row、col、panel_by、panels、figsize、dpi、save、show
    figure: dict[str, Any] = field(default_factory=dict)
    # 选区：members、times、height、angle、radius、member_time_limits（时次单位小时，高度单位米，都是实际值）
    selection: dict[str, Any] = field(default_factory=dict)
    # 图层：{绘图变量名: 图层设置}；绘图变量名在非水平图里，层中心变量带“1”（如 vt1）
    variables: dict[str, Any] = field(default_factory=dict)
    # 风箭头：{"enabled": bool, "variables": {...}, 可选 headwidth、headlength、stream}
    quiver: dict[str, Any] = field(default_factory=lambda: {"enabled": False, "variables": {}})
    format: dict[str, Any] = field(default_factory=dict)
    colorbar: dict[str, Any] = field(default_factory=lambda: {"enabled": False})
    quiver_key: dict[str, Any] = field(default_factory=lambda: {"enabled": False})
    polar_dir_style: str = "same"
    polar_radius_resolution_km: float | None = None
    variable_type_registry: dict[str, str] = field(default_factory=lambda: dict(VARIABLE_TYPE_REGISTRY))

    @property
    def fig_type(self) -> int:
        return int(self.figure.get("fig_type", 0))


# 功能说明：把网页出图请求翻成绘图规格（选区展开成实际值列表、变量名换成绘图用名、补变量默认配色）。
def build_plot_spec(request: PlotRequest, output_path: str | Path) -> PlotSpec:
    from ..services.data_manager import request_member_names

    fig_type = int(request.fig_type)
    members = request_member_names(request)
    time_default = None if fig_type == 6 else [0]
    selection = {
        "members": members,
        "times": selection_values(request.time, time_default),
        "height": selection_values(request.height, None),
        "angle": selection_values(request.angle, [0, 360]),
        "radius": selection_values(request.radius, [0, 200]),
    }
    member_time_limits = (request.selection or {}).get("member_time_limits")
    if isinstance(member_time_limits, dict):
        selection["member_time_limits"] = member_time_limits
    selection = {key: value for key, value in selection.items() if value is not None}

    figure = {
        "fig_type": fig_type,
        "row": request.row,
        "col": request.col,
        "panel_by": request.panel_by,
        "panels": request.panels,
        "dpi": request.dpi,
        "save": request.save,
        "show": request.show,
    }
    if request.figsize:
        figure["figsize"] = request.figsize
    figure = {key: value for key, value in figure.items() if value is not None}

    variable_configs = [config for config in (_variable_config(item) for item in request.variables) if config.enabled]
    quiver_configs = [config for config in (_variable_config(item) for item in request.quiver) if config.enabled]
    format_config = _format_config(request.format)
    colorbar = dict(format_config.colorbar or {})
    if fig_type == 6:
        track_format = dict(format_config.track or {})
        if track_format.get("draw_rainnc_background", False):
            colorbar["enabled"] = bool(track_format.get("rainnc_colorbar", True))
            if colorbar.get("title") in (None, "auto"):
                colorbar["title"] = track_format.get("rainnc_colorbar_title", "RAINNC (mm)")

    return PlotSpec(
        data_root=absolute_path(request.processed_root or request.data_root),
        output_path=Path(output_path),
        members=members,
        domain=request.domain,
        domain_config=domain_config_dict(request),
        figure=figure,
        selection=selection,
        variables={plot_variable_name(config.name, fig_type): layer_config(config) for config in variable_configs},
        quiver={
            "enabled": bool(quiver_configs),
            "variables": {plot_variable_name(config.name, fig_type): layer_config(config) for config in quiver_configs},
        },
        format=_model_dump(format_config),
        colorbar=colorbar,
        quiver_key=format_config.quiver_key,
        polar_dir_style=request.polar_dir_style,
        polar_radius_resolution_km=request.polar_radius_resolution_km,
        variable_type_registry=plotting_variable_type_registry(),
    )


# 功能说明：把一个变量的网页配置换成绘图核心的图层字典：补登记表里的默认配色和 levels，选区展开成列表，extra 摊平。
def layer_config(config: VariableConfig) -> dict[str, Any]:
    raw = _model_dump(config)
    name = raw.pop("name", None)
    raw["plot_type"] = _enum_value(raw.get("plot_type"))
    resolved = resolve_variable_name(str(name))
    item = VARIABLE_REGISTRY.get(resolved.base_name)
    if raw.get("cmap") is None and item is not None and item.default_cmap:
        raw["cmap"] = item.default_cmap
    if raw.get("levels") is None and item is not None and item.default_levels:
        raw["levels"] = item.default_levels
    for key in ("time", "height", "angle", "radius"):
        raw_key = "times" if key == "time" else key
        raw.pop(key, None)
        raw[raw_key] = selection_values(getattr(config, key), None)
    extra = raw.pop("extra", {}) or {}
    raw.update(extra)
    return {key: _enum_value(value) for key, value in raw.items() if value is not None}


# 功能说明：绘图用的变量名：水平图用基础名；其他图里层中心变量写成“基础名 + 1”（如 vt1），对应 vt1_polar 等文件。
def plot_variable_name(name: str, fig_type: int) -> str:
    resolved = resolve_variable_name(name)
    if fig_type != 0 and resolved.center_mode == "layer":
        return f"{resolved.base_name}1"
    return resolved.base_name


# 功能说明：把选择规则展开成实际参与绘图的数值列表；整数值写成 int。
def selection_values(spec: Any, default: list[float] | None) -> list[float] | None:
    values = expand_selection(spec, default=default)
    if not values:
        return None
    return [int(value) if float(value).is_integer() else value for value in values]


# 功能说明：取得请求的域配置并转换为绘图核心需要的字典；请求没给时返回空表（绘图时按默认网格）。
def domain_config_dict(request: PlotRequest) -> dict[int, dict[str, Any]]:
    if request.domain_config is None:
        return {}
    data = _model_dump(request.domain_config)
    return {
        request.domain: {
            "dx_km": data.get("dx_km") or 1.0,
            "dy_km": data.get("dy_km") or data.get("dx_km") or 1.0,
            "grid_number": data.get("nx") or data.get("ny") or 0,
            "nx": data.get("nx"),
            "ny": data.get("ny"),
        }
    }


def _variable_config(value: VariableConfig | dict[str, Any]) -> VariableConfig:
    return value if isinstance(value, VariableConfig) else VariableConfig(**value)


def _format_config(value: FormatConfig | dict[str, Any]) -> FormatConfig:
    return value if isinstance(value, FormatConfig) else FormatConfig(**value)


def _model_dump(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    if hasattr(model, "dict"):
        return model.dict()
    return dict(model or {})


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)
