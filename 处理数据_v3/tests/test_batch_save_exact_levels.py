"""处理加速（2026-11-03）：

A. interplevel 等值缺测：某层坐标恰等于目标值时取该层原值（fill_exact_levels）；
B/C. 批量写切片（NetCDFStorage.save_many）：一次打开、一次写盘，结果与逐个 save 相同。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from wrf_processing_v3.wrf_reader import fill_exact_levels


# ---- A：纯数值，不需要 netCDF4 ----

def test_exact_level_points_take_the_level_value():
    field = np.arange(3 * 4 * 5, dtype=float).reshape(3, 4, 5)
    z = np.stack([np.full((4, 5), 1000.0), np.full((4, 5), 2000.0), np.full((4, 5), 3000.0)])
    z[1, 2, 3] = 2500.0           # 这一格第 1 层正好在 2500 m
    values = np.full((4, 5), 7.0)
    values[2, 3] = np.nan          # interplevel 在这里给了缺测
    values[0, 0] = np.nan          # 真正的缺测（没有任何层等于 2500 m）应原样保留
    fixed = fill_exact_levels(values, field, z, 2500)
    assert fixed[2, 3] == field[1, 2, 3]
    assert np.isnan(fixed[0, 0])
    assert np.array_equal(np.delete(fixed.ravel(), [0, 13]), np.full(18, 7.0))
    assert np.isnan(values[2, 3])  # 不改动输入


def test_no_missing_or_mismatched_shapes_are_returned_unchanged():
    values = np.ones((4, 5))
    assert fill_exact_levels(values, np.zeros((3, 4, 5)), np.zeros((3, 4, 5)), 0) is values
    broken = np.full((4, 5), np.nan)
    assert np.isnan(fill_exact_levels(broken, np.zeros((3, 4, 6)), np.zeros((3, 4, 6)), 0)).all()


def test_masked_field_and_pressure_coordinate():
    field = np.ma.masked_array(np.ones((2, 2, 2)) * 5.0, mask=False)
    pressure = np.array([[[850.0, 850.0], [850.0, 900.0]], [[200.0, 200.0], [200.0, 200.0]]])
    values = np.array([[1.0, 1.0], [1.0, np.nan]])
    assert fill_exact_levels(values, field, pressure, 900.0)[1, 1] == 5.0


def test_interpolate_with_wrf_fixes_exact_levels():
    pytest.importorskip("wrf")
    from wrf_processing_v3.wrf_reader import NetCDFBackend

    nz, ny, nx = 6, 5, 5
    z = np.broadcast_to(np.arange(nz, dtype=float)[:, None, None] * 1000.0 + 500.0, (nz, ny, nx)).copy()
    z[:, 2, 2] += 0.25            # 只有中心列不在整数高度上
    field = np.broadcast_to(np.arange(nz, dtype=float)[:, None, None], (nz, ny, nx)).copy()
    values = NetCDFBackend().interpolate(field, z, 2500.0)
    assert not np.isnan(values).any()
    assert values[0, 0] == pytest.approx(2.0)   # 2500 m 正好是第 2 层


# ---- 读取器按字段批量调用 save_many ----

def test_reader_saves_each_field_in_batches(monkeypatch):
    from wrf_processing_v3 import wrf_reader
    from wrf_processing_v3.request import Task

    monkeypatch.setattr(wrf_reader, "SAVE_BATCH", 4)
    nz, ny, nx = 4, 3, 3
    heights = list(range(100, 1100, 100))

    class Backend:
        def open(self, path):
            class Handle:
                def __enter__(self_inner):
                    return SimpleNamespace()

                def __exit__(self_inner, *args):
                    return False
            return Handle()

        def getvar(self, dataset, name, timeidx, **kwargs):
            if name == "z":
                return np.broadcast_to(np.arange(nz, dtype=float)[:, None, None] * 400.0, (nz, ny, nx)).copy()
            return np.ones((nz, ny, nx)) * (2.0 if name == "rh" else 3.0)

        def interpolate(self, field, z, height):
            return field[0] * 0 + height

    class Source:
        def is_file(self):
            return False

        def __str__(self):
            return "src"

    source = Source()

    class Index:
        def resolve(self, experiment, domain, time):
            return source, 0

    class Storage:
        def __init__(self):
            self.batches = []

        def save_many(self, items):
            items = list(items)
            self.batches.append([(task.variable, task.height_m) for task, *_ in items])
            for task, values, spec, metadata in items:
                assert values[0, 0] == task.height_m and metadata["interp_exact_level_fix"] == "v1"
            return [f"{task.variable}.nc" for task, *_ in items]

        def save(self, *args, **kwargs):
            raise AssertionError("有 save_many 时不应逐个 save")

    spec = SimpleNamespace(kind="getvar", wrf_name=None, options={}, dimensions="height", handler=None)
    registry = SimpleNamespace(resolve=lambda name: SimpleNamespace(**{**vars(spec), "name": name}))
    request = SimpleNamespace(options={}, dtype="float32")
    tasks = [Task("exp", 3, 0, name, height) for name in ("rh", "QVAPOR") for height in heights]
    messages = []
    storage = Storage()
    reader = wrf_reader.WrfReader(index=Index(), backend=Backend())
    paths = reader.prepare(tasks, request, registry, storage, progress=messages.append)
    assert len(paths) == len(tasks) == len(messages)
    assert [len(batch) for batch in storage.batches] == [4, 4, 2, 4, 4, 2]   # 每个字段单独成批，不跨字段
    assert all(len({name for name, _ in batch}) == 1 for batch in storage.batches)
    assert messages[-1].startswith(f"源切片 {len(tasks)}/{len(tasks)}")


# ---- B/C：真实 NetCDF ----

def _storage(tmp_path, name="out"):
    pytest.importorskip("netCDF4")
    from wrf_processing_v3.storage import NetCDFStorage

    return NetCDFStorage(tmp_path / name, time_origin="2023-09-01_00:00:00")


def _spec(layout="cartesian_3d"):
    return SimpleNamespace(storage_layout=layout, units="K", options={"algorithm_version": "test-v1"})


def _dump(path):
    from netCDF4 import Dataset

    with Dataset(path) as dataset:
        dataset.set_auto_mask(False)
        result = {name: np.asarray(dataset[name][:]) for name in ("time_minutes", "height_m", "completed")}
        result["data"] = np.asarray(dataset["data"][:])
        # 未写过的变长字符串不能整体读取（netCDF4 会报 HDF error），只读已完成的位置
        flags = result["completed"]
        result["metadata_json"] = [str(dataset["metadata_json"][tuple(int(i) for i in position)])
                                   for position in np.argwhere(flags == 1)]
        if "lowest" in dataset.groups:
            group = dataset.groups["lowest"]
            result["lowest_completed"] = np.asarray(group["completed"][:])
            result["lowest_data"] = np.asarray(group["data"][:])
    return result


def test_save_many_matches_individual_saves(tmp_path):
    from wrf_processing_v3.request import Task

    rng = np.random.default_rng(3)
    tasks = [Task("exp", 3, t, "theta", h) for t in (60, 0) for h in (1000, 3000, 12345, "lowest")]
    arrays = [rng.normal(size=(5, 7)) for _ in tasks]
    meta = lambda task: {"source_time_index": task.time_minutes, "note": f"{task.height_m}"}  # noqa: E731

    one = _storage(tmp_path, "one")
    for task, array in zip(tasks, arrays):
        one.save(task, array, _spec(), metadata=meta(task))
    many = _storage(tmp_path, "many")
    paths = many.save_many([(task, array, _spec(), meta(task)) for task, array in zip(tasks, arrays)])
    assert paths == [many.path_for(task) for task in tasks]

    a, b = _dump(one.path_for(tasks[0])), _dump(many.path_for(tasks[0]))
    assert a.keys() == b.keys()
    for key in a:
        if key == "metadata_json":
            assert a[key] == b[key] and len(a[key]) == 6
        else:
            np.testing.assert_array_equal(a[key], b[key])
    assert many.existing(tasks) == set(tasks)
    for task, array in zip(tasks, arrays):
        np.testing.assert_array_equal(many.load(task), array.astype(np.float32))
        assert many.load_metadata(task)["note"] == str(task.height_m)


def test_save_many_opens_once_and_syncs_once(tmp_path, monkeypatch):
    from wrf_processing_v3 import storage as storage_module
    from wrf_processing_v3.request import Task

    storage = _storage(tmp_path)
    storage.save(Task("exp", 3, 0, "theta", 1000), np.zeros((5, 7)), _spec())   # 先建好文件
    counts = {"open": 0, "sync": 0}
    original = storage_module.Dataset

    class Counting(original):
        def __init__(self, *args, **kwargs):
            counts["open"] += 1
            super().__init__(*args, **kwargs)

        def sync(self):
            counts["sync"] += 1
            return super().sync()

    monkeypatch.setattr(storage_module, "Dataset", Counting)
    tasks = [Task("exp", 3, 60, "theta", h) for h in (1000, 2000, 3000, 4000)]
    storage.save_many([(task, np.ones((5, 7)), _spec(), None) for task in tasks])
    assert counts == {"open": 1, "sync": 1}


def test_overwriting_completed_slices_clears_flags_first(tmp_path, monkeypatch):
    from netCDF4 import Dataset

    from wrf_processing_v3 import storage as storage_module
    from wrf_processing_v3.request import Task

    storage = _storage(tmp_path)
    task = Task("exp", 3, 0, "theta", 1000)
    storage.save(task, np.zeros((5, 7)), _spec())
    path = storage.path_for(task)

    def fail(*args, **kwargs):
        raise RuntimeError("模拟写数据时出错")

    monkeypatch.setattr(storage_module.NetCDFStorage, "_write_data", staticmethod(fail))
    with pytest.raises(RuntimeError):
        storage.save(task, np.ones((5, 7)), _spec())
    with Dataset(path) as dataset:
        dataset.set_auto_mask(False)
        assert int(dataset["completed"][0, list(dataset["height_m"][:]).index(1000)]) == 0
    monkeypatch.undo()
    assert task not in storage.existing([task])
    storage.save(task, np.full((5, 7), 2.0), _spec())
    assert storage.existing([task]) == {task}
    np.testing.assert_array_equal(storage.load(task), np.full((5, 7), 2.0, dtype=np.float32))


def test_session_storage_save_many_invalidates_cache(tmp_path):
    from wrf_processing_v3.execution_cache import SessionStorage
    from wrf_processing_v3.request import Task

    base = _storage(tmp_path)
    session = SessionStorage(base)
    task = Task("exp", 3, 0, "theta", 1000)
    session.save(task, np.zeros((5, 7)), _spec())
    np.testing.assert_array_equal(session.load(task), np.zeros((5, 7)))
    session.save_many([(task, np.ones((5, 7)), _spec(), None)])
    np.testing.assert_array_equal(session.load(task), np.ones((5, 7)))
    session.clear()


def test_index_stays_valid_after_batch(tmp_path):
    from wrf_processing_v3 import completion_index
    from wrf_processing_v3.request import Task

    storage = _storage(tmp_path)
    tasks = [Task("exp", 3, t, "theta", h) for t in (0, 60) for h in (1000, 2000)]
    storage.save_many([(task, np.ones((5, 7)), _spec(), None) for task in tasks[:3]])
    index = completion_index.load_valid(storage.path_for(tasks[0]))
    assert index is not None
    assert [completion_index.lookup(index, task) for task in tasks] == [True, True, True, False]
    profile = json.loads(json.dumps(list(completion_index.completed_profiles(index))))
    assert len(profile) == 1


def test_reader_interpolates_each_batch_of_heights_in_one_call(monkeypatch):
    from wrf_processing_v3 import wrf_reader
    from wrf_processing_v3.request import Task

    monkeypatch.setattr(wrf_reader, "SAVE_BATCH", 4)
    calls = []

    class Backend:
        def open(self, path):
            class Handle:
                def __enter__(self_inner):
                    return SimpleNamespace()

                def __exit__(self_inner, *args):
                    return False
            return Handle()

        def getvar(self, dataset, name, timeidx, **kwargs):
            return np.ones((3, 2, 2)) * (0 if name == "z" else 5)

        def interpolate(self, field, z, height):
            calls.append(("one", height))
            return np.full((2, 2), float(height))

        def interpolate_many(self, field, z, heights):
            calls.append(("many", tuple(heights)))
            return [np.full((2, 2), float(h)) for h in heights]

    class Source:
        def is_file(self):
            return False

    source = Source()
    index = SimpleNamespace(resolve=lambda *args: (source, 0))
    saved = {}

    class Storage:
        def save_many(self, items):
            for task, values, spec, metadata in items:
                saved[task.height_m] = values
            return ["x"] * len(items)

    spec = SimpleNamespace(kind="getvar", wrf_name=None, options={}, dimensions="height", handler=None)
    registry = SimpleNamespace(resolve=lambda name: SimpleNamespace(**{**vars(spec), "name": name}))
    heights = ["lowest", 100, 200, 300, 400, 500]
    tasks = [Task("exp", 3, 0, "rh", h) for h in heights]
    wrf_reader.WrfReader(index=index, backend=Backend()).prepare(tasks, SimpleNamespace(options={}, dtype="float32"), registry, Storage())
    assert calls == [("many", (100, 200, 300)), ("many", (400, 500))]   # 每批一次，最低层不插值
    assert saved["lowest"][0, 0] == 5 and all(saved[h][0, 0] == h for h in heights[1:])
