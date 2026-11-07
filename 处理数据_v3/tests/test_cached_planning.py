"""无需源文件的缓存路径，使用临时 NetCDF 验证，禁止访问真实 wrfout。"""
from dataclasses import replace

import numpy as np
import pytest
from netCDF4 import Dataset

from wrf_processing_v3.processing_manager import ProcessingManager
from wrf_processing_v3.register import build_registry
from wrf_processing_v3.request import Request, Task
from wrf_processing_v3.source_index import SourceIndex
from wrf_processing_v3.storage import NetCDFStorage

ORIGIN = '2023-09-01_00:00:00'


def setup_request(tmp_path, variables=('wind_speed_10',), experiments=('exp',)):
    request = Request(experiments=experiments, domains=(3,), times_minutes=(0,),
                      heights_m=(100,), variables=variables,
                      source_root=tmp_path/'offline', output_root=tmp_path/'out')
    return request, build_registry(request.options, times_minutes=request.times_minutes)


def save(request, registry, variable, value, experiment='exp', origin=ORIGIN):
    return NetCDFStorage(request.output_root, time_origin=origin).save(
        Task(experiment, 3, 0, variable, None), np.full((2, 2), value),
        registry.resolve(variable))


def forbid_scan(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail('缓存路径不应扫描 wrfout')
    monkeypatch.setattr(SourceIndex, '_paths', fail)


@pytest.mark.parametrize('dry_run', [False, True])
def test_complete_cache_does_not_scan_sources(tmp_path, monkeypatch, dry_run):
    request, registry = setup_request(tmp_path)
    save(request, registry, 'wind_speed_10', 5)
    forbid_scan(monkeypatch)
    result = ProcessingManager().process(replace(request, dry_run=dry_run))
    assert len(result['reused']) == 1
    assert result['computed'] == []


@pytest.mark.parametrize('force', [False, True])
def test_derived_only_matches_original_formula_without_sources(tmp_path, monkeypatch, force):
    request, registry = setup_request(tmp_path)
    save(request, registry, 'U10', 3)
    save(request, registry, 'V10', 4)
    forbid_scan(monkeypatch)
    result = ProcessingManager().process(replace(request, force=force))
    assert len(result['computed']) == 1
    storage = NetCDFStorage(request.output_root, time_origin=ORIGIN)
    np.testing.assert_array_equal(storage.load(Task('exp', 3, 0, 'wind_speed_10', None)), 5)


def test_missing_native_input_still_scans(tmp_path, monkeypatch):
    request, registry = setup_request(tmp_path)
    save(request, registry, 'U10', 3)
    calls = []
    def paths(*args):
        calls.append(True)
        return []
    monkeypatch.setattr(SourceIndex, '_paths', paths)
    with pytest.raises(FileNotFoundError, match='No wrfout'):
        ProcessingManager().process(request)
    assert calls


def test_conflicting_cached_origins_are_rejected(tmp_path, monkeypatch):
    request, registry = setup_request(tmp_path)
    save(request, registry, 'U10', 3)
    save(request, registry, 'V10', 4, origin='2023-09-02_00:00:00')
    forbid_scan(monkeypatch)
    with pytest.raises(ValueError, match='time_origin'):
        ProcessingManager().process(request)


def test_missing_origin_falls_back_to_scan(tmp_path, monkeypatch):
    request, registry = setup_request(tmp_path)
    path = save(request, registry, 'wind_speed_10', 5)
    with Dataset(path, 'r+') as ds:
        ds.delncattr('time_origin')
    calls = []
    monkeypatch.setattr(SourceIndex, '_paths', lambda *args: calls.append(True) or [])
    with pytest.raises(FileNotFoundError):
        ProcessingManager().process(request)
    assert calls


def test_each_member_must_have_completed_inputs(tmp_path, monkeypatch):
    request, registry = setup_request(tmp_path, experiments=('exp', 'other'))
    save(request, registry, 'wind_speed_10', 5)
    calls = []
    monkeypatch.setattr(SourceIndex, '_paths', lambda *args: calls.append(True) or [])
    with pytest.raises(FileNotFoundError):
        ProcessingManager().process(request)
    assert calls


def test_force_native_cache_still_requires_source_index(tmp_path, monkeypatch):
    request, registry = setup_request(tmp_path, variables=('U10',))
    save(request, registry, 'U10', 3)
    calls = []
    monkeypatch.setattr(SourceIndex, '_paths', lambda *args: calls.append(True) or [])
    with pytest.raises(FileNotFoundError):
        ProcessingManager().process(replace(request, force=True))
    assert calls


def test_explicit_origin_cannot_override_cached_origin(tmp_path, monkeypatch):
    request, registry = setup_request(tmp_path)
    save(request, registry, 'wind_speed_10', 5)
    forbid_scan(monkeypatch)
    with pytest.raises(ValueError, match='time_origin'):
        ProcessingManager().process(replace(request, options={'time_origin': '2023-09-02_00:00:00'}))


def test_consistent_multiple_members_skip_source_index(tmp_path, monkeypatch):
    request, registry = setup_request(tmp_path, experiments=('exp', 'other'))
    for experiment in request.experiments:
        save(request, registry, 'wind_speed_10', 5, experiment=experiment)
    forbid_scan(monkeypatch)
    result = ProcessingManager().process(request)
    assert len(result['reused']) == 2


def test_incomplete_native_slice_cannot_be_reused(tmp_path, monkeypatch):
    request, registry = setup_request(tmp_path)
    save(request, registry, 'U10', 3)
    path = save(request, registry, 'V10', 4)
    with Dataset(path, 'r+') as ds:
        ds['completed'][0] = 0
    calls = []
    monkeypatch.setattr(SourceIndex, '_paths', lambda *args: calls.append(True) or [])
    with pytest.raises(FileNotFoundError):
        ProcessingManager().process(request)
    assert calls
