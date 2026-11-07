"""尺寸查询、极坐标几何和多变量纯计算共享；保持原数值语义。"""
from dataclasses import replace
from types import SimpleNamespace
from collections import Counter
import numpy as np
import pytest

from wrf_processing_v3.register import build_registry
from wrf_processing_v3.request import Request,Task
from wrf_processing_v3.storage import NetCDFStorage
from wrf_processing_v3.processing_manager import ProcessingManager

ORIGIN='2023-09-01_00:00:00'


class CachedOnlyReader:
    def prepare(self,*args,**kwargs):raise AssertionError('不得读取wrfout')


def seeded(tmp_path, *,cache=True):
    options={'time_origin':ORIGIN,'radius_km':5.,'radius_resolution_km':1.,'processing_cache':cache,'layer_polar_boundary_policy':'all_safe'}
    req=Request(experiments=('exp',),domains=(3,),times_minutes=(0,5),heights_m=(100,1000),
                variables=('dbz_polar','temp_polar','dbz1_polar','temp1_polar'),source_root=tmp_path/'source',output_root=tmp_path/'out',options=options)
    reg=build_registry(options);storage=NetCDFStorage(req.output_root,time_origin=ORIGIN)
    y,x=np.indices((31,31),dtype=float)
    for minute in req.times_minutes:
        storage.save(Task('exp',3,minute,'slp_center',None),np.array([15,15]),reg.resolve('slp_center'),metadata={'grid_spacing':{'dx_km':1.,'dy_km':1.}})
        for height,center in ((100,[15,16]),(1000,[16,15])):
            for name,values in [('dbz',x+2*y+minute),('temp',280+.1*y+minute),('ua',x),('va',y)]:
                storage.save(Task('exp',3,minute,name,height),values,reg.resolve(name),metadata={'grid_spacing':{'dx_km':1.,'dy_km':1.}})
            storage.save(Task('exp',3,minute,'layer_center',height),np.asarray(center),reg.resolve('layer_center'),metadata={'grid_spacing':{'dx_km':1.,'dy_km':1.}})
    return req,reg,storage


def test_layer_audit_uses_shape_without_loading_payload():
    from wrf_processing_v3.layer_polar_safety import audit_group
    def read(name):
        assert name=='layer_center','安全检查不能为取shape读取整个源场'
        return [15,15]
    ctx=SimpleNamespace(task=SimpleNamespace(experiment='exp',domain=3,time=0,variable='dbz1_polar',height=100),
        options={'radius_km':5.},spec=SimpleNamespace(options={'source':'dbz','center':'layer_center'}),
        read=read,read_shape=lambda name:(31,31))
    assert audit_group([ctx])['all_safe']
    ctx.read=lambda name:[0,15]
    assert not audit_group([ctx])['all_safe']


def test_storage_shape_checks_slice_without_reading_data(tmp_path,monkeypatch):
    _,_,storage=seeded(tmp_path)
    monkeypatch.setattr(NetCDFStorage,'_read_data',staticmethod(lambda *args:pytest.fail('shape读取了数据')))
    assert storage.shape(Task('exp',3,0,'dbz',100))==(31,31)
    assert storage.shape(Task('exp',3,0,'slp_center',None))==(2,)
    with pytest.raises(KeyError):storage.shape(Task('exp',3,99,'dbz',100))


def test_shape_query_rejects_incomplete_slice(tmp_path):
    from netCDF4 import Dataset
    _,_,storage=seeded(tmp_path);task=Task('exp',3,0,'dbz',100)
    with Dataset(storage.path_for(task),'r+') as d:d['completed'][0,0]=0
    with pytest.raises(KeyError,match='未完成'):storage.shape(task)


def test_shape_reuses_verified_metadata_open(tmp_path,monkeypatch):
    from wrf_processing_v3.execution_cache import SessionStorage
    _,_,storage=seeded(tmp_path)
    cache=SessionStorage(storage);task=Task('exp',3,0,'dbz',100)
    cache.load_metadata(task)
    monkeypatch.setattr(NetCDFStorage,'shape',lambda *args:pytest.fail('尺寸查询重复打开文件'))
    assert cache.shape(task)==(31,31)


def test_polar_handler_reads_source_once_even_without_cache(tmp_path,monkeypatch):
    from wrf_processing_v3.context import ProcessingContext
    req,reg,_=seeded(tmp_path,cache=False)
    calls=Counter();original=ProcessingContext.read
    def read(self,name,*args,**kwargs):
        calls[name]+=1
        return original(self,name,*args,**kwargs)
    monkeypatch.setattr(ProcessingContext,'read',read)
    ProcessingManager(registry=reg,reader=CachedOnlyReader()).process(replace(req,times_minutes=(0,),heights_m=(100,),variables=('dbz_polar',)))
    assert calls['dbz']==1


@pytest.mark.parametrize('enabled',[False,True])
def test_polar_sampling_geometry_shared_only_for_equal_parameters(tmp_path,monkeypatch,enabled):
    from wrf_processing_v3 import common
    req,reg,storage=seeded(tmp_path,cache=enabled)
    calls=[];original=np.sin
    def sin(values,*args,**kwargs):
        if np.shape(values)==(360,1):calls.append(1)
        return original(values,*args,**kwargs)
    monkeypatch.setattr(common.np,'sin',sin)
    ProcessingManager(registry=reg,reader=CachedOnlyReader()).process(req)
    assert len(calls)==(6 if enabled else 16)
    for minute in req.times_minutes:
        for height in req.heights_m:
            for name in req.variables:
                field='dbz' if name.startswith('dbz') else 'temp'
                center='layer_center' if name.endswith('1_polar') else 'slp_center'
                cy,cx=storage.load(Task('exp',3,minute,center,height if center=='layer_center' else None))
                expected,_=common.cartesian_to_polar(storage.load(Task('exp',3,minute,field,height)),center_y=cy,center_x=cx,dx_km=1.,dy_km=1.,radius_km=5.,radius_resolution_km=1.)
                np.testing.assert_array_equal(storage.load(Task('exp',3,minute,name,height)),expected.astype(np.float32))


def test_sampling_cache_distinguishes_shape_center_spacing_radius_and_resolution(tmp_path):
    from wrf_processing_v3.common import cartesian_to_polar
    from wrf_processing_v3.execution_cache import SessionStorage
    storage=SessionStorage(NetCDFStorage(tmp_path,time_origin=ORIGIN));storage.begin_scope(('exp',3,0))
    cases=[((31,31),15.,15.,1.,1.,5.,1.),((31,31),15.25,15.,1.,1.,5.,1.),
           ((31,31),15.,15.,2.,1.,5.,1.),((31,31),15.,15.,1.,2.,5.,1.),
           ((31,31),15.,15.,1.,1.,3.,1.),((31,31),15.,15.,1.,1.,5.,2.),((33,31),15.,15.,1.,1.,5.,1.)]
    for shape,y,x,dx,dy,radius,resolution in cases*2:
        data=np.arange(np.prod(shape),dtype=float).reshape(shape)
        opts=dict(center_y=y,center_x=x,dx_km=dx,dy_km=dy,radius_km=radius,radius_resolution_km=resolution)
        a,am=cartesian_to_polar(data,**opts)
        b,bm=cartesian_to_polar(data,**opts,coordinate_cache=storage.cached_geometry)
        np.testing.assert_array_equal(a,b);assert am==bm


def test_geometry_cache_returns_shared_readonly_views(tmp_path):
    from wrf_processing_v3.execution_cache import SessionStorage
    cache=SessionStorage(NetCDFStorage(tmp_path,time_origin=ORIGIN))
    a=cache.cached_geometry(('grid',),lambda:np.arange(12.).reshape(2,6))
    with pytest.raises(ValueError):a[0,0]=999
    with pytest.raises(ValueError):a.setflags(write=True)
    b=cache.cached_geometry(('grid',),lambda:pytest.fail('重复计算几何'))
    assert np.shares_memory(a,b)
    np.testing.assert_array_equal(b,np.arange(12.).reshape(2,6))


@pytest.mark.parametrize('enabled',[False,True])
def test_fourier_waves_share_fft_but_keep_each_wave(tmp_path,monkeypatch,enabled):
    req,reg,storage=seeded(tmp_path,cache=enabled)
    angle=np.deg2rad(np.arange(360))[:,None]
    values=np.repeat(np.cos(angle)+.5*np.sin(2*angle),6,axis=1).astype(np.float32)
    storage.save(Task('exp',3,0,'dbz_polar',100),values,reg.resolve('dbz_polar'),metadata={'request_options': req.options, 'radius_resolution_km':1.,'grid_spacing':{'dx_km':1.,'dy_km':1.}})
    calls=[];original=np.fft.fft
    def fft(*args,**kwargs):calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(np.fft,'fft',fft)
    variables=('dbz_asy1w_polar','dbz_asy2w_polar')
    ProcessingManager(registry=reg,reader=CachedOnlyReader()).process(replace(req,times_minutes=(0,),heights_m=(100,),variables=variables))
    assert len(calls)==(1 if enabled else 2)
    from wrf_processing_v3.common import extract_wave
    for name,wave in zip(variables,(1,2)):
        np.testing.assert_array_equal(storage.load(Task('exp',3,0,name,100)),extract_wave(values,wave).astype(np.float32))
