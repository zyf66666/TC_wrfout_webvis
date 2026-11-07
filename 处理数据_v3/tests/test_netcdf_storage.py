from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from netCDF4 import Dataset


def v3_api():
    try:
        from wrf_processing_v3.request import Task
        from wrf_processing_v3.storage import DEFAULT_HEIGHTS_M, NetCDFStorage
    except ModuleNotFoundError:
        pytest.fail("wrf_processing_v3 NetCDF storage has not been implemented")
    return Task, DEFAULT_HEIGHTS_M, NetCDFStorage


def spec(name: str, layout: str, units: str = "K", **options):
    return SimpleNamespace(
        name=name,
        storage_layout=layout,
        units=units,
        options={"algorithm_version": "test-v1", **options},
    )


def test_cartesian_3d_schema_uses_standard_heights_and_required_filters(tmp_path):
    Task, standard_heights, Storage = v3_api()
    storage = Storage(tmp_path, time_origin="2023-09-01_00:00:00")
    task = Task("exp", 3, 60, "theta", 1000)

    storage.save(task, np.ones((5, 7), dtype=np.float64), spec("theta", "cartesian_3d"))

    path = tmp_path / "exp" / "d03" / "theta.nc"
    with Dataset(path) as dataset:
        assert dataset.data_model == "NETCDF4"
        assert dataset.getncattr("time_origin") == "2023-09-01_00:00:00"
        assert dataset.getncattr("layout") == "cartesian_3d"
        assert dataset["data"].dimensions == ("record", "height", "y", "x")
        assert dataset["completed"].dimensions == ("record", "height")
        assert dataset["data"].dtype == np.dtype("float32")
        assert dataset["time_minutes"].dtype == np.dtype("int64")
        assert dataset["height_m"].dtype == np.dtype("int32")
        np.testing.assert_array_equal(dataset["height_m"][:], standard_heights)
        filters = dataset["data"].filters()
        assert filters["zlib"] is True
        assert filters["complevel"] == 2
        assert filters["shuffle"] is True
        assert filters["fletcher32"] is True
        assert dataset["data"].chunking() == [1, 1, 5, 7]
        assert dataset["completed"].chunking() == [1, 1]
        assert dataset["metadata_json"].chunking() == [1, 1]
        height_index = list(dataset["height_m"][:]).index(1000)
        assert int(dataset["completed"][0, height_index]) == 1


def test_coordinate_lookup_reads_out_of_order_records_in_sorted_time_order(tmp_path):
    Task, _, Storage = v3_api()
    storage = Storage(tmp_path, time_origin="2023-09-01_00:00:00")
    theta = spec("theta", "cartesian_3d")
    for minute, value in [(120, 12), (0, 0), (60, 6)]:
        storage.save(
            Task("exp", 3, minute, "theta", 100),
            np.full((2, 3), value, dtype=np.float32),
            theta,
        )

    assert storage.list_time_minutes("exp", 3, "theta") == [0, 60, 120]
    np.testing.assert_array_equal(
        storage.load(Task("exp", 3, 60, "theta", 100)),
        np.full((2, 3), 6, dtype=np.float32),
    )


def test_nonstandard_integer_height_is_appended_and_addressed_by_value(tmp_path):
    Task, standard_heights, Storage = v3_api()
    storage = Storage(tmp_path, time_origin="2023-09-01_00:00:00")
    task = Task("exp", 3, 0, "theta", 1100)

    storage.save(task, np.ones((2, 2), dtype=np.float32), spec("theta", "cartesian_3d"))

    heights = storage.list_heights_m("exp", 3, "theta")
    assert heights == sorted([*standard_heights, 1100])
    np.testing.assert_array_equal(storage.load(task), np.ones((2, 2), dtype=np.float32))


def test_incomplete_slice_is_not_available_and_can_be_resumed(tmp_path):
    Task, _, Storage = v3_api()
    storage = Storage(tmp_path, time_origin="2023-09-01_00:00:00")
    task = Task("exp", 3, 0, "theta", 100)
    theta = spec("theta", "cartesian_3d")
    storage.save(task, np.ones((2, 2), dtype=np.float32), theta)
    path = storage.path_for(task)
    with Dataset(path, "r+") as dataset:
        dataset["completed"][0, 0] = 0
        dataset.sync()

    assert storage.exists(task) is False
    with pytest.raises(KeyError, match="未完成"):
        storage.load(task)

    storage.save(task, np.full((2, 2), 9, dtype=np.float32), theta)
    assert storage.exists(task) is True
    np.testing.assert_array_equal(storage.load(task), np.full((2, 2), 9, dtype=np.float32))


def test_existing_reads_completed_slices_for_a_whole_variable_batch(tmp_path):
    Task, _, Storage = v3_api()
    storage = Storage(tmp_path, time_origin="2023-09-01_00:00:00")
    theta = spec("theta", "cartesian_3d")
    complete = Task("exp", 3, 0, "theta", 100)
    incomplete = Task("exp", 3, 0, "theta", 200)
    absent = Task("exp", 3, 60, "theta", 100)
    storage.save(complete, np.ones((2, 2), dtype=np.float32), theta)
    storage.save(incomplete, np.ones((2, 2), dtype=np.float32), theta)
    with Dataset(storage.path_for(incomplete), "r+") as dataset:
        dataset["completed"][0, 1] = 0
        dataset.sync()

    assert storage.existing([complete, incomplete, absent]) == {complete}


def test_duplicate_time_coordinate_is_rejected_as_corrupt_schema(tmp_path):
    Task, _, Storage = v3_api()
    storage = Storage(tmp_path, time_origin="2023-09-01_00:00:00")
    task = Task("exp", 3, 0, "theta", 100)
    storage.save(task, np.ones((2, 2), dtype=np.float32), spec("theta", "cartesian_3d"))
    with Dataset(storage.path_for(task), "r+") as dataset:
        dataset["time_minutes"][1] = 0
        dataset.sync()

    with pytest.raises(ValueError, match="重复.*time_minutes"):
        storage.list_time_minutes("exp", 3, "theta")


def test_polar_schema_writes_explicit_azimuth_and_radius_coordinates(tmp_path):
    Task, _, Storage = v3_api()
    storage = Storage(tmp_path, time_origin="2023-09-01_00:00:00")
    task = Task("exp", 3, 0, "theta_anomaly1_polar", 1000)
    polar = spec(
        "theta_anomaly1_polar",
        "polar_3d",
        radius_resolution_km=1.0,
    )

    storage.save(task, np.ones((360, 101), dtype=np.float32), polar)

    with Dataset(storage.path_for(task)) as dataset:
        assert dataset["data"].dimensions == (
            "record",
            "height",
            "azimuth",
            "radius",
        )
        np.testing.assert_array_equal(dataset["azimuth_deg"][:], np.arange(360))
        np.testing.assert_allclose(dataset["radius_km"][:], np.arange(101))
        assert dataset["radius_km"].getncattr("units") == "km"


def test_scalar_schema_uses_only_record_dimension(tmp_path):
    Task, _, Storage = v3_api()
    storage = Storage(tmp_path, time_origin="2023-09-01_00:00:00")
    task = Task("exp", 3, 0, "rmw", None)

    storage.save(task, np.asarray([42.5]), spec("rmw", "scalar", units="km"))

    with Dataset(storage.path_for(task)) as dataset:
        assert dataset["data"].dimensions == ("record",)
        assert dataset["completed"].dimensions == ("record",)
    assert storage.load(task).shape == (1,)
    np.testing.assert_allclose(storage.load(task), [42.5])


@pytest.mark.parametrize(
    "layout,values,options",
    [
        ("cartesian_3d", np.ones((2, 3)), {}),
        ("polar_3d", np.ones((360, 3)), {"radius_resolution_km": 1.0}),
        ("height_vector", np.asarray([1.0, 2.0]), {}),
        ("height_scalar", np.asarray([1.0]), {}),
    ],
)
def test_lowest_model_level_coexists_with_physical_heights(tmp_path, layout, values, options):
    Task, standard_heights, Storage = v3_api()
    storage = Storage(tmp_path, time_origin="2023-09-01_00:00:00")
    variable_spec = spec("field", layout, **options)
    lowest = Task("exp", 3, 0, "field", "lowest")
    physical = Task("exp", 3, 0, "field", 1000)
    later_physical = Task("exp", 3, 60, "field", 1000)
    later_lowest = Task("exp", 3, 60, "field", "lowest")
    missing_height = Task("exp", 3, 0, "field", 100)

    storage.save(lowest, values, variable_spec, metadata={"level": "lowest"})
    storage.save(physical, values * 10, variable_spec, metadata={"level": "1000m"})
    storage.save(later_physical, values * 20, variable_spec)

    assert storage.path_for(lowest) == storage.path_for(physical)
    np.testing.assert_array_equal(storage.load(lowest), values)
    np.testing.assert_array_equal(storage.load(physical), values * 10)
    assert storage.load_metadata(lowest)["level"] == "lowest"
    assert storage.load_metadata(physical)["level"] == "1000m"
    assert storage.fingerprint(lowest)["slice"]["completed"] is True
    assert storage.existing([lowest, physical, later_physical, later_lowest, missing_height]) == {
        lowest, physical, later_physical
    }
    assert storage.exists(later_lowest) is False
    assert storage.load_metadata(later_lowest) == {}
    assert storage.list_heights_m("exp", 3, "field") == list(standard_heights)
    with Dataset(storage.path_for(lowest)) as dataset:
        group = dataset.groups["lowest"]
        assert "height" not in group["data"].dimensions
        assert group["completed"].dimensions == ("record",)
        assert group.model_level_index == 0
        assert group["data"].dtype == np.dtype("float32")
        assert "height_m" not in group["data"].coordinates.split()


def test_lowest_model_level_can_be_added_to_existing_file_and_resumed(tmp_path):
    Task, _, Storage = v3_api()
    storage = Storage(tmp_path, time_origin="2023-09-01_00:00:00")
    ua = spec("ua", "cartesian_3d", units="m s-1")
    physical = Task("exp", 3, 0, "ua", 1000)
    lowest = Task("exp", 3, 0, "ua", "lowest")
    physical_values = np.full((2, 3), 30, dtype=np.float32)
    storage.save(physical, physical_values, ua)
    assert storage.exists(lowest) is False
    assert storage.existing([lowest, physical]) == {physical}
    assert storage.load_metadata(lowest) == {}
    with pytest.raises(KeyError):
        storage.load(lowest)

    storage.save(lowest, np.ones((2, 3)), ua)
    with Dataset(storage.path_for(lowest), "r+") as dataset:
        dataset.groups["lowest"]["completed"][0] = 0

    assert storage.exists(lowest) is False
    assert storage.existing([lowest, physical]) == {physical}
    assert storage.load_metadata(lowest) == {}
    with pytest.raises(KeyError, match="未完成"):
        storage.load(lowest)
    storage.save(lowest, np.full((2, 3), 3), ua)
    assert storage.exists(lowest) is True
    np.testing.assert_array_equal(storage.load(lowest), np.full((2, 3), 3))
    np.testing.assert_array_equal(storage.load(physical), physical_values)
