"""请求内缓存的数值隔离、失效、内存与重复I/O契约。"""
from collections import Counter
from dataclasses import replace
import os

import numpy as np
import pytest

from wrf_processing_v3.dependency_planner import DependencyPlanner
from wrf_processing_v3.processing_manager import ProcessingManager
from wrf_processing_v3.register import build_registry
from wrf_processing_v3.request import Request, Task
from wrf_processing_v3.storage import NetCDFStorage


ORIGIN = '2023-09-01_00:00:00'


class CachedOnlyReader:
    def prepare(self, *args, **kwargs):
        raise AssertionError('不得重读wrfout')


def seeded(tmp_path, *, cache=True):
    options = {'time_origin': ORIGIN, 'processing_cache': cache}
    req = Request(experiments=('exp',), domains=(3,), times_minutes=(0,), heights_m=(100,),
                  variables=('axis_mean_max_wind', 'rmw'), source_root=tmp_path/'source',
                  output_root=tmp_path/'out', options=options)
    reg = build_registry(options)
    storage = NetCDFStorage(req.output_root, time_origin=ORIGIN)
    yy, xx = np.indices((21,21), dtype=float)
    radius = np.hypot(xx-10, yy-10)
    speed = 20*np.exp(-((radius-4)/1.2)**2)
    sin = np.divide(yy-10, radius, out=np.zeros_like(radius), where=radius>0)
    cos = np.divide(xx-10, radius, out=np.zeros_like(radius), where=radius>0)
    for name, values, height in (('ua', -speed*sin, 'lowest'), ('va', speed*cos, 'lowest'), ('slp_center',[10,10],None)):
        storage.save(Task('exp',3,0,name,height), np.asarray(values), reg.resolve(name),
                     metadata={'grid_spacing':{'dx_km':1.,'dy_km':1.}})
    return req, reg, storage


def session(storage, max_bytes=256*1024*1024):
    from wrf_processing_v3.execution_cache import SessionStorage
    return SessionStorage(storage, max_bytes=max_bytes)


@pytest.mark.parametrize('enabled', [False, True])
def test_pair_shares_sources_and_profile_only_when_enabled(tmp_path, monkeypatch, enabled):
    from wrf_processing_v3 import common
    req, reg, storage = seeded(tmp_path, cache=enabled)
    loads = Counter()
    original_load = NetCDFStorage.load
    original_profile = common.azimuthal_mean
    calls = []
    def load(self, task):
        loads[task.variable] += 1
        return original_load(self, task)
    def profile(*args, **kwargs):
        calls.append(1)
        return original_profile(*args, **kwargs)
    monkeypatch.setattr(NetCDFStorage, 'load', load)
    monkeypatch.setattr(common, 'azimuthal_mean', profile)
    ProcessingManager(registry=reg, reader=CachedOnlyReader()).process(req)
    assert loads['ua'] == (1 if enabled else 2)
    assert loads['va'] == (1 if enabled else 2)
    assert loads['slp_center'] == (1 if enabled else 4)
    assert len(calls) == (1 if enabled else 2)
    np.testing.assert_array_equal(storage.load(Task('exp',3,0,'rmw',None)), [4.])


def test_cache_returns_independent_arrays_and_metadata(tmp_path):
    _, _, storage = seeded(tmp_path)
    cached = session(storage)
    task = Task('exp',3,0,'ua','lowest')
    original = cached.load(task)
    changed = cached.load(task)
    changed[:] = 999
    np.testing.assert_array_equal(cached.load(task), original)
    metadata = cached.load_metadata(task)
    metadata['grid_spacing']['dx_km'] = 999
    assert cached.load_metadata(task)['grid_spacing']['dx_km'] == 1.


def test_scope_change_releases_arrays_but_keeps_small_metadata(tmp_path,monkeypatch):
    _,_,storage=seeded(tmp_path)
    cached=session(storage)
    counts=Counter()
    for method in ('load','load_metadata'):
        original=getattr(NetCDFStorage,method)
        def counted(self,task,_original=original,_method=method):
            counts[_method]+=1
            return _original(self,task)
        monkeypatch.setattr(NetCDFStorage,method,counted)
    task=Task('exp',3,0,'ua','lowest')
    cached.begin_scope(('exp',3,0))
    cached.load(task);cached.load_metadata(task)
    cached.begin_scope(('exp',3,5))
    cached.load(task);cached.load_metadata(task)
    assert counts['load']==2
    assert counts['load_metadata']==1


def test_cached_read_detects_write_through_other_storage(tmp_path):
    _, reg, storage = seeded(tmp_path)
    cached = session(storage)
    task = Task('exp',3,0,'ua','lowest')
    before = cached.load(task)
    storage.save(task, np.full_like(before,7), reg.resolve('ua'))
    np.testing.assert_array_equal(cached.load(task), 7.)


def test_cached_read_detects_replacement_with_preserved_mtime(tmp_path):
    _, reg, storage = seeded(tmp_path)
    cached = session(storage)
    task = Task('exp',3,0,'ua','lowest')
    cached.load(task)
    path = storage.path_for(task)
    old = path.stat()
    other = NetCDFStorage(tmp_path/'replacement',time_origin=ORIGIN)
    replacement = other.save(task, np.full((21,21),9), reg.resolve('ua'))
    os.utime(replacement, ns=(old.st_atime_ns,old.st_mtime_ns))
    os.replace(replacement,path)
    np.testing.assert_array_equal(cached.load(task), 9.)


def test_cache_separates_lowest_from_physical_height(tmp_path):
    _, reg, storage = seeded(tmp_path)
    low = Task('exp',3,0,'ua','lowest')
    high = Task('exp',3,0,'ua',1000)
    storage.save(high,np.full((21,21),30),reg.resolve('ua'))
    cached = session(storage)
    a,b=cached.load(low),cached.load(high)
    assert not np.array_equal(a,b)
    np.testing.assert_array_equal(cached.load(high),30)
    np.testing.assert_array_equal(cached.load(low),a)


def test_array_larger_than_budget_is_not_retained(tmp_path, monkeypatch):
    _, _, storage = seeded(tmp_path)
    cached = session(storage,max_bytes=64)
    task = Task('exp',3,0,'ua','lowest')
    calls=[];original=NetCDFStorage.load
    def load(self, task):
        calls.append(1)
        return original(self,task)
    monkeypatch.setattr(NetCDFStorage,'load',load)
    cached.load(task);cached.load(task)
    assert len(calls)==2
    assert cached.cache_bytes <= 64
    cached.clear()
    assert cached.cache_bytes==0


def test_many_slices_evict_old_arrays_within_memory_budget(tmp_path, monkeypatch):
    _,reg,storage=seeded(tmp_path)
    tasks=[Task('exp',3,minute,'ua','lowest') for minute in range(1,10)]
    for task in tasks:
        storage.save(task,np.full((21,21),task.time_minutes),reg.resolve('ua'))
    cached=session(storage,max_bytes=10_000)
    calls=Counter();original=NetCDFStorage.load
    def load(self, task):
        calls[task]+=1
        return original(self,task)
    monkeypatch.setattr(NetCDFStorage,'load',load)
    for task in tasks:
        np.testing.assert_array_equal(cached.load(task),task.time_minutes)
        assert cached.cache_bytes<=10_000
    np.testing.assert_array_equal(cached.load(tasks[0]),1.)
    assert calls[tasks[0]]==2
    assert cached.peak_bytes<=10_000


@pytest.mark.parametrize('case',['partial_nan','zero_tie'])
def test_missing_values_and_tied_peaks_match_disabled_path(tmp_path,case):
    results=[]
    for enabled in (False,True):
        req,reg,storage=seeded(tmp_path/str(enabled),cache=enabled)
        for name in ('ua','va'):
            task=Task('exp',3,0,name,'lowest')
            values=storage.load(task)
            if case=='zero_tie':
                values[:]=0
            else:
                values[10,14]=np.nan
            storage.save(task,values,reg.resolve(name),metadata={'grid_spacing':{'dx_km':1.,'dy_km':1.}})
        ProcessingManager(registry=reg,reader=CachedOnlyReader()).process(req)
        results.append([storage.load(Task('exp',3,0,name,None)) for name in req.variables])
    for a,b in zip(*results):
        np.testing.assert_array_equal(a,b)
    if case=='zero_tie':
        np.testing.assert_array_equal(results[1],[np.array([0.]),np.array([0.])])


def test_members_do_not_share_cached_wind_or_profile(tmp_path):
    req,reg,storage=seeded(tmp_path)
    for name in ('ua','va','slp_center'):
        original=Task('exp',3,0,name,'lowest' if name in ('ua','va') else None)
        other=replace(original,experiment='other')
        values=storage.load(original)*(2 if name in ('ua','va') else 1)
        storage.save(other,values,reg.resolve(name),metadata={'grid_spacing':{'dx_km':1.,'dy_km':1.}})
    ProcessingManager(registry=reg,reader=CachedOnlyReader()).process(replace(req,experiments=('exp','other')))
    a=storage.load(Task('exp',3,0,'axis_mean_max_wind',None))
    b=storage.load(Task('other',3,0,'axis_mean_max_wind',None))
    np.testing.assert_array_equal(b,a*2)


def test_own_failed_write_invalidates_cached_completion_and_values(tmp_path, monkeypatch):
    _, reg, storage = seeded(tmp_path)
    cached = session(storage)
    task=Task('exp',3,0,'ua','lowest')
    cached.load(task)
    original=NetCDFStorage._write_data
    def interrupted(*args):
        original(*args)
        raise RuntimeError('interrupted')
    monkeypatch.setattr(NetCDFStorage,'_write_data',staticmethod(interrupted))
    with pytest.raises(RuntimeError,match='interrupted'):
        cached.save(task,np.ones((21,21)),reg.resolve('ua'))
    assert not cached.exists(task)
    with pytest.raises(KeyError,match='未完成'):
        cached.load(task)


def test_force_recompute_and_new_request_do_not_reuse_old_profile(tmp_path):
    req,reg,storage=seeded(tmp_path)
    manager=ProcessingManager(registry=reg,reader=CachedOnlyReader())
    manager.process(req)
    target=Task('exp',3,0,'axis_mean_max_wind',None)
    before=storage.load(target)
    for name in ('ua','va'):
        task=Task('exp',3,0,name,'lowest')
        storage.save(task,storage.load(task)*2,reg.resolve(name),metadata={'grid_spacing':{'dx_km':1.,'dy_km':1.}})
    result=manager.process(replace(req,force=True))
    assert len(result['computed'])==2
    np.testing.assert_array_equal(storage.load(target),before*2)


def test_profile_invalidates_when_dependency_changes_in_same_session(tmp_path):
    from wrf_processing_v3.context import ProcessingContext
    from wrf_processing_v3.variables.axis_mean_max_wind import process
    req,reg,storage=seeded(tmp_path)
    cached=session(storage)
    target=Task('exp',3,0,'axis_mean_max_wind',None)
    process(ProcessingContext(target,req,reg.resolve(target.variable),cached,reg))
    before=storage.load(target)
    for name in ('ua','va'):
        task=Task('exp',3,0,name,'lowest')
        cached.save(task,cached.load(task)*2,reg.resolve(name),metadata={'grid_spacing':{'dx_km':1.,'dy_km':1.}})
    process(ProcessingContext(target,req,reg.resolve(target.variable),cached,reg))
    np.testing.assert_array_equal(storage.load(target),before*2)


def test_executor_restores_storage_and_clears_cache_after_cancellation(tmp_path, monkeypatch):
    from wrf_processing_v3.executor import Executor
    from wrf_processing_v3.execution_cache import SessionStorage
    req,reg,storage=seeded(tmp_path)
    plan=DependencyPlanner(reg,storage).plan(req)
    cleared=[];original=SessionStorage.clear
    def clear(self):
        original(self)
        cleared.append(self.cache_bytes)
    monkeypatch.setattr(SessionStorage,'clear',clear)
    executor=Executor(CachedOnlyReader(),reg,storage)
    def cancel(message):
        raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):
        executor.execute(plan,req,progress=cancel)
    assert executor.storage is storage
    assert cleared and cleared[-1]==0
