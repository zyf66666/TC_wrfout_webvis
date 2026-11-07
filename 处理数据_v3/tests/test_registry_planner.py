from __future__ import annotations

import numpy as np
import pytest


def imports():
    try:
        from wrf_processing_v3.dependency_planner import DependencyPlanner
        from wrf_processing_v3.register import build_registry
        from wrf_processing_v3.request import Request, Task
        from wrf_processing_v3.storage import NetCDFStorage
    except ImportError:
        pytest.fail("v3 registry and dependency planner have not been implemented")
    return DependencyPlanner, build_registry, Request, Task, NetCDFStorage


OPTIONS = {
    "radius_km_by_domain": {2: 800.0, 3: 100.0},
    "radius_resolution_km": 1.0,
    "theta_environment_domain": 2,
    "theta_environment_ring_km": [400.0, 500.0],
}


def request(tmp_path, *, variables=("theta_anomaly1_polar",), options=None):
    _, _, Request, _, _ = imports()
    return Request(
        experiments=("exp",),
        domains=(3,),
        times_minutes=(0,),
        heights_m=(100, 1000, 15000),
        variables=variables,
        source_root=tmp_path / "source",
        output_root=tmp_path / "out",
        options={**OPTIONS, **(options or {})},
    )


def test_registry_preserves_cross_domain_and_polar_variable_structure():
    _, build_registry, _, _, _ = imports()
    registry = build_registry(OPTIONS, times_minutes=(0, 60, 120))

    theta = registry.resolve("theta")
    anomaly = registry.resolve("theta_anomaly")
    polar = registry.resolve("theta_anomaly1_polar")
    storm_motion = registry.resolve("storm_motion")

    assert theta.storage_layout == "cartesian_3d"
    assert anomaly.storage_layout == "cartesian_3d"
    assert [(item.name, item.domain) for item in anomaly.dependencies] == [
        ("theta", None),
        ("theta1_polar", 2),
    ]
    assert polar.storage_layout == "polar_3d"
    assert [item.name for item in polar.dependencies] == [
        "theta_anomaly",
        "layer_center",
    ]
    assert str(polar.handler).startswith("wrf_processing_v3.variables.")
    assert storm_motion.options["times"] == [0, 60, 120]


def test_planner_expands_cross_domain_dependencies_and_vertical_center_chain(tmp_path):
    Planner, build_registry, _, _, Storage = imports()
    req = request(tmp_path, options={"center_options_by_domain": {
        2: {"layer_mode": "vertical"}, 3: {"layer_mode": "vertical"},
    }})
    registry = build_registry(req.options, times_minutes=req.times_minutes)
    storage = Storage(req.output_root, time_origin="2023-09-01_00:00:00")

    plan = Planner(registry, storage).plan(req)
    tasks = [item.task for item in plan]
    target = next(
        item
        for item in plan
        if item.task.variable == "theta_anomaly1_polar"
        and item.task.height_m == 15000
    )
    assert target.explicit
    assert target.should_compute
    assert any(
        task.domain == 2 and task.variable == "theta1_polar" and task.height_m == 15000
        for task in tasks
    )
    assert any(
        task.domain == 3 and task.variable == "theta" and task.height_m == 100
        for task in tasks
    )

    layer_1000 = next(
        item
        for item in plan
        if item.task.domain == 3
        and item.task.variable == "layer_center"
        and item.task.height_m == 1000
    )
    assert any(
        dependency.name == "layer_center" and dependency.height_m == 900
        for dependency in layer_1000.spec.dependencies
    )
    layer_15000 = next(
        item
        for item in plan
        if item.task.domain == 3
        and item.task.variable == "layer_center"
        and item.task.height_m == 15000
    )
    assert any(
        dependency.name == "layer_center" and dependency.height_m == 14750
        for dependency in layer_15000.spec.dependencies
    )
    assert any(
        task.domain == 3
        and task.variable == "layer_center"
        and task.height_m == 1250
        for task in tasks
    )
    positions = {task: index for index, task in enumerate(tasks)}
    previous = next(
        task
        for task in tasks
        if task.domain == 3
        and task.variable == "layer_center"
        and task.height_m == 900
    )
    assert positions[previous] < positions[layer_1000.task]


def test_planner_batches_completed_slice_lookup(tmp_path):
    Planner, build_registry, _, _, _ = imports()
    req = request(tmp_path, variables=("theta",))
    registry = build_registry(req.options, times_minutes=req.times_minutes)

    class BatchOnlyStorage:
        def __init__(self):
            self.calls = []

        def existing(self, tasks):
            batch = frozenset(tasks)
            self.calls.append(batch)
            return set()

        def exists(self, task):
            raise AssertionError("规划器不应逐切片打开 NetCDF 文件")

    storage = BatchOnlyStorage()
    plan = Planner(registry, storage).plan(req)

    assert plan
    assert len(storage.calls) == 1
    assert {task for task in storage.calls[0] if task.variable == "theta"} == {
        item.task for item in plan if item.task.variable == "theta"
    }


def test_completed_slice_is_reused_and_force_slice_only_refreshes_match(tmp_path):
    Planner, build_registry, Request, Task, Storage = imports()
    base = request(tmp_path, variables=("theta",))
    registry = build_registry(base.options, times_minutes=base.times_minutes)
    storage = Storage(base.output_root, time_origin="2023-09-01_00:00:00")
    theta_spec = registry.resolve("theta")
    for height in base.heights_m:
        storage.save(
            Task("exp", 3, 0, "theta", height),
            np.ones((2, 2), dtype=np.float32),
            theta_spec,
        )

    reused = Planner(registry, storage).plan(base)
    assert all(not item.should_compute for item in reused)

    forced = Request(
        experiments=base.experiments,
        domains=base.domains,
        times_minutes=base.times_minutes,
        heights_m=base.heights_m,
        variables=base.variables,
        source_root=base.source_root,
        output_root=base.output_root,
        options={
            **base.options,
            "force_slices": [
                {
                    "variable": "theta",
                    "domain": 3,
                    "time_minutes": 0,
                    "height_m": 1000,
                }
            ],
        },
    )
    result = Planner(registry, storage).plan(forced)
    assert [
        item.task.height_m for item in result if item.should_compute
    ] == [1000]


def test_force_variable_refreshes_all_requested_slices_without_force_flag(tmp_path):
    Planner, build_registry, Request, Task, Storage = imports()
    base = request(tmp_path, variables=("theta",))
    registry = build_registry(base.options, times_minutes=base.times_minutes)
    storage = Storage(base.output_root, time_origin="2023-09-01_00:00:00")
    for height in base.heights_m:
        storage.save(
            Task("exp", 3, 0, "theta", height),
            np.zeros((2, 2), dtype=np.float32),
            registry.resolve("theta"),
        )
    forced = Request(
        experiments=base.experiments,
        domains=base.domains,
        times_minutes=base.times_minutes,
        heights_m=base.heights_m,
        variables=base.variables,
        source_root=base.source_root,
        output_root=base.output_root,
        options={**base.options, "force_variables": ["theta"]},
    )

    result = Planner(registry, storage).plan(forced)
    assert [item.task.height_m for item in result if item.should_compute] == [
        100,
        1000,
        15000,
    ]
