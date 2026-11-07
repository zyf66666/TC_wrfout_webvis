from __future__ import annotations

import re
from typing import Any

from .schemas import PlotLayerType, VariableDimension, VariableRegistryItem


# 标准高度表：已处理数据可能存在的全部高度（读图、补齐层中心都按它），保持到 15000 m。
DEFAULT_HEIGHT_LEVELS_M = list(range(100, 1001, 100)) + list(
    range(1250, 15001, 250)
)

# 处理时不填高度的默认值（2026-10-16 用户要求）：暖心（位温异常、相当位温异常）算到 15000 m，其余变量算到 10000 m。
# 暖心清单须与 处理数据_v3/project_config.py 的 WARM_CORE_VARIABLES 一致（有测试检查）。
DEFAULT_PROCESSING_HEIGHTS_M = [height for height in DEFAULT_HEIGHT_LEVELS_M if height <= 10000]
WARM_CORE_VARIABLES = frozenset({
    "theta_anomaly", "theta_anomaly_polar", "theta_anomaly1_polar",
    "theta_e_anomaly", "theta_e_anomaly_polar", "theta_e_anomaly1_polar",
})


# 功能说明：按本次请求的变量给出默认处理高度；全部是暖心变量时到 15000 m，否则到 10000 m。
def default_processing_heights(variables) -> list[int]:
    names = [str(name) for name in (variables or [])]
    if names and all(name in WARM_CORE_VARIABLES for name in names):
        return list(DEFAULT_HEIGHT_LEVELS_M)
    return list(DEFAULT_PROCESSING_HEIGHTS_M)


DEFAULT_DOMAIN_CONFIG: dict[int, dict[str, Any]] = {
    1: {"dx_km": 25.0, "dy_km": 25.0, "nx": 201, "ny": 201},
    2: {"dx_km": 5.0, "dy_km": 5.0, "nx": 405, "ny": 405},
    3: {"dx_km": 1.0, "dy_km": 1.0, "nx": 805, "ny": 805},
}


FIGURE_TYPE_REGISTRY = {
    0: "horizontal",
    1: "radius_height",
    2: "time_radius",
    3: "time_azimuth",
    4: "height_azimuth",
    5: "time_series",
    6: "track",
    7: "three_d",
    8: "time_height",
}


# 功能说明：把变量名称、维度和绘图默认值组装成一个变量登记项。
def _item(
    name: str,
    dimension: VariableDimension,
    *,
    wrf_name: str | None = None,
    needs_height_interp: bool = False,
    needs_polar: bool = False,
    supports: list[PlotLayerType] | None = None,
    default_cmap: str | None = None,
    default_levels: list[float] | None = None,
    default_extend: str = "both",
    derived: bool = False,
    dependencies: list[str] | None = None,
    motion_correctable: bool = False,
) -> VariableRegistryItem:
    return VariableRegistryItem(
        name=name,
        wrf_name=wrf_name or name,
        dimension=dimension,
        needs_height_interp=needs_height_interp,
        needs_polar=needs_polar,
        supports=supports or [PlotLayerType.CONTOURF, PlotLayerType.CONTOUR],
        default_cmap=default_cmap,
        default_levels=default_levels,
        default_extend=default_extend,
        derived=derived,
        dependencies=dependencies or [],
        motion_correctable=motion_correctable,
    )


VARIABLE_REGISTRY: dict[str, VariableRegistryItem] = {
    "dbz": _item("dbz", VariableDimension.THREE_D, needs_height_interp=True, needs_polar=True, default_cmap="cmaps.precip3_16lev"),
    "ua": _item("ua", VariableDimension.THREE_D, needs_height_interp=True, needs_polar=True, motion_correctable=True),
    "va": _item("va", VariableDimension.THREE_D, needs_height_interp=True, needs_polar=True, motion_correctable=True),
    "vr": _item(
        "vr",
        VariableDimension.THREE_D,
        needs_height_interp=True,
        needs_polar=True,
        derived=True,
        dependencies=["ua", "va", "slp_center"],
        default_cmap="RdBu_r",
        default_levels=[-40, -32, -24, -16, -8, 0, 8, 16, 24, 32, 40],
    ),
    "vt": _item(
        "vt",
        VariableDimension.THREE_D,
        needs_height_interp=True,
        needs_polar=True,
        derived=True,
        dependencies=["ua", "va", "slp_center"],
        default_cmap="RdBu_r",
        default_levels=[-40, -32, -24, -16, -8, 0, 8, 16, 24, 32, 40],
    ),
    "wa": _item("wa", VariableDimension.THREE_D, needs_height_interp=True, needs_polar=True),
    "u": _item("u", VariableDimension.THREE_D, needs_height_interp=True, needs_polar=True, motion_correctable=True),
    "v": _item("v", VariableDimension.THREE_D, needs_height_interp=True, needs_polar=True, motion_correctable=True),
    "rh": _item("rh", VariableDimension.THREE_D, needs_height_interp=True, needs_polar=True),
    "pressure": _item(
        "pressure",
        VariableDimension.THREE_D,
        needs_height_interp=True,
        needs_polar=True,
        default_cmap="viridis_r",
        default_levels=[600, 625, 650, 675, 700, 725, 750, 775, 800, 825, 850, 875, 900, 925, 950, 975, 1000],
    ),
    "theta": _item("theta", VariableDimension.THREE_D, needs_height_interp=True, needs_polar=True),
    "theta_e": _item("theta_e", VariableDimension.THREE_D, needs_height_interp=True, needs_polar=True),
    "theta_anomaly": _item("theta_anomaly", VariableDimension.THREE_D, needs_polar=True, derived=True, dependencies=["theta"], default_cmap="RdBu_r", default_levels=[-20, -15, -10, -5, -2, 0, 2, 5, 10, 15, 20]),
    "theta_e_anomaly": _item("theta_e_anomaly", VariableDimension.THREE_D, needs_polar=True, derived=True, dependencies=["theta_e"], default_cmap="RdBu_r", default_levels=[-20, -15, -10, -5, -2, 0, 2, 5, 10, 15, 20]),
    "pvo": _item("pvo", VariableDimension.THREE_D, needs_height_interp=True, needs_polar=True, default_cmap="RdBu_r"),
    "QVAPOR": _item("QVAPOR", VariableDimension.THREE_D, needs_height_interp=True, needs_polar=True),
    "temp": _item("temp", VariableDimension.THREE_D, needs_height_interp=True, needs_polar=True),
    "mdbz": _item("mdbz", VariableDimension.TWO_D, needs_polar=True, default_cmap="cmaps.precip3_16lev"),
    "slp": _item("slp", VariableDimension.TWO_D, needs_polar=True),
    "cape": _item("cape", VariableDimension.TWO_D, wrf_name="cape_2d", needs_polar=True),
    "RAINNCV": _item("RAINNCV", VariableDimension.TWO_D, needs_polar=True),
    "RAINNC": _item("RAINNC", VariableDimension.TWO_D, needs_polar=True),
    "HFX": _item("HFX", VariableDimension.TWO_D, needs_polar=True),
    "LH": _item("LH", VariableDimension.TWO_D, needs_polar=True),
    "U10": _item("U10", VariableDimension.TWO_D, needs_polar=True, motion_correctable=True),
    "V10": _item("V10", VariableDimension.TWO_D, needs_polar=True, motion_correctable=True),
    "UST": _item("UST", VariableDimension.TWO_D, needs_polar=True),
    "PBLH": _item("PBLH", VariableDimension.TWO_D, needs_polar=True),
    "convergence": _item("convergence", VariableDimension.THREE_D, needs_polar=True, derived=True, dependencies=["ua", "va"]),
    "mass_convergence": _item("mass_convergence", VariableDimension.THREE_D, needs_polar=True, derived=True, dependencies=["ua", "va", "pressure", "temp", "QVAPOR"]),
    "mfc": _item("mfc", VariableDimension.THREE_D, needs_polar=True, derived=True, dependencies=["ua", "va", "pressure", "temp", "QVAPOR"]),
    "qflux_r": _item("qflux_r", VariableDimension.THREE_D, needs_polar=True, derived=True, dependencies=["ua", "va", "pressure", "temp", "QVAPOR", "slp_center"]),
    "inflow_angle": _item("inflow_angle", VariableDimension.THREE_D, needs_polar=True, derived=True, dependencies=["ua", "va", "slp_center"]),
    # 2026-10-26 超梯度力诊断（m s-2，向外为正；算法见 处理数据_v3/wrf_processing_v3/variables/_agradient.py）
    "pgf_r": _item("pgf_r", VariableDimension.THREE_D, needs_polar=True, derived=True, dependencies=["pressure", "density", "slp_center"], default_cmap="RdBu_r", default_levels=[-0.03, -0.025, -0.02, -0.015, -0.01, -0.005, 0, 0.005, 0.01, 0.015, 0.02, 0.025, 0.03]),
    "cen_r": _item("cen_r", VariableDimension.THREE_D, needs_polar=True, derived=True, dependencies=["ua", "va", "slp_center", "center_geo"], default_cmap="RdBu_r", default_levels=[-0.03, -0.025, -0.02, -0.015, -0.01, -0.005, 0, 0.005, 0.01, 0.015, 0.02, 0.025, 0.03]),
    "cor_r": _item("cor_r", VariableDimension.THREE_D, needs_polar=True, derived=True, dependencies=["ua", "va", "slp_center", "center_geo"], default_cmap="RdBu_r", default_levels=[-0.003, -0.0025, -0.002, -0.0015, -0.001, -0.0005, 0, 0.0005, 0.001, 0.0015, 0.002, 0.0025, 0.003]),
    "motf_r": _item("motf_r", VariableDimension.TWO_D, needs_polar=True, derived=True, dependencies=["slp", "slp_center", "center_geo"], default_cmap="RdBu_r", default_levels=[-0.0004, -0.0003, -0.0002, -0.0001, 0, 0.0001, 0.0002, 0.0003, 0.0004]),
    "agf_r": _item("agf_r", VariableDimension.THREE_D, needs_polar=True, derived=True, dependencies=["pgf_r", "cen_r", "cor_r", "motf_r"], default_cmap="RdBu_r", default_levels=[-0.01, -0.008, -0.006, -0.004, -0.002, 0, 0.002, 0.004, 0.006, 0.008, 0.01]),
    "bl_mass_convergence": _item("bl_mass_convergence", VariableDimension.TWO_D, needs_polar=True, derived=True, dependencies=["mass_convergence"]),
    "bl_mfc": _item("bl_mfc", VariableDimension.TWO_D, needs_polar=True, derived=True, dependencies=["mfc"]),
    "wind_speed_10": _item(
        "wind_speed_10",
        VariableDimension.TWO_D,
        needs_polar=True,
        derived=True,
        dependencies=["U10", "V10"],
    ),
    "local_max_wind": _item(
        "local_max_wind",
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["ua", "va"],
        supports=[PlotLayerType.LINE],
        default_levels=[0, 10, 20, 30, 40, 50, 60],
    ),
    "axis_mean_max_wind": _item(
        "axis_mean_max_wind",
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["ua", "va", "slp_center"],
        supports=[PlotLayerType.LINE],
        default_levels=[0, 10, 20, 30, 40, 50, 60],
    ),
    "rmw": _item(
        "rmw",
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["ua", "va", "slp_center"],
        supports=[PlotLayerType.LINE],
        default_levels=[0, 50, 100, 150, 200],
    ),
    "r_34": _item(
        "r_34",
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["ua", "va", "slp_center"],
        supports=[PlotLayerType.LINE],
        default_levels=[0, 50, 100, 150, 200, 250, 300],
    ),
    "center_slp_5km": _item(
        "center_slp_5km",
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["slp", "slp_center"],
        supports=[PlotLayerType.LINE],
        default_levels=[900, 920, 940, 960, 980, 1000],
    ),
    "center_land_distance": _item(
        "center_land_distance",
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["slp_center", "land_distance"],
        supports=[PlotLayerType.LINE],
        default_levels=[-200, -100, 0, 100, 200],
    ),
    "precipitation_asymmetry": _item(
        "precipitation_asymmetry",
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["RAINNCV_polar", "slp_center"],
        supports=[PlotLayerType.LINE],
        default_levels=[0, 0.25, 0.5, 0.75, 1.0],
        default_extend="neither",
    ),
    "mdbz_asymmetry": _item(
        "mdbz_asymmetry",
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["mdbz_polar", "slp_center"],
        supports=[PlotLayerType.LINE],
        default_levels=[0, 0.25, 0.5, 0.75, 1.0],
        default_extend="neither",
    ),
    "dbz_asymmetry": _item(
        "dbz_asymmetry",
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["dbz1_polar", "layer_center"],
        supports=[PlotLayerType.LINE],
        default_levels=[0, 0.25, 0.5, 0.75, 1.0],
        default_extend="neither",
    ),
    "shear_200_850_u": _item(
        "shear_200_850_u",
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["ua", "va", "pressure", "slp_center"],
        supports=[PlotLayerType.LINE],
        default_levels=[-20, -15, -10, -5, 0, 5, 10, 15, 20],
    ),
    "shear_200_850_v": _item(
        "shear_200_850_v",
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["ua", "va", "pressure", "slp_center"],
        supports=[PlotLayerType.LINE],
        default_levels=[-20, -15, -10, -5, 0, 5, 10, 15, 20],
    ),
    "shear_200_850_magnitude": _item(
        "shear_200_850_magnitude",
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["ua", "va", "pressure", "slp_center"],
        supports=[PlotLayerType.LINE],
        default_levels=[0, 5, 10, 15, 20, 25, 30],
    ),
    "shear_200_850_direction": _item(
        "shear_200_850_direction",
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["ua", "va", "pressure", "slp_center"],
        supports=[PlotLayerType.LINE],
        default_levels=[-180, -90, 0, 90, 180],
    ),
    "tilt_x": _item(
        "tilt_x",
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["pressure", "layer_center"],
        supports=[PlotLayerType.LINE],
        default_levels=[-100, -50, 0, 50, 100],
    ),
    "tilt_y": _item(
        "tilt_y",
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["pressure", "layer_center"],
        supports=[PlotLayerType.LINE],
        default_levels=[-100, -50, 0, 50, 100],
    ),
    "tilt_magnitude": _item(
        "tilt_magnitude",
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["pressure", "layer_center"],
        supports=[PlotLayerType.LINE],
        default_levels=[0, 20, 40, 60, 80, 100, 120],
    ),
    "tilt_angle": _item(
        "tilt_angle",
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["pressure", "layer_center"],
        supports=[PlotLayerType.LINE],
        default_levels=[-180, -90, 0, 90, 180],
    ),
    "diabatic_heating": _item(
        "diabatic_heating",
        VariableDimension.THREE_D,
        wrf_name="H_DIABATIC",
        needs_height_interp=True,
        needs_polar=True,
    ),
}


# 2026-11-04：内核区风切变 shear_core_200_850_*（处理套餐用目标域 d03 的 150–350 km 环带，见处理端注册表），
# 与 d02 环境切变 shear_200_850_* 同样是逐时次标量折线；登记后时间序列图不再去找 *_polar 文件。
for component, levels in (
    ("u", [-20, -15, -10, -5, 0, 5, 10, 15, 20]),
    ("v", [-20, -15, -10, -5, 0, 5, 10, 15, 20]),
    ("magnitude", [0, 5, 10, 15, 20, 25, 30]),
    ("direction", [-180, -90, 0, 90, 180]),
):
    VARIABLE_REGISTRY[f"shear_core_200_850_{component}"] = _item(
        f"shear_core_200_850_{component}",
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["ua", "va", "pressure", "slp_center"],
        supports=[PlotLayerType.LINE],
        default_levels=levels,
    )


for name, levels in (
    ("tilt_1_6", [0, 20, 40, 60, 80, 100, 120]),
    ("tilt_x_1_6", [-100, -50, 0, 50, 100]),
    ("tilt_y_1_6", [-100, -50, 0, 50, 100]),
    ("tilt_angle_1_6", [-180, -90, 0, 90, 180]),
    # 2026-10-26 Claude：固定层次倾斜 2–4、2–6、2–10 km（处理端同名变量）；
    # 2026-11-04 加 2–8 km（tilt_2_8 等，取代旧名 tilt_magnitude / tilt_x / tilt_y / tilt_angle）。
    *(
        item
        for suffix in ("2_4", "2_6", "2_8", "2_10")
        for item in (
            (f"tilt_{suffix}", [0, 20, 40, 60, 80, 100, 120]),
            (f"tilt_x_{suffix}", [-100, -50, 0, 50, 100]),
            (f"tilt_y_{suffix}", [-100, -50, 0, 50, 100]),
            (f"tilt_angle_{suffix}", [-180, -90, 0, 90, 180]),
        )
    ),
):
    VARIABLE_REGISTRY[name] = _item(
        name,
        VariableDimension.ONE_D,
        derived=True,
        dependencies=["layer_center"],
        supports=[PlotLayerType.LINE],
        default_levels=levels,
    )


# 功能说明：解析变量别名后从登记表取配置；未登记时提示错误。
def get_variable_item(name: str) -> VariableRegistryItem:
    resolved = resolve_variable_name(name)
    if resolved.base_name not in VARIABLE_REGISTRY:
        raise KeyError(f"Variable is not registered: {name}")
    return VARIABLE_REGISTRY[resolved.base_name]


# 功能说明：生成绘图端需要的“变量名到维度”对照表。
def plotting_variable_type_registry() -> dict[str, str]:
    registry = {name: item.dimension.value for name, item in VARIABLE_REGISTRY.items()}
    for name, item in VARIABLE_REGISTRY.items():
        if item.dimension == VariableDimension.THREE_D:
            registry[f"{name}1"] = item.dimension.value
    return registry


class ResolvedVariable:
    # 功能说明：保存原变量名、基础名和中心模式，供后续命名。
    def __init__(self, requested_name: str, base_name: str, center_mode: str) -> None:
        self.requested_name = requested_name
        self.base_name = base_name
        self.center_mode = center_mode

    # 功能说明：根据中心模式拼出该变量的极坐标数据名。
    @property
    def polar_name(self) -> str:
        if self.center_mode == "layer":
            return f"{self.base_name}1_polar"
        return f"{self.base_name}_polar"


# 功能说明：识别普通变量或分层中心变量，返回其基础名和中心模式。
def resolve_variable_name(name: str) -> ResolvedVariable:
    if name in VARIABLE_REGISTRY:
        return ResolvedVariable(name, name, "slp")
    if name.endswith("_polar"):
        prefix = name[:-6]
        if prefix in VARIABLE_REGISTRY or (prefix.endswith("1") and prefix[:-1] in VARIABLE_REGISTRY):
            return resolve_variable_name(prefix)
    if name.endswith("1") and name[:-1] in VARIABLE_REGISTRY:
        return ResolvedVariable(name, name[:-1], "layer")
    return ResolvedVariable(name, name, "slp")


class ResolvedFourierVariable:
    # 功能说明：保存傅里叶变量的前缀、成分、中心模式和波数。
    def __init__(
        self,
        *,
        requested_name: str,
        prefix: str,
        base_name: str,
        center_mode: str,
        component: str,
        wavenumber: int | None = None,
    ) -> None:
        self.requested_name = requested_name
        self.prefix = prefix
        self.base_name = base_name
        self.center_mode = center_mode
        self.component = component
        self.wavenumber = wavenumber

    # 功能说明：解析前缀后返回傅里叶变量应读取的极坐标源名。
    @property
    def polar_source_name(self) -> str:
        return resolve_variable_name(self.prefix).polar_name

    # 功能说明：根据变量前缀拼出非对称极坐标数据名。
    @property
    def asymmetry_name(self) -> str:
        return f"{self.prefix}_asy_polar"

    # 功能说明：依据成分类型选择并返回实际数据源名。
    @property
    def source_name(self) -> str:
        if self.component == "asymmetry":
            return self.polar_source_name
        return self.asymmetry_name


# 功能说明：识别方位非对称或波数变量名，匹配时返回解析结果。
def parse_fourier_variable_name(name: str) -> ResolvedFourierVariable | None:
    wave = re.match(r"^(?P<prefix>.+)_asy(?P<wavenumber>\d+)w_polar$", name)
    if wave is not None:
        prefix = wave.group("prefix")
        resolved = resolve_variable_name(prefix)
        return ResolvedFourierVariable(
            requested_name=name,
            prefix=prefix,
            base_name=resolved.base_name,
            center_mode=resolved.center_mode,
            component="wave",
            wavenumber=int(wave.group("wavenumber")),
        )
    if name.endswith("_asy_polar"):
        prefix = name[: -len("_asy_polar")]
        resolved = resolve_variable_name(prefix)
        return ResolvedFourierVariable(
            requested_name=name,
            prefix=prefix,
            base_name=resolved.base_name,
            center_mode=resolved.center_mode,
            component="asymmetry",
        )
    return None


class ResolvedCartesianFromPolarVariable:
    # 功能说明：保存极坐标还原变量的来源和解析结果。
    def __init__(
        self,
        *,
        requested_name: str,
        prefix: str,
        base_name: str,
        center_mode: str,
        source_name: str,
    ) -> None:
        self.requested_name = requested_name
        self.prefix = prefix
        self.base_name = base_name
        self.center_mode = center_mode
        self.source_name = source_name


# 功能说明：识别要由极坐标还原的非对称变量名，匹配时返回解析结果。
def parse_cartesian_from_polar_variable_name(name: str) -> ResolvedCartesianFromPolarVariable | None:
    match = re.match(r"^(?P<prefix>.+)_asy(?:(?P<wavenumber>\d+)w)?$", name)
    if match is None:
        return None
    prefix = match.group("prefix")
    resolved = resolve_variable_name(prefix)
    return ResolvedCartesianFromPolarVariable(
        requested_name=name,
        prefix=prefix,
        base_name=resolved.base_name,
        center_mode=resolved.center_mode,
        source_name=f"{name}_polar",
    )
