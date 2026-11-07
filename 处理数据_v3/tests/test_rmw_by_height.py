from __future__ import annotations

import numpy as np
import pytest
from netCDF4 import Dataset


def _request(tmp_path, *, variables=("rmw_by_height",)):
    from wrf_processing_v3.request import Request

    return Request(
        experiments=("exp",),
        domains=(3,),
        times_minutes=(0,),
        heights_m=(100, 200),
        variables=variables,
        source_root=tmp_path / "source",
        output_root=tmp_path / "out",
        options={
            "time_origin": "2023-09-01_00:00:00",
            "domain_config": {3: {"dx_km": 1.0, "dy_km": 1.0}},
        },
    )


def test_request_rejects_a_single_variable_string(tmp_path):
    from wrf_processing_v3.request import Request

    with pytest.raises(ValueError, match="variables.*序列"):
        Request(
            experiments=("exp",),
            domains=(3,),
            times_minutes=(0,),
            heights_m=(100,),
            variables="rmw_by_height",
            source_root=tmp_path,
        )


def test_rmw_by_height_plans_wind_at_each_requested_height(tmp_path):
    from wrf_processing_v3.dependency_planner import DependencyPlanner
    from wrf_processing_v3.register import build_registry
    from wrf_processing_v3.request import Task
    from wrf_processing_v3.storage import NetCDFStorage

    request = _request(tmp_path)
    registry = build_registry(request.options, times_minutes=request.times_minutes)
    spec = registry.resolve("rmw_by_height")
    assert spec.storage_layout == "height_scalar"
    assert [(dep.name, dep.height_m) for dep in spec.dependencies] == [
        ("ua", "same"),
        ("va", "same"),
        ("slp_center", None),
    ]

    storage = NetCDFStorage(request.output_root, time_origin=request.options["time_origin"])
    tasks = {item.task for item in DependencyPlanner(registry, storage).plan(request)}
    for height in request.heights_m:
        assert Task("exp", 3, 0, "rmw_by_height", height) in tasks
        assert Task("exp", 3, 0, "ua", height) in tasks
        assert Task("exp", 3, 0, "va", height) in tasks
    assert Task("exp", 3, 0, "slp_center", None) in tasks
    assert registry.resolve("rmw").storage_layout == "scalar"


def test_height_scalar_stores_independent_time_height_slices(tmp_path):
    from wrf_processing_v3.register import build_registry
    from wrf_processing_v3.request import Task
    from wrf_processing_v3.storage import NetCDFStorage

    registry = build_registry()
    storage = NetCDFStorage(tmp_path, time_origin="2023-09-01_00:00:00")
    spec = registry.resolve("rmw_by_height")
    first = Task("exp", 3, 0, "rmw_by_height", 100)
    second = Task("exp", 3, 0, "rmw_by_height", 200)
    later = Task("exp", 3, 60, "rmw_by_height", 100)
    for task, value in ((first, 2.0), (second, 3.0), (later, 4.0)):
        storage.save(task, np.asarray([value]), spec)

    with Dataset(storage.path_for(first)) as dataset:
        assert dataset["data"].dimensions == ("record", "height")
        assert dataset["completed"].dimensions == ("record", "height")
        assert dataset["metadata_json"].dimensions == ("record", "height")
        assert dataset["data"].chunking() == [1, 1]
    assert storage.existing((first, second, later)) == {first, second, later}
    assert storage.load(first).shape == (1,)
    np.testing.assert_allclose(storage.load(first), [2.0])
    np.testing.assert_allclose(storage.load(second), [3.0])
    np.testing.assert_allclose(storage.load(later), [4.0])


def test_rmw_by_height_uses_each_levels_wind_and_records_peak(tmp_path):
    from wrf_processing_v3.context import ProcessingContext
    from wrf_processing_v3.register import build_registry
    from wrf_processing_v3.request import Task
    from wrf_processing_v3.storage import NetCDFStorage
    from wrf_processing_v3.variables.rmw_by_height import process

    request = _request(tmp_path)
    registry = build_registry(request.options, times_minutes=request.times_minutes)
    storage = NetCDFStorage(request.output_root, time_origin=request.options["time_origin"])
    center_task = Task("exp", 3, 0, "slp_center", None)
    storage.save(center_task, np.asarray([7, 7]), registry.resolve("slp_center"))
    yy, xx = np.indices((15, 15))
    dx, dy = xx - 7, yy - 7
    radius = np.hypot(dx, dy)
    sin = np.divide(dy, radius, out=np.zeros_like(radius, dtype=float), where=radius > 0)
    cos = np.divide(dx, radius, out=np.zeros_like(radius, dtype=float), where=radius > 0)

    for height, peak_radius in ((100, 2), (200, 3)):
        speed = 20.0 * np.exp(-((radius - peak_radius) ** 2) / 0.1)
        storage.save(Task("exp", 3, 0, "ua", height), -speed * sin, registry.resolve("ua"))
        storage.save(Task("exp", 3, 0, "va", height), speed * cos, registry.resolve("va"))
        task = Task("exp", 3, 0, "rmw_by_height", height)
        process(ProcessingContext(task, request, registry.resolve("rmw_by_height"), storage, registry))
        np.testing.assert_allclose(storage.load(task), [peak_radius])
        assert storage.load_metadata(task)["axis_mean_max_wind_m_s"] > 10.0
        assert storage.load_metadata(task)["center_mode"] == "slp_center"


def test_layer_center_rmw_plans_a_center_at_each_height(tmp_path):
    from wrf_processing_v3.dependency_planner import DependencyPlanner
    from wrf_processing_v3.register import build_registry
    from wrf_processing_v3.request import Task
    from wrf_processing_v3.storage import NetCDFStorage

    request = _request(tmp_path, variables=("rmw_by_layer_center",))
    registry = build_registry(request.options, times_minutes=request.times_minutes)
    spec = registry.resolve("rmw_by_layer_center")
    assert spec.storage_layout == "height_scalar"
    assert [(dep.name, dep.height_m) for dep in spec.dependencies] == [
        ("ua", "same"),
        ("va", "same"),
        ("layer_center", "same"),
    ]
    storage = NetCDFStorage(request.output_root, time_origin=request.options["time_origin"])
    tasks = {item.task for item in DependencyPlanner(registry, storage).plan(request)}
    for height in request.heights_m:
        assert Task("exp", 3, 0, "rmw_by_layer_center", height) in tasks
        assert Task("exp", 3, 0, "layer_center", height) in tasks
        assert Task("exp", 3, 0, "pressure", height) in tasks


def test_layer_center_rmw_uses_its_own_center(tmp_path):
    from wrf_processing_v3.context import ProcessingContext
    from wrf_processing_v3.register import build_registry
    from wrf_processing_v3.request import Task
    from wrf_processing_v3.storage import NetCDFStorage
    from wrf_processing_v3.variables.rmw_by_layer_center import process

    request = _request(tmp_path, variables=("rmw_by_layer_center",))
    registry = build_registry(request.options, times_minutes=request.times_minutes)
    storage = NetCDFStorage(request.output_root, time_origin=request.options["time_origin"])
    yy, xx = np.indices((21, 21))
    for height, cy, cx, peak_radius in ((100, 10, 10, 2), (200, 12, 12, 3)):
        radius = np.hypot(xx - cx, yy - cy)
        sin = np.divide(yy - cy, radius, out=np.zeros_like(radius), where=radius > 0)
        cos = np.divide(xx - cx, radius, out=np.zeros_like(radius), where=radius > 0)
        speed = 20.0 * np.exp(-((radius - peak_radius) ** 2) / 0.1)
        storage.save(Task("exp", 3, 0, "ua", height), -speed * sin, registry.resolve("ua"))
        storage.save(Task("exp", 3, 0, "va", height), speed * cos, registry.resolve("va"))
        storage.save(
            Task("exp", 3, 0, "layer_center", height),
            np.asarray([cy, cx]),
            registry.resolve("layer_center"),
        )
        task = Task("exp", 3, 0, "rmw_by_layer_center", height)
        process(ProcessingContext(task, request, registry.resolve(task.variable), storage, registry))
        np.testing.assert_allclose(storage.load(task), [peak_radius])
        assert storage.load_metadata(task)["center_mode"] == "layer_center"
