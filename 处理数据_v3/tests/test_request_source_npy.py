from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset


def test_request_is_minute_native_and_builds_integer_tasks(tmp_path):
    try:
        from wrf_processing_v3.request import Request
    except ImportError:
        pytest.fail("minute-native Request has not been implemented")

    request = Request(
        experiments=("exp",),
        domains=(3,),
        times_minutes=(0, 5, 60),
        heights_m=(100, 1000),
        variables=("theta",),
        source_root=tmp_path / "source",
        output_root=tmp_path / "out",
    )

    assert [task.time_minutes for task in request.tasks] == [0, 0, 5, 5, 60, 60]
    assert [task.height_m for task in request.tasks] == [100, 1000] * 3
    with pytest.raises(ValueError, match="整数分钟"):
        Request(
            experiments=("exp",),
            domains=(3,),
            times_minutes=(1.5,),
            heights_m=(100,),
            variables=("theta",),
            source_root=tmp_path,
        )


def _write_wrf(path: Path, times: list[str], **attrs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Dataset(path, "w") as dataset:
        dataset.createDimension("Time", len(times))
        dataset.createDimension("DateStrLen", 19)
        variable = dataset.createVariable("Times", "S1", ("Time", "DateStrLen"))
        variable[:] = np.asarray([list(value) for value in times], dtype="S1")
        for name, value in attrs.items():
            setattr(dataset, name, value)


def test_source_index_prefers_start_date_and_returns_integer_minutes(tmp_path):
    from wrf_processing_v3.request import Request
    try:
        from wrf_processing_v3.source_index import SourceIndex
    except ImportError:
        pytest.fail("minute-native SourceIndex has not been implemented")

    source = tmp_path / "source" / "exp" / "wrfout_d03_sample"
    _write_wrf(
        source,
        ["2023-09-01_00:00:00", "2023-09-01_00:05:00"],
        START_DATE="2023-09-01_00:00:00",
        SIMULATION_START_DATE="2023-08-31_23:00:00",
    )
    request = Request(
        ("exp",),
        (3,),
        (0, 5),
        (100,),
        ("theta",),
        tmp_path / "source",
        options={"simulation_start": "2023-08-31_23:00:00"},
    )

    index = SourceIndex(request)
    assert index.time_origin("exp", 3) == "2023-09-01_00:00:00"
    assert index.resolve("exp", 3, 0) == (source, 0)
    assert index.resolve("exp", 3, 5) == (source, 1)


def test_source_index_rejects_non_integral_minutes_and_duplicate_source_time(tmp_path):
    from wrf_processing_v3.request import Request
    from wrf_processing_v3.source_index import SourceIndex

    root = tmp_path / "source"
    _write_wrf(
        root / "exp" / "wrfout_d03_a",
        ["2023-09-01_00:00:30"],
        START_DATE="2023-09-01_00:00:00",
    )
    request = Request(("exp",), (3,), (0,), (100,), ("theta",), root)
    with pytest.raises(ValueError, match="整数分钟"):
        SourceIndex(request).resolve("exp", 3, 0)

    (root / "exp" / "wrfout_d03_a").unlink()
    for suffix in ("a", "b"):
        _write_wrf(
            root / "exp" / f"wrfout_d03_{suffix}",
            ["2023-09-01_00:00:00"],
            START_DATE="2023-09-01_00:00:00",
        )
    with pytest.raises(ValueError, match="重复源时间"):
        SourceIndex(request).resolve("exp", 3, 0)


def test_npy_storage_reads_v2_hour_minute_layout_without_writing(tmp_path):
    from wrf_processing_v3.request import Task
    try:
        from wrf_processing_v3.npy_storage import NpyStorage
    except ImportError:
        pytest.fail("NpyStorage compatibility reader has not been implemented")

    data_path = tmp_path / "exp" / "d03" / "theta" / "0h5min" / "1000m.npy"
    data_path.parent.mkdir(parents=True)
    np.save(data_path, np.arange(6, dtype=np.float32).reshape(2, 3))
    Path(f"{data_path}.json").write_text(
        json.dumps({"units": "K", "radius_resolution_km": 1.0}),
        encoding="utf-8",
    )
    task = Task("exp", 3, 5, "theta", 1000)
    storage = NpyStorage(tmp_path)

    assert storage.path_for(task) == data_path
    assert storage.exists(task)
    np.testing.assert_array_equal(
        storage.load(task), np.arange(6, dtype=np.float32).reshape(2, 3)
    )
    assert storage.load_metadata(task)["units"] == "K"
    assert list(tmp_path.rglob("*.nc")) == []


def test_npy_storage_reads_surface_and_scalar_paths(tmp_path):
    from wrf_processing_v3.npy_storage import NpyStorage
    from wrf_processing_v3.request import Task

    storage = NpyStorage(tmp_path)
    for variable, value in [("slp", np.ones((2, 2))), ("rmw", np.asarray([42.0]))]:
        task = Task("exp", 3, 120, variable, None)
        path = storage.path_for(task)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, value)
        np.testing.assert_array_equal(storage.load(task), value)
