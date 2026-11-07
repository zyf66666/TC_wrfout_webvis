from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from wrf_webvis.app.plotting.draw_variables import (
    load_per_height_stack,
    reduce_radius_height_files,
)
from 处理数据_v3.wrf_processing_v3.npy_storage import NpyStorage
from 处理数据_v3.wrf_processing_v3.request import Task
from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage


def _spec(name: str, layout: str):
    return SimpleNamespace(
        name=name,
        storage_layout=layout,
        units="K",
        options={"algorithm_version": "web-draw-test", "radius_resolution_km": 1.0},
    )


def _write_v2(root, task: Task, values: np.ndarray) -> None:
    path = NpyStorage(root).path_for(task)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, np.asarray(values, dtype=np.float32))


# 2026-11-01：v2 回退已去掉。逐高度叠加只认 v3；某一高度只有 v2 .npy 时视为缺失（返回 None），不混读。
def test_per_height_stack_reads_v3_only_and_ignores_v2_slices(tmp_path):
    v2_root = tmp_path / "processed_v2"
    v3_root = tmp_path / "processed_v3"
    storage = NetCDFStorage(v3_root, time_origin="2023-09-01_00:00:00")
    storage.save(Task("member", 3, 0, "theta", 100), np.full((2, 3), 3), _spec("theta", "cartesian_3d"))
    storage.save(Task("member", 3, 0, "theta", 500), np.full((2, 3), 5), _spec("theta", "cartesian_3d"))
    _write_v2(v2_root, Task("member", 3, 0, "theta", 1000), np.full((2, 3), 2))

    kwargs = dict(member_name="member", domain=3, variable="theta", time=0, polar=False, polar_dir_style="same")
    # 旧 v2 根目录改指同级 processed_v3。
    result = load_per_height_stack(data_root=v2_root, heights_m=[100, 500], **kwargs)
    assert result is not None
    values, info = result
    np.testing.assert_array_equal(values[0], 3)
    np.testing.assert_array_equal(values[1], 5)
    assert info["backends"] == ["netcdf4", "netcdf4"]
    assert info["shape"] == (2, 2, 3)

    assert load_per_height_stack(data_root=v3_root, heights_m=[100, 1000], **kwargs) is None


def test_radius_height_streaming_reducer_reads_v3_polar_slices(tmp_path):
    v2_root = tmp_path / "processed_v2"
    storage = NetCDFStorage(
        tmp_path / "processed_v3", time_origin="2023-09-01_00:00:00"
    )
    variable = "theta_anomaly1_polar"
    storage.save(
        Task("member", 3, 60, variable, 100),
        np.full((4, 5), 10),
        _spec(variable, "polar_3d"),
    )
    storage.save(
        Task("member", 3, 60, variable, 1000),
        np.full((4, 5), 20),
        _spec(variable, "polar_3d"),
    )

    result = reduce_radius_height_files(
        data_root=v2_root,
        member_name="member",
        domain=3,
        variable="theta_anomaly1",
        times=[1],
        heights_m=[100, 1000],
        angle=[0, 3],
        radius_indices=[0, 1, 2, 3, 4],
        radius_mode="list",
        radius_resolution_km=1.0,
        polar_dir_style="auto",
    )

    assert result is not None
    values, radius, heights, infos = result
    np.testing.assert_array_equal(values, [[10] * 5, [20] * 5])
    np.testing.assert_array_equal(radius, [0, 1, 2, 3, 4])
    np.testing.assert_array_equal(heights, [100, 1000])
    assert infos[0]["backends"] == ["netcdf4", "netcdf4"]
