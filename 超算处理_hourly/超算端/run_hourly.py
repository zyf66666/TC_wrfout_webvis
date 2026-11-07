#!/usr/bin/env python3
"""超算上逐小时处理 d03：中心（slp_center、center_geo）、轴平均最大风速、局地最大风速、
RMW、中心 5 km 平均气压；处理完自动导出小表并打成下载包（slp 全场最小值在导出时由 slp.nc 现算）。

算法与本地 wrf-webvis/处理数据_v3 完全相同（pipeline/ 是打包时的原样拷贝，中心方案 base =
Bezier PCC v1），输出是同一种 processed_v3 NetCDF 格式。

用法（在后处理目录下，即 ./WRF_0_sea_P1/run 所在的目录）：
    python tc_hourly_sc/run_hourly.py WRF_0_sea_P1 --dry-run      # 只看计划
    python tc_hourly_sc/run_hourly.py WRF_0_sea_P1                 # 实际计算 + 导出
    python tc_hourly_sc/run_hourly.py WRF_0_sea_P1 WRF_0_sea_P2    # 多个试验依次处理
"""

from __future__ import annotations

import os

# 必须在导入 netCDF4/HDF5 之前设置：很多超算的并行文件系统（Lustre/GPFS）不支持 HDF5 文件锁
os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import argparse
import errno
import fcntl
from pathlib import Path
import sys
import time
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
PIPELINE = HERE / "pipeline"
for _path in (str(PIPELINE), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# 与 processing_presets.json 里 basic_tc / center_intensity 组同一批变量（去掉需要陆地的 center_land_distance、r_34）
VARIABLES = (
    "slp_center", "center_geo", "center_slp_5km",
    "axis_mean_max_wind", "local_max_wind", "rmw",
)
DOMAIN = 3

_UNSUPPORTED_LOCK_ERRNOS = {
    getattr(errno, name) for name in ("ENOSYS", "EINVAL", "EOPNOTSUPP", "ENOTSUP", "ENOLCK", "EBADF")
    if hasattr(errno, name)
}
_flock_warned = False


def _install_tolerant_flock() -> None:
    """管线用 fcntl.flock 做文件锁；Lustre 等未挂 flock 的文件系统会直接报错。
    本脚本每个试验单进程串行写，锁不是必需的：遇到“不支持”时跳过并提示一次，真正的锁冲突照常抛出。"""
    original = fcntl.flock

    def flock(fd, operation):
        global _flock_warned
        try:
            return original(fd, operation)
        except BlockingIOError:
            raise
        except OSError as exc:
            if exc.errno in _UNSUPPORTED_LOCK_ERRNOS:
                if not _flock_warned:
                    print(f"  提示：此文件系统不支持 flock（{exc}），已跳过文件锁；"
                          "请不要让两个进程同时处理同一个试验。", flush=True)
                    _flock_warned = True
                return None
            raise

    fcntl.flock = flock


_settle_reported: set = set()


def _install_settle_filter(settle_minutes: float) -> None:
    """模式还在跑时，最近 settle_minutes 分钟内被修改过的 wrfout 可能正在写入：整份文件先不读，
    免得把写了一半的时次当成完成结果存下来（completed=1 之后重跑不会再覆盖）。
    管线自己的 SourceIndex 也走这里，所以计划、读取都看不到这些文件；下次重跑时它们写完了就会补上。"""
    from wrf_processing_v3.source_index import SourceIndex

    original = SourceIndex._paths

    def _paths(self, experiment, domain):
        paths = original(self, experiment, domain)
        if settle_minutes <= 0:
            return paths
        now = time.time()
        fresh = [p for p in paths if now - p.stat().st_mtime < settle_minutes * 60]
        if fresh and (experiment, domain) not in _settle_reported:
            _settle_reported.add((experiment, domain))
            names = ", ".join(p.name for p in fresh[:3]) + (" …" if len(fresh) > 3 else "")
            print(f"  跳过 {len(fresh)} 个最近 {settle_minutes:g} 分钟内还在变的文件（可能正在写入）：{names}", flush=True)
        return [p for p in paths if p not in fresh]

    SourceIndex._paths = _paths


def find_run_dir(root: Path, experiment: str, run_subdir: str) -> Path:
    candidate = (root / experiment / run_subdir) if run_subdir else (root / experiment)
    if not candidate.is_dir():
        raise FileNotFoundError(f"找不到 {candidate}（--root {root}、--run-subdir {run_subdir!r}）")
    files = sorted(candidate.glob(f"**/wrfout_d{DOMAIN:02d}*"))
    if not files:
        raise FileNotFoundError(f"{candidate} 下没有 wrfout_d{DOMAIN:02d}* 文件")
    return candidate


def available_minutes(experiment: str, run_dir: Path) -> dict[int, tuple[Path, int]]:
    """用管线自己的 SourceIndex 扫描 wrfout 时间（相对 START_DATE 的整数分钟）。"""
    from wrf_processing_v3.source_index import SourceIndex

    request = SimpleNamespace(source_root=run_dir, options={"wrfout_template": str(run_dir)})
    index = SourceIndex(request)
    mapping = index._scan(experiment, DOMAIN)
    index._cache[(experiment, DOMAIN)] = mapping
    print(f"  START_DATE = {index.time_origin(experiment, DOMAIN)}", flush=True)
    return mapping


def select_minutes(mapping, step: int, start_h: float | None, end_h: float | None) -> list[int]:
    minutes = sorted(mapping)
    if not minutes:
        return []
    files = sorted({str(path) for path, _ in mapping.values()})
    gaps = sorted({b - a for a, b in zip(minutes, minutes[1:])})
    print(f"  d03 共 {len(minutes)} 个时次、{len(files)} 个文件；"
          f"范围 {minutes[0] / 60:g}–{minutes[-1] / 60:g} h；输出间隔 {gaps[:3]} 分钟", flush=True)
    lo = minutes[0] if start_h is None else int(round(start_h * 60))
    hi = minutes[-1] if end_h is None else int(round(end_h * 60))
    first = ((lo + step - 1) // step) * step
    wanted = list(range(first, hi + 1, step))
    present = set(minutes)
    chosen = [m for m in wanted if m in present]
    missing = [m for m in wanted if m not in present]
    if missing:
        shown = ", ".join(f"{m / 60:g}" for m in missing[:20])
        print(f"  注意：缺 {len(missing)} 个整点时次（h）：{shown}{' …' if len(missing) > 20 else ''}", flush=True)
    return chosen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("experiments", nargs="+", help="试验名，即后处理目录下的文件夹名，如 WRF_0_sea_P1")
    parser.add_argument("--root", type=Path, default=Path("."), help="后处理目录（默认当前目录）")
    parser.add_argument("--run-subdir", default="run", help="wrfout 所在子目录（默认 run；直接在试验目录下就写 ''）")
    parser.add_argument("--out", type=Path, default=Path("tc_hourly_out"), help="输出目录（默认 ./tc_hourly_out）")
    parser.add_argument("--step", type=int, default=60, help="时间步长，分钟（默认 60 = 1 h）")
    parser.add_argument("--start-hour", type=float, default=None, help="起始积分小时（默认第一个时次）")
    parser.add_argument("--end-hour", type=float, default=None, help="结束积分小时（默认最后一个时次）")
    parser.add_argument("--dry-run", action="store_true", help="只显示依赖计划，不计算")
    parser.add_argument("--no-export", action="store_true", help="算完不导出下载包")
    parser.add_argument("--include-slp", action="store_true", help="下载包里也带 slp.nc（全场 SLP，较大）")
    parser.add_argument("--settle-minutes", type=float, default=30.0,
                        help="跳过最近这么多分钟内还在被修改的 wrfout（模式还在跑时防止读到写了一半的文件；默认 30，0 = 不跳过）")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    out = args.out if args.out.is_absolute() else (Path.cwd() / args.out)
    v3_root = out / "v3"
    _install_tolerant_flock()
    _install_settle_filter(args.settle_minutes)

    import project_config as pc  # pipeline/project_config.py（与本地同一份）

    pc.SOURCE_ROOT = root            # 必须在导入 process_data 之前改
    import process_data              # pipeline/process_data.py

    failures = []
    for experiment in args.experiments:
        print(f"\n========== {experiment} ==========", flush=True)
        try:
            run_dir = find_run_dir(root, experiment, args.run_subdir)
            print(f"  wrfout 目录：{run_dir}", flush=True)
            minutes = select_minutes(available_minutes(experiment, run_dir), args.step, args.start_hour, args.end_hour)
            if not minutes:
                raise RuntimeError("没有可处理的时次（模式刚开始跑、文件都还在写入时会这样，稍后再运行）")
            print(f"  将处理 {len(minutes)} 个时次：{minutes[0] / 60:g}–{minutes[-1] / 60:g} h，步长 {args.step} min", flush=True)
            print(f"  变量：{', '.join(VARIABLES)}", flush=True)
            print(f"  输出：{v3_root / experiment / f'd{DOMAIN:02d}'}", flush=True)

            pc.SOURCE_DIRECTORIES[experiment] = str(run_dir)  # process_data 引用的是同一个 dict
            request = process_data.build_request(
                experiment,
                dry_run=args.dry_run,
                output_root=v3_root,
                variables=VARIABLES,
                heights_m=(None,),
                center_scheme="base",
                times_minutes=tuple(minutes),
            )
            started = time.time()
            process_data.process_request(request)
            print(f"  用时 {(time.time() - started) / 60:.1f} min", flush=True)

            if not args.dry_run and not args.no_export:
                from export_hourly import export

                export(experiment, out, step_minutes=args.step, include_slp=args.include_slp,
                       source_dir=run_dir, n_times=len(minutes))
        except Exception as exc:  # 一个试验失败不影响后面的
            import traceback

            traceback.print_exc()
            print(f"  [{experiment}] 失败：{exc}", flush=True)
            failures.append(experiment)

    if failures:
        print(f"\n失败的试验：{', '.join(failures)}（日志里有详细错误）", flush=True)
        return 1
    print("\n全部完成。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
