"""变量注册表：新增普通派生变量只需添加条目和 variables/<变量>.py。

kind=getvar：交给统一读取器；derived：依赖就绪后调用 process(ctx)；
native：自定义原生模式层诊断（例如 PVO），计算后由读取器统一高度插值。
"""

from dataclasses import dataclass, field, replace
import re
from typing import Any


@dataclass(frozen=True)
class Dependency:
    """same 表示继承目标高度；lowest 明确表示最低模式层，不是最低请求高度。"""

    name: str
    height: Any = "same"
    domain: int | None = None
    time_offset: float = 0.0
    time: float | None = None


@dataclass(frozen=True)
class VariableSpec:
    name: str
    kind: str
    units: str
    dimensions: str = "height"
    dependencies: tuple[Dependency, ...] = ()
    handler: Any = None
    wrf_name: str | None = None
    options: dict = field(default_factory=dict)

    def __post_init__(self):
        # 改动科学算法时提升此版本或给单个变量显式指定版本，严格缓存随之失效。
        settings = {"algorithm_version": "v2.1", **self.options}
        object.__setattr__(self, "options", settings)


# 本身已按逐层中心 layer_center 分解、名字以 1 结尾的风场：X1_polar 时源变量就是它自己（不去掉末尾的 1）。
# 2026-11-03 加风暴相对风 vr_sr1 / vt_sr1、入流角 inflow_angle_sr1、径向水汽通量 qflux_r_sr1。
LAYER_CENTER_WINDS = ("vr1", "vt1", "vr_sr1", "vt_sr1", "inflow_angle_sr1", "qflux_r_sr1")


class Registry:
    """只解析明确登记的变量和约定后缀；拼写错误不能悄悄传给 getvar。"""

    def __init__(self, specs=()):
        self.specs = {spec.name: spec for spec in specs}

    def add(self, spec):
        if spec.name in self.specs:
            raise ValueError(f"重复登记变量：{spec.name}")
        self.specs[spec.name] = spec

    def resolve(self, name):
        if name in self.specs:
            return self.specs[name]
        aliases = {
            "center": "slp_center",
            "height_center": "layer_center",
            "pressure_center": "layer_center",
            "cloud_classification": "classification",
            "motion": "storm_motion",
            "land_distance": "center_land_distance",
        }
        if name in aliases:
            source = aliases[name]
            base = self.resolve(source)
            return VariableSpec(
                name,
                "derived",
                base.units,
                base.dimensions,
                (Dependency(source),),
                "wrf_processing_v3.variables.alias:process",
                options={**base.options, "source": source},
            )
        # 波数分量的依赖是非对称场，非对称场的依赖是原始极坐标场。
        wave = re.fullmatch(r"(.+)_asy(\d+)w_polar", name)
        if wave or name.endswith("_asy_polar"):
            prefix = wave[1] if wave else name[:-10]
            source = f"{prefix}_asy_polar" if wave else f"{prefix}_polar"
            base = self.resolve(source)
            return VariableSpec(
                name,
                "derived",
                base.units,
                base.dimensions,
                (Dependency(source),),
                "wrf_processing_v3.variables.fourier:process",
                options={
                    "source": source,
                    "mode": "wave" if wave else "asymmetry",
                    "wavenumber": int(wave[2]) if wave else None,
                },
            )
        cartesian = re.fullmatch(r"(.+)_asy(?:\d+w)?", name)
        if cartesian:
            source = f"{name}_polar"
            base = self.resolve(source)
            prefix = cartesian[1]
            layered = prefix.endswith("1")
            reference = (
                prefix
                if prefix in LAYER_CENTER_WINDS
                else prefix[:-1] if layered else prefix
            )
            center = "layer_center" if layered else "slp_center"
            return VariableSpec(
                name,
                "derived",
                base.units,
                base.dimensions,
                (Dependency(source), Dependency(reference), Dependency(center)),
                "wrf_processing_v3.variables.cartesian:process",
                options={"source": source, "reference": reference, "center": center},
            )
        if name.endswith("_polar"):
            prefix = name[:-6]
            layered = prefix.endswith("1")
            source = (
                prefix
                if prefix in LAYER_CENTER_WINDS
                else prefix[:-1] if layered else prefix
            )
            base = self.resolve(source)
            if base.dimensions not in ("height", "surface"):
                raise ValueError(f"{source} 不是可插值的二维场")
            if layered and base.dimensions != "height":
                raise ValueError("二维地面场不能选择分层中心")
            center = "layer_center" if layered else "slp_center"
            return VariableSpec(
                name,
                "derived",
                base.units,
                base.dimensions,
                (Dependency(source), Dependency(center)),
                "wrf_processing_v3.variables.polar:process",
                options={"source": source, "center": center},
            )
        raise KeyError(f"变量未登记：{name}。请检查 register.py。")


REGISTRY = Registry()
# 单位既是保存约定，也是 getvar 读取时应满足的物理单位。
for name, units, dimensions, wrf_name, kwargs in [
    ("ua", "m s-1", "height", "ua", {"units": "m s-1"}),
    ("va", "m s-1", "height", "va", {"units": "m s-1"}),
    ("wa", "m s-1", "height", "wa", {"units": "m s-1"}),
    ("u", "m s-1", "height", "ua", {"units": "m s-1"}),
    ("v", "m s-1", "height", "va", {"units": "m s-1"}),
    ("pressure", "hPa", "height", "pressure", {}),
    ("temp", "K", "height", "temp", {"units": "K"}),
    ("theta", "K", "height", "theta", {"units": "K"}),
    ("theta_e", "K", "height", "theta_e", {"units": "K"}),
    ("rh", "%", "height", "rh", {}),
    ("QVAPOR", "kg kg-1", "height", "QVAPOR", {}),
    ("dbz", "dBZ", "height", "dbz", {}),
    ("diabatic_heating", "K s-1", "height", "H_DIABATIC", {}),
    ("slp", "hPa", "surface", "slp", {"units": "hPa"}),
    ("mdbz", "dBZ", "surface", "mdbz", {}),
    ("cape", "J kg-1", "surface", "cape_2d", {}),
    ("RAINNCV", "mm", "surface", "RAINNCV", {}),
    ("RAINNC", "mm", "surface", "RAINNC", {}),
    ("HFX", "W m-2", "surface", "HFX", {}),
    ("LH", "W m-2", "surface", "LH", {}),
    ("U10", "m s-1", "surface", "U10", {}),
    ("V10", "m s-1", "surface", "V10", {}),
    ("UST", "m s-1", "surface", "UST", {}),
    ("PBLH", "m", "surface", "PBLH", {}),
]:
    REGISTRY.add(
        VariableSpec(
            name,
            "getvar",
            units,
            dimensions,
            wrf_name=wrf_name,
            options={"getvar_kwargs": kwargs},
        )
    )


def derived(name, units, dependencies, dimensions="height"):
    """短写法适合绝大多数同域、同时次、同高度的派生变量。"""
    deps = tuple(Dependency(d) if isinstance(d, str) else d for d in dependencies)
    REGISTRY.add(
        VariableSpec(
            name,
            "derived",
            units,
            dimensions,
            deps,
            f"wrf_processing_v3.variables.{name}:process",
        )
    )


derived("density", "kg m-3", ["pressure", "temp", "QVAPOR"])
derived("slp_center", "grid index", ["slp"], "surface")
derived("layer_center", "grid index", ["pressure"])
for name, center in [
    ("vr", "slp_center"),
    ("vt", "slp_center"),
    ("vr1", "layer_center"),
    ("vt1", "layer_center"),
]:
    derived(name, "m s-1", ["ua", "va", center])
# 2026-11-03 风暴相对风（减去风暴移速）。命名约定：与风速有关的变量后缀 _sr = 用风暴相对风；
# 末尾再带 1 的以本高度 layer_center 分解，否则以 slp_center 分解。
# 移速与超梯度诊断同一口径：center_geo 在 t ± 3 h 的中心差分；开头/结尾缺一侧时用单侧差分（见 variables/_storm_relative.py）。
# 不另设 _sr 的：辐合、相对涡度（减去均匀移速后不变）、最低模式层的轴平均最大风/RMW（整圈平均的切向风基本不变）、
# 局地最大风与 10 m 风速（强度按惯例用相对地面的风）、超梯度诊断（内部已减移速）。
# RMW 随高度另有 _sr 版本（用户 11-03 要求分析套餐的风相关量全部用风暴相对风），数值通常与原版几乎相同。
STORM_RELATIVE_VARIABLES = {
    # 名字: (单位, [风以外的依赖])
    "ua_sr": ("m s-1", []),
    "va_sr": ("m s-1", []),
    "vr_sr": ("m s-1", ["slp_center"]),
    "vt_sr": ("m s-1", ["slp_center"]),
    "vr_sr1": ("m s-1", ["layer_center"]),
    "vt_sr1": ("m s-1", ["layer_center"]),
    "inflow_angle_sr": ("degree", ["slp_center"]),
    "inflow_angle_sr1": ("degree", ["layer_center"]),
    "qflux_r_sr": ("kg m-2 s-1", ["pressure", "temp", "QVAPOR", "slp_center"]),
    "qflux_r_sr1": ("kg m-2 s-1", ["pressure", "temp", "QVAPOR", "layer_center"]),
}
for name, (units, others) in STORM_RELATIVE_VARIABLES.items():
    winds = {"ua_sr": ["ua"], "va_sr": ["va"]}.get(name, ["ua", "va"])
    REGISTRY.add(
        VariableSpec(
            name, "derived", units, "height",
            (*(Dependency(item) for item in winds + others), Dependency("center_geo", None)),
            f"wrf_processing_v3.variables.{name}:process",
            options={"motion_half_window_h": 3.0, "algorithm_version": "storm_relative_v1"},
        )
    )
for name, center_dependency in [
    ("rmw_by_height_sr", Dependency("slp_center", None)),
    ("rmw_by_layer_center_sr", Dependency("layer_center")),
]:
    REGISTRY.add(
        VariableSpec(
            name, "derived", "km", "height_scalar",
            (Dependency("ua"), Dependency("va"), center_dependency, Dependency("center_geo", None)),
            f"wrf_processing_v3.variables.{name}:process",
            options={"motion_half_window_h": 3.0, "algorithm_version": "storm_relative_v1"},
        )
    )
# motion_correct 打开时要换成地球相对风的全部风暴相对变量
STORM_RELATIVE_ALL = (*STORM_RELATIVE_VARIABLES, "rmw_by_height_sr", "rmw_by_layer_center_sr")
derived("convergence", "s-1", ["ua", "va"])
derived("vort_rel", "s-1", ["ua", "va"])
derived("mass_convergence", "kg m-3 s-1", ["ua", "va", "pressure", "temp", "QVAPOR"])
derived("mfc", "kg m-3 s-1", ["ua", "va", "pressure", "temp", "QVAPOR"])
derived(
    "qflux_r", "kg m-2 s-1", ["ua", "va", "pressure", "temp", "QVAPOR", "slp_center"]
)
derived("inflow_angle", "degree", ["ua", "va", "slp_center"])
derived("wind_speed_10", "m s-1", ["U10", "V10"], "surface")
for name, units in [
    ("rmw", "km"),
    ("local_max_wind", "m s-1"),
    ("axis_mean_max_wind", "m s-1"),
    ("r_34", "km"),
]:
    deps = [Dependency("ua", "lowest"), Dependency("va", "lowest")]
    if name != "local_max_wind":
        deps.append(Dependency("slp_center", None))
    derived(name, units, deps, "scalar")
derived(
    "rmw_by_height",
    "km",
    ["ua", "va", Dependency("slp_center", None)],
    "height_scalar",
)
derived(
    "rmw_by_layer_center",
    "km",
    ["ua", "va", "layer_center"],
    "height_scalar",
)
REGISTRY.add(
    VariableSpec(
        "pvo",
        "native",
        "PVU",
        handler="wrf_processing_v3.variables.pvo:calculate",
        options={
            "raw_dependencies": ["U", "V", "T", "P", "PB", "F (or wrfinput_d01)"],
            "vertical_stage": "before_height_interpolation",
        },
    )
)


def build_registry(options=None, times=None):
    """根据本次参数创建注册表，避免修改全局表污染下一次试验。

    固定层、跨域依赖在规划前变成明确的 Dependency，所以管理器不需要
    针对每个诊断写 if/else。算法参数改变时应 FORCE 重算或使用严格缓存。
    """
    options = options or {}
    registry = Registry(REGISTRY.specs.values())

    def add(name, units, dependencies, dimensions="height", **settings):
        registry.add(
            VariableSpec(
                name,
                "derived",
                units,
                dimensions,
                tuple(Dependency(d) if isinstance(d, str) else d for d in dependencies),
                f"wrf_processing_v3.variables.{name}:process",
                options=settings,
            )
        )

    heights = [900.0, 1000.0, 1250.0, 1500.0, 1750.0, 2000.0, 3000.0, 4000.0, 8000.0]
    add(
        "classification",
        "1",
        [Dependency(v, h) for v in ("dbz", "wa") for h in heights],
        "surface",
        heights=heights,
        output_dtype="int8",
    )
    explicit_heights = options.get("boundary_layer_heights_m")
    if explicit_heights is None:
        import numpy as np

        lower_bl = float(options.get("boundary_layer_bottom_m", 100.0))
        upper_bl = float(options.get("boundary_layer_top_m", 1000.0))
        if upper_bl <= lower_bl:
            raise ValueError("边界层顶部必须高于底部")
        explicit_heights = list(np.arange(lower_bl, upper_bl, 100.0)) + [upper_bl]
    bl_heights = sorted(set(float(h) for h in explicit_heights))
    if len(bl_heights) < 2:
        raise ValueError("边界层积分至少需要两个不同高度")
    for name, source in [
        ("bl_mass_convergence", "mass_convergence"),
        ("bl_mfc", "mfc"),
    ]:
        add(
            name,
            "kg m-2 s-1",
            [Dependency(source, h) for h in bl_heights],
            "surface",
            heights=bl_heights,
            source=source,
        )
    lower = float(options.get("tilt_lower_height_m", 2000.0))
    upper = float(options.get("tilt_upper_height_m", 8000.0))
    if not 0 <= lower < upper:
        raise ValueError("倾斜诊断需要 0 <= lower_height < upper_height")
    for name in ("tilt_x", "tilt_y", "tilt_magnitude", "tilt_angle"):
        add(
            name,
            "degree" if name == "tilt_angle" else "km",
            [Dependency("layer_center", lower), Dependency("layer_center", upper)],
            "scalar",
            lower_height=lower,
            upper_height=upper,
        )
    for name in ("tilt_1_6", "tilt_x_1_6", "tilt_y_1_6", "tilt_angle_1_6"):
        add(
            name,
            "degree" if name == "tilt_angle_1_6" else "km",
            [Dependency("layer_center", 1000), Dependency("layer_center", 6000)],
            "scalar",
            lower_height=1000,
            upper_height=6000,
        )
    # 2026-10-26 Claude：固定层次的倾斜 2–4、2–6、2–10 km，与 tilt_1_6 同一套算法，各自独立保存。
    # 2026-11-04：加 2–8 km（tilt_2_8 / tilt_x_2_8 / tilt_y_2_8 / tilt_angle_2_8），命名与其他层次一致；
    # 上面的旧名 tilt_magnitude / tilt_x / tilt_y / tilt_angle 保留，供旧数据和旧看板使用（数值相同）。
    for lower_m, upper_m in ((2000, 4000), (2000, 6000), (2000, 8000), (2000, 10000)):
        suffix = f"{lower_m // 1000}_{upper_m // 1000}"
        for prefix in ("tilt", "tilt_x", "tilt_y", "tilt_angle"):
            add(
                f"{prefix}_{suffix}",
                "degree" if prefix == "tilt_angle" else "km",
                [Dependency("layer_center", lower_m), Dependency("layer_center", upper_m)],
                "scalar",
                lower_height=lower_m,
                upper_height=upper_m,
            )
    for name, suffix, wind in [
        ("vr_ventilation_polar", "", "vr"),
        ("w_ventilation_polar", "", "wa"),
        ("vr1_ventilation_polar", "1", "vr"),
        ("w1_ventilation_polar", "1", "wa"),
        # 2026-11-03 风暴相对径向风版本
        ("vr_sr_ventilation_polar", "", "vr_sr"),
        ("vr_sr1_ventilation_polar", "1", "vr_sr"),
    ]:
        add(
            name,
            "kg K m-2 s-1",
            [
                f"density{suffix}_polar",
                f"theta_e{suffix}_asy_polar",
                f"{wind}{suffix}_polar",
                f"{wind}{suffix}_asy_polar",
            ],
        )
    resolution = float(
        options.get(
            "radius_resolution_km", options.get("polar_radius_resolution_km", 1.0)
        )
    )
    radius = float(options.get("asymmetry_radius_km", 200.0))
    for name, source, height in [
        ("precipitation_asymmetry", "RAINNCV_polar", None),
        ("mdbz_asymmetry", "mdbz_polar", None),
        (
            "dbz_asymmetry",
            "dbz1_polar",
            float(options.get("dbz_asymmetry_height_m", 3000.0)),
        ),
    ]:
        add(
            name,
            "1",
            [Dependency(source, height)],
            "scalar",
            radius_km=radius,
            resolution_km=resolution,
        )
    environment_domain = int(options.get("theta_e_environment_domain", 2))
    add(
        "theta_e_anomaly",
        "K",
        ["theta_e", Dependency("theta_e1_polar", domain=environment_domain)],
        environment_domain=environment_domain,
        ring_km=list(options.get("theta_e_environment_ring_km", [400.0, 500.0])),
        resolution_km=resolution,
    )
    # 普通位温异常与相当位温异常使用相同的跨域环境环定义；如有需要，
    # 可用 theta_environment_* 参数单独覆盖 domain 和环带范围。
    theta_environment_domain = int(
        options.get("theta_environment_domain", environment_domain)
    )
    add(
        "theta_anomaly",
        "K",
        ["theta", Dependency("theta1_polar", domain=theta_environment_domain)],
        environment_domain=theta_environment_domain,
        ring_km=list(
            options.get(
                "theta_environment_ring_km",
                options.get("theta_e_environment_ring_km", [400.0, 500.0]),
            )
        ),
        resolution_km=resolution,
    )
    # 气压层辅助变量是 getvar 场的选择方式，仍与其他字段共享源文件读取。
    for wind in ("ua", "va"):
        for label, pressure in [
            ("upper", float(options.get("shear_upper_hpa", 200.0))),
            ("lower", float(options.get("shear_lower_hpa", 850.0))),
        ]:
            registry.add(
                VariableSpec(
                    f"_{wind}_{label}",
                    "getvar",
                    "m s-1",
                    "surface",
                    wrf_name=wind,
                    options={"pressure_hpa": pressure},
                )
            )
    shear_source_domain = int(options.get("shear_source_domain", 2))
    shear_dependencies = [
        Dependency(name, domain=shear_source_domain)
        for name in ("_ua_upper", "_ua_lower", "_va_upper", "_va_lower", "slp_center")
    ]
    for component in ("u", "v", "magnitude", "direction"):
        add(
            f"shear_200_850_{component}",
            "degree" if component == "direction" else "m s-1",
            shear_dependencies,
            "scalar",
            shear_source_domain=shear_source_domain,
            shear_radius_inner_km=float(options.get("shear_radius_inner_km", 150.0)),
            shear_radius_outer_km=float(options.get("shear_radius_outer_km", 350.0)),
            algorithm_version="shear_source_km_v1",
        )
    # 2026-11-02 Claude（用户口径）：内核区风切变 shear_core_200_850_*。与环境切变同一算法（_shear.shear_values），
    # 但来源就是目标域本身（处理套餐用 d03）：以同一时次、同一域的 slp_center 为圆心，
    # 在 150–350 km 环带对 200 hPa 与 850 hPa 风求均值再相减。半径可用 core_shear_radius_*_km 覆盖。
    core_dependencies = [
        Dependency(name)
        for name in ("_ua_upper", "_ua_lower", "_va_upper", "_va_lower", "slp_center")
    ]
    for component in ("u", "v", "magnitude", "direction"):
        add(
            f"shear_core_200_850_{component}",
            "degree" if component == "direction" else "m s-1",
            core_dependencies,
            "scalar",
            shear_source="target_domain",
            shear_radius_inner_km=float(options.get("core_shear_radius_inner_km", 150.0)),
            shear_radius_outer_km=float(options.get("core_shear_radius_outer_km", 350.0)),
            algorithm_version="shear_core_v1",
        )
    # 经纬度也作为显式依赖，使移动嵌套网格不会误用其他时次坐标。
    for name, units in [
        ("XLAT", "degree_north"),
        ("XLONG", "degree_east"),
        ("XLAND", "1"),
    ]:
        registry.add(VariableSpec(name, "getvar", units, "surface", wrf_name=name))
    add("center_geo", "degree", ["slp_center", "XLAT", "XLONG"], "surface")
    add("center_slp_5km", "hPa", ["slp", "slp_center"], "scalar")
    add("land_boundary", "degree", ["XLAND", "XLAT", "XLONG"], "surface")
    reference_time = float(options.get("land_boundary_time", 0.0))
    add(
        "center_land_distance",
        "km",
        ["center_geo", Dependency("land_boundary", domain=1, time=reference_time)],
        "scalar",
    )
    motion_times = sorted(
        set(float(t) for t in (times or options.get("motion_times", [0.0])))
    )
    add(
        "storm_motion",
        "mixed",
        [Dependency("center_geo", time=t) for t in motion_times],
        "surface",
        times=motion_times,
    )
    if options.get("motion_correct", False):
        # 把原始风保留在私有依赖中，避免 ua -> ua 自依赖。
        for component, name in enumerate(("ua", "va")):
            source = f"_{name}_earth"
            original = registry.specs[name]
            registry.add(
                VariableSpec(
                    source,
                    "getvar",
                    original.units,
                    wrf_name=original.wrf_name,
                    options=original.options,
                )
            )
            registry.specs[name] = VariableSpec(
                name,
                "derived",
                original.units,
                "height",
                (Dependency(source), Dependency("storm_motion")),
                "wrf_processing_v3.variables.motion_corrected:process",
                options={"source": source, "component": component},
            )
        # ua/va 已减移速时，风暴相对变量改读地球相对风，避免减两次（11-03）。
        earth = {"ua": "_ua_earth", "va": "_va_earth"}
        for name in STORM_RELATIVE_ALL:
            spec = registry.specs[name]
            registry.specs[name] = replace(spec, dependencies=tuple(
                replace(dep, name=earth[dep.name]) if dep.name in earth else dep for dep in spec.dependencies
            ))
    # 2026-10-26 超梯度力诊断（算法见 variables/_agradient.py）：pgf_r、cen_r、cor_r、motf_r 及合力 agf_r。
    # 风暴移速用 center_geo 在 t ± agradient_motion_half_window_h 的中心差分（移动嵌套可用）；
    # 若本次配置已对 ua/va 做移动订正，改读私有的地球相对风，避免重复减移速。
    half_window = float(options.get("agradient_motion_half_window_h", 3.0))
    agradient = {
        "coriolis_f": float(options.get("coriolis_f", 4.9881e-05)),
        "motion_half_window_h": half_window,
        "algorithm_version": "agradient_v1",
    }
    winds = ("_ua_earth", "_va_earth") if options.get("motion_correct", False) else ("ua", "va")
    motion_dependencies = [
        Dependency("center_geo", None, time_offset=-half_window),
        Dependency("center_geo", None, time_offset=half_window),
    ]
    surface_center = Dependency("slp_center", None)
    add("pgf_r", "m s-2", ["pressure", "density", surface_center], algorithm_version="agradient_v1")
    for name in ("cen_r", "cor_r"):
        add(name, "m s-2", [winds[0], winds[1], surface_center, *motion_dependencies],
            wind_u=winds[0], wind_v=winds[1], **agradient)
    add("motf_r", "m s-2", ["slp", surface_center, *motion_dependencies], "surface", **agradient)
    add("agf_r", "m s-2", ["pgf_r", "cen_r", "cor_r", Dependency("motf_r", None)], **agradient)
    return registry
