#!/usr/bin/env python3
"""独立服务器入口：手动增量处理 d03，导出强度曲线。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# 解压后的包优先；仓库内开发时只读取当前 v3 模块。
if not (HERE / "wrf_processing_v3").is_dir():
    sys.path.insert(0, str(HERE.parent / "处理数据_v3"))

INTENSITY_VARIABLES = ("slp_center", "center_geo", "axis_mean_max_wind", "center_slp_5km")
REQUIRED_FIELDS = ("P", "PB", "T", "QVAPOR", "PH", "PHB", "U", "V", "XLAT", "XLONG", "QRAIN")


def timestamp(text):
    return datetime.strptime(str(text).strip().replace("T", "_"), "%Y-%m-%d_%H:%M:%S")


def fingerprint(path):
    stat = path.stat()
    return {"path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


@dataclass
class SourceSnapshot:
    experiment: str
    origin: str
    records: dict
    fingerprints: dict
    deferred: list

    def verify(self, minute):
        path, _ = self.records[minute]
        if fingerprint(path) != self.fingerprints[path]:
            raise RuntimeError(f"源文件在本轮计算期间变化，暂缓此时次：{path}")

    def resolve(self, experiment, domain, minute):
        if experiment != self.experiment or domain != 3:
            raise ValueError("快照只对应当前试验的 d03")
        self.verify(minute)
        return self.records[minute]

    def time_origin(self, experiment, domain):
        if experiment != self.experiment or domain != 3:
            raise ValueError("快照只对应当前试验的 d03")
        return self.origin


def discover_sources(run_dir, *, experiment="exp", interval_minutes=60,
                     stability_seconds=2, min_age_seconds=120,
                     simulation_finished=False, time_origin=None):
    """检查源文件，固定本轮输入；不创建输出或源索引缓存。"""
    import numpy as np
    from netCDF4 import Dataset, chartostring

    run_dir = Path(run_dir).expanduser().resolve()
    if interval_minutes <= 0 or stability_seconds < 0 or min_age_seconds < 0:
        raise ValueError("时间间隔必须为正，稳定检查时间必须非负")
    if not run_dir.is_dir():
        raise FileNotFoundError(f"wrfout 目录不存在：{run_dir}")
    paths = sorted(path for path in run_dir.glob("wrfout_d03*") if path.is_file())
    if not paths:
        raise FileNotFoundError(f"目录中没有 wrfout_d03 文件：{run_dir}")
    before = {path: fingerprint(path) for path in paths}
    newest = max(item["mtime_ns"] for item in before.values())
    deferred, candidates = [], []
    for path in paths:
        if not simulation_finished and before[path]["mtime_ns"] == newest:
            deferred.append(f"暂缓最新文件（试验仍在运行）：{path}")
        elif time.time() - before[path]["mtime_ns"] / 1e9 < min_age_seconds:
            deferred.append(f"暂缓近期修改文件：{path}")
        else:
            candidates.append(path)
    if candidates and stability_seconds:
        time.sleep(stability_seconds)
    raw_records, starts, accepted = [], [], {}
    for path in candidates:
        try:
            if fingerprint(path) != before[path]:
                raise RuntimeError("文件大小或修改时间变化")
            with Dataset(path, "r") as ds:
                missing = set(("Times", *REQUIRED_FIELDS)) - set(ds.variables)
                if missing:
                    raise ValueError(f"缺少 WRF 字段：{', '.join(sorted(missing))}")
                if float(getattr(ds, "DX", 0)) <= 0 or float(getattr(ds, "DY", 0)) <= 0:
                    raise ValueError("缺少有效的 DX/DY 网格间距")
                times = [timestamp(value) for value in chartostring(ds["Times"][:])]
                if not times:
                    raise ValueError("文件没有完整时次")
                for name in REQUIRED_FIELDS:
                    var = ds[name]
                    if var.dimensions and var.dimensions[0] == "Time" and var.shape[0] < len(times):
                        raise ValueError(f"字段 {name} 时次尚未完整")
                start = str(getattr(ds, "START_DATE", "") or "")
            if fingerprint(path) != before[path]:
                raise RuntimeError("文件在检查期间变化")
            if start:
                starts.append(timestamp(start))
            raw_records.extend((value, path, index) for index, value in enumerate(times))
            accepted[path] = before[path]
        except (OSError, ValueError, RuntimeError) as error:
            deferred.append(f"暂缓无法确认完整的文件：{path}；{error}")
    origin = timestamp(time_origin) if time_origin else min(starts or [item[0] for item in raw_records], default=None)
    records = {}
    seen = set()
    for value, path, index in raw_records:
        seconds = (value - origin).total_seconds()
        minute = round(seconds / 60)
        if abs(seconds - minute * 60) > 1e-7 or minute < 0:
            raise ValueError(f"源时间不是相对固定起始时刻的非负整数分钟：{path}；{value}")
        if minute in seen:
            raise ValueError(f"重复源时间 {minute} 分钟，不能自动选择不同的 wrfout：{path}")
        seen.add(minute)
        if minute % interval_minutes == 0:
            records[minute] = (path, index)
    return SourceSnapshot(experiment, origin.strftime("%Y-%m-%d_%H:%M:%S") if origin else "", records, accepted, deferred)


def build_request(cfg, experiment, run_dir, minutes, origin, *, dry_run=True, variables=None):
    from wrf_processing_v3.request import Request
    return Request(
        experiments=(experiment,), domains=(3,), times_minutes=tuple(minutes),
        heights_m=tuple(cfg.HEIGHTS_M), variables=tuple(cfg.VARIABLES if variables is None else variables),
        source_root=Path(run_dir).expanduser().resolve(), output_root=Path(cfg.OUTPUT_ROOT).expanduser().resolve(),
        options={**cfg.OPTIONS, "time_origin": origin, "verbose": True}, dry_run=dry_run,
    )


def state_path(cfg, experiment):
    return Path(cfg.OUTPUT_ROOT).expanduser().resolve() / experiment / "server_state.json"


def load_state(cfg, experiment):
    path = state_path(cfg, experiment)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_state(cfg, experiment, state):
    path = state_path(cfg, experiment)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".server_state.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def scientific_settings(cfg):
    import wrf_processing_v3
    package = Path(wrf_processing_v3.__file__).parent
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        if not path.name.startswith("._"):
            digest.update(str(path.relative_to(package)).encode())
            digest.update(path.read_bytes())
    # JSON 规范化使整数键、tuple 与落盘后的表示完全一致。
    return json.loads(json.dumps({"domain": 3, "heights_m": list(cfg.HEIGHTS_M),
        "options": cfg.OPTIONS, "pipeline_sha256": digest.hexdigest()}))


def prepare_state(cfg, experiment, run_dir, snapshot, *, dry_run):
    state = load_state(cfg, experiment)
    settings = scientific_settings(cfg)
    directory = str(Path(run_dir).expanduser().resolve())
    if state:
        if state["science"] != settings:
            raise ValueError("科学参数、计算高度或模块版本已改变，请设置新的 OUTPUT_ROOT；禁止混用已有结果")
        if state["time_origin"] != snapshot.origin or state["source_directory"] != directory:
            raise ValueError("固定时间原点或源目录与已有结果不一致，请使用新的 OUTPUT_ROOT")
        return state
    if list((state_path(cfg, experiment).parent / "d03").glob("*.nc")):
        raise ValueError("目录已有未登记科学参数的 NetCDF，请使用新的 OUTPUT_ROOT")
    state = {"schema_version": 1, "time_origin": snapshot.origin, "science": settings,
             "source_directory": directory, "sources": {}}
    if not dry_run:
        save_state(cfg, experiment, state)
    return state


def invalidate_time(cfg, experiment, minute):
    """变化的源文件不允许沿用旧派生结果；下次运行会补算。"""
    import numpy as np
    from netCDF4 import Dataset
    from wrf_processing_v3.locking import file_lock
    for path in (state_path(cfg, experiment).parent / "d03").glob("*.nc"):
        with file_lock(path, exclusive=True):
            with Dataset(path, "r+") as ds:
                indices = np.where(np.asarray(ds["time_minutes"][:]) == minute)[0]
                for index in indices:
                    ds["completed"][index] = 0
                    if "lowest" in ds.groups:
                        ds.groups["lowest"]["completed"][index] = 0
                ds.sync()


def begin_time(cfg, experiment, snapshot, minute, state, *, dry_run):
    path, index = snapshot.records[minute]
    signature = {**snapshot.fingerprints[path], "record_index": index}
    previous = state["sources"].get(str(minute))
    changed = previous is not None and previous != signature
    if changed:
        print(f"[{experiment}] {minute}min 源文件已改变，{'将重新计算' if dry_run else '作废旧切片后重新计算'}", flush=True)
        if not dry_run:
            invalidate_time(cfg, experiment, minute)
    if not dry_run:
        state["sources"][str(minute)] = signature
        save_state(cfg, experiment, state)
    return changed


@contextmanager
def experiment_lock(cfg, experiment):
    """阻止两个运行入口同时更新同一试验的 manifest 和结果。"""
    from wrf_processing_v3.locking import file_lock
    with file_lock(state_path(cfg, experiment), exclusive=True, timeout_seconds=1):
        yield


def process_experiment(cfg, experiment, run_dir, *, simulation_finished=False, dry_run=True, backend=None):
    if not dry_run:
        with experiment_lock(cfg, experiment):
            return _process_experiment(cfg, experiment, run_dir, simulation_finished, dry_run, backend)
    return _process_experiment(cfg, experiment, run_dir, simulation_finished, dry_run, backend)


def _process_experiment(cfg, experiment, run_dir, simulation_finished, dry_run, backend):
    from wrf_processing_v3.processing_manager import ProcessingManager
    from wrf_processing_v3.wrf_reader import WrfReader
    old_state = load_state(cfg, experiment)
    configured_origin = cfg.TIME_ORIGINS.get(experiment)
    if old_state and configured_origin and configured_origin != old_state["time_origin"]:
        raise ValueError("TIME_ORIGINS 与已经固定的时间原点不一致")
    snapshot = discover_sources(run_dir, experiment=experiment, interval_minutes=cfg.INTERVAL_MINUTES,
        stability_seconds=cfg.STABILITY_CHECK_SECONDS, min_age_seconds=cfg.MIN_FILE_AGE_SECONDS,
        simulation_finished=simulation_finished, time_origin=(old_state or {}).get("time_origin", configured_origin))
    for message in snapshot.deferred:
        print(f"[{experiment}] {message}", flush=True)
    if not snapshot.records:
        print(f"[{experiment}] 当前没有可处理的整点时次。", flush=True)
        return not simulation_finished
    print(f"[{experiment}] 起始时间 {snapshot.origin}；{len(snapshot.records)} 个时次，"
          f"{min(snapshot.records)/60:g}–{max(snapshot.records)/60:g} h；{len(cfg.HEIGHTS_M)} 个高度", flush=True)
    state = prepare_state(cfg, experiment, run_dir, snapshot, dry_run=dry_run)
    manager = ProcessingManager(reader=WrfReader(index=snapshot, backend=backend))
    variables = tuple(cfg.VARIABLES)
    stages = [tuple(name for name in INTENSITY_VARIABLES if name in variables),
              tuple(name for name in variables if name not in INTENSITY_VARIABLES)]
    totals = {"computed": 0, "reused": 0, "planned": 0}
    okay = True
    # 强度先完成，再计算大体量的层中心和 dbz；失败时强度仍可导出。
    for stage in filter(None, stages):
        for minute in sorted(snapshot.records):
            try:
                changed = begin_time(cfg, experiment, snapshot, minute, state, dry_run=dry_run)
                request = build_request(cfg, experiment, run_dir, (minute,), snapshot.origin, dry_run=dry_run, variables=stage)
                if changed and dry_run:
                    from dataclasses import replace
                    request = replace(request, force=True, force_dependencies=True)
                snapshot.verify(minute)
                result = manager.process(request)
                snapshot.verify(minute)
                for name in totals:
                    totals[name] += len(result[name])
            except Exception as error:
                okay = False
                print(f"[{experiment}] 失败 {minute}min {','.join(stage)}：{error}", flush=True)
                if not dry_run:
                    try:
                        snapshot.verify(minute)
                    except (OSError, RuntimeError):
                        invalidate_time(cfg, experiment, minute)
    ending = f"，{totals['planned'] - totals['reused']} 个待计算" if dry_run else ""
    print(f"[{experiment}] {'预览' if dry_run else '处理'}：{totals['planned']} 个计划节点，"
          f"{totals['computed']} 个已计算，{totals['reused']} 个复用{ending}", flush=True)
    return okay


def load_config(path):
    path = Path(path).expanduser().resolve()
    spec = importlib.util.spec_from_file_location("_wrf_server_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if getattr(module, "DOMAIN", 3) != 3:
        raise ValueError("本代码包仅处理 d03；DOMAIN 必须为 3")
    if not isinstance(module.EXPERIMENTS, dict) or not module.EXPERIMENTS:
        raise ValueError("EXPERIMENTS 必须是非空的试验名称到 run 目录的映射")
    from wrf_processing_v3.request import Task
    for experiment in module.EXPERIMENTS:
        Task(experiment, 3, 0, "slp_center", None)
    if not isinstance(module.INTERVAL_MINUTES, int) or module.INTERVAL_MINUTES <= 0:
        raise ValueError("INTERVAL_MINUTES 必须是正整数分钟")
    return module


def check_python_version():
    if sys.version_info < (3, 10):
        version = ".".join(str(part) for part in sys.version_info[:3])
        raise RuntimeError(
            f"需要 Python 3.10 或更高版本，当前为 {version}；解释器：{sys.executable}。"
            "请使用 conda run -n wrf-server-tools python run_server.py --dry-run，"
            "或激活正确的 Python 环境。"
        )


def check_environment(*, plot_only=False):
    check_python_version()
    modules = ["numpy", "netCDF4", "matplotlib"] + ([] if plot_only else ["scipy", "wrf"])
    loaded = {}
    for name in modules:
        try:
            if name == "wrf":
                from wrf_processing_v3.wrf_reader import prepare_wrf_import
                prepare_wrf_import()
            loaded[name] = importlib.import_module(name)
        except (ImportError, OSError) as error:
            package = "wrf-python" if name == "wrf" else name
            if name == "wrf" and getattr(error, "name", None) == "pkg_resources":
                raise RuntimeError(
                    "wrf-python 依赖的 pkg_resources 无法导入。请在运行脚本的 Python 环境中安装 "
                    "setuptools<82；setuptools 82 起不再提供 pkg_resources。"
                ) from error
            raise RuntimeError(f"依赖 {package} 无法导入：{error}。请按 README.md 安装环境。") from error
    if not plot_only:
        import re
        wrf_version = getattr(loaded["wrf"], "__version__", "")
        netcdf_version = getattr(loaded["netCDF4"], "__version__", "")
        wrf_parts = tuple(map(int, re.findall(r"\d+", wrf_version)[:3]))
        # 不只按版本号推测；不同构建的迭代接口可能不同。
        dataset_is_iterable = hasattr(getattr(loaded["netCDF4"], "Dataset", None), "__iter__")
        if wrf_parts and wrf_parts < (1, 4, 1) and dataset_is_iterable:
            raise RuntimeError(
                f"wrf-python {wrf_version} 与 netCDF4 {netcdf_version} 的 Dataset 迭代接口不兼容，"
                "实际读取会报 Dataset is not picklable。请在当前环境安装 netcdf4=1.6.5，"
                "再运行；或使用已修复此问题的 wrf-python>=1.4.1。"
            )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="只检查和预览，不计算（默认）")
    modes.add_argument("--run", action="store_true", help="增量计算并导出强度图")
    modes.add_argument("--plot-only", action="store_true", help="只读取已有结果绘图，无需 wrfout 或 wrf-python")
    parser.add_argument("--config", type=Path, default=HERE / "server_config.py")
    parser.add_argument("--experiment", action="append", help="选择试验，可重复，默认配置中的全部试验")
    parser.add_argument("--output-root", type=Path, help="本次运行覆盖结果目录")
    parser.add_argument("--simulation-finished", action="store_true", help="试验结束后纳入稳定的最新文件")
    args = parser.parse_args(argv)
    try:
        # 先查版本，避免导入 v3 的 dataclass(slots=True) 时产生误导性报错。
        check_python_version()
        cfg = load_config(args.config)
        if args.output_root is not None:
            cfg.OUTPUT_ROOT = args.output_root.expanduser().resolve()
        experiments = list(dict.fromkeys(args.experiment or cfg.EXPERIMENTS))
        unknown = set(experiments) - set(cfg.EXPERIMENTS)
        if unknown:
            raise ValueError(f"配置中不存在试验：{', '.join(sorted(unknown))}")
        check_environment(plot_only=args.plot_only)
    except Exception as error:
        print(f"检查失败：{error}", flush=True)
        return 1
    failed = []
    if not args.plot_only:
        for experiment in experiments:
            try:
                print(f"[{experiment}] 开始", flush=True)
                if not process_experiment(cfg, experiment, cfg.EXPERIMENTS[experiment],
                        simulation_finished=args.simulation_finished, dry_run=not args.run):
                    failed.append(experiment)
            except Exception as error:
                failed.append(experiment)
                print(f"[{experiment}] 失败：{error}", flush=True)
    if args.run or args.plot_only:
        try:
            from plot_intensity import export_intensity
            outputs = export_intensity(Path(cfg.OUTPUT_ROOT).expanduser().resolve(), experiments, cfg.INTERVAL_MINUTES)
            for path in outputs:
                print(f"图片：{path}", flush=True)
        except Exception as error:
            failed.append("绘图")
            print(f"绘图失败（可用试验已独立导出）：{error}", flush=True)
    if failed:
        print(f"本次有失败项目：{', '.join(dict.fromkeys(failed))}；查看上方日志，修复后可重复运行。", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已中断；重新运行可补齐未完成切片。", flush=True)
        raise SystemExit(130)
