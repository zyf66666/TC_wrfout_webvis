"""处理套餐（2026-11-02）：processing_presets.json 的读取、校验与高度展开。"""

from __future__ import annotations

import pytest

import project_config
from wrf_processing_v3.processing_presets import (
    PRESET_FILE,
    load_processing_presets,
    parse_processing_presets,
    preset_minutes,
)
from wrf_processing_v3.register import build_registry


TOKENS = {"standard": project_config.HEIGHTS_M, "warm_core": project_config.WARM_CORE_HEIGHTS_M}


def _registry():
    return build_registry(project_config.OPTIONS, times_minutes=(0,))


def test_shipped_presets_load_and_every_variable_is_registered():
    presets = load_processing_presets(height_tokens=TOKENS, registry=_registry())
    basic = presets["basic_tc"]
    assert basic["dataset"] == "post"
    groups = {group["id"]: group for group in basic["groups"]}
    center = groups["center_intensity_5min"]
    assert center["time_step_minutes"] == 5 and center["heights_m"] == []
    assert {"slp_center", "axis_mean_max_wind", "rmw", "local_max_wind", "center_land_distance"} <= set(center["variables"])
    assert groups["layer_center_1h"]["heights_m"] == list(project_config.HEIGHTS_M)
    assert len(groups["layer_center_1h"]["heights_m"]) == 46
    assert "pressure" in groups["layer_center_1h"]["variables"]   # 11-04 用户要求加三维气压
    # 用户 11-02 要求：反射率组含笛卡尔坐标 dbz
    assert {"dbz", "mdbz", "dbz1_polar", "mdbz_polar"} <= set(groups["reflectivity_1h"]["variables"])
    assert set(groups["shear_1h"]["variables"]) == {
        f"shear_core_200_850_{part}" for part in ("u", "v", "magnitude", "direction")
    }
    assert all(not group["enabled"] for group in basic["groups"] if group["id"].startswith("extra_"))
    # 用户 11-04 要求：降水放进基本量（默认勾选），回波/降水非对称仍在分析量
    precipitation = groups["precipitation_1h"]
    assert precipitation["enabled"] and precipitation["time_step_minutes"] == 60 and precipitation["heights_m"] == []
    assert set(precipitation["variables"]) == {"RAINNC", "RAINNCV", "RAINNCV_polar"}
    analysis_variables = {name for group in presets["analysis_tc"]["groups"] for name in group["variables"]}
    assert {"mdbz_asymmetry", "precipitation_asymmetry"} <= analysis_variables
    assert not {"RAINNC", "RAINNCV", "RAINNCV_polar"} & analysis_variables
    assert PRESET_FILE.name == "processing_presets.json"


def test_height_variable_without_heights_is_rejected():
    raw = {"p_one": {"groups": [{"id": "g_one", "time_step_minutes": 60, "heights": None, "variables": ["dbz"]}]}}
    with pytest.raises(ValueError, match="随高度变化"):
        parse_processing_presets(raw, height_tokens=TOKENS, registry=_registry())


@pytest.mark.parametrize(
    "group",
    [
        {"id": "g_one", "time_step_minutes": 0, "heights": None, "variables": ["rmw"]},
        {"id": "g_one", "time_step_minutes": 5, "heights": "nope", "variables": ["rmw"]},
        {"id": "g_one", "time_step_minutes": 5, "heights": None, "variables": []},
        {"id": "g_one", "time_step_minutes": 5, "heights": None, "variables": ["no_such_variable"]},
    ],
)
def test_invalid_groups_are_rejected(group):
    with pytest.raises(ValueError):
        parse_processing_presets({"p_one": {"groups": [group]}}, height_tokens=TOKENS, registry=_registry())


def test_duplicate_group_ids_rejected_and_explicit_heights_sorted():
    group = {"id": "g_one", "time_step_minutes": 60, "heights": [3000, 1000, 1000], "variables": ["layer_center"]}
    parsed = parse_processing_presets({"p_one": {"groups": [group]}}, height_tokens=TOKENS, registry=_registry())
    assert parsed["p_one"]["groups"][0]["heights_m"] == [1000, 3000]
    with pytest.raises(ValueError, match="重复"):
        parse_processing_presets({"p_one": {"groups": [group, group]}}, height_tokens=TOKENS)


def test_preset_minutes_include_both_ends():
    assert len(preset_minutes(0, 120 * 60, 5)) == 1441
    assert preset_minutes(60, 180, 60) == [60, 120, 180]
    with pytest.raises(ValueError):
        preset_minutes(120, 60, 5)


def test_analysis_preset_runs_groups_separately_and_covers_the_requested_fields():
    presets = load_processing_presets(height_tokens=TOKENS, registry=_registry())
    analysis = presets["analysis_tc"]
    assert analysis["merge_groups"] is False and presets["basic_tc"]["merge_groups"] is True
    variables = {name for group in analysis["groups"] if group["enabled"] for name in group["variables"]}
    assert {"tilt_2_4", "tilt_2_6", "tilt_2_10", "inflow_angle_sr_polar", "inflow_angle_sr1_polar"} <= variables
    # 11-03 用户要求：分析套餐里与风有关的量全部用风暴相对风（_sr）
    assert {"ua_sr", "va_sr", "qflux_r_sr", "qflux_r_sr_polar", "qflux_r_sr1_polar",
            "rmw_by_height_sr", "rmw_by_layer_center_sr"} <= variables
    assert not {"ua", "va", "inflow_angle", "inflow_angle_polar", "vr", "vt", "vr1", "vt1", "qflux_r",
                "rmw_by_height", "rmw_by_layer_center"} & variables
    # 每个三维物理量：笛卡尔 + 海平面中心极坐标 + 各层中心极坐标；风用风暴相对风
    for name in ("temp", "rh", "QVAPOR", "theta_e", "wa", "pvo", "diabatic_heating", "vr_sr", "vt_sr", "qflux_r_sr"):
        assert {name, f"{name}_polar"} <= variables
    for name in ("temp1_polar", "rh1_polar", "QVAPOR1_polar", "theta_e1_polar", "wa1_polar", "pvo1_polar", "diabatic_heating1_polar",
                 "vr_sr1", "vt_sr1", "vr_sr1_polar", "vt_sr1_polar"):
        assert name in variables
    boundary = next(group for group in analysis["groups"] if group["id"] == "boundary_layer_1h")
    assert boundary["heights_m"] == list(range(100, 1001, 100))
    warm = next(group for group in analysis["groups"] if group["id"] == "extra_warm_core_1h")
    assert not warm["enabled"] and warm["heights_m"][-1] == 15000
    # 11-06 用户要求：分析量默认含云分类（二维类别图，依赖 dbz、wa 的 9 个固定高度）
    cloud = next(group for group in analysis["groups"] if group["id"] == "cloud_classification_1h")
    assert cloud["enabled"] and cloud["variables"] == ["classification"] and cloud["heights_m"] == []
    ventilation = next(group for group in analysis["groups"] if group["id"] == "extra_ventilation_1h")
    assert {"vr_sr_ventilation_polar", "vr_sr1_ventilation_polar"} <= set(ventilation["variables"])
