"""2026-10-16 用户要求：暖心（位温异常）算到 15000 m，其余变量算到 10000 m。"""
from wrf_webvis.app.core.registry import (
    DEFAULT_HEIGHT_LEVELS_M,
    DEFAULT_PROCESSING_HEIGHTS_M,
    WARM_CORE_VARIABLES,
    default_processing_heights,
)


def test_standard_height_table_still_reaches_15km_for_reading_data():
    assert DEFAULT_HEIGHT_LEVELS_M[-1] == 15000


def test_default_processing_heights_depend_on_variables():
    assert DEFAULT_PROCESSING_HEIGHTS_M[0] == 100 and DEFAULT_PROCESSING_HEIGHTS_M[-1] == 10000
    assert default_processing_heights(["diabatic_heating"]) == DEFAULT_PROCESSING_HEIGHTS_M
    assert default_processing_heights([]) == DEFAULT_PROCESSING_HEIGHTS_M
    for name in WARM_CORE_VARIABLES:
        assert default_processing_heights([name])[-1] == 15000
    # 混选时按 10000 m（v1 不拆分；v3 命令行会拆成两组）
    assert default_processing_heights(["theta_anomaly_polar", "vt"])[-1] == 10000


def test_warm_core_list_matches_v3_project_config():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "处理数据_v3" / "project_config.py"
    spec = importlib.util.spec_from_file_location("v3_project_config_for_test", path)
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)
    assert set(config.WARM_CORE_VARIABLES) == set(WARM_CORE_VARIABLES)
    assert {"theta_e_anomaly", "theta_e_anomaly_polar", "theta_e_anomaly1_polar"} <= WARM_CORE_VARIABLES
