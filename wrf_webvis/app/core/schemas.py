from __future__ import annotations

from .config import DEFAULT_PLOT_PROCESSED_ROOT, DEFAULT_PROCESSED_ROOT, DEFAULT_EXPERIMENTS_ROOT, normalize_path_options

from enum import Enum
import math
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

try:
    from pydantic import BaseModel, Field
except ImportError:  # pragma: no cover - keeps core utilities usable before web deps are installed.
    class _FieldDefault:
        # 功能说明：保存字段默认值或默认生成器。
        def __init__(self, default: Any = None, default_factory: Any = None) -> None:
            self.default = default
            self.default_factory = default_factory

        # 功能说明：从字段默认值或默认生成器创建一个可独立使用的值。
        def make(self) -> Any:
            if self.default_factory is not None:
                return self.default_factory()
            if isinstance(self.default, (list, dict)):
                return self.default.copy()
            return self.default

    # 功能说明：在没有 Pydantic 时创建兼容的字段默认值对象。
    def Field(default=None, **kwargs: Any):  # type: ignore
        return _FieldDefault(default=default, default_factory=kwargs.get("default_factory"))

    class BaseModel:  # type: ignore
        # 功能说明：按字段声明填充传入数据、默认值和额外字段。
        def __init__(self, **data: Any) -> None:
            annotations = getattr(self, "__annotations__", {})
            for name in annotations:
                if hasattr(self.__class__, name):
                    default = getattr(self.__class__, name)
                    if isinstance(default, _FieldDefault):
                        default = default.make()
                    elif isinstance(default, (list, dict)):
                        default = default.copy()
                    setattr(self, name, data.pop(name, default))
            for name, value in data.items():
                setattr(self, name, value)

        # 功能说明：把模型当前字段复制成普通字典。
        def dict(self, **_: Any) -> dict[str, Any]:
            return dict(self.__dict__)

        # 功能说明：调用兼容的字典转换接口，返回可序列化字段。
        def model_dump(self, **kwargs: Any) -> dict[str, Any]:
            return self.dict(**kwargs)


class PlotType(int, Enum):
    HORIZONTAL = 0
    RADIUS_HEIGHT = 1
    TIME_RADIUS = 2
    TIME_AZIMUTH = 3
    HEIGHT_AZIMUTH = 4
    TIME_SERIES = 5
    TRACK = 6
    THREE_D = 7
    TIME_HEIGHT = 8
    CUSTOM_CODE = 9


class VariableDimension(str, Enum):
    ONE_D = "1d"
    TWO_D = "2d"
    THREE_D = "3d"


class PlotLayerType(str, Enum):
    CONTOURF = "contourf"
    CONTOUR = "contour"
    LINE = "line"
    QUIVER = "quiver"
    STREAM = "stream"


MAX_SELECTION_VALUES = 10000


class SelectionSpec(BaseModel):
    values: Optional[List[float]] = None
    start: Optional[float] = None
    end: Optional[float] = None
    step: Optional[float] = None
    average: bool = False

    # 功能说明：初始化选择条件后立即检查其合法性。
    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self.checked_count()

    # 功能说明：检查选择列表或起止步长是否合法且不过大，返回会选中的数量。
    def checked_count(self) -> int:
        """Validate before allocating ranges, including models edited in Python."""
        numbers = [value for value in (self.start, self.end, self.step) if value is not None]
        numbers.extend(self.values or [])
        if not all(math.isfinite(value) for value in numbers):
            raise ValueError("Selection values must be finite")
        if self.values is not None:
            if any(value is not None for value in (self.start, self.end, self.step)):
                raise ValueError("Use either selection values or start/end/step")
            count = len(self.values)
        elif self.start is None and self.end is None and self.step is None:
            return 0
        else:
            if self.start is None or self.end is None:
                raise ValueError("Selection range requires both start and end")
            step = 1.0 if self.step is None else self.step
            if step == 0:
                raise ValueError("Selection step cannot be 0")
            distance = (self.end - self.start) / step
            if distance < 0:
                raise ValueError("Selection step must point from start toward end")
            if not math.isfinite(distance) or distance >= MAX_SELECTION_VALUES:
                raise ValueError(f"Selection cannot exceed {MAX_SELECTION_VALUES} values")
            if self.start != self.end and self.start + step == self.start:
                raise ValueError("Selection step is too small to advance the range")
            count = math.floor(distance + 1e-9) + 1
        if count > MAX_SELECTION_VALUES:
            raise ValueError(f"Selection cannot exceed {MAX_SELECTION_VALUES} values")
        return count


class DomainConfig(BaseModel):
    domain: int
    dx_km: Optional[float] = None
    dy_km: Optional[float] = None
    nx: Optional[int] = None
    ny: Optional[int] = None
    source: str = "manual"
    wrfout_path: Optional[Path] = None


class VariableRegistryItem(BaseModel):
    name: str
    wrf_name: Optional[str] = None
    dimension: VariableDimension = VariableDimension.TWO_D
    needs_height_interp: bool = False
    needs_polar: bool = False
    supports: List[PlotLayerType] = Field(default_factory=list)
    default_cmap: Optional[str] = None
    default_levels: Optional[List[float]] = None
    default_extend: str = "both"
    sigma: Optional[float] = None
    scale_factor: float = 1.0
    derived: bool = False
    dependencies: List[str] = Field(default_factory=list)
    motion_correctable: bool = False


class VariableConfig(BaseModel):
    name: str
    plot_type: PlotLayerType = PlotLayerType.CONTOURF
    enabled: bool = True
    cmap: Optional[str] = None
    color: Optional[str] = None
    levels: Optional[List[float]] = None
    extend: str = "both"
    scale_factor: float = 1.0
    sigma: Optional[float] = None
    alpha: Optional[float] = None
    linewidth: Optional[float] = None
    linestyle: Optional[str] = None
    clabel: bool = True
    colorbar_title: Optional[str] = None
    missing_value_policy: Literal["interpolate", "mask", "error"] = "interpolate"
    use_polar: Optional[bool] = None
    motion_correct: bool = False
    time: Optional[SelectionSpec] = None
    height: Optional[SelectionSpec] = None
    angle: Optional[SelectionSpec] = None
    radius: Optional[SelectionSpec] = None
    # Time-series options.  Keeping them explicit prevents Pydantic from
    # discarding the advanced keys used by wrf_hpc_parameters.py.
    label: Optional[str] = None
    marker: Optional[str] = None
    units: Optional[str] = None
    unit: Optional[str] = None
    ylabel: Optional[str] = None
    axis_label: Optional[str] = None
    time_smooth_sigma: Optional[float] = None
    angle_smooth_sigma: Optional[float] = None
    angle_wrap: Optional[str] = None
    ylim: Optional[List[float]] = None
    yticks: Optional[List[float]] = None
    ytick_start: Optional[float] = None
    ytick_end: Optional[float] = None
    ytick_interval: Optional[float] = None
    integer_yticks: Optional[bool] = None
    ytick_nbins: Optional[int] = None
    direction_tick_values: Optional[List[float]] = None
    direction_tick_labels: Optional[List[str]] = None
    direction_ylim: Optional[List[float]] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class FormatConfig(BaseModel):
    common: Dict[str, Any] = Field(default_factory=dict)
    horizontal: Dict[str, Any] = Field(default_factory=dict)
    radius_height: Dict[str, Any] = Field(default_factory=dict)
    time_radius: Dict[str, Any] = Field(default_factory=dict)
    time_azimuth: Dict[str, Any] = Field(default_factory=dict)
    height_azimuth: Dict[str, Any] = Field(default_factory=dict)
    time_series: Dict[str, Any] = Field(default_factory=dict)
    track: Dict[str, Any] = Field(default_factory=dict)
    three_d: Dict[str, Any] = Field(default_factory=dict)
    time_height: Dict[str, Any] = Field(default_factory=dict)
    colorbar: Dict[str, Any] = Field(default_factory=dict)
    quiver_key: Dict[str, Any] = Field(default_factory=dict)


class PathRequest(BaseModel):
    # 功能说明：先规范化路径字段，再初始化请求模型。
    def __init__(self, **data: Any) -> None:
        super().__init__(**normalize_path_options(data))


class PlotRequest(PathRequest):
    dashboard_id: Optional[str] = None
    title: str = "WRF plot"
    data_root: Path = DEFAULT_PLOT_PROCESSED_ROOT
    processed_root: Optional[Path] = None
    wrfout_root: Optional[Path] = None
    wrfout_file: Optional[Path] = None
    member_name: str = "WRF_225_land"
    member_names: Optional[List[str]] = None
    # 2026-11-02 中心方案对比（处理数据_v3/center_schemes.json 的编号）：每个试验按这些方案展开成“试验@方案”成员，
    # 可按成员分面板或叠加比较。不填或只填 base 时与以前相同。成员名也可以直接写“试验@方案”。
    center_schemes: Optional[List[str]] = None
    experiment_type: Optional[str] = None
    domain: int = Field(default=3, ge=1, le=99)
    domain_config: Optional[DomainConfig] = None
    fig_type: PlotType = PlotType.HORIZONTAL
    custom_code: Optional[str] = None
    row: int = Field(default=1, ge=1, le=64)
    col: int = Field(default=1, ge=1, le=64)
    panel_by: Optional[str] = None
    panels: Optional[List[Dict[str, Any]]] = None
    output_format: Literal["png", "jpg", "jpeg", "pdf", "svg", "eps", "ps", "tif", "tiff", "webp"] = "png"
    dpi: int = Field(default=200, ge=1, le=1200)
    figsize: Optional[List[float]] = None
    save: bool = True
    show: bool = False
    dry_run: bool = False
    recompute: bool = False
    variables: List[VariableConfig] = Field(default_factory=list)
    quiver: List[VariableConfig] = Field(default_factory=list)
    time: SelectionSpec = Field(default_factory=SelectionSpec)
    height: Optional[SelectionSpec] = None
    angle: Optional[SelectionSpec] = None
    radius: Optional[SelectionSpec] = None
    selection: Dict[str, Any] = Field(default_factory=dict)
    format: FormatConfig = Field(default_factory=FormatConfig)
    polar_dir_style: str = "auto"
    polar_max_radius_km: Optional[float] = None
    polar_radius_resolution_km: Optional[float] = None
    extra: Dict[str, Any] = Field(default_factory=dict)

    # 功能说明：初始化绘图请求后检查画布大小和面板数量。
    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if self.fig_type == PlotType.CUSTOM_CODE:
            if not self.custom_code or not self.custom_code.strip():
                raise ValueError("custom_code 不能为空")
            self.output_format = "png"
        if self.figsize is not None and (
            len(self.figsize) != 2
            or not all(math.isfinite(value) and value > 0 for value in self.figsize)
        ):
            raise ValueError("figsize must contain two positive finite dimensions")
        if self.panels is not None and len(self.panels) > self.row * self.col:
            raise ValueError("Panel count exceeds row * col")


class ProcessingRequest(PathRequest):
    data_root: Path = DEFAULT_EXPERIMENTS_ROOT
    processed_root: Path = DEFAULT_PROCESSED_ROOT
    wrfout_root: Optional[Path] = None
    wrfout_file: Optional[Path] = None
    member_name: str
    domain: int
    variables: List[str]
    times: Optional[SelectionSpec] = None
    heights: Optional[SelectionSpec] = None
    dtype: str = "float32"
    motion_correct: bool = False
    force: bool = False
    trust_existing: bool = False
    options: Dict[str, Any] = Field(default_factory=dict)


class DashboardImage(BaseModel):
    id: str
    title: str
    image_path: str
    request: Dict[str, Any]
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # 用户写在图片下方的文字批注（2026-10-20）
    note: str = ""
    note_updated_at: str = ""
    note_updated_by: str = ""
    # 由哪个普通账号出的图（root/本机出的图为空）；出图账号只能删自己出的图（2026-11-06）
    created_by: str = ""


class Dashboard(BaseModel):
    id: str
    title: str
    images: List[DashboardImage] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # 父看板编号；None 表示顶层看板（2026-11-06，只支持两级）
    parent_id: Optional[str] = None


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class JobRecord(BaseModel):
    id: str
    status: JobStatus = JobStatus.PENDING
    request: Dict[str, Any] = Field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    progress: List[str] = Field(default_factory=list)
