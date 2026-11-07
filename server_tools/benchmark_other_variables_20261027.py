"""多变量polar真实缓存基准：输入只读，目标独立目录，逐值对照改前公式。"""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from time import perf_counter
import argparse
import importlib.util
import json
import resource
import sys

import numpy as np
from netCDF4 import Dataset

WEB=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(WEB/'处理数据_v3'))
from project_config import OPTIONS,OUTPUT_ROOT
from wrf_processing_v3 import processing_manager
from wrf_processing_v3.storage import NetCDFStorage
from wrf_processing_v3.request import Request,Task
import wrf_processing_v3.executor
import wrf_processing_v3.variables.polar

FIELDS=('dbz','temp','ua','va')
TARGETS=tuple(name+suffix for name in FIELDS for suffix in ('_polar','1_polar'))
TIMES=(0,60,120)
HEIGHTS=(2000,4000,6000,8000)


class OverlayStorage(NetCDFStorage):
    def path_for_variable(self,experiment,domain,variable):
        return (self.root if variable in TARGETS else OUTPUT_ROOT)/experiment/f'd{domain:02d}'/f'{variable}.nc'
    def save(self,task,*args,**kwargs):
        assert task.variable in TARGETS,'拒绝向研究输入缓存写入'
        return super().save(task,*args,**kwargs)


class CachedOnlyReader:
    def prepare(self,*args,**kwargs):
        raise AssertionError('缺少缓存，基准禁止读取wrfout')


def main():
    global TARGETS,HEIGHTS
    parser=argparse.ArgumentParser();parser.add_argument('--label',required=True);parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--before',action='store_true');parser.add_argument('--dense-layer',action='store_true')
    args=parser.parse_args()
    backup=Path('/tmp/polar_web_optimization_backup.txt').read_text().strip()
    old=Path(backup)/'处理数据_v3/wrf_processing_v3/common.py'
    spec=importlib.util.spec_from_file_location('before_polar_math',old);reference=importlib.util.module_from_spec(spec);spec.loader.exec_module(reference)
    if args.dense_layer:
        TARGETS=('dbz1_polar','temp1_polar')
        HEIGHTS=tuple(range(100,1001,100))+tuple(range(1250,10001,250))
    if args.before:
        # 在隔离基准进程内加载改前处理器，生产模块文件不回退。
        import wrf_processing_v3.common as common_module
        common_module.cartesian_to_polar=reference.cartesian_to_polar
        for rel,name in [('layer_polar_safety.py','wrf_processing_v3._before_audit'),('variables/polar.py','wrf_processing_v3.variables._before_polar')]:
            file=Path(backup)/'处理数据_v3/wrf_processing_v3'/rel
            module_spec=importlib.util.spec_from_file_location(name,file);module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(module)
            if rel.startswith('variables'):
                wrf_processing_v3.variables.polar.process=module.process
            else:
                wrf_processing_v3.executor.audit_group=module.audit_group
    out=OUTPUT_ROOT.parent/'processed_v3_benchmarks'/(args.label+'_'+datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S'))
    out.mkdir(parents=True)
    processing_manager.NetCDFStorage=OverlayStorage
    options={**OPTIONS,'verbose':False,'time_origin':'2023-09-01_00:00:00'}
    original=NetCDFStorage.load;loads=[]
    def load(self,task):
        loads.append(task.variable)
        return original(self,task)
    NetCDFStorage.load=load
    report={'root':str(out),'before_math_source':str(old),'variables':TARGETS,'heights_m':HEIGHTS,'times_minutes':TIMES,'runs':[]}
    print('BASELINE_MODULES_LOADED',flush=True)
    for repeat in range(args.repeats):
        loads.clear();dest=out/f'run{repeat+1}'
        start=perf_counter()
        for member in ('WRF_315_sea','WRF_315_land'):
            req=Request(experiments=(member,),domains=(3,),times_minutes=TIMES,heights_m=HEIGHTS,
                        variables=TARGETS,source_root=OUTPUT_ROOT,output_root=dest,options=options)
            processing_manager.ProcessingManager(reader=CachedOnlyReader()).process(req)
        seconds=perf_counter()-start;read_count=len(loads);checked=0
        source=NetCDFStorage(OUTPUT_ROOT,time_origin=options['time_origin'])
        target=NetCDFStorage(dest,time_origin=options['time_origin'])
        for member in ('WRF_315_sea','WRF_315_land'):
            for minute in TIMES:
                for height in HEIGHTS:
                    for name in TARGETS:
                        field=next(f for f in FIELDS if name in (f+'_polar',f+'1_polar'))
                        center_name='layer_center' if name.endswith('1_polar') else 'slp_center'
                        center_height=height if center_name=='layer_center' else None
                        cy,cx=source.load(Task(member,3,minute,center_name,center_height))
                        data=source.load(Task(member,3,minute,field,height))
                        expected,_=reference.cartesian_to_polar(data,center_y=float(cy),center_x=float(cx),dx_km=1.,dy_km=1.,radius_km=300.,radius_resolution_km=1.)
                        t=Task(member,3,minute,name,height)
                        assert target.exists(t)
                        np.testing.assert_array_equal(target.load(t),expected.astype(np.float32))
                        checked+=1
        row={'seconds':seconds,'payload_loads':read_count,'checked_slices':checked,'process_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
        report['runs'].append(row)
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'label':args.label,'repeat':repeat+1,**row,'report':str(out/'report.json')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
