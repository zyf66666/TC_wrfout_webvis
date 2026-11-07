"""完成情况索引（2026-11-03）：索引与 NetCDF 一致时代替逐个读完成标记；文件被改动后自动失效。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from wrf_processing_v3 import completion_index


# ---- 纯逻辑：用假的 dataset，不需要 netCDF4 ----

class _Variable:
    def __init__(self, values):
        self.values = np.asarray(values, dtype=object if np.asarray(values).dtype.kind in "OUS" else None)

    def __getitem__(self, key):
        return self.values[key]

    @property
    def ndim(self):
        return self.values.ndim

    @property
    def shape(self):
        return self.values.shape


class _FakeDataset:
    def __init__(self, times, heights, completed, metadata, lowest=None, attrs=None):
        self.variables = {
            "time_minutes": _Variable(times),
            "completed": _Variable(np.asarray(completed, dtype=np.uint8)),
            "metadata_json": _Variable(np.asarray(metadata, dtype=object)),
        }
        if heights is not None:
            self.variables["height_m"] = _Variable(heights)
        self.groups = {}
        if lowest is not None:
            self.groups["lowest"] = _Group({
                "completed": _Variable(np.asarray(lowest[0], dtype=np.uint8)),
                "metadata_json": _Variable(np.asarray(lowest[1], dtype=object)),
            })
        self._attrs = attrs or {"schema_version": "3.0", "experiment": "exp", "domain": np.int32(3), "variable": "v",
                                "layout": "polar_3d", "time_origin": "2023-09-01_00:00:00", "algorithm_version": "v2.1"}

    def __getitem__(self, name):
        return self.variables[name]

    def ncattrs(self):
        return list(self._attrs)

    def getncattr(self, name):
        return self._attrs[name]


class _Group:
    def __init__(self, variables):
        self.variables = variables

    def __getitem__(self, name):
        return self.variables[name]


def _flags(dataset):
    return np.asarray(dataset["completed"][:])


def _meta(radius=300.0, scheme="base", algorithm="v2.1"):
    options = {"radius_km_by_domain": {"3": radius}, "radius_resolution_km": 1.0, "center_scheme": scheme}
    return json.dumps({"request_options": options, "algorithm_version": algorithm})


def test_build_lookup_and_profiles_from_dataset():
    metadata = np.full((2, 3), "", dtype=object)
    metadata[0, 0] = _meta()
    metadata[1, 2] = _meta()
    dataset = _FakeDataset([0, 60], [1000, 2000, 3000], [[1, 0, 0], [0, 0, 1]], metadata,
                           lowest=([1, 0], [_meta(), ""]))
    index = completion_index.build(dataset, _flags)
    assert index["attrs"]["domain"] == 3 and isinstance(index["attrs"]["domain"], int)
    task = lambda t, h: SimpleNamespace(time_minutes=t, height_m=h)  # noqa: E731
    assert completion_index.lookup(index, task(0, 1000))
    assert not completion_index.lookup(index, task(0, 2000))
    assert completion_index.lookup(index, task(60, 3000))
    assert not completion_index.lookup(index, task(120, 1000))   # 没有这个时次
    assert not completion_index.lookup(index, task(0, 4000))     # 没有这个高度
    assert completion_index.lookup(index, task(0, "lowest"))
    assert not completion_index.lookup(index, task(60, "lowest"))
    profiles = list(completion_index.completed_profiles(index))
    assert len(profiles) == 1 and profiles[0]["science"]["radius_km_by_domain"] == {"3": 300.0}
    assert profiles[0]["center_scheme"] == "base" and profiles[0]["algorithm"] == "v2.1"


def test_invalid_metadata_is_flagged():
    dataset = _FakeDataset([0], None, [1], ["not json"])
    index = completion_index.build(dataset, _flags)
    assert list(completion_index.completed_profiles(index)) == [{"invalid": True}]


def test_updated_appends_times_and_heights_and_sets_the_slice():
    metadata = np.full((1, 2), "", dtype=object)
    metadata[0, 0] = _meta()
    index = completion_index.build(_FakeDataset([0], [1000, 2000], [[1, 0]], metadata), _flags)
    task = SimpleNamespace(time_minutes=60, height_m=3000)
    new = completion_index.updated(index, [0, 60], [1000, 2000, 3000], task, json.loads(_meta(radius=120.0)))
    assert new["completed"] == [[1, 0, 0], [0, 0, 1]]
    assert len(list(completion_index.completed_profiles(new))) == 2
    assert index["completed"] == [[1, 0]]  # 原索引不被改动
    low = completion_index.updated(new, [0, 60], [1000, 2000, 3000], SimpleNamespace(time_minutes=0, height_m="lowest"), json.loads(_meta()))
    assert low["lowest"] == {"completed": [1, 0], "profile": [0, -1]}
    # 坐标不是“末尾追加”时放弃增量，交给整体重建
    assert completion_index.updated(index, [60, 0], [1000, 2000], task, {}) is None
    assert completion_index.updated(index, [0], None, task, {}) is None


def test_scalar_index_update():
    index = completion_index.build(_FakeDataset([], None, np.zeros(0), np.asarray([], dtype=object)), _flags)
    new = completion_index.updated(index, [5], None, SimpleNamespace(time_minutes=5, height_m=None), json.loads(_meta()))
    assert new["completed"] == [1] and completion_index.lookup(new, SimpleNamespace(time_minutes=5, height_m=None))


# ---- 与真实 NetCDF 存储的整合（需要 netCDF4） ----

def _storage(tmp_path):
    pytest.importorskip("netCDF4")
    from wrf_processing_v3.storage import NetCDFStorage

    return NetCDFStorage(tmp_path / "out", time_origin="2023-09-01_00:00:00")


def _spec(layout, radius=300.0):
    return SimpleNamespace(storage_layout=layout, units="K", options={"algorithm_version": "v2.1"}), {
        "request_options": {"radius_km_by_domain": {"3": radius}, "radius_resolution_km": 1.0},
        "radius_resolution_km": 1.0,
    }


def test_save_keeps_index_in_sync_and_existing_does_not_open_netcdf(tmp_path, monkeypatch):
    from wrf_processing_v3 import storage as storage_module
    from wrf_processing_v3.request import Task

    storage = _storage(tmp_path)
    spec, meta = _spec("polar_3d")
    saved = [Task("exp", 3, t, "v1_polar", h) for t in (0, 60) for h in (1000, 2000)]
    for task in saved[:3]:
        storage.save(task, np.ones((4, 5)), spec, metadata=meta)
    path = storage.path_for(saved[0])
    assert completion_index.index_path(path).is_file()
    index = completion_index.load_valid(path)
    assert index is not None and index["times"] == [0, 60]

    def refuse(*args, **kwargs):
        raise AssertionError("有有效索引时不应打开 NetCDF")

    monkeypatch.setattr(storage_module, "Dataset", refuse)
    assert storage.existing(saved) == set(saved[:3])
    assert storage.exists(saved[0]) and not storage.exists(saved[3])


def test_index_is_ignored_after_external_change_and_rebuilt(tmp_path):
    from netCDF4 import Dataset

    from wrf_processing_v3.request import Task

    storage = _storage(tmp_path)
    spec, meta = _spec("scalar")
    tasks = [Task("exp", 3, t, "rmw", None) for t in (0, 5, 10)]
    for task in tasks:
        storage.save(task, np.ones(1), spec, metadata=meta)
    path = storage.path_for(tasks[0])
    with Dataset(path, "r+") as dataset:   # 绕过 storage 直接把 5 min 标成未完成
        dataset["completed"][1] = 0
    assert completion_index.load_valid(path) is None
    assert storage.existing(tasks) == {tasks[0], tasks[2]}
    assert completion_index.load_valid(path) is not None  # 已按文件现状重建


def test_legacy_file_without_index_gets_one_and_compatibility_uses_it(tmp_path, monkeypatch):
    from wrf_processing_v3 import cache_compatibility
    from wrf_processing_v3.request import Task

    storage = _storage(tmp_path)
    spec, meta = _spec("polar_2d")
    task = Task("exp", 3, 0, "mdbz_polar", None)
    storage.save(task, np.ones((4, 5)), spec, metadata=meta)
    path = storage.path_for(task)
    completion_index.index_path(path).unlink()  # 模拟改动前写的旧文件
    completion_index._MEMORY.clear()
    assert storage.existing([task]) == {task}
    assert completion_index.index_path(path).is_file()

    item = SimpleNamespace(task=task, spec=SimpleNamespace(options={"algorithm_version": "v2.1"}))
    cache_compatibility._COMPATIBILITY_CACHE.clear()
    cache_compatibility.check_cache_compatibility([item], storage, {"radius_km_by_domain": {"3": 300.0}, "radius_resolution_km": 1.0})
    cache_compatibility._COMPATIBILITY_CACHE.clear()
    with pytest.raises(ValueError, match="radius_km_by_domain 参数不一致"):
        cache_compatibility.check_cache_compatibility([item], storage, {"radius_km_by_domain": {"3": 120.0}, "radius_resolution_km": 1.0})


def test_index_can_be_disabled(tmp_path, monkeypatch):
    from wrf_processing_v3.request import Task

    monkeypatch.setenv("WRF_PROCESSING_V3_COMPLETION_INDEX", "0")
    storage = _storage(tmp_path)
    spec, meta = _spec("scalar")
    task = Task("exp", 3, 0, "rmw", None)
    storage.save(task, np.ones(1), spec, metadata=meta)
    assert not completion_index.index_path(storage.path_for(task)).exists()
    assert storage.existing([task]) == {task}
