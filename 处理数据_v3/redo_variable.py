#!/usr/bin/env python3
"""用当前配置把某个已重算过的变量再算一遍（例如改了半径），范围取中心重算时记录的原覆盖范围。

当前数据文件（及 .nc.lock、_safety.json）先改名移到备份目录，加 --label 后缀
（如 theta_anomaly1_polar.r300.nc），然后按 manifest_*.json 里该变量的切片重算并核对。
中断后再次运行即续算。

  python 处理数据_v3/redo_variable.py --experiment WRF_315_land \
      --variable theta_anomaly1_polar --label r300 [--run]
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from project_config import OPTIONS, OUTPUT_ROOT
from recompute_center_downstream import DEFAULT_BACKUP_ROOT, log, read_coverage, requests_for, slice_count
from wrf_processing_v3.common import polar_radius_km
from wrf_processing_v3.processing_manager import ProcessingManager


def original_entry(backup_dir: Path, variable: str) -> dict:
    for path in sorted(backup_dir.glob("manifest_*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if variable in manifest.get("variables", {}):
            return manifest["variables"][variable]
    raise KeyError(f"{backup_dir} 的 manifest 里没有 {variable}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--experiment", action="append", required=True)
    parser.add_argument("--variable", required=True)
    parser.add_argument("--label", required=True, help="另存当前文件时加的后缀，例如 r300")
    parser.add_argument("--domain", type=int, default=3)
    parser.add_argument("--tag", default="center_bezier_pcc_v1_20261013")
    parser.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT))
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    radius = polar_radius_km(dict(OPTIONS), args.variable, args.domain)
    for experiment in args.experiment:
        data_dir = Path(OUTPUT_ROOT) / experiment / f"d{args.domain:02d}"
        backup_dir = Path(args.backup_root) / args.tag / experiment / f"d{args.domain:02d}"
        entry = original_entry(backup_dir, args.variable)
        log(f"{experiment} {args.variable}：按当前配置重算（极坐标半径 {radius} km），原覆盖 {slice_count(entry)} 切片")
        if not args.run:
            continue
        for suffix in (".nc", ".nc.lock", "_safety.json"):
            source = data_dir / f"{args.variable}{suffix}"
            target = backup_dir / f"{args.variable}.{args.label}{suffix}"
            if source.exists() and not target.exists():
                os.rename(source, target)
                log(f"  另存 {source.name} → {target}")
            elif source.exists() and suffix == ".nc":
                log(f"  续算：{target.name} 已在备份中，保留数据目录里正在重算的新文件")
        computed = skipped = 0
        for request in requests_for(args.variable, entry, experiment, args.domain):
            summary = ProcessingManager().process(request)
            computed += len(summary["computed"])
            skipped += len(summary["skipped"])
        expected = {(int(t), h) for t, hs in entry["cells"].items() for h in ([None] if hs is None else hs)}
        got = {(int(t), h) for t, hs in read_coverage(data_dir / f"{args.variable}.nc")["cells"].items()
               for h in ([None] if hs is None else hs)}
        missing = sorted(expected - got, key=str)
        log(f"  计算 {computed} 切片，安全审计跳过 {skipped}；完成 {len(expected & got)}/{len(expected)}"
            + (f"，缺 {missing[:5]}" if missing else "，与原覆盖范围一致"))


if __name__ == "__main__":
    main()
