"""网页预览→真实隔离子进程处理→完成覆盖→重复使用与强制重算。"""
import numpy as np
from fastapi.testclient import TestClient


def test_web_polar_job_and_force_recompute_preserve_results(tmp_path,monkeypatch):
    from wrf_webvis.app.main import app
    from wrf_webvis.app.api import data as data_api, processing_v3 as processing_api, jobs as jobs_api
    from wrf_webvis.app.services import progress as progress_module
    from wrf_webvis.app.services.progress import ProgressStore
    from wrf_webvis.tests.test_v3_management import _service
    from 处理数据_v3.wrf_processing_v3.register import build_registry
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage
    from 处理数据_v3.wrf_processing_v3.common import cartesian_to_polar

    service=_service(tmp_path)
    service.default_options={'radius_km_by_domain':{'3':5.},'radius_resolution_km':1.,'layer_polar_boundary_policy':'all_safe'}
    registry=build_registry(service.default_options)
    storage=NetCDFStorage(service.output_root,time_origin='2023-09-01_00:00:00')
    meta={'grid_spacing':{'dx_km':1.,'dy_km':1.},'request_options':service.default_options}
    y,x=np.indices((31,31),dtype=float)
    for minute in (0,5):
        storage.save(Task('exp',3,minute,'slp_center',None),np.array([15,15]),registry.resolve('slp_center'),metadata=meta)
        for height in (100,1000):
            storage.save(Task('exp',3,minute,'dbz',height),x+2*y+minute,registry.resolve('dbz'),metadata=meta)
            storage.save(Task('exp',3,minute,'layer_center',height),np.array([15,16]),registry.resolve('layer_center'),metadata=meta)
    store=ProgressStore()
    for owner in (progress_module,processing_api,jobs_api):monkeypatch.setattr(owner,'progress_store',store)
    monkeypatch.setattr(data_api,'service',service)
    monkeypatch.setattr(processing_api,'service',service)
    client=TestClient(app,client=('127.0.0.1',50000))
    payload={'experiment':'exp','domain':3,'variables':['dbz_polar','dbz1_polar'],
             'times_minutes':[0,5],'heights_m':[100,1000],'profile_id':'default'}
    plan=client.post('/api/processing/v3/plan',json=payload)
    assert plan.status_code==200
    assert plan.json()['counts']['compute']==8
    assert not plan.json()['missing_source_minutes']
    assert not [x for x in plan.json()['tasks'] if x['status']=='compute' and x['variable'] in ('dbz','pressure','slp','layer_center','slp_center')]
    job=client.post('/api/processing/v3/jobs',json={**payload,'plan_hash':plan.json()['plan_hash']})
    assert job.status_code==200
    status=client.get(f"/api/jobs/{job.json()['job_id']}/progress").json()
    assert status['status']=='complete',status.get('error')
    assert len(status['result']['computed'])==8
    for name in payload['variables']:
        coverage=client.get('/api/data/coverage',params={'experiment':'exp','domain':3,'variable':name})
        assert coverage.status_code==200 and len(coverage.json()['completed'])==4
        for minute in (0,5):
            for height in (100,1000):
                cy,cx=(15,16) if name=='dbz1_polar' else (15,15)
                expected,_=cartesian_to_polar(x+2*y+minute,center_y=cy,center_x=cx,dx_km=1.,dy_km=1.,radius_km=5.,radius_resolution_km=1.)
                np.testing.assert_array_equal(storage.load(Task('exp',3,minute,name,height)),expected.astype(np.float32))
    repeated=client.post('/api/processing/v3/plan',json=payload).json()
    assert repeated['counts']['compute']==0
    force={**payload,'force':True}
    preview=client.post('/api/processing/v3/plan',json=force).json()
    assert preview['counts']['compute']==8
    submitted=client.post('/api/processing/v3/jobs',json={**force,'plan_hash':preview['plan_hash']}).json()
    assert client.get(f"/api/jobs/{submitted['job_id']}/progress").json()['status']=='complete'
