from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_inventory_and_historical_coverage_work_without_wrfout(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import numpy as np

    from wrf_webvis.app.main import app
    from wrf_webvis.app.api import data as data_api
    from wrf_webvis.tests.test_v3_management import _service
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    service = _service(tmp_path)
    NetCDFStorage(service.output_root, time_origin="2023-09-01_00:00:00").save(
        Task("old_exp", 3, 0, "historical", None), np.ones((2, 2)),
        SimpleNamespace(storage_layout="cartesian_2d", units="K", options={"algorithm_version": "v2.1"}),
    )
    monkeypatch.setattr(data_api, "service", service)
    client = TestClient(app, client=("127.0.0.1", 50000))
    inventory = client.get("/api/data/inventory")
    assert inventory.status_code == 200
    assert [(item["experiment"], item["variable"]) for item in inventory.json()["items"]] == [("old_exp", "historical")]
    params = {"profile_id": "default", "experiment": "old_exp", "domain": 3, "variable": "historical"}
    assert client.get("/api/data/coverage", params=params).json()["completed"] == [{"time_minutes": 0, "height_m": None}]
    assert client.get("/api/data/sources", params={"experiment": "old_exp", "domain": 3}).status_code == 400
    assert client.post("/api/processing/v3/plan", json={**params, "variables": ["historical"], "times_minutes": [0], "heights_m": []}).status_code in {400, 422}


def test_v3_catalog_and_plan_endpoints_revalidate_before_submission(tmp_path, monkeypatch):
    from wrf_webvis.app.main import app
    from wrf_webvis.app.api import data as data_api
    from wrf_webvis.app.api import processing_v3 as processing_api
    from wrf_webvis.tests.test_v3_management import _service

    service = _service(tmp_path)
    monkeypatch.setattr(data_api, "service", service)
    monkeypatch.setattr(processing_api, "service", service)
    client = TestClient(app, client=("127.0.0.1", 50000))
    catalog = client.get("/api/data/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["experiments"] == ["exp"]
    assert "dbz_polar" in catalog.json()["variables"]
    source = client.get("/api/data/sources", params={"experiment": "exp", "domain": 3})
    assert source.json()["time_minutes"] == [0, 5]

    payload = {
        "experiment": "exp", "domain": 3, "variables": ["wind_speed_10"],
        "times_minutes": [0], "heights_m": [], "profile_id": "default",
    }
    plan = client.post("/api/processing/v3/plan", json=payload)
    assert plan.status_code == 200
    assert plan.json()["counts"]["compute"] == 3
    stale = client.post("/api/processing/v3/jobs", json={**payload, "plan_hash": "old"})
    assert stale.status_code == 409
    fractional = client.post("/api/processing/v3/plan", json={**payload, "times_minutes": [2.5]})
    assert fractional.status_code in {400, 422}
    missing_source_payload = {**payload, "times_minutes": [10]}
    missing_source_plan = client.post("/api/processing/v3/plan", json=missing_source_payload).json()
    assert missing_source_plan["missing_source_minutes"] == [10]
    blocked = client.post("/api/processing/v3/jobs", json={**missing_source_payload, "plan_hash": missing_source_plan["plan_hash"]})
    assert blocked.status_code == 422


def test_v3_coverage_and_slice_api_use_completed_flag(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import numpy as np

    from wrf_webvis.app.main import app
    from wrf_webvis.app.api import data as data_api
    from wrf_webvis.tests.test_v3_management import _service
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    service = _service(tmp_path)
    monkeypatch.setattr(data_api, "service", service)
    storage = NetCDFStorage(service.output_root, time_origin="2023-09-01_00:00:00")
    storage.save(
        Task("exp", 3, 0, "theta", 100), np.ones((2, 2)),
        SimpleNamespace(storage_layout="cartesian_3d", units="K", options={"algorithm_version": "v2.1"}),
        metadata={"source_file": "example-wrfout"},
    )
    client = TestClient(app, client=("127.0.0.1", 50000))
    params = {"profile_id": "default", "experiment": "exp", "domain": 3, "variable": "theta"}
    response = client.get("/api/data/coverage", params=params)
    assert response.status_code == 200
    assert response.json()["completed"] == [{"time_minutes": 0, "height_m": 100}]
    detail = client.get("/api/data/slice", params={**params, "time_minutes": 0, "height_m": 100})
    assert detail.json()["source_file"] == "example-wrfout"
    assert client.get("/api/data/slice", params={**params, "time_minutes": 5, "height_m": 100}).status_code == 404


def test_web_job_processes_selected_minutes_and_records_result(tmp_path, monkeypatch):
    from wrf_webvis.app.main import app
    from wrf_webvis.app.api import processing_v3 as processing_api
    from wrf_webvis.app.services.progress import progress_store
    from wrf_webvis.tests.test_v3_management import _service

    service = _service(tmp_path)
    monkeypatch.setattr(processing_api, "service", service)
    client = TestClient(app, client=("127.0.0.1", 50000))
    payload = {
        "experiment": "exp", "domain": 3, "variables": ["wind_speed_10"],
        "times_minutes": [0], "heights_m": [], "profile_id": "default",
    }
    preview = client.post("/api/processing/v3/plan", json=payload).json()
    created = client.post("/api/processing/v3/jobs", json={**payload, "plan_hash": preview["plan_hash"]})
    assert created.status_code == 200
    job = progress_store.get(created.json()["job_id"])
    assert job["status"] == "complete"
    assert job["result"]["profile_id"] == "default"
    assert service.coverage("default", "exp", 3, "wind_speed_10")["completed"] == [
        {"time_minutes": 0, "height_m": None}
    ]


def test_processing_job_records_failure_and_cancellation(monkeypatch):
    from wrf_webvis.app.api import processing_v3 as processing_api
    from wrf_webvis.app.services.progress import JobCancelled, progress_store

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic processor failure")

    monkeypatch.setattr(processing_api, "run_isolated_job", fail)
    progress_store.start("v3-failed-test", "failure", kind="processing")
    processing_api._run_job({}, "v3-failed-test")
    assert progress_store.get("v3-failed-test")["status"] == "failed"
    assert "synthetic processor failure" in progress_store.get("v3-failed-test")["error"]

    def cancel(*args, **kwargs):
        raise JobCancelled("canceled")

    monkeypatch.setattr(processing_api, "run_isolated_job", cancel)
    progress_store.start("v3-canceled-test", "cancel", kind="processing")
    processing_api._run_job({}, "v3-canceled-test")
    assert progress_store.get("v3-canceled-test")["status"] == "canceled"


def test_custom_experiments_are_registered_without_touching_project_config(tmp_path, monkeypatch):
    """2026-10-21：网页上添加/移除试验，只写 custom_experiments.json。"""
    from wrf_webvis.app.main import app
    from wrf_webvis.app.api import data as data_api
    from wrf_webvis.app.api import processing_v3 as processing_api
    from wrf_webvis.app.services.v3_management import V3ManagementService
    from wrf_webvis.tests.test_v3_management import _source

    fixed = tmp_path / "fixed" / "run"
    _source(fixed / "wrfout_d03_sample")
    extra = tmp_path / "extra" / "run"
    _source(extra / "wrfout_d03_sample")
    store = tmp_path / "custom_experiments.json"
    service = V3ManagementService(
        output_root=tmp_path / "processed_v3", source_directories={"exp": fixed},
        default_options={"radius_km_by_domain": {3: 300.0}, "radius_resolution_km": 1.0},
        custom_experiments_path=store,
    )
    monkeypatch.setattr(data_api, "service", service)
    monkeypatch.setattr(processing_api, "service", service)
    client = TestClient(app, client=("127.0.0.1", 50000))

    assert client.post("/api/data/experiments", json={"name": "bad name", "path": str(extra)}).status_code == 400
    assert client.post("/api/data/experiments", json={"name": "exp", "path": str(extra)}).status_code == 400
    assert client.post("/api/data/experiments", json={"name": "new_exp", "path": str(tmp_path / "missing")}).status_code == 400
    added = client.post("/api/data/experiments", json={"name": "new_exp", "path": str(extra)})
    assert added.status_code == 200 and added.json()["domains"] == [3]
    catalog = client.get("/api/data/catalog").json()
    assert catalog["experiments"] == ["exp", "new_exp"]
    assert [row["custom"] for row in catalog["experiment_details"]] == [False, True]
    assert catalog["variable_info"]["U10"]["units"] == "m s-1"
    assert client.get("/api/data/sources", params={"experiment": "new_exp", "domain": 3}).json()["time_minutes"] == [0, 5]
    plan = client.post("/api/processing/v3/plan", json={
        "experiment": "new_exp", "domain": 3, "variables": ["wind_speed_10"],
        "times_minutes": [0], "heights_m": [], "profile_id": "default",
    })
    assert plan.status_code == 200
    assert client.delete("/api/data/experiments/exp").status_code == 400
    assert client.delete("/api/data/experiments/new_exp").status_code == 200
    assert client.get("/api/data/catalog").json()["experiments"] == ["exp"]
    assert extra.is_dir()


def test_inventory_details_report_dimensions_of_completed_slices(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import numpy as np

    from wrf_webvis.app.main import app
    from wrf_webvis.app.api import data as data_api
    from wrf_webvis.tests.test_v3_management import _service
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    service = _service(tmp_path)
    monkeypatch.setattr(data_api, "service", service)
    storage = NetCDFStorage(service.output_root, time_origin="2023-09-01_00:00:00")
    meta = SimpleNamespace(storage_layout="cartesian_3d", units="K", options={"algorithm_version": "v2.1"})
    for minute, height in ((0, 100), (60, 100), (60, 1000)):
        storage.save(Task("exp", 3, minute, "theta", height), np.ones((2, 3)), meta)
    client = TestClient(app, client=("127.0.0.1", 50000))
    response = client.get("/api/data/inventory/details", params=[("key", "default|exp|3|theta"), ("key", "default|exp|3|absent")])
    assert response.status_code == 200
    first, second = response.json()["items"]
    assert first["layout"] == "cartesian_3d" and first["units"] == "K"
    assert first["time_hours"]["count"] == 2 and first["time_hours"]["max"] == 1.0
    assert first["heights_m"]["count"] == 2 and first["heights_m"]["min"] == 100 and first["heights_m"]["max"] == 1000
    assert first["spatial"] == {"y": 2, "x": 3}
    assert first["completed_slices"] == 3
    assert "error" in second
    assert client.get("/api/data/inventory/details", params=[("key", "bad")]).status_code == 400


def test_inventory_details_count_completed_flags_in_background_and_cache_on_disk(tmp_path, monkeypatch):
    """2026-10-21：大文件的 completed 标记读十几秒，网页服务先返回坐标，后台统计后写磁盘缓存。"""
    from types import SimpleNamespace

    import numpy as np

    from wrf_webvis.app.services import v3_management
    from wrf_webvis.app.services.v3_management import V3ManagementService
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    cache = tmp_path / "summary.json"
    service = V3ManagementService(
        output_root=tmp_path / "processed_v3", source_directories={},
        default_options={"radius_km_by_domain": {3: 300.0}}, summary_cache_path=cache,
    )
    storage = NetCDFStorage(service.output_root, time_origin="2023-09-01_00:00:00")
    meta = SimpleNamespace(storage_layout="cartesian_3d", units="K", options={"algorithm_version": "v2.1"})
    for minute in (0, 60):
        storage.save(Task("exp", 3, minute, "theta", 500), np.ones((2, 3)), meta)
    first = service.file_summary("default", "exp", 3, "theta")
    assert first["time_hours"]["count"] == 2 and first["dims"][1] == ["height", first["file_height_count"]]
    v3_management._SUMMARY_QUEUE.join()
    done = service.file_summary("default", "exp", 3, "theta")
    assert done["completed_pending"] is False
    assert done["height_values"] == [500] and done["completed_slices"] == 2
    fresh = V3ManagementService(
        output_root=tmp_path / "processed_v3", source_directories={},
        default_options={"radius_km_by_domain": {3: 300.0}}, summary_cache_path=cache,
    )
    assert fresh.file_summary("default", "exp", 3, "theta")["height_values"] == [500]


# ---- 2026-11-02 数据集 post 与处理套餐 ----

def _two_dataset_client(tmp_path, monkeypatch):
    from wrf_webvis.app.main import app
    from wrf_webvis.app.api import data as data_api
    from wrf_webvis.app.api import processing_v3 as processing_api
    from wrf_webvis.app.services import datasets
    from wrf_webvis.app.services.v3_management import V3ManagementService
    from wrf_webvis.tests.test_v3_management import _service

    v3 = _service(tmp_path)
    post = V3ManagementService(
        output_root=tmp_path / "post", source_directories=v3.source_directories,
        default_options={"radius_km_by_domain": {3: 300.0}, "radius_resolution_km": 1.0},
    )
    monkeypatch.setattr(data_api, "service", v3)
    monkeypatch.setattr(processing_api, "service", v3)
    monkeypatch.setitem(datasets.SERVICES, "post", post)
    return TestClient(app, client=("127.0.0.1", 50000)), v3, post


def test_dataset_selects_output_root_and_defaults_to_processed_v3(tmp_path, monkeypatch):
    client, v3, post = _two_dataset_client(tmp_path, monkeypatch)
    payload = {
        "experiment": "exp", "domain": 3, "variables": ["wind_speed_10"],
        "times_minutes": [0], "heights_m": [], "profile_id": "default",
    }
    old = client.post("/api/processing/v3/plan", json=payload).json()
    assert old["root"] == str(v3.output_root) and old["dataset"] == "v3"
    new = client.post("/api/processing/v3/plan", json={**payload, "dataset": "post"}).json()
    assert new["root"] == str(post.output_root) and new["dataset"] == "post"
    assert all(item["path"].startswith(str(post.output_root)) for item in new["tasks"])
    assert new["plan_hash"] != old["plan_hash"]
    # 用 post 的计划编号提交到 v3 会被拒（数据集不同即计划不同）
    assert client.post("/api/processing/v3/jobs", json={**payload, "plan_hash": new["plan_hash"]}).status_code == 409
    assert client.post("/api/processing/v3/plan", json={**payload, "dataset": "nope"}).status_code == 400
    assert not post.output_root.exists()  # 预览不建目录


def test_catalog_lists_datasets_and_presets_and_inventory_merges(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import numpy as np

    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    client, v3, post = _two_dataset_client(tmp_path, monkeypatch)
    catalog = client.get("/api/data/catalog", params={"dataset": "post"}).json()
    assert [item["id"] for item in catalog["datasets"]] == ["post", "v3"]
    assert catalog["dataset"] == "post" and catalog["profiles"][0]["root"] == str(post.output_root)
    assert catalog["processing_presets_error"] is None
    basic = next(item for item in catalog["processing_presets"] if item["id"] == "basic_tc")
    assert basic["groups"][0]["time_step_minutes"] == 5
    assert "shear_core_200_850_magnitude" in catalog["variables"]

    meta = SimpleNamespace(storage_layout="cartesian_2d", units="K", options={"algorithm_version": "v2.1"})
    NetCDFStorage(v3.output_root, time_origin="2023-09-01_00:00:00").save(Task("exp", 3, 0, "old_var", None), np.ones((2, 2)), meta)
    NetCDFStorage(post.output_root, time_origin="2023-09-01_00:00:00").save(Task("exp", 3, 0, "new_var", None), np.ones((2, 2)), meta)
    items = client.get("/api/data/inventory").json()["items"]
    assert sorted((item["dataset"], item["variable"]) for item in items) == [("post", "new_var"), ("v3", "old_var")]
    only_post = client.get("/api/data/inventory", params={"dataset": "post"}).json()["items"]
    assert [item["variable"] for item in only_post] == ["new_var"]
    details = client.get("/api/data/inventory/details", params={"key": ["post|default|exp|3|new_var", "default|exp|3|old_var"]}).json()["items"]
    assert [(item["dataset"], item.get("error")) for item in details] == [("post", None), ("v3", None)]
    coverage = client.get("/api/data/coverage", params={"experiment": "exp", "domain": 3, "variable": "new_var", "dataset": "post"}).json()
    assert coverage["completed"] == [{"time_minutes": 0, "height_m": None}] and coverage["dataset"] == "post"


def test_request_accepts_1441_times_for_5min_series(tmp_path):
    from wrf_webvis.tests.test_v3_management import _service

    service = _service(tmp_path)
    base = {"experiment": "exp", "domain": 3, "variables": ["wind_speed_10"], "heights_m": []}
    request, _ = service._request({**base, "times_minutes": list(range(0, 120 * 60 + 1, 5))}, dry_run=True)
    assert len(request.times_minutes) == 1441
    import pytest

    with pytest.raises(ValueError, match="2000"):
        service._request({**base, "times_minutes": list(range(2001))}, dry_run=True)


def test_batch_submission_checks_every_plan_before_starting_any_job(tmp_path, monkeypatch):
    import threading

    from wrf_webvis.app.api import processing_v3 as processing_api

    client, v3, post = _two_dataset_client(tmp_path, monkeypatch)
    started = []
    done = threading.Event()

    def fake_run(values, job_id, target=None):
        started.append((values["variables"], values.get("label"), str(target.output_root)))
        if len(started) == 2:
            done.set()

    monkeypatch.setattr(processing_api, "_run_job", fake_run)
    base = {"experiment": "exp", "domain": 3, "times_minutes": [0], "heights_m": [], "profile_id": "default", "dataset": "post"}
    first = {**base, "variables": ["wind_speed_10"], "label": "组一"}
    second = {**base, "variables": ["U10"], "label": "组二"}
    plans = [client.post("/api/processing/v3/plan", json=item).json() for item in (first, second)]

    stale = client.post("/api/processing/v3/jobs/batch", json={"jobs": [
        {**first, "plan_hash": plans[0]["plan_hash"]}, {**second, "plan_hash": "old"},
    ]})
    assert stale.status_code == 409
    assert [item["index"] for item in stale.json()["detail"]["problems"]] == [1]
    assert started == []  # 一个不通过就全部不提交

    ok = client.post("/api/processing/v3/jobs/batch", json={"jobs": [
        {**first, "plan_hash": plans[0]["plan_hash"]}, {**second, "plan_hash": plans[1]["plan_hash"]},
    ]})
    assert ok.status_code == 200
    body = ok.json()
    assert len(body["jobs"]) == 2 and body["workers"] == processing_api.PROCESSING_WORKERS
    assert [job["label"] for job in body["jobs"]] == ["组一", "组二"]
    assert done.wait(5)
    assert sorted(item[1] for item in started) == ["组一", "组二"]
    assert all(item[2] == str(post.output_root) for item in started)
    assert client.post("/api/processing/v3/jobs/batch", json={"jobs": []}).status_code == 400


def test_processing_workers_come_from_project_config_and_catalog_lists_hidden_variables(tmp_path, monkeypatch):
    from wrf_webvis.app.api import processing_v3 as processing_api
    from 处理数据_v3 import project_config

    assert processing_api.PROCESSING_WORKERS == max(1, min(16, int(project_config.WEB_PROCESSING_WORKERS)))
    assert processing_api.processing_slot.limit == processing_api.PROCESSING_WORKERS  # 测试时没有网页设置文件
    client, _, _ = _two_dataset_client(tmp_path, monkeypatch)
    catalog = client.get("/api/data/catalog", params={"dataset": "post"}).json()
    assert catalog["processing_workers"] == processing_api.PROCESSING_WORKERS
    assert "_va_lower" in catalog["hidden_variables"] and "XLAT" in catalog["hidden_variables"]
    assert "dbz" not in catalog["hidden_variables"] and "_va_lower" in catalog["variables"]
    assert catalog["experiment_end_hours"] == {}  # 测试试验 exp 不在 EXPERIMENT_END_HOURS 里


def test_catalog_experiment_end_hours_follow_project_config(tmp_path):
    from wrf_webvis.app.services.v3_management import V3ManagementService
    from 处理数据_v3 import project_config

    service = V3ManagementService(
        output_root=tmp_path / "out", source_directories={"WRF_315_land": tmp_path, "custom": tmp_path},
        default_options={"radius_km_by_domain": {3: 300.0}, "radius_resolution_km": 1.0},
    )
    assert service.experiment_end_hours(["WRF_315_land", "custom"]) == {
        "WRF_315_land": float(project_config.EXPERIMENT_END_HOURS["WRF_315_land"])
    }


def test_submission_reuses_previewed_plan_until_files_change(tmp_path, monkeypatch):
    import os
    import threading

    from wrf_webvis.app.api import processing_v3 as processing_api

    client, v3, post = _two_dataset_client(tmp_path, monkeypatch)
    monkeypatch.setattr(processing_api, "_run_job", lambda values, job_id, target=None: None)
    payload = {"experiment": "exp", "domain": 3, "variables": ["wind_speed_10"], "times_minutes": [0], "heights_m": [],
               "profile_id": "default", "dataset": "post", "label": "组一"}
    plan = client.post("/api/processing/v3/plan", json=payload).json()

    real_plan = post.plan
    calls = []

    def counting(values):
        calls.append(values)
        return real_plan(values)

    monkeypatch.setattr(post, "plan", counting)
    ok = client.post("/api/processing/v3/jobs/batch", json={"jobs": [{**payload, "label": "改了标题也算同一请求", "plan_hash": plan["plan_hash"]}]})
    assert ok.status_code == 200 and calls == []  # 文件没变：不重做计划

    source = next(iter(v3.source_directories.values()))
    for wrfout in Path(source).glob("wrfout_*"):
        stat = wrfout.stat()
        os.utime(wrfout, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    stale = client.post("/api/processing/v3/jobs/batch", json={"jobs": [{**payload, "plan_hash": plan["plan_hash"]}]})
    assert len(calls) == 1 and stale.status_code == 409  # wrfout 变了：重做计划，编号不同被拒


def test_processing_slots_limit_can_change_while_running():
    # 11-04：同时运行任务数网页可改；调大立即放行排队的任务，调小不打断正在运行的
    from wrf_webvis.app.api.processing_v3 import ProcessingSlots

    slots = ProcessingSlots(1)
    assert slots.acquire(blocking=False) and not slots.acquire(blocking=False)
    assert not slots.acquire(timeout=0.01)
    slots.set_limit(2)
    assert slots.acquire(blocking=False) and slots.active == 2
    slots.set_limit(1)
    slots.release()
    assert not slots.acquire(blocking=False)   # 还有 1 个在跑，等于新上限
    slots.release()
    assert slots.acquire(blocking=False) and slots.active == 1


def test_processing_settings_endpoint_saves_and_applies(tmp_path, monkeypatch):
    import json as _json

    from wrf_webvis.app.api import processing_v3 as processing_api

    settings = tmp_path / "settings.json"
    monkeypatch.setenv("WRF_WEBVIS_PROCESSING_SETTINGS", str(settings))
    client, _, _ = _two_dataset_client(tmp_path, monkeypatch)
    original = processing_api.processing_slot.limit
    try:
        body = client.put("/api/processing/v3/settings", json={"workers": 3}).json()
        assert body["workers"] == 3 and processing_api.processing_slot.limit == 3
        assert _json.loads(settings.read_text())["workers"] == 3
        assert processing_api._saved_workers() == 3            # 网页重启后仍用 3
        assert client.get("/api/processing/v3/settings").json()["max_workers"] == 4
        assert client.get("/api/data/catalog", params={"dataset": "post"}).json()["processing_workers"] == 3
        for bad in (0, 5):
            assert client.put("/api/processing/v3/settings", json={"workers": bad}).status_code == 422
    finally:
        processing_api.processing_slot.set_limit(original)
