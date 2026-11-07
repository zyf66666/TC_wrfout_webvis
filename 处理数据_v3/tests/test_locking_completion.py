from __future__ import annotations

import multiprocessing
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from netCDF4 import Dataset


def _spec():
    return SimpleNamespace(
        name="theta",
        storage_layout="cartesian_3d",
        units="K",
        options={"algorithm_version": "lock-test"},
    )


def _write_worker(root: str, minute: int, result_queue) -> None:
    try:
        from wrf_processing_v3.request import Task
        from wrf_processing_v3.storage import NetCDFStorage

        storage = NetCDFStorage(root, time_origin="2023-09-01_00:00:00")
        storage.save(
            Task("exp", 3, minute, "theta", 100),
            np.full((16, 16), minute, dtype=np.float32),
            _spec(),
        )
        result_queue.put(None)
    except Exception as error:  # pragma: no cover - returned to parent
        result_queue.put(repr(error))


def test_interruption_after_data_write_leaves_completed_zero_and_resumes(
    tmp_path, monkeypatch
):
    from wrf_processing_v3.request import Task
    from wrf_processing_v3.storage import NetCDFStorage

    storage = NetCDFStorage(tmp_path, time_origin="2023-09-01_00:00:00")
    task = Task("exp", 3, 0, "theta", 100)
    original = NetCDFStorage._write_data

    def interrupted(dataset, layout, selection, values):
        original(dataset, layout, selection, values)
        dataset.sync()
        raise RuntimeError("simulated crash after data flush")

    monkeypatch.setattr(NetCDFStorage, "_write_data", staticmethod(interrupted))
    with pytest.raises(RuntimeError, match="simulated crash"):
        storage.save(task, np.ones((8, 8), dtype=np.float32), _spec())

    with Dataset(storage.path_for(task)) as dataset:
        dataset.set_auto_mask(False)
        height_index = list(dataset["height_m"][:]).index(100)
        assert int(dataset["completed"][0, height_index]) == 0
    assert storage.exists(task) is False

    monkeypatch.setattr(NetCDFStorage, "_write_data", staticmethod(original))
    storage.save(task, np.full((8, 8), 7, dtype=np.float32), _spec())
    assert storage.exists(task)
    np.testing.assert_array_equal(storage.load(task), 7)


def test_multiple_processes_serialize_writes_to_one_variable_file(tmp_path):
    from wrf_processing_v3.storage import NetCDFStorage

    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    minutes = [120, 0, 60, 5]
    processes = [
        context.Process(
            target=_write_worker,
            args=(str(tmp_path), minute, result_queue),
        )
        for minute in minutes
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    errors = [result_queue.get(timeout=2) for _ in processes]
    assert errors == [None] * len(processes)

    storage = NetCDFStorage(tmp_path, time_origin="2023-09-01_00:00:00")
    assert storage.list_time_minutes("exp", 3, "theta") == [0, 5, 60, 120]
    path = Path(tmp_path) / "exp" / "d03" / "theta.nc"
    with Dataset(path) as dataset:
        assert len(dataset.dimensions["record"]) == 4


def test_large_logical_file_still_reads_only_one_two_dimensional_slice(tmp_path):
    from wrf_processing_v3.request import Task
    from wrf_processing_v3.storage import NetCDFStorage

    storage = NetCDFStorage(tmp_path, time_origin="2023-09-01_00:00:00")
    task = Task("exp", 3, 0, "theta", 100)
    expected = np.arange(128 * 128, dtype=np.float32).reshape(128, 128)
    storage.save(task, expected, _spec())
    with Dataset(storage.path_for(task), "r+") as dataset:
        dataset.set_auto_mask(False)
        dataset["time_minutes"][:1000] = np.arange(1000, dtype=np.int64)
        dataset.sync()
        assert dataset["data"].shape == (1000, 66, 128, 128)
        assert np.prod(dataset["data"].shape) * 4 > 4_000_000_000

    loaded = storage.load(task)
    assert loaded.shape == (128, 128)
    assert loaded.nbytes == expected.nbytes
    np.testing.assert_array_equal(loaded, expected)
