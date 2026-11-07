"""标准 Manager/Planner/Executor 的缓存派生任务基准，独立输出，禁止 wrfout 读取。"""
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo
import argparse
import functools
import json
import resource
import sys

import numpy as np
from netCDF4 import Dataset

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB / '处理数据_v3'))
from project_config import OPTIONS
from wrf_processing_v3 import common, processing_manager, storage as storage_module
from wrf_processing_v3.dependency_planner import DependencyPlanner
from wrf_processing_v3.request import Request
from wrf_processing_v3.storage import NetCDFStorage

SOURCE = Path('/Volumes/land_1/processed_v3')
STATS = defaultdict(lambda: {'calls': 0, 'seconds': 0.})
OPENS = 0
TARGETS = {'axis_mean_max_wind', 'rmw', 'dbz_polar'}


class OverlayStorage(NetCDFStorage):
    def path_for_variable(self, experiment, domain, variable):
        root = self.root if variable in TARGETS else SOURCE
        return root / experiment / f'd{int(domain):02d}' / f'{variable}.nc'


class CachedOnlyReader:
    def prepare(self, tasks, *args, **kwargs):
        raise AssertionError(f'基准禁止读取或重算wrfout：{tasks}')


def timed(owner, method, label):
    original = getattr(owner, method)
    @functools.wraps(original)
    def call(*args, **kwargs):
        start = perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            STATS[label]['calls'] += 1
            STATS[label]['seconds'] += perf_counter() - start
    setattr(owner, method, call)


def verify(out, member, variables, times, heights):
    checked = 0
    for name in variables:
        with Dataset(out/member/'d03'/f'{name}.nc') as actual, Dataset(SOURCE/member/'d03'/f'{name}.nc') as reference:
            actual.set_auto_mask(False); reference.set_auto_mask(False)
            aidx = {int(t): i for i,t in enumerate(actual['time_minutes'][:])}
            ridx = {int(t): i for i,t in enumerate(reference['time_minutes'][:])}
            for t in times:
                if name.endswith('_polar'):
                    az = {int(z): i for i,z in enumerate(actual['height_m'][:])}
                    rz = {int(z): i for i,z in enumerate(reference['height_m'][:])}
                    for z in heights:
                        assert actual['completed'][aidx[t],az[z]] == 1
                        np.testing.assert_array_equal(actual['data'][aidx[t],az[z]], reference['data'][ridx[t],rz[z]])
                        checked += 1
                else:
                    assert actual['completed'][aidx[t]] == 1
                    np.testing.assert_array_equal(actual['data'][aidx[t]], reference['data'][ridx[t]])
                    checked += 1
    return checked


def main():
    global OPENS
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--cache', choices=('on','off'), default='on')
    parser.add_argument('--hours', type=int, default=6)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--polar', action='store_true')
    args = parser.parse_args()
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S')
    root = SOURCE.parent/'processed_v3_benchmarks'/f'{args.label}_{stamp}'
    root.mkdir(parents=True)
    report = {'label':args.label, 'root':str(root), 'runs':[],
              'scope':'包含Manager、依赖规划、读取/检查、计算与实际保存；已知time_origin且输入缓存齐全，禁止WRF读取；未测源索引扫描',
              'cache':args.cache, 'timing_note':'各分项存在嵌套，不可相加；首次与后续运行未强制清理系统缓存'}
    original_dataset = storage_module.Dataset
    def dataset(*a, **kw):
        global OPENS
        OPENS += 1
        return original_dataset(*a, **kw)
    storage_module.Dataset = dataset
    for name in ('load','load_metadata','exists','save','existing'):
        timed(NetCDFStorage, name, name)
    timed(DependencyPlanner, 'plan', 'plan')
    for name in ('azimuthal_mean','radial_tangential_wind','cartesian_to_polar'):
        timed(common, name, name)
    processing_manager.NetCDFStorage = OverlayStorage
    options = {**OPTIONS, 'verbose':False, 'time_origin':'2023-09-01_00:00:00', 'processing_cache':args.cache=='on'}
    times = tuple(range(0,args.hours*60+1,5))
    variables = ('axis_mean_max_wind','rmw')
    heights = (100,)
    if args.polar:
        times, variables, heights = (0,60,120), ('dbz_polar',), (2000,8000)
    for repeat in range(args.repeats):
        STATS.clear(); OPENS = 0
        output = root/f'run{repeat+1}'
        start = perf_counter(); summaries=[]
        for member in ('WRF_315_sea','WRF_315_land'):
            req = Request(experiments=(member,),domains=(3,),times_minutes=times, heights_m=heights,
                          variables=variables,source_root=SOURCE,output_root=output,options=options)
            completed = [0]
            def progress(message):
                if message.startswith('派生切片 '):
                    completed[0] += 1
                    if completed[0] % 500 == 0:
                        print(json.dumps({'event':'progress','label':args.label,'member':member,
                                          'completed':completed[0],'elapsed_seconds':perf_counter()-start},ensure_ascii=False),flush=True)
            result = processing_manager.ProcessingManager(reader=CachedOnlyReader()).process(req,progress=progress)
            summaries.append({'member':member,'computed':len(result['computed']),'reused':len(result['reused'])})
        seconds = perf_counter()-start
        stats = json.loads(json.dumps(STATS))
        opens = OPENS
        checked = sum(verify(output,member,variables,times,heights) for member in ('WRF_315_sea','WRF_315_land'))
        row = {'repeat':repeat+1,'seconds':seconds,'dataset_opens':opens,'stats':stats,
               'process_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
               'checked_values_or_slices':checked,'summaries':summaries}
        report['runs'].append(row)
        (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'label':args.label,'repeat':repeat+1,'seconds':seconds,'dataset_opens':opens,
                          'checked':checked,'report':str(root/'report.json')},ensure_ascii=False),flush=True)
    print('COMPLETE', root/'report.json',flush=True)


if __name__=='__main__':
    main()
