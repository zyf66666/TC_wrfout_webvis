from __future__ import annotations

import json

import numpy as np
import pytest


def imports():
    try:
        from wrf_processing_v3.comparison import compare_task
        from wrf_processing_v3.migration import migrate_tasks
        from wrf_processing_v3.npy_storage import NpyStorage
        from wrf_processing_v3.register import build_registry
        from wrf_processing_v3.request import Task
        from wrf_processing_v3.storage import NetCDFStorage
    except ImportError:
        pytest.fail("v3 migration and comparison tools have not been implemented")
    return compare_task, migrate_tasks, NpyStorage, build_registry, Task, NetCDFStorage


@pytest.mark.parametrize("heights", [(100, 1000), ("lowest",)])
def test_migration_is_lossless_preserves_source_and_skips_completed_target(tmp_path, heights):
    compare, migrate, NpyStorage, build_registry, Task, NetCDFStorage = imports()
    npy_root = tmp_path / "v2"
    nc_root = tmp_path / "v3"
    npy = NpyStorage(npy_root)
    nc = NetCDFStorage(nc_root, time_origin="2023-09-01_00:00:00")
    registry = build_registry({}, times_minutes=(0, 5))
    tasks = [
        Task("exp", 3, minute, "theta", height)
        for minute in (0, 5)
        for height in heights
    ]
    before = {}
    for index, task in enumerate(tasks):
        path = npy.path_for(task)
        path.parent.mkdir(parents=True, exist_ok=True)
        values = np.arange(12, dtype=np.float32).reshape(3, 4) + index
        values[0, 0] = np.nan
        np.save(path, values)
        before[path] = path.read_bytes()

    first = migrate(
        tasks,
        source=npy,
        target=nc,
        registry=registry,
        processing_options={},
        verify=True,
    )
    assert len(first["migrated"]) == len(tasks)
    assert first["skipped"] == []
    assert all(path.read_bytes() == payload for path, payload in before.items())
    for task in tasks:
        report = compare(task, source=npy, target=nc, expected_units="K")
        assert report["shape_match"]
        assert report["dtype_match"]
        assert report["nan_mask_match"]
        assert report["values_match"]
        assert report["units_match"]
        assert report["time_coordinate_match"]
        assert report["height_coordinate_match"]
        assert report["spatial_coordinates_match"]
        assert report["coordinates_match"]

    second = migrate(
        tasks,
        source=npy,
        target=nc,
        registry=registry,
        processing_options={},
    )
    assert second["migrated"] == []
    assert len(second["skipped"]) == len(tasks)


def test_polar_migration_writes_and_compares_radius_coordinate(tmp_path):
    compare, migrate, NpyStorage, build_registry, Task, NetCDFStorage = imports()
    options = {
        "radius_resolution_km": 1.0,
        "theta_environment_domain": 2,
        "theta_environment_ring_km": [400.0, 500.0],
    }
    registry = build_registry(options, times_minutes=(0,))
    source = NpyStorage(tmp_path / "v2")
    target = NetCDFStorage(
        tmp_path / "v3", time_origin="2023-09-01_00:00:00"
    )
    task = Task("exp", 3, 0, "theta_anomaly1_polar", 1000)
    path = source.path_for(task)
    path.parent.mkdir(parents=True)
    np.save(path, np.ones((360, 101), dtype=np.float32))
    path.with_suffix(".npy.json").write_text(
        json.dumps({"units": "K", "radius_resolution_km": 1.0}),
        encoding="utf-8",
    )

    migrate(
        [task],
        source=source,
        target=target,
        registry=registry,
        processing_options=options,
    )
    report = compare(task, source=source, target=target, expected_units="K")
    assert report["radius_match"]
    assert report["radius_count"] == 101
    assert report["radius_min_km"] == 0.0
    assert report["radius_max_km"] == 100.0
    assert report["azimuth_match"]
    assert report["coordinates_match"]


def test_migration_rejects_missing_source_without_creating_netcdf(tmp_path):
    _, migrate, NpyStorage, build_registry, Task, NetCDFStorage = imports()
    source = NpyStorage(tmp_path / "v2")
    target = NetCDFStorage(
        tmp_path / "v3", time_origin="2023-09-01_00:00:00"
    )
    task = Task("exp", 3, 0, "theta", 100)

    with pytest.raises(FileNotFoundError):
        migrate(
            [task],
            source=source,
            target=target,
            registry=build_registry({}, times_minutes=(0,)),
            processing_options={},
        )
    assert not target.path_for(task).exists()
