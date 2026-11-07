"""处理入口的缓存校验：全部使用临时文件，不读取真实 wrfout。"""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from netCDF4 import Dataset

from wrf_processing_v3.processing_manager import ProcessingManager
from wrf_processing_v3.request import Request, Task
from wrf_processing_v3.storage import NetCDFStorage


def prepared(tmp_path, variable='dbz_polar', options=None, saved_options=None, algorithm='v2.1'):
    options = options or {'radius_resolution_km': 1.0}
    request = Request(experiments=('exp',), domains=(3,), times_minutes=(0,),
                      heights_m=(100,), variables=(variable,), source_root=tmp_path/'offline',
                      output_root=tmp_path/'out', options=options)
    spec = SimpleNamespace(storage_layout='polar_3d' if '_polar' in variable else 'cartesian_3d',
                           units='K', options={'algorithm_version': algorithm, 'radius_resolution_km': 1.0})
    storage = NetCDFStorage(request.output_root, time_origin='2023-09-01_00:00:00')
    path = storage.save(Task('exp', 3, 0, variable, 100), np.ones((2, 2)), spec,
                        metadata={'request_options': saved_options if saved_options is not None else options})
    return request, path


@pytest.mark.parametrize('dry_run', [True, False])
@pytest.mark.parametrize('custom_reader', [True, False])
def test_manager_rejects_parameter_conflict_before_execution(tmp_path, dry_run, custom_reader):
    request, path = prepared(tmp_path, saved_options={'radius_resolution_km': 9.0})
    before = path.read_bytes()
    class Reader:
        def prepare(self, *args, **kwargs):
            pytest.fail('不兼容缓存不应进入计算')
    if custom_reader:
        request = replace(request, options={**request.options, 'time_origin': '2023-09-01_00:00:00'})
    with pytest.raises(ValueError, match='参数不一致'):
        ProcessingManager(reader=Reader() if custom_reader else None).process(replace(request, dry_run=dry_run))
    assert path.read_bytes() == before


def test_manager_rejects_old_native_algorithm(tmp_path):
    request, _ = prepared(tmp_path, variable='theta', algorithm='old')
    with pytest.raises(ValueError, match='算法版本不一致'):
        ProcessingManager().process(request)


def test_manager_requires_scientific_metadata_for_polar(tmp_path):
    request, path = prepared(tmp_path)
    with Dataset(path, 'r+') as ds:
        ds['metadata_json'][0, 0] = '{}'
    with pytest.raises(ValueError, match='缺少.*参数'):
        ProcessingManager().process(request)


def test_runtime_flags_and_other_domains_do_not_invalidate(tmp_path):
    request, _ = prepared(tmp_path,
        options={'radius_km_by_domain': {2: 100, 3: 300}, 'radius_resolution_km': 1,
                 'processing_cache': False},
        saved_options={'radius_km_by_domain': {'2': 999, '3': 300.0},
                       'radius_resolution_km': 1.0, 'processing_cache': True})
    result = ProcessingManager().process(request)
    assert len(result['reused']) == 1


def test_unchanged_file_does_not_compute(tmp_path):
    request, _ = prepared(tmp_path)
    assert ProcessingManager().process(request)['computed'] == []


def test_cached_validation_is_invalidated_after_same_size_metadata_change(tmp_path):
    import os
    import json
    request, path = prepared(tmp_path)
    assert ProcessingManager().process(request)['computed'] == []
    before = path.stat()
    with Dataset(path, 'r+') as ds:
        details = json.loads(ds['metadata_json'][0, 0])
        details['request_options']['radius_resolution_km'] = 9.0
        ds['metadata_json'][0, 0] = json.dumps(details, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ValueError, match='参数不一致'):
        ProcessingManager().process(request)


def test_force_does_not_overwrite_incompatible_cache(tmp_path):
    request, path = prepared(tmp_path, saved_options={'radius_resolution_km': 9.0})
    before = path.read_bytes()
    # 用注入 reader 验证强制计算也会先检查输出目录，禁止写入混杂结果。
    request = replace(request, force=True, options={**request.options, 'time_origin': '2023-09-01_00:00:00'})
    with pytest.raises(ValueError, match='参数不一致'):
        ProcessingManager(reader=object()).process(request)
    assert path.read_bytes() == before
