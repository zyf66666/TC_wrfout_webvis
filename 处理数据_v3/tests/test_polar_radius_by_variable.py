"""极坐标半径按“变量 > domain > 全局”取值（2026-10-14：暖心 d03 用 100 km）。"""

from __future__ import annotations

from types import SimpleNamespace

from wrf_processing_v3.common import polar_radius_km


OPTIONS = {
    "radius_km": 300.0,
    "radius_km_by_domain": {2: 800.0, 3: 300.0},
    "radius_km_by_variable": {"theta_anomaly1_polar": {3: 100.0}, "special_polar": 50.0},
}


def test_variable_override_wins_for_its_domain_only():
    assert polar_radius_km(OPTIONS, "theta_anomaly1_polar", 3) == 100.0
    assert polar_radius_km(OPTIONS, "theta_anomaly1_polar", 2) == 800.0
    assert polar_radius_km(OPTIONS, "vr1_polar", 3) == 300.0


def test_scalar_variable_override_and_string_domain_keys():
    assert polar_radius_km(OPTIONS, "special_polar", 2) == 50.0
    options = {"radius_km_by_domain": {"3": 300.0}, "radius_km_by_variable": {"x_polar": {"3": 120.0}}}
    assert polar_radius_km(options, "x_polar", 3) == 120.0
    assert polar_radius_km(options, "y_polar", 3) == 300.0


def test_falls_back_to_global_radius():
    assert polar_radius_km({"radius_km": 250.0}, "vr_polar", 3) == 250.0
    assert polar_radius_km({"polar_max_radius_km": 90.0}, "vr_polar", 3) == 90.0


def test_layer_safety_audit_uses_same_radius():
    from wrf_processing_v3.layer_polar_safety import _requested_radius

    context = SimpleNamespace(options=OPTIONS, task=SimpleNamespace(domain=3, variable="theta_anomaly1_polar"))
    assert _requested_radius(context) == 100.0
    context = SimpleNamespace(options=OPTIONS, task=SimpleNamespace(domain=3, variable="vr1_polar"))
    assert _requested_radius(context) == 300.0


def test_project_config_warm_core_radius():
    import project_config

    assert polar_radius_km(project_config.OPTIONS, "theta_anomaly1_polar", 3) == 100.0
    assert polar_radius_km(project_config.OPTIONS, "theta_anomaly_polar", 3) == 100.0
    assert polar_radius_km(project_config.OPTIONS, "vr_polar", 3) == 300.0
    assert polar_radius_km(project_config.OPTIONS, "theta1_polar", 2) == 800.0
