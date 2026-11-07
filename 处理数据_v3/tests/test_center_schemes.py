"""中心方案（2026-11-02）：依赖中心的判断、按方案分目录存放、方案参数并入、算法登记表与旧调用逐位一致。"""

import json

import numpy as np
import pytest

from wrf_processing_v3.center_schemes import (
    apply_center_scheme, base_center_options, center_scheme, ensure_scheme_manifest, load_center_schemes,
    member_key, processed_path, split_member, validate_scheme_id,
)
from wrf_processing_v3.dependency_planner import DependencyPlanner
from wrf_processing_v3.register import build_registry
from wrf_processing_v3.request import Request

ORIGIN = "2023-09-01_00:00:00"


def test_center_dependence_follows_the_dependency_graph():
    registry = build_registry({})
    for name in ("slp_center", "layer_center", "vt1_polar", "vr_polar", "vt1_asy2w_polar", "dbz1_polar",
                 "RAINNCV_polar", "tilt_1_6", "tilt_magnitude", "rmw", "rmw_by_layer_center", "r_34",
                 "center_geo", "center_land_distance", "storm_motion", "theta_e_anomaly", "theta_anomaly",
                 "shear_200_850_u", "inflow_angle", "agf_r", "center"):
        assert registry.center_dependent(name), name
    for name in ("ua", "va", "wa", "theta", "theta_e", "dbz", "slp", "pressure", "RAINNC", "XLAT",
                 "land_boundary", "local_max_wind", "vort_rel", "classification", "pvo"):
        assert not registry.center_dependent(name), name


def test_base_scheme_is_the_project_default_and_ids_are_checked():
    import project_config

    assert base_center_options() == project_config.OPTIONS["center_options_by_domain"]
    schemes = load_center_schemes()
    assert list(schemes)[0] == "base" and {"pcc_vertical", "fpcc_v2"} <= set(schemes)
    for bad in ("Base", "x", "a-b", "../x", "1abc"):
        with pytest.raises(ValueError):
            validate_scheme_id(bad)
    with pytest.raises(KeyError):
        center_scheme("not_registered")


def test_paths_only_move_center_dependent_variables(tmp_path):
    dependent = build_registry({}).center_dependent
    assert processed_path(tmp_path, "exp", 3, "vt1_polar") == tmp_path / "exp/d03/vt1_polar.nc"
    assert processed_path(tmp_path, "exp", 3, "vt1_polar", center_scheme="fpcc_v2", center_dependent=dependent) == (
        tmp_path / "centers/fpcc_v2/exp/d03/vt1_polar.nc"
    )
    assert processed_path(tmp_path, "exp", 3, "ua", center_scheme="fpcc_v2", center_dependent=dependent) == (
        tmp_path / "exp/d03/ua.nc"
    )
    with pytest.raises(ValueError):
        processed_path(tmp_path, "exp", 3, "vt1_polar", center_scheme="fpcc_v2")
    assert split_member("WRF_315_land@fpcc_v2") == ("WRF_315_land", "fpcc_v2")
    assert split_member("WRF_315_land") == ("WRF_315_land", "base")
    assert member_key("WRF_315_land", "base") == "WRF_315_land"
    assert member_key("WRF_315_land", "fpcc_v2") == "WRF_315_land@fpcc_v2"


def test_apply_scheme_merges_center_options_and_rejects_conflicts():
    merged = apply_center_scheme({"radius_km": 300}, "fpcc_v2")
    assert merged["center_scheme"] == "fpcc_v2"
    assert merged["center_options_by_domain"][3]["algorithm"] == "smooth_fg_float_pcc_v2"
    assert apply_center_scheme(merged, "fpcc_v2") == merged  # 重复调用结果不变
    base = apply_center_scheme({"center_options_by_domain": base_center_options()}, "base")
    assert "center_scheme" not in base
    with pytest.raises(ValueError, match="不一致"):
        apply_center_scheme({"center_options_by_domain": {3: {"search_radius_km": 30}}}, "base")
    with pytest.raises(ValueError, match="不一致"):
        apply_center_scheme({"center_scheme": "pcc_vertical"}, "fpcc_v2")
    with pytest.raises(ValueError):
        apply_center_scheme({"center_search_radius_km": 30}, "fpcc_v2")


def test_scheme_manifest_is_written_once_and_cannot_change(tmp_path):
    path = ensure_scheme_manifest(tmp_path, "fpcc_v2")
    assert path == tmp_path / "centers/fpcc_v2/scheme.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["id"] == "fpcc_v2" and stored["center_options_by_domain"]["3"]["algorithm"] == "smooth_fg_float_pcc_v2"
    assert ensure_scheme_manifest(tmp_path, "fpcc_v2") == path
    stored["center_options_by_domain"]["3"]["search_radius_km"] = 99
    path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(ValueError, match="不能改参数"):
        ensure_scheme_manifest(tmp_path, "fpcc_v2")
    assert ensure_scheme_manifest(tmp_path, "base") is None


def test_planner_puts_only_center_dependent_tasks_under_the_scheme(tmp_path):
    request = Request(
        experiments=("exp",), domains=(3,), times_minutes=(0,), heights_m=(1000,),
        variables=("vt1_polar", "ua"), source_root=tmp_path / "src", output_root=tmp_path / "out",
        options={"time_origin": ORIGIN}, center_scheme="pcc_vertical",
    )
    registry = build_registry(apply_center_scheme(request.options, "pcc_vertical"))
    plan = DependencyPlanner(registry).plan(request)
    from wrf_processing_v3.storage import NetCDFStorage

    storage = NetCDFStorage(request.output_root, time_origin=ORIGIN, center_scheme="pcc_vertical",
                            center_dependent=registry.center_dependent)
    paths = {item.task.variable: storage.path_for(item.task) for item in plan}
    root = tmp_path / "out"
    assert paths["vt1_polar"] == root / "centers/pcc_vertical/exp/d03/vt1_polar.nc"
    assert paths["layer_center"] == root / "centers/pcc_vertical/exp/d03/layer_center.nc"
    assert paths["ua"] == root / "exp/d03/ua.nc"
    assert paths["pressure"] == root / "exp/d03/pressure.nc"
    # 逐层连续方案：规划器按方案的 layer_mode 补全连续中心链（含地面中心）。
    assert "slp_center" in paths
    assert any(item.spec.options.get("algorithm_version") == "bezier_pcc_vertical_v1"
               for item in plan if item.task.variable == "layer_center")


def _vortex(cy, cx, depth=20.0, width=40.0):
    yy, xx = np.mgrid[:401, :401]
    return 1000.0 - depth * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * width ** 2))


def test_algorithm_registry_matches_direct_calls():
    from types import SimpleNamespace

    from wrf_processing_v3._center_algorithm import find_pressure_center, find_pressure_center_v2
    from wrf_processing_v3.variables import slp_center
    from wrf_processing_v3.variables._center_options import slp_center_algorithm

    field = _vortex(190.3, 215.7)
    for scheme in ("base", "fpcc_v2"):
        options = {**apply_center_scheme({}, scheme), "domain_config": {3: {"dx_km": 1, "dy_km": 1}}}
        saved = {}
        ctx = SimpleNamespace(
            task=SimpleNamespace(domain=3, variable="slp_center"), options=options,
            read=lambda name, **kwargs: field,
            save=lambda name, values, metadata: saved.update(values=values, metadata=metadata),
        )
        slp_center.process(ctx)
        name, params = slp_center_algorithm(options, 3)
        if name == "bezier_pcc_v1":
            y, x, _ = find_pressure_center(field, dx_km=1, dy_km=1, **params)
        else:
            y, x, _, extra = find_pressure_center_v2(field, dx_km=1, dy_km=1, **params)
            for key, value in extra.items():
                assert saved["metadata"][key] == value
        np.testing.assert_array_equal(saved["values"], [round(y), round(x)])
        assert saved["metadata"]["algorithm"] == name


def test_pipeline_writes_scheme_centers_and_reuses_shared_pressure(tmp_path):
    from wrf_processing_v3._center_algorithm import find_pressure_center_v2
    from wrf_processing_v3.processing_manager import ProcessingManager
    from wrf_processing_v3.request import Task
    from wrf_processing_v3.storage import NetCDFStorage

    root = tmp_path / "out"
    options = {"time_origin": ORIGIN}
    registry = build_registry(apply_center_scheme(options, "fpcc_v2"))
    shared = NetCDFStorage(root, time_origin=ORIGIN)
    slp = _vortex(200.4, 180.6)
    shared.save(Task("exp", 3, 0, "slp", None), slp, registry.resolve("slp"),
                metadata={"grid_spacing": {"dx_km": 1.0, "dy_km": 1.0}})
    pressure = _vortex(210.0, 190.0, depth=15.0)
    shared.save(Task("exp", 3, 0, "pressure", 2000), pressure, registry.resolve("pressure"),
                metadata={"grid_spacing": {"dx_km": 1.0, "dy_km": 1.0}})

    class CachedOnlyReader:
        def prepare(self, tasks, *args, **kwargs):
            raise AssertionError(f"共用的 slp/pressure 已有，不应读取 wrfout: {tasks}")

    request = Request(
        experiments=("exp",), domains=(3,), times_minutes=(0,), heights_m=(2000,),
        variables=("slp_center", "layer_center"), source_root=tmp_path / "src", output_root=root,
        options=options, center_scheme="fpcc_v2",
    )
    result = ProcessingManager(registry=registry, reader=CachedOnlyReader()).process(request)
    assert len(result["computed"]) == 2
    scheme_storage = NetCDFStorage(root, time_origin=ORIGIN, center_scheme="fpcc_v2",
                                   center_dependent=registry.center_dependent)
    center_task = Task("exp", 3, 0, "slp_center", None)
    assert scheme_storage.path_for(center_task) == root / "centers/fpcc_v2/exp/d03/slp_center.nc"
    assert not (root / "exp/d03/slp_center.nc").exists()
    assert (root / "centers/fpcc_v2/scheme.json").is_file()
    from wrf_processing_v3.variables._center_options import slp_center_algorithm

    _, params = slp_center_algorithm(apply_center_scheme({}, "fpcc_v2"), 3)
    y, x, _, _ = find_pressure_center_v2(slp.astype(np.float32), dx_km=1.0, dy_km=1.0, **params)
    np.testing.assert_array_equal(scheme_storage.load(center_task), [round(y), round(x)])
    metadata = scheme_storage.load_metadata(center_task)
    assert metadata["algorithm"] == "smooth_fg_float_pcc_v2"
    assert metadata["request_options"]["center_scheme"] == "fpcc_v2"
    layer = scheme_storage.load_metadata(Task("exp", 3, 0, "layer_center", 2000))
    assert layer["algorithm"] == "bezier_pcc_v1" and layer["layer_center_mode"] == "independent"
    # 同一请求再跑一次全部复用；base 目录里没有中心文件，base 请求需要重新计算。
    assert ProcessingManager(registry=registry, reader=CachedOnlyReader()).process(request)["computed"] == []
    base_request = Request(
        experiments=("exp",), domains=(3,), times_minutes=(0,), heights_m=(2000,),
        variables=("slp_center",), source_root=tmp_path / "src", output_root=root,
        options=options, center_scheme="base", dry_run=True,
    )
    base_registry = build_registry(apply_center_scheme(options, "base"))
    planned = ProcessingManager(registry=base_registry, reader=CachedOnlyReader()).process(base_request)
    assert str(root / "exp/d03/slp_center.nc") in planned["planned"]
