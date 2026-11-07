"""读取加速（2026-11-02）：最低层 U/V 只读一层；同一时次 mdbz 复用 dbz。结果须与原路径逐值相同。"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from netCDF4 import Dataset


def _write_wind_wrf(path, u, v):
    path.parent.mkdir(parents=True, exist_ok=True)
    nt, nz, ny, nx1 = u.shape
    with Dataset(path, "w") as dataset:
        for name, size in [
            ("Time", nt), ("DateStrLen", 19), ("bottom_top", nz),
            ("south_north", ny), ("west_east", nx1 - 1),
            ("west_east_stag", nx1), ("south_north_stag", ny + 1),
        ]:
            dataset.createDimension(name, size)
        times = dataset.createVariable("Times", "S1", ("Time", "DateStrLen"))
        times[:] = np.asarray([list(f"2023-09-01_00:{5 * i:02d}:00") for i in range(nt)], dtype="S1")
        dataset.START_DATE = "2023-09-01_00:00:00"
        dataset.SIMULATION_START_DATE = "2023-09-01_00:00:00"
        dataset.DX = 1000.0
        dataset.DY = 1000.0
        dataset.MAP_PROJ = 0
        dataset.createVariable("U", "f4", ("Time", "bottom_top", "south_north", "west_east_stag"))[:] = u
        dataset.createVariable("V", "f4", ("Time", "bottom_top", "south_north_stag", "west_east"))[:] = v


def test_destagger_level_matches_wrf_destagger_formula():
    from wrf_processing_v3.wrf_reader import destagger_level

    rng = np.random.default_rng(0)
    u = rng.normal(size=(5, 6, 8)).astype("f4")
    v = rng.normal(size=(5, 7, 7)).astype("f4")
    expected_u = .5 * (u[..., 0:7] + u[..., 1:8])
    expected_v = .5 * (v[:, 0:6, :] + v[:, 1:7, :])
    np.testing.assert_array_equal(destagger_level(u[0], -1), expected_u[0])
    np.testing.assert_array_equal(destagger_level(v[0], -2), expected_v[0])
    assert destagger_level(u[0], -1).dtype == np.float32
    try:
        from wrf import destagger
    except Exception:  # noqa: BLE001 - 没有 wrf-python 的环境只核对公式
        return
    np.testing.assert_array_equal(destagger_level(u[0], -1), np.asarray(destagger(u, -1))[0])
    np.testing.assert_array_equal(destagger_level(v[0], -2), np.asarray(destagger(v, -2))[0])


def test_lowest_only_winds_read_one_level_and_match_full_destagger(tmp_path):
    from wrf_processing_v3.processing_manager import ProcessingManager
    from wrf_processing_v3.request import Request, Task
    from wrf_processing_v3.storage import NetCDFStorage
    from wrf_processing_v3.wrf_reader import NetCDFBackend, WrfReader

    rng = np.random.default_rng(1)
    u = rng.normal(5, 3, size=(2, 4, 6, 8)).astype("f4")
    v = rng.normal(-2, 3, size=(2, 4, 7, 7)).astype("f4")
    _write_wind_wrf(tmp_path / "source" / "exp" / "wrfout_d03_sample", u, v)

    calls = []

    class Backend(NetCDFBackend):
        def getvar_lowest(self, dataset, name, timeidx, **kwargs):
            calls.append((name, timeidx))
            return super().getvar_lowest(dataset, name, timeidx, **kwargs)

        def getvar(self, dataset, name, timeidx, **kwargs):  # 走到这里说明没用快速路径
            raise AssertionError(f"不应调用 getvar({name})")

    request = Request(
        experiments=("exp",), domains=(3,), times_minutes=(0, 5), heights_m=(None,),
        variables=("local_max_wind",), source_root=tmp_path / "source", output_root=tmp_path / "out",
        options={"time_origin": "2023-09-01_00:00:00"},
    )
    ProcessingManager(reader=WrfReader(backend=Backend())).process(request)
    assert sorted(calls) == [("ua", 0), ("ua", 1), ("va", 0), ("va", 1)]
    storage = NetCDFStorage(request.output_root, time_origin="2023-09-01_00:00:00")
    for index, minute in enumerate((0, 5)):
        ua = storage.load(Task("exp", 3, minute, "ua", "lowest"))
        va = storage.load(Task("exp", 3, minute, "va", "lowest"))
        np.testing.assert_array_equal(ua, (.5 * (u[index, :, :, 0:7] + u[index, :, :, 1:8]))[0])
        np.testing.assert_array_equal(va, (.5 * (v[index, :, 0:6, :] + v[index, :, 1:7, :]))[0])
        speed = np.hypot(ua.astype(float), va.astype(float)).max()
        np.testing.assert_allclose(storage.load(Task("exp", 3, minute, "local_max_wind", None)), [speed], rtol=1e-6)
        assert storage.load_metadata(Task("exp", 3, minute, "ua", "lowest")).get("fast_read") == "lowest_level_only"


def test_fast_reads_can_be_turned_off_and_fake_getvar_backends_are_respected(tmp_path):
    from wrf_processing_v3.request import Request
    from wrf_processing_v3.wrf_reader import NetCDFBackend, WrfReader

    class FakeGetvar(NetCDFBackend):
        def getvar(self, dataset, name, timeidx, **kwargs):
            return np.zeros((2, 3, 4))

    request = Request(
        experiments=("exp",), domains=(3,), times_minutes=(0,), heights_m=(None,), variables=("ua",),
        source_root=tmp_path, output_root=tmp_path / "out", options={},
    )
    assert WrfReader(backend=FakeGetvar())._lowest_reader(request) is None
    assert WrfReader()._lowest_reader(request) is not None
    off = replace(request, options={"fast_source_reads": False})
    assert WrfReader()._lowest_reader(off) is None


def test_mdbz_reuses_dbz_of_the_same_time(tmp_path):
    from wrf_processing_v3.processing_manager import ProcessingManager
    from wrf_processing_v3.request import Request, Task
    from wrf_processing_v3.storage import NetCDFStorage
    from wrf_processing_v3.wrf_reader import NetCDFBackend, WrfReader

    rng = np.random.default_rng(2)
    _write_wind_wrf(tmp_path / "source" / "exp" / "wrfout_d03_sample",
                    np.zeros((1, 3, 4, 6), "f4"), np.zeros((1, 3, 5, 5), "f4"))
    dbz = rng.uniform(-10, 60, size=(3, 4, 5)).astype("f4")
    calls = []

    class Backend(NetCDFBackend):
        def getvar(self, dataset, name, timeidx, **kwargs):
            calls.append(name)
            if name == "dbz":
                return dbz
            if name == "z":
                return np.stack([np.full((4, 5), h) for h in (500.0, 1500.0, 2500.0)])
            if name == "mdbz":
                return dbz.max(axis=0)
            raise AssertionError(name)

        def interpolate(self, field, z, height):
            return field[1]

    request = Request(
        experiments=("exp",), domains=(3,), times_minutes=(0,), heights_m=(1500,),
        variables=("mdbz", "dbz"), source_root=tmp_path / "source", output_root=tmp_path / "out",
        options={"time_origin": "2023-09-01_00:00:00"},
    )
    ProcessingManager(reader=WrfReader(backend=Backend())).process(request)
    assert "mdbz" not in calls and calls.count("dbz") == 1
    storage = NetCDFStorage(request.output_root, time_origin="2023-09-01_00:00:00")
    np.testing.assert_array_equal(storage.load(Task("exp", 3, 0, "mdbz", None)), dbz.max(axis=0))
    assert storage.load_metadata(Task("exp", 3, 0, "mdbz", None)).get("fast_read") == "mdbz_from_dbz"

    calls.clear()
    off = replace(request, output_root=tmp_path / "out_off", options={**request.options, "fast_source_reads": False})
    ProcessingManager(reader=WrfReader(backend=Backend())).process(off)
    assert "mdbz" in calls
    np.testing.assert_array_equal(
        NetCDFStorage(off.output_root, time_origin="2023-09-01_00:00:00").load(Task("exp", 3, 0, "mdbz", None)),
        storage.load(Task("exp", 3, 0, "mdbz", None)),
    )


def test_installed_wrf_python_mdbz_is_column_max_of_dbz():
    """核对本机 wrf-python 的 mdbz 实现确实是 dbz 的柱最大值，否则复用不成立。"""
    try:
        import inspect

        from wrf import g_dbz
    except Exception:  # noqa: BLE001
        pytest.skip("没有 wrf-python")
    try:
        source = inspect.getsource(inspect.unwrap(g_dbz.get_max_dbz))
    except (OSError, TypeError):
        pytest.skip("读不到 wrf-python 源码")
    assert "get_dbz(" in source and "max(" in source and "axis=-3" in source
