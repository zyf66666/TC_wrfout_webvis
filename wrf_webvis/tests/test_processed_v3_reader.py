from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from netCDF4 import Dataset


def _spec(layout="cartesian_3d"):
    return SimpleNamespace(
        name="theta",
        storage_layout=layout,
        units="K",
        options={"algorithm_version": "web-test"},
    )


def _write_v2(root, task, values):
    from 处理数据_v3.wrf_processing_v3.npy_storage import NpyStorage

    path = NpyStorage(root).path_for(task)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, np.asarray(values, dtype=np.float32))
    return path


def test_reader_reads_completed_v3_slice(tmp_path):
    from wrf_webvis.app.services.processed_data import ProcessedDataReader
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    task = Task("exp", 3, 60, "theta", 1000)
    storage = NetCDFStorage(
        tmp_path / "v3", time_origin="2023-09-01_00:00:00"
    )
    storage.save(task, np.full((2, 3), 3), _spec())

    reader = ProcessedDataReader(tmp_path / "v3")
    reference = reader.resolve(
        member_name="exp",
        domain=3,
        variable="theta",
        time_hour=1,
        height_m=1000,
    )

    assert reference is not None
    assert reference.backend == "netcdf4"
    assert reference.path == storage.path_for(task)
    np.testing.assert_array_equal(reader.load(reference), 3)
    assert reader.load_metadata(reference)["units"] == "K"


# 2026-11-01：v2 回退已去掉；v3 切片未完成就是缺失，即使旁边有同名 v2 .npy 也不读。
def test_reader_treats_incomplete_v3_slice_as_missing_even_with_v2_file(tmp_path):
    from wrf_webvis.app.services.processed_data import ProcessedDataReader
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    task = Task("exp", 3, 0, "theta", 100)
    _write_v2(tmp_path / "v3", task, np.full((2, 2), 2))
    storage = NetCDFStorage(
        tmp_path / "v3", time_origin="2023-09-01_00:00:00"
    )
    storage.save(task, np.full((2, 2), 3), _spec())
    with Dataset(storage.path_for(task), "r+") as dataset:
        dataset.set_auto_mask(False)
        dataset["completed"][0, 0] = 0
        dataset.sync()

    reader = ProcessedDataReader(tmp_path / "v3")
    reference = reader.resolve(
        member_name="exp",
        domain=3,
        variable="theta",
        time_hour=0,
        height_m=100,
    )

    assert reference is None
    assert reader.expected_path(task) == storage.path_for(task)


@pytest.mark.parametrize("completed", [True, False])
def test_reader_prefetches_lowest_model_level_separately_from_physical_height(tmp_path, completed):
    from wrf_webvis.app.services.processed_data import ProcessedDataReader
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    lowest = Task("exp", 3, 0, "ua", "lowest")
    physical = Task("exp", 3, 0, "ua", 1000)
    storage = NetCDFStorage(tmp_path / "v3", time_origin="2023-09-01_00:00:00")
    storage.save(lowest, np.full((2, 3), 3), _spec())
    storage.save(physical, np.full((2, 3), 30), _spec())
    if not completed:
        with Dataset(storage.path_for(lowest), "r+") as dataset:
            dataset.groups["lowest"]["completed"][0] = 0

    reader = ProcessedDataReader(tmp_path / "v3")
    reader.prefetch([lowest, physical])
    reference = reader.resolve(
        member_name="exp", domain=3, variable="ua", time_hour=0, height_m="lowest",
    )
    if completed:
        assert reference.backend == "netcdf4"
        np.testing.assert_array_equal(reader.load(reference), 3)
    else:
        assert reference is None
    reference = reader.resolve(
        member_name="exp", domain=3, variable="ua", time_hour=0, height_m=1000,
    )
    assert reference.backend == "netcdf4"
    np.testing.assert_array_equal(reader.load(reference), 30)


def test_reader_rejects_non_integral_minute_request(tmp_path):
    from wrf_webvis.app.services.processed_data import ProcessedDataReader

    reader = ProcessedDataReader(tmp_path / "v3")
    with pytest.raises(ValueError, match="整数分钟"):
        reader.resolve(
            member_name="exp",
            domain=3,
            variable="theta",
            time_hour=1 / 360,
            height_m=100,
        )


def test_data_manager_plan_reports_v3_backend_and_netcdf_file(tmp_path):
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.data_manager import DataManager
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    task = Task("exp", 3, 0, "theta", 100)
    storage = NetCDFStorage(
        tmp_path / "v3", time_origin="2023-09-01_00:00:00"
    )
    storage.save(task, np.ones((2, 2), dtype=np.float32), _spec())
    request = PlotRequest(
        member_name="exp",
        data_root=tmp_path / "v2",
        processed_root=tmp_path / "v2",
        fig_type=0,
        time={"values": [0]},
        height={"values": [100]},
        variables=[{"name": "theta", "use_polar": False}],
    )

    plan = DataManager(
        tmp_path / "v2", processed_v3_root=tmp_path / "v3"
    ).plan_for_plot(request)

    assert plan.missing_files == []
    assert len(plan.required_files) == 1
    assert plan.required_files[0].backend == "netcdf4"
    assert plan.required_files[0].path == storage.path_for(task)


def test_plot_dry_run_accepts_processed_v3_as_data_root(tmp_path):
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.plot_service import PlotService
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    v3_root = tmp_path / "processed_v3"
    task = Task("exp", 3, 0, "theta", 100)
    path = NetCDFStorage(v3_root, time_origin="2023-09-01_00:00:00").save(
        task, np.ones((2, 2), dtype=np.float32), _spec()
    )
    request = PlotRequest(
        member_name="exp",
        data_root=v3_root,
        processed_root=v3_root,
        fig_type=0,
        time={"values": [0]},
        height={"values": [100]},
        variables=[{"name": "theta", "use_polar": False}],
    )

    plan = PlotService().dry_run(request)["plan"]

    assert plan["missing_files"] == []
    assert plan["required_files"][0]["path"] == str(path)
    assert plan["required_files"][0]["backend"] == "netcdf4"


def test_v3_root_does_not_fall_back_to_v2_and_reports_v3_path(tmp_path):
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.plot_service import PlotService
    from 处理数据_v3.wrf_processing_v3.request import Task

    v3_root = tmp_path / "processed_v3"
    v2_root = tmp_path / "processed_v2"
    task = Task("exp", 3, 0, "theta", 100)
    _write_v2(v2_root, task, np.ones((2, 2)))
    request = PlotRequest(
        member_name="exp",
        data_root=v3_root,
        processed_root=v3_root,
        fig_type=0,
        time={"values": [0]},
        height={"values": [100]},
        variables=[{"name": "theta", "use_polar": False}],
    )

    plan = PlotService().dry_run(request)["plan"]

    assert len(plan["missing_files"]) == 1
    assert plan["missing_files"][0]["path"] == str(v3_root / "exp/d03/theta.nc")

    with pytest.raises(FileNotFoundError) as error:
        PlotService(tmp_path / "figures").create_plot(request)
    message = str(error.value)
    assert "theta.nc" in message
    assert "0 h" in message
    assert "100 m" in message
    assert "processed_v2" not in message


def test_plot_dry_run_accepts_v3_slp_center(tmp_path):
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.plot_service import PlotService
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    v3_root = tmp_path / "processed_v3"
    storage = NetCDFStorage(v3_root, time_origin="2023-09-01_00:00:00")
    storage.save(Task("exp", 3, 0, "theta", 100), np.ones((3, 3)), _spec())
    center_path = storage.save(
        Task("exp", 3, 0, "slp_center", None),
        np.array([0, 2], dtype=np.float32),
        _spec("vector"),
    )
    request = PlotRequest(
        member_name="exp",
        data_root=v3_root,
        processed_root=v3_root,
        fig_type=0,
        time={"values": [0]},
        height={"values": [100]},
        variables=[{"name": "theta", "use_polar": False}],
        format={"horizontal": {"center_enabled": True}},
    )

    plan = PlotService().dry_run(request)["plan"]

    assert plan["missing_files"] == []
    assert str(center_path) in [item["path"] for item in plan["required_files"]]


def test_profile_root_does_not_fall_back_and_preflight_opens_variable_once(tmp_path, monkeypatch):
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services import processed_data
    from wrf_webvis.app.services.plot_service import PlotService
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    root = tmp_path / "processed_v3" / "profiles" / "p-example"
    root.mkdir(parents=True)
    (root / "profile.json").write_text('{"id":"p-example","options":{}}')
    storage = NetCDFStorage(root, time_origin="2023-09-01_00:00:00")
    for minute in (0, 60):
        for height in (100, 200):
            storage.save(Task("exp", 3, minute, "theta", height), np.ones((2, 2)), _spec())
    opens = []
    original = processed_data.Dataset

    def tracked(path, *args, **kwargs):
        opens.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(processed_data, "Dataset", tracked)
    request = PlotRequest(
        member_name="exp", data_root=root, processed_root=root, fig_type=0,
        time={"values": [0, 1]}, height={"values": [100, 200]},
        variables=[{"name": "theta", "use_polar": False}],
    )
    plan = PlotService().dry_run(request)["plan"]
    assert plan["missing_files"] == []
    assert len(opens) == 1


def test_horizontal_center_reads_v3_vector_coordinates(tmp_path):
    from wrf_webvis.app.plotting.draw_format import resolve_center
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    v3_root = tmp_path / "processed_v3"
    NetCDFStorage(v3_root, time_origin="2023-09-01_00:00:00").save(
        Task("exp", 3, 0, "slp_center", None),
        np.array([0, 2], dtype=np.float32),
        _spec("vector"),
    )

    center = resolve_center(
        data_root=v3_root,
        member_name="exp",
        domain=3,
        times=[0],
        grid_shape=(3, 3),
        center_enabled=True,
        center_path=None,
        center_height_m=None,
        center_index=None,
    )

    assert center == (0.0, 2.0)


# 2026-11-01：旧请求里的 processed_v2 根目录自动改读同级 processed_v3，并在预检里给出提示。
def test_legacy_v2_root_redirects_to_sibling_v3_with_warning(tmp_path):
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.plot_service import PlotService
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    v3_root = tmp_path / "processed_v3"
    v2_root = tmp_path / "processed_v2"
    task = Task("exp", 3, 0, "theta", 100)
    _write_v2(v2_root, task, np.full((2, 2), 2))
    path = NetCDFStorage(v3_root, time_origin="2023-09-01_00:00:00").save(
        task, np.full((2, 2), 3, dtype=np.float32), _spec()
    )
    request = PlotRequest(
        member_name="exp",
        data_root=v2_root,
        fig_type=0,
        time={"values": [0]},
        height={"values": [100]},
        variables=[{"name": "theta", "use_polar": False}],
    )

    result = PlotService(tmp_path / "figures").dry_run(request)
    plan = result["plan"]

    assert plan["missing_files"] == []
    assert plan["required_files"][0]["path"] == str(path)
    assert any("processed_v2" in message and "processed_v3" in message for message in plan["warnings"])
