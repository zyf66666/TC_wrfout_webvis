from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from netCDF4 import Dataset


def _source(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with Dataset(path, "w") as dataset:
        dataset.createDimension("Time", 2)
        dataset.createDimension("DateStrLen", 19)
        dataset.createDimension("south_north", 2)
        dataset.createDimension("west_east", 2)
        times = dataset.createVariable("Times", "S1", ("Time", "DateStrLen"))
        times[:] = np.asarray(
            [list("2023-09-01_00:00:00"), list("2023-09-01_00:05:00")], dtype="S1"
        )
        dataset.START_DATE = "2023-09-01_00:00:00"
        dataset.DX = 1000.0
        dataset.DY = 1000.0
        for name, value in (("U10", 3.0), ("V10", 4.0)):
            field = dataset.createVariable(name, "f4", ("Time", "south_north", "west_east"))
            field[:] = value


def _service(tmp_path):
    from wrf_webvis.app.services.v3_management import V3ManagementService

    source = tmp_path / "source" / "exp" / "run"
    _source(source / "wrfout_d03_sample")
    return V3ManagementService(
        output_root=tmp_path / "processed_v3",
        source_directories={"exp": source},
        default_options={"radius_km_by_domain": {3: 300.0}, "radius_resolution_km": 1.0},
    )


def test_inventory_lists_default_and_valid_profile_files_without_opening_netcdf(tmp_path, monkeypatch):
    from wrf_webvis.app.services import v3_management

    service = _service(tmp_path)
    default_dir = service.output_root / "exp" / "d03"
    default_dir.mkdir(parents=True)
    real = default_dir / "historical.nc"
    real.write_bytes(b"historical")
    (default_dir / "._historical.nc").write_bytes(b"appledouble")
    (default_dir / "historical.nc.lock").write_bytes(b"")
    (default_dir / "interrupted.nc.tmp").write_bytes(b"")
    profile = service.profile_for({"radius_resolution_km": 2.0})
    service.ensure_profile(profile)
    profile_dir = profile["root"] / "old_exp" / "d02"
    profile_dir.mkdir(parents=True)
    (profile_dir / "legacy.nc").write_bytes(b"profile")
    invalid_dir = service.output_root / "profiles" / "p-invalid" / "exp" / "d03"
    invalid_dir.mkdir(parents=True)
    (invalid_dir / "ignored.nc").write_bytes(b"invalid")
    mismatched_root = service.output_root / "profiles" / ("p-" + "0" * 16)
    mismatched_root.mkdir(parents=True)
    (mismatched_root / "profile.json").write_text('{"id":"p-0000000000000000","options":{}}')
    mismatched_dir = mismatched_root / "exp" / "d03"
    mismatched_dir.mkdir(parents=True)
    (mismatched_dir / "wrong_profile.nc").write_bytes(b"invalid")

    monkeypatch.setattr(v3_management, "Dataset", lambda *_args, **_kwargs: pytest.fail("inventory opened NetCDF"))
    rows = service.inventory()["items"]
    assert {(row["profile_id"], row["experiment"], row["domain"], row["variable"]) for row in rows} == {
        ("default", "exp", 3, "historical"), (profile["id"], "old_exp", 2, "legacy"),
    }
    assert rows[0]["size_bytes"] == real.stat().st_size
    assert rows[0]["modified_at"] and rows[0]["path"] == str(real)


def test_coverage_cache_reloads_completed_flags_when_file_changes(tmp_path, monkeypatch):
    import os
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage
    from wrf_webvis.app.services import v3_management

    service = _service(tmp_path)
    storage = NetCDFStorage(service.output_root, time_origin="2023-09-01_00:00:00")
    path = storage.save(
        Task("exp", 3, 0, "theta", 100), np.ones((2, 2)),
        SimpleNamespace(storage_layout="cartesian_3d", units="K", options={"algorithm_version": "v2.1"}),
    )
    with Dataset(path, "r+") as dataset:
        dataset["completed"][0, 0] = 0
    original = v3_management.Dataset
    opens = []

    def tracked(*args, **kwargs):
        opens.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(v3_management, "Dataset", tracked)
    assert service.coverage("default", "exp", 3, "theta")["completed"] == []
    assert service.coverage("default", "exp", 3, "theta")["completed"] == []
    assert len(opens) == 1
    with Dataset(path, "r+") as dataset:
        dataset["completed"][0, 0] = 1
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert service.coverage("default", "exp", 3, "theta")["completed"] == [{"time_minutes": 0, "height_m": 100}]
    assert len(opens) == 2


def test_historical_file_coverage_does_not_require_registered_source_or_variable(tmp_path):
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    service = _service(tmp_path)
    path = NetCDFStorage(service.output_root, time_origin="2023-09-01_00:00:00").save(
        Task("old_exp", 3, 0, "historical", None), np.ones((2, 2)),
        SimpleNamespace(storage_layout="cartesian_2d", units="K", options={"algorithm_version": "v2.1"}),
        metadata={"source_file": "old-wrfout"},
    )
    assert path.is_file()
    coverage = service.coverage("default", "old_exp", 3, "historical")
    assert coverage["completed"] == [{"time_minutes": 0, "height_m": None}]
    assert service.slice_metadata("default", "old_exp", 3, "historical", 0, None)["source_file"] == "old-wrfout"
    with pytest.raises(ValueError):
        service.coverage("default", "../old_exp", 3, "historical")


def test_plan_reports_source_minutes_dependencies_and_reuse(tmp_path):
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    service = _service(tmp_path)
    payload = {
        "experiment": "exp", "domain": 3, "variables": ["wind_speed_10"],
        "times_minutes": [0, 5], "heights_m": [], "profile_id": "default",
    }
    first = service.plan(payload)
    assert first["missing_source_minutes"] == []
    assert first["source_minutes"] == [0, 5]
    assert first["counts"]["compute"] == 6
    assert {item["variable"] for item in first["tasks"]} == {"U10", "V10", "wind_speed_10"}

    storage = NetCDFStorage(tmp_path / "processed_v3", time_origin="2023-09-01_00:00:00")
    storage.save(
        Task("exp", 3, 0, "wind_speed_10", None), np.ones((2, 2)),
        SimpleNamespace(storage_layout="cartesian_2d", units="m s-1", options={"algorithm_version": "v2.1"}),
        metadata={"request_options": service.options_for("default")},
    )
    second = service.plan(payload)
    assert second["counts"]["reuse"] >= 1
    assert second["plan_hash"] != first["plan_hash"]


def test_plan_hash_changes_when_source_file_changes_without_changing_minutes(tmp_path):
    import os

    service = _service(tmp_path)
    payload = {
        "experiment": "exp", "domain": 3, "variables": ["U10"],
        "times_minutes": [0], "heights_m": [], "profile_id": "default",
    }
    first = service.plan(payload)
    source = service.source_directories["exp"] / "wrfout_d03_sample"
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    second = service.plan(payload)
    assert second["plan_hash"] != first["plan_hash"]


@pytest.mark.parametrize('cached_target', [False, True])
def test_cached_plan_and_run_skip_source_scan(tmp_path, monkeypatch, cached_target):
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.register import build_registry
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage
    from 处理数据_v3.wrf_processing_v3.source_index import SourceIndex

    service = _service(tmp_path)
    options = service.options_for('default')
    registry = build_registry(options, times_minutes=(0,))
    storage = NetCDFStorage(service.output_root, time_origin='2023-09-01_00:00:00')
    for variable, value in ([('wind_speed_10', 5)] if cached_target else [('U10', 3), ('V10', 4)]):
        storage.save(Task('exp', 3, 0, variable, None), np.full((2, 2), value),
                     registry.resolve(variable), metadata={'request_options': options})
    def forbidden(*args):
        pytest.fail('缓存预览/执行不应枚举 wrfout')
    monkeypatch.setattr(SourceIndex, '_paths', forbidden)
    payload = {'experiment': 'exp', 'domain': 3, 'variables': ['wind_speed_10'],
               'times_minutes': [0], 'heights_m': []}
    plan = service.plan(payload)
    assert plan['source_index_skipped'] is True
    assert plan['source_minutes'] == []  # 未扫描，不伪造源文件覆盖率。
    assert plan['missing_source_minutes'] == []
    assert plan['counts']['compute'] == (0 if cached_target else 1)
    result = service.run(payload)
    assert len(result['computed']) == (0 if cached_target else 1)
    np.testing.assert_allclose(storage.load(Task('exp', 3, 0, 'wind_speed_10', None)), 5)


def test_plan_normalizes_surface_and_height_variables_in_one_batch(tmp_path):
    service = _service(tmp_path)
    plan = service.plan({
        "experiment": "exp", "domain": 3, "variables": ["wind_speed_10", "theta"],
        "times_minutes": [0], "heights_m": [100], "profile_id": "default",
    })
    surface = [item for item in plan["tasks"] if item["variable"] == "wind_speed_10"]
    height = [item for item in plan["tasks"] if item["variable"] == "theta"]
    assert len(surface) == 1 and surface[0]["height_m"] is None
    assert len(height) == 1 and height[0]["height_m"] == 100


def test_source_index_reuses_unchanged_wrfout_time_scan(tmp_path, monkeypatch):
    import netCDF4

    service = _service(tmp_path)
    original = netCDF4.Dataset
    opens = []

    def tracked(path, *args, **kwargs):
        opens.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(netCDF4, "Dataset", tracked)
    assert service.source_coverage("exp", 3)["time_minutes"] == [0, 5]
    assert service.source_coverage("exp", 3)["time_minutes"] == [0, 5]
    assert len(opens) == 1


def test_profile_options_are_immutable_and_default_conflict_is_rejected(tmp_path):
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    service = _service(tmp_path)
    changed = service.profile_for({"radius_resolution_km": 2.0})
    assert changed["id"] != "default"
    assert changed["root"].parent.parent == tmp_path / "processed_v3"
    service.ensure_profile(changed)
    assert service.profile_for({"radius_resolution_km": 2.0})["id"] == changed["id"]
    with pytest.raises(ValueError, match="不可修改"):
        service.options_for(changed["id"], {"radius_resolution_km": 3.0})

    storage = NetCDFStorage(tmp_path / "processed_v3", time_origin="2023-09-01_00:00:00")
    storage.save(
        Task("exp", 3, 0, "dbz_polar", 100), np.ones((2, 2)),
        SimpleNamespace(storage_layout="polar_3d", units="dBZ", options={"algorithm_version": "v2.1", "radius_resolution_km": 1.0}),
        metadata={"request_options": {"radius_resolution_km": 9.0}},
    )
    with pytest.raises(ValueError, match="参数不一致"):
        service.plan({
            "experiment": "exp", "domain": 3, "variables": ["dbz_polar"],
            "times_minutes": [0, 5], "heights_m": [100], "profile_id": "default",
        })


@pytest.mark.parametrize('action', ['plan', 'run'])
@pytest.mark.parametrize('isolated', [True, False])
def test_all_web_entrypoints_reject_incompatible_cache(tmp_path, action, isolated):
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    service = _service(tmp_path)
    profile = service.profile_for({'radius_resolution_km': 2.0} if isolated else {})
    service.ensure_profile(profile)
    path = NetCDFStorage(profile['root'], time_origin='2023-09-01_00:00:00').save(
        Task('exp', 3, 0, 'dbz_polar', 100), np.ones((2, 2)),
        SimpleNamespace(storage_layout='polar_3d', units='dBZ',
                        options={'algorithm_version': 'v2.1', 'radius_resolution_km': 1.0}),
        metadata={'request_options': {**service.options_for(profile['id']), 'radius_resolution_km': 9.0}})
    before = path.read_bytes()
    with pytest.raises(ValueError, match='参数不一致'):
        getattr(service, action)({'experiment': 'exp', 'domain': 3, 'variables': ['dbz_polar'],
                                 'times_minutes': [0], 'heights_m': [100], 'profile_id': profile['id']})
    assert path.read_bytes() == before


def test_browser_number_fields_preserve_default_profile(tmp_path):
    from 处理数据_v3.project_config import OPTIONS
    from wrf_webvis.app.services.v3_management import V3ManagementService

    service = V3ManagementService(
        output_root=tmp_path / "processed_v3",
        source_directories={"exp": tmp_path / "source"},
        default_options=OPTIONS,
    )
    browser_options = {
        "radius_km_by_domain": {3: 300},
        "radius_resolution_km": 1,
        "center_options_by_domain": {3: {"search_radius_km": 60}},
        "theta_environment_domain": 2,
        "theta_environment_ring_km": [400, 500],
        "shear_source_domain": 2,
        "shear_radius_inner_km": 150,
        "shear_radius_outer_km": 350,
    }
    assert service.profile_for(browser_options)["id"] == "default"


def test_default_profile_refuses_to_append_when_old_options_are_unknown(tmp_path):
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    service = _service(tmp_path)
    NetCDFStorage(service.output_root, time_origin="2023-09-01_00:00:00").save(
        Task("exp", 3, 0, "dbz_polar", 100), np.ones((2, 2)),
        SimpleNamespace(storage_layout="polar_3d", units="dBZ", options={"algorithm_version": "v2.1", "radius_resolution_km": 1.0}),
        metadata={"source_file": "unknown"},
    )
    with pytest.raises(ValueError, match="缺少.*参数"):
        service.plan({
            "experiment": "exp", "domain": 3, "variables": ["dbz_polar"],
            "times_minutes": [0, 5], "heights_m": [100], "profile_id": "default",
        })


def test_cartesian_native_slice_ignores_unrelated_polar_options(tmp_path):
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    service = _service(tmp_path)
    NetCDFStorage(service.output_root, time_origin="2023-09-01_00:00:00").save(
        Task("exp", 3, 0, "theta", 100), np.ones((2, 2)),
        SimpleNamespace(storage_layout="cartesian_3d", units="K", options={"algorithm_version": "v2.1"}),
        metadata={"request_options": {"radius_resolution_km": 9.0}},
    )
    plan = service.plan({
        "experiment": "exp", "domain": 3, "variables": ["theta"],
        "times_minutes": [0, 5], "heights_m": [100], "profile_id": "default",
    })
    assert plan["counts"]["reuse"] == 1


def test_default_profile_rejects_native_algorithm_version_conflict(tmp_path):
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    service = _service(tmp_path)
    NetCDFStorage(service.output_root, time_origin="2023-09-01_00:00:00").save(
        Task("exp", 3, 0, "theta", 100), np.ones((2, 2)),
        SimpleNamespace(storage_layout="cartesian_3d", units="K", options={"algorithm_version": "old"}),
    )
    with pytest.raises(ValueError, match="算法版本不一致"):
        service.plan({
            "experiment": "exp", "domain": 3, "variables": ["theta"],
            "times_minutes": [0, 5], "heights_m": [100], "profile_id": "default",
        })


def test_default_profile_checks_polar_settings_for_selected_domain_only(tmp_path):
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    service = _service(tmp_path)
    stored_options = {
        "radius_km_by_domain": {2: 999, 3: 300},
        "radius_resolution_km": 1,
        "center_options_by_domain": {2: {"search_radius_km": 999}, 3: {"search_radius_km": 30}},
    }
    service.default_options["center_options_by_domain"] = {"2": {"search_radius_km": 30}, "3": {"search_radius_km": 30}}
    NetCDFStorage(service.output_root, time_origin="2023-09-01_00:00:00").save(
        Task("exp", 3, 0, "dbz_polar", 100), np.ones((2, 2)),
        SimpleNamespace(storage_layout="polar_3d", units="dBZ", options={"algorithm_version": "v2.1", "radius_resolution_km": 1}),
        metadata={"request_options": stored_options},
    )
    plan = service.plan({
        "experiment": "exp", "domain": 3, "variables": ["dbz_polar"],
        "times_minutes": [0], "heights_m": [100], "profile_id": "default",
    })
    assert plan["counts"]["reuse"] == 1


def test_coverage_and_slice_ignore_uncompleted_data(tmp_path):
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    service = _service(tmp_path)
    storage = NetCDFStorage(tmp_path / "processed_v3", time_origin="2023-09-01_00:00:00")
    path = storage.save(
        Task("exp", 3, 0, "theta", 100), np.ones((2, 2)),
        SimpleNamespace(storage_layout="cartesian_3d", units="K", options={"algorithm_version": "v2.1"}),
        metadata={"source_file": "wrfout", "request_options": service.options_for("default")},
    )
    with Dataset(path, "r+") as dataset:
        dataset["completed"][1, 0] = 0
    coverage = service.coverage("default", "exp", 3, "theta")
    assert coverage["completed"] == [{"time_minutes": 0, "height_m": 100}]
    assert service.slice_metadata("default", "exp", 3, "theta", 0, 100)["source_file"] == "wrfout"
    with pytest.raises(KeyError):
        service.slice_metadata("default", "exp", 3, "theta", 5, 100)


def test_coverage_reads_later_completed_3d_records(tmp_path):
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    service = _service(tmp_path)
    storage = NetCDFStorage(service.output_root, time_origin="2023-09-01_00:00:00")
    spec = SimpleNamespace(storage_layout="cartesian_3d", units="K", options={"algorithm_version": "v2.1"})
    for minute in (0, 5):
        storage.save(Task("exp", 3, minute, "theta", 100), np.ones((2, 2)), spec)
    assert service.coverage("default", "exp", 3, "theta")["completed"] == [
        {"time_minutes": 0, "height_m": 100},
        {"time_minutes": 5, "height_m": 100},
    ]


def test_run_reports_slice_progress_and_reuses_completed_outputs(tmp_path):
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    service = _service(tmp_path)
    payload = {
        "experiment": "exp", "domain": 3, "variables": ["wind_speed_10"],
        "times_minutes": [0, 5], "heights_m": [], "profile_id": "default",
    }
    messages = []
    result = service.run(payload, progress=messages.append)
    assert len(result["computed"]) == 6
    assert any("切片" in message for message in messages)
    storage = NetCDFStorage(service.output_root, time_origin="2023-09-01_00:00:00")
    np.testing.assert_allclose(storage.load(Task("exp", 3, 5, "wind_speed_10", None)), 5.0)
    second = service.run(payload)
    assert second["computed"] == []


def test_catalog_lists_storm_relative_variables_without_double_layer_names(tmp_path):
    # 11-03：_sr 变量都在可处理列表；已按各层中心分解的 X1 只列 X1_polar，不出现 X11_polar
    names = set(_service(tmp_path).catalog_variables())
    assert {"ua_sr", "va_sr", "inflow_angle_sr", "inflow_angle_sr_polar", "inflow_angle_sr1_polar",
            "qflux_r_sr1_polar", "vr_sr_ventilation_polar", "vr_sr1_ventilation_polar"} <= names
    assert not {"vr11_polar", "vr_sr11_polar", "inflow_angle_sr11_polar", "qflux_r_sr11_polar"} & names
    assert "theta_e1_polar" in names


def test_asymmetry_options_are_editable_and_get_their_own_profile(tmp_path):
    # 11-04：非对称度圆盘半径 / dbz 非对称度高度进网页表单；等于基准值仍是 default，改了就是独立参数配置
    from wrf_webvis.app.services.v3_management import EDITABLE_KEYS

    assert {"asymmetry_radius_km", "dbz_asymmetry_height_m"} <= set(EDITABLE_KEYS)
    service = _service(tmp_path)
    assert service.profile_for({"asymmetry_radius_km": 100.0, "dbz_asymmetry_height_m": 3000})["id"] == "default"
    changed = service.profile_for({"asymmetry_radius_km": 150.0})
    assert changed["id"] != "default" and changed["options"]["asymmetry_radius_km"] == 150.0
    assert "非对称度圆盘半径 150 km" in service.profile_label(changed["options"])
    service.ensure_profile(changed)
    assert service.options_for(changed["id"])["asymmetry_radius_km"] == 150.0   # 编号与参数指纹一致
    with pytest.raises(ValueError, match="正数"):
        service.profile_for({"asymmetry_radius_km": 0})
    with pytest.raises(ValueError, match="整数米"):
        service.profile_for({"dbz_asymmetry_height_m": 2500.5})


def test_netcdf_entrypoints_are_serialized_in_the_web_process():
    # 11-04：HDF5 不是线程安全的；网页进程里打开 NetCDF 的入口必须一次只进一个线程
    import threading
    import time

    from wrf_webvis.app.services import v3_management as module

    for name in ("_quick_summary", "_completed_summary", "coverage", "slice_metadata", "plan", "verify_plan",
                 "source_coverage", "experiment_details"):
        assert hasattr(getattr(module.V3ManagementService, name), "__wrapped__"), name
    inside, peak = [0], [0]

    @module.netcdf_serialized
    def reader():
        inside[0] += 1
        peak[0] = max(peak[0], inside[0])
        time.sleep(0.02)
        inside[0] -= 1

    threads = [threading.Thread(target=reader) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak[0] == 1
