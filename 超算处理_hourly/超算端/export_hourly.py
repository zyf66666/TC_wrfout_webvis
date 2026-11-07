#!/usr/bin/env python3
"""把处理结果导出成小表 + 下载包。run_hourly.py 算完会自动调用；也可以单独重跑：

    python tc_hourly_sc/export_hourly.py WRF_0_sea_P1 [WRF_0_sea_P2 ...] [--out tc_hourly_out]

生成：
    tc_hourly_out/download/tc_hourly_<试验>.tar.gz   ← 下载这个
      └─ <试验>/<试验>_hourly.csv      逐小时表（列说明见 README）
         <试验>/d03/*.nc               processed_v3 格式的结果文件（可直接放进本地 processed_v3/post）
         <试验>/run_info.json          来源、时次、软件版本
"""

from __future__ import annotations

import os

os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")

import argparse
from datetime import datetime
import json
from pathlib import Path
import platform
import shutil
import sys
import tarfile

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import numpy as np

from v3_table import RESULT_VARIABLES, build_table, column, write_csv

DOMAIN_DIR = "d03"


def _package_version() -> str:
    try:
        return (HERE / "VERSION.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def export(
    experiment: str,
    out: Path,
    *,
    step_minutes: int = 60,
    include_slp: bool = False,
    source_dir: Path | None = None,
    n_times: int | None = None,
) -> Path:
    out = Path(out)
    domain_dir = out / "v3" / experiment / DOMAIN_DIR
    if not domain_dir.is_dir():
        raise FileNotFoundError(f"没有处理结果目录 {domain_dir}，先运行 run_hourly.py")
    print(f"  导出 {experiment}：读取 {domain_dir}", flush=True)
    rows = build_table(domain_dir, step_minutes=step_minutes)
    if not rows:
        raise RuntimeError("结果文件里没有已完成的时次")

    stage = out / "download" / experiment
    if stage.exists():
        shutil.rmtree(stage)  # 只删本脚本自己生成的暂存目录
    (stage / DOMAIN_DIR).mkdir(parents=True)
    csv_path = write_csv(rows, stage / f"{experiment}_hourly.csv")

    names = list(RESULT_VARIABLES) + (["slp"] if include_slp else [])
    copied = []
    for name in names:
        source = domain_dir / f"{name}.nc"
        if source.exists():
            shutil.copy2(source, stage / DOMAIN_DIR / source.name)
            copied.append(source.name)

    def _summary(key):
        values = column(rows, key)
        good = values[np.isfinite(values)]
        return None if good.size == 0 else {"min": float(good.min()), "max": float(good.max()), "n": int(good.size)}

    info = {
        "experiment": experiment,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_dir": str(source_dir) if source_dir else None,
        "domain": DOMAIN_DIR,
        "step_minutes": step_minutes,
        "times_requested": n_times,
        "times_in_table": len(rows),
        "first_hour": rows[0]["time_h"],
        "last_hour": rows[-1]["time_h"],
        "files": copied,
        "summary": {key: _summary(key) for key in ("vmax_axis_mean_ms", "vmax_local_ms", "slp_min_hPa", "rmw_km")},
        "center_scheme": "base (bezier_pcc_v1, d03 independent, 60 km search)",
        "package_version": _package_version(),
        "python": platform.python_version(),
        "host": platform.node(),
    }
    (stage / "run_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    tar_path = out / "download" / f"tc_hourly_{experiment}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as archive:
        archive.add(stage, arcname=experiment)
    size_mb = tar_path.stat().st_size / 1024 / 1024

    # 屏幕上给一个快速核对
    s = info["summary"]
    def fmt(item, unit, use="max"):
        return "—" if not item else f"{item[use]:.1f} {unit}"
    print(f"  表：{csv_path}（{len(rows)} 行，{info['first_hour']:g}–{info['last_hour']:g} h）", flush=True)
    print(f"  峰值：轴平均最大风 {fmt(s['vmax_axis_mean_ms'], 'm/s')}，局地最大风 {fmt(s['vmax_local_ms'], 'm/s')}，"
          f"最低气压 {fmt(s['slp_min_hPa'], 'hPa', 'min')}", flush=True)
    print(f"  下载包：{tar_path.resolve()}（{size_mb:.1f} MB）", flush=True)
    return tar_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("experiments", nargs="+")
    parser.add_argument("--out", type=Path, default=Path("tc_hourly_out"))
    parser.add_argument("--step", type=int, default=60)
    parser.add_argument("--include-slp", action="store_true")
    args = parser.parse_args(argv)
    status = 0
    for experiment in args.experiments:
        try:
            export(experiment, args.out, step_minutes=args.step, include_slp=args.include_slp)
        except Exception as exc:
            print(f"  [{experiment}] 导出失败：{exc}", flush=True)
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
