"""Shared v3 experiment paths and default processing parameters."""

from pathlib import Path


EXPERIMENTS = ("WRF_315_land", "WRF_315_sea",
               "WRF_0_land", "WRF_0_sea", "WRF_45_land", "WRF_45_sea", "WRF_90_land", "WRF_90_sea",
    "WRF_135_land", "WRF_135_sea", "WRF_180_land", "WRF_180_sea", "WRF_225_land", "WRF_225_sea",
    "WRF_270_land", "WRF_270_sea", )
EXPERIMENTS = ("WRF_315_land", "WRF_315_sea",)
DOMAINS = (3,)

TIMES_MINUTES = tuple(range(0, 120 * 60 + 1, 60))

EXPERIMENT_END_HOURS = {
    "WRF_0_land": 120,
    "WRF_0_sea": 120,
    "WRF_45_land": 120,
    "WRF_45_sea": 120,
    "WRF_90_land": 84,
    "WRF_90_sea": 120,
    "WRF_135_land": 48,
    "WRF_135_sea": 69,
    "WRF_180_land": 39,
    "WRF_180_sea": 59,
    "WRF_225_land": 42,
    "WRF_225_sea": 69,
    "WRF_270_land": 66,
    "WRF_270_sea": 84,
    "WRF_315_land": 102,
    "WRF_315_sea": 102,
}

TIMES_MINUTES_BY_EXPERIMENT = {
    experiment: tuple(range(0, end_hour * 60 + 1, 60))
    for experiment, end_hour in EXPERIMENT_END_HOURS.items()
}

# 2026-10-16：暖心（位温异常、相当位温异常）算到 15000 m，其余变量算到 10000 m。
# 同时选了两类变量时，process_data.build_requests 会按高度拆成两组请求。
HEIGHTS_M = (
    tuple(range(100, 1001, 100))
    + tuple(range(1250, 10001, 250))
)
WARM_CORE_VARIABLES = (
    "theta_anomaly", "theta_anomaly_polar", "theta_anomaly1_polar",
    "theta_e_anomaly", "theta_e_anomaly_polar", "theta_e_anomaly1_polar",
)
WARM_CORE_HEIGHTS_M = HEIGHTS_M + tuple(range(10250, 15001, 250))

VARIABLES = ("HFX", "LH", "PBLH", "QVAPOR", "RAINNC", "RAINNCV",
    "U10", "UST", "V10", "cape", "dbz", "diabatic_heating",
    "mdbz", "pressure", "pvo", "rh", "slp", "temp",
    "theta_e", "ua", "va", "wa",)

VARIABLES = [
    "HFX_polar", "LH_polar", "QVAPOR_polar", "RAINNCV_polar",
    "bl_mfc_polar", "convergence_polar", "dbz1_polar", "dbz_polar",
    "density1_polar", "density_polar", "diabatic_heating1_polar",
    "inflow_angle_polar", "mdbz_polar", "pvo1_polar", "pvo_polar",
    "qflux_r_polar", "rh1_polar", "rh_polar",
    "theta_e1_asy_polar", "theta_e1_polar", 
    "theta_e_asy_polar", "theta_e_polar",
    "ua_polar", "va_polar",
    "vr1_asy_polar", "vr1_polar", "vr1_ventilation_polar",
    "vr_asy_polar", "vr_polar", "vr_ventilation_polar", "vt_polar",
    "w1_ventilation_polar", "w_ventilation_polar",
    "wa1_asy_polar", "wa1_polar", "wa_asy_polar", "wa_polar",
    "wind_speed_10_polar",
]



VARIABLES = [
    "axis_mean_max_wind", "bl_mfc", "center_land_distance",
    "center_slp_5km", "classification", "convergence", "dbz_asymmetry",
    "density", "inflow_angle", "land_distance", "local_max_wind",
    "mass_convergence", "mdbz_asymmetry", "mfc", "motion",
    "precipitation_asymmetry", "qflux_r", "r_34", "rmw",
    "shear_200_850_direction", "shear_200_850_magnitude",
    "shear_200_850_u", "shear_200_850_v", "theta_e_anomaly",
    "tilt_angle", "tilt_magnitude", "tilt_x", "tilt_y",
    "vr", "vr1", "vt", "wind_speed_10",
]


VARIABLES = [
    "axis_mean_max_wind", "center_land_distance",
    "center_slp_5km", "classification", "dbz_asymmetry",
    "density", "local_max_wind",
    "mass_convergence", 
    "r_34", "rmw",
    "tilt_angle", "tilt_magnitude", "tilt_x", "tilt_y",
    "vr", "vr1", "vt", "wind_speed_10",
]

VARIABLES = ["tilt_1_6","tilt_x_1_6","tilt_y_1_6","tilt_angle_1_6"]
VARIABLES = ["vt1","vt1_polar"]

SOURCE_ROOT = Path("/Volumes/sea_2")

SOURCE_DIRECTORIES = {
    # land_2 中保存的陆地试验。
    "WRF_0_land": "/Volumes/land_2/WRF_0_land/run",
    "WRF_45_land": "/Volumes/land_2/WRF_45_land/run",
    "WRF_90_land": "/Volumes/land_2/WRF_90_land/run",
    "WRF_135_land": "/Volumes/land_2/WRF_135_land/run",
    "WRF_180_land": "/Volumes/land_2/WRF_180_land/run",
    "WRF_225_land": "/Volumes/land_2/WRF_225_land/run",
    "WRF_270_land": "/Volumes/sea_1/WRF_270_land/run",
    "WRF_315_land": "/Volumes/sea_2/WRF_315_land/run",
    # sea_1 中保存的海洋试验。
    "WRF_0_sea": "/Volumes/sea_1/WRF_0_sea/run",
    "WRF_45_sea": "/Volumes/sea_1/WRF_45_sea/run",
    "WRF_90_sea": "/Volumes/sea_1/WRF_90_sea/run",
    "WRF_135_sea": "/Volumes/sea_1/WRF_135_sea/run",
    # sea_2 中保存的海洋试验。
    "WRF_180_sea": "/Volumes/sea_2/WRF_180_sea/run",
    "WRF_225_sea": "/Volumes/sea_2/WRF_225_sea/run",
    "WRF_270_sea": "/Volumes/sea_2/WRF_270_sea/run",
    "WRF_315_sea": "/Volumes/sea_2/WRF_315_sea/run",
}


OUTPUT_ROOT = Path("/Volumes/land_1/processed_v3")

# 2026-11-02（用户决定）：网页上由用户亲自选择处理的数据写到独立根目录 post，目录结构与 processed_v3 相同
# （<试验>/dXX/<变量>.nc、profiles/、centers/<方案>/）。processed_v3 里有很多是以前让 AI 算的，post 不与它互相复用。
# 网页“数据管理”的“数据集”下拉框在这两个根目录间选择（网页表单默认 post）；
# 接口不带 dataset 时仍写 processed_v3（wrf-lab 的 AI 处理、命令行默认都不写 post）。
POST_ROOT = Path("/Volumes/land_1/post")
DATASETS = {"post": POST_ROOT, "v3": OUTPUT_ROOT}

# 2026-11-02：网页同时运行的处理任务数（每个任务一个子进程、约一个 CPU 核、0.5–1.5 GB 内存）。
# 超过这个数的任务排队。外接机械硬盘读不过来时可调小；改后重启网页生效。
# 2026-11-03：用户要求改为 2（分析套餐全是三维场，3 个并行时机械硬盘和 16 GB 内存偏紧）。
# 2026-11-04：用户要求改为 1（串行；并行时机械硬盘寻道声大，用户希望减轻硬盘负担）。
WEB_PROCESSING_WORKERS = 1

# 2026-11-02：命令行处理默认的中心方案（见 center_schemes.json）。None 表示沿用旧行为（不按方案分目录）；
# "base" 与 None 写在同一位置，但会检查中心参数与 base 方案一致；其他方案写到 OUTPUT_ROOT/centers/<方案>/。
CENTER_SCHEME = "base"


def _base_center_options():
    import json

    raw = json.loads(Path(__file__).with_name("center_schemes.json").read_text(encoding="utf-8"))
    return {int(domain): dict(entry) for domain, entry in raw["base"]["center_options_by_domain"].items()}


OPTIONS = {
    "verbose": True,
    "layer_polar_boundary_policy": "all_safe",
    "cache_policy": "exists",
    # 2026-11-02：中心参数统一由中心方案决定，base 方案的参数写在 center_schemes.json（只此一处）。
    # 原值（2026-10-27 用户指定恢复已复现的 WRF 原盘独立 Bezier/PCC）：d02 30 km/24/9/4/10；
    # d03 bezier_pcc_v1、independent、60 km、121、44、20、10。换中心请新增方案，不要改这里。
    "center_options_by_domain": _base_center_options(),
    "radius_km": 300.0,
    "radius_resolution_km": 1.0,
    "radius_km_by_domain": {2: 800.0, 3: 300.0},
    # 2026-10-14：暖心（位温异常）极坐标在 d03 只取 0–100 km；其余极坐标变量仍按上面的 300 km。
    # 取值规则见 wrf_processing_v3/common.polar_radius_km（变量 > domain > 全局）。
    "radius_km_by_variable": {
        "theta_anomaly1_polar": {3: 100.0},
        "theta_anomaly_polar": {3: 100.0},
    },
    "theta_environment_domain": 2,
    "theta_environment_ring_km": [400.0, 500.0],
    "shear_source_domain": 2,
    "shear_radius_inner_km": 150.0,
    "shear_radius_outer_km": 350.0,
    # 2026-10-26 Claude（用户决定）：非对称度（mdbz/precipitation/dbz_asymmetry）圆盘半径 0–100 km，
    # 与旧 v2（ASYMMETRY_RADIUS_KM = 100）一致；不设时 _scientific_registry 默认 200 km。
    "asymmetry_radius_km": 100.0,
    # 2026-11-04：dbz_asymmetry 取哪一层的 dbz1_polar（m）。以上两项网页“通用处理选项”可改；
    # 与这里的值不同时结果自动放进独立参数配置目录（profiles/），见 cache_compatibility.OPTIONAL_SCIENCE_KEYS。
    "dbz_asymmetry_height_m": 3000.0,
    # 2026-10-26 超梯度力诊断（pgf_r、cen_r、cor_r、motf_r、agf_r）：f 平面常数取自各试验 d03 wrfout 的 F
    # （16 个试验均为 4.9881e-5 s-1，CEN_LAT 20°）；风暴移速用 center_geo 在 t ± 3 h 的中心差分。
    "coriolis_f": 4.9881e-05,
    "agradient_motion_half_window_h": 3.0,
    # 2026-11-02 内核区风切变 shear_core_200_850_*：目标域（套餐用 d03）slp_center 的 150–350 km 环带、200–850 hPa。
    # 不在此写 core_shear_radius_inner_km / core_shear_radius_outer_km 时即用 150 / 350 km（见 _scientific_registry.py）。
}
