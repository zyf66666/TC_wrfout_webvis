"""服务器配置：只需在 EXPERIMENTS 中增添试验名称和 run 目录。"""
from pathlib import Path

EXPERIMENTS = {
    "WRF_315_land_P1": "/home/qqli_group/qqli/data/lqq_zyf/expr/WRF_315_land_P1/run",
    # "WRF_315_land_P2": "/home/qqli_group/qqli/data/lqq_zyf/expr/WRF_315_land_P2/run",
}
OUTPUT_ROOT = Path("/home/qqli_group/qqli/data/lqq_zyf/expr/processed_v3")
DOMAIN = 3
INTERVAL_MINUTES = 60
HEIGHTS_M = tuple(range(100, 1001, 100)) + tuple(range(1250, 10001, 250))
VARIABLES = ("slp_center", "center_geo", "layer_center", "axis_mean_max_wind", "center_slp_5km", "dbz")

# 文件至少 120 秒未修改，再观察 2 秒，确认大小及修改时间不变。
MIN_FILE_AGE_SECONDS = 120
STABILITY_CHECK_SECONDS = 2
# 通常自动读取 START_DATE 并固定保存；仅截取了重启后的文件时可显式指定。
TIME_ORIGINS = {}  # {"WRF_315_land_P1": "2023-09-01_00:00:00"}

# 当前项目 project_config.OPTIONS 的快照；不包含服务器连接信息。
OPTIONS = {'verbose': True,
 'layer_polar_boundary_policy': 'all_safe',
 'cache_policy': 'exists',
 'center_options_by_domain': {2: {'search_radius_km': 30.0,
                                  'cross_radius_grid': 24,
                                  'pcc_radius_grid': 9,
                                  'boundary_width': 4,
                                  'max_iterations': 10},
                              3: {'algorithm': 'smooth_fg_float_pcc_v2',
                                  'first_guess_radius_km': 50.0,
                                  'pcc_radius_km': 80.0,
                                  'tolerance_grid': 0.05,
                                  'float_max_iterations': 50,
                                  'search_radius_km': 30.0,
                                  'cross_radius_grid': 121,
                                  'pcc_radius_grid': 44,
                                  'boundary_width': 20,
                                  'max_iterations': 10}},
 'radius_km': 300.0,
 'radius_resolution_km': 1.0,
 'radius_km_by_domain': {2: 800.0, 3: 300.0},
 'radius_km_by_variable': {'theta_anomaly1_polar': {3: 100.0},
                           'theta_anomaly_polar': {3: 100.0}},
 'theta_environment_domain': 2,
 'theta_environment_ring_km': [400.0, 500.0],
 'shear_source_domain': 2,
 'shear_radius_inner_km': 150.0,
 'shear_radius_outer_km': 350.0}
