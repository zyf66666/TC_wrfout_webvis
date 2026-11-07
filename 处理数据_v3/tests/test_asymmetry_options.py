"""非对称度参数进网页表单（2026-11-04）：asymmetry_radius_km、dbz_asymmetry_height_m。

只在偏离基准值（100 km、3000 m）时计入科学参数，已有参数配置编号与完成情况索引不变；
已存切片元数据里没有这两项时按基准值核对。
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from wrf_processing_v3 import cache_compatibility
from wrf_processing_v3.cache_compatibility import OPTIONAL_SCIENCE_KEYS, _relevant_science_keys, _science


def test_baseline_values_do_not_change_science_fingerprint():
    base = {"radius_resolution_km": 1.0, "radius_km_by_domain": {"3": 300.0}}
    assert _science({**base, "asymmetry_radius_km": 100.0, "dbz_asymmetry_height_m": 3000}) == _science(base)
    changed = _science({**base, "asymmetry_radius_km": 150.0})
    assert changed["asymmetry_radius_km"] == 150.0 and "dbz_asymmetry_height_m" not in changed
    assert OPTIONAL_SCIENCE_KEYS == {"asymmetry_radius_km": 100.0, "dbz_asymmetry_height_m": 3000.0}


def test_relevant_keys_only_for_quadrant_asymmetry_indices():
    assert "asymmetry_radius_km" in _relevant_science_keys("mdbz_asymmetry")
    assert {"asymmetry_radius_km", "dbz_asymmetry_height_m"} <= _relevant_science_keys("dbz_asymmetry")
    assert "dbz_asymmetry_height_m" not in _relevant_science_keys("precipitation_asymmetry")
    assert not {"asymmetry_radius_km", "dbz_asymmetry_height_m"} & _relevant_science_keys("dbz_asy_polar")


def test_registry_uses_the_options():
    from wrf_processing_v3.register import build_registry

    spec = build_registry({"asymmetry_radius_km": 150.0, "dbz_asymmetry_height_m": 2000.0}, times_minutes=(0,)).resolve("dbz_asymmetry")
    assert spec.options["radius_km"] == 150.0
    assert [(dep.name, dep.height) for dep in spec.dependencies] == [("dbz1_polar", 2000.0)]


def _saved(tmp_path, request_options):
    pytest.importorskip("netCDF4")
    from wrf_processing_v3.request import Task
    from wrf_processing_v3.storage import NetCDFStorage

    storage = NetCDFStorage(tmp_path / "out", time_origin="2023-09-01_00:00:00")
    task = Task("exp", 3, 0, "mdbz_asymmetry", None)
    spec = SimpleNamespace(storage_layout="scalar", units="1", options={"algorithm_version": "v2.1"})
    storage.save(task, np.ones(1), spec, metadata={"request_options": request_options})
    item = SimpleNamespace(task=task, spec=SimpleNamespace(options={"algorithm_version": "v2.1"}))
    return storage, item


@pytest.mark.parametrize("use_index", [True, False])
def test_old_slices_without_the_key_count_as_baseline(tmp_path, monkeypatch, use_index):
    if not use_index:
        monkeypatch.setenv("WRF_PROCESSING_V3_COMPLETION_INDEX", "0")
    storage, item = _saved(tmp_path, {"radius_resolution_km": 1.0})   # 元数据里没有 asymmetry_radius_km
    cache_compatibility._COMPATIBILITY_CACHE.clear()
    cache_compatibility.check_cache_compatibility([item], storage, {"asymmetry_radius_km": 100.0})
    cache_compatibility._COMPATIBILITY_CACHE.clear()
    with pytest.raises(ValueError, match="asymmetry_radius_km 参数不一致"):
        cache_compatibility.check_cache_compatibility([item], storage, {"asymmetry_radius_km": 150.0})


@pytest.mark.parametrize("use_index", [True, False])
def test_slices_with_a_changed_radius_reject_baseline_requests(tmp_path, monkeypatch, use_index):
    if not use_index:
        monkeypatch.setenv("WRF_PROCESSING_V3_COMPLETION_INDEX", "0")
    storage, item = _saved(tmp_path, {"asymmetry_radius_km": 150.0})
    cache_compatibility._COMPATIBILITY_CACHE.clear()
    cache_compatibility.check_cache_compatibility([item], storage, {"asymmetry_radius_km": 150.0})
    cache_compatibility._COMPATIBILITY_CACHE.clear()
    with pytest.raises(ValueError, match="参数不一致"):
        cache_compatibility.check_cache_compatibility([item], storage, {"asymmetry_radius_km": 100.0})
