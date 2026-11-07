#!/usr/bin/env python3
"""补算层中心极坐标变量（*1_polar）因安全审计整时次跳过的时次，只算到 --max-height。

layer_polar_boundary_policy=all_safe 时，同一时次只要有一层的安全半径小于请求半径，
整个时次的所有高度都会被跳过。高层（如 12–15 km）层中心偏离网格中心较远时最常见。
本脚本找出数据文件里“有时次坐标但一个高度都没完成”的时次，按 max-height 以下的
标准高度重新提交：审计只针对这些高度，因此低层可以补齐，高层仍留空。
安全审计报告 <变量>_safety.json 会把新审计合并进原报告（原报告另存为 .before_fill.json）。

  python 处理数据_v3/fill_layer_polar_gaps.py --experiment WRF_315_land \
      --variable theta_anomaly1_polar --max-height 12000 [--run]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from project_config import OPTIONS, OUTPUT_ROOT, SOURCE_DIRECTORIES, SOURCE_ROOT
from recompute_center_downstream import log, read_coverage
from wrf_processing_v3.processing_manager import ProcessingManager
from wrf_processing_v3.request import Request
from wrf_processing_v3.storage import DEFAULT_HEIGHTS_M
from netCDF4 import Dataset


def missing_times(path: Path) -> list[int]:
    with Dataset(path) as dataset:
        dataset.set_auto_mask(False)
        times = [int(v) for v in dataset["time_minutes"][:]]
    done = read_coverage(path)["cells"]
    return sorted(t for t in times if not done.get(t))


def merge_report(report: Path, before: Path, gap_times: set[int]) -> None:
    old = json.loads(before.read_text(encoding="utf-8"))
    new = json.loads(report.read_text(encoding="utf-8"))
    by_time = {int(a["time"]): a for a in old.get("audits", [])}
    for audit in new.get("audits", []):
        if int(audit["time"]) in gap_times:
            audit = {**audit, "note": "gap fill: heights limited by fill_layer_polar_gaps.py"}
            by_time[int(audit["time"])] = audit
    merged = {
        **old,
        "audits": [by_time[t] for t in sorted(by_time)],
        "selected_times": sorted(set(old.get("selected_times", [])) | set(new.get("selected_times", []))),
    }
    report.write_text(json.dumps(merged, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--experiment", action="append", required=True)
    parser.add_argument("--variable", default="theta_anomaly1_polar")
    parser.add_argument("--domain", type=int, default=3)
    parser.add_argument("--max-height", type=int, default=12000)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    heights = tuple(h for h in DEFAULT_HEIGHTS_M if h <= args.max_height)
    for experiment in args.experiment:
        data_dir = Path(OUTPUT_ROOT) / experiment / f"d{args.domain:02d}"
        path = data_dir / f"{args.variable}.nc"
        gaps = missing_times(path)
        log(f"{experiment} {args.variable}：整时次缺失 {len(gaps)} 个 {gaps}；补算高度 {heights[0]}–{heights[-1]} m（{len(heights)} 层）")
        if not gaps or not args.run:
            continue
        report = data_dir / f"{args.variable}_safety.json"
        before = data_dir / f"{args.variable}_safety.before_fill.json"
        if report.exists() and not before.exists():
            shutil.copy2(report, before)
        request = Request(
            experiments=(experiment,), domains=(args.domain,), times_minutes=tuple(gaps),
            heights_m=heights, variables=(args.variable,), source_root=SOURCE_ROOT,
            output_root=Path(OUTPUT_ROOT), dry_run=False, dtype="float32",
            options={**OPTIONS, "wrfout_template": str(SOURCE_DIRECTORIES[experiment]),
                     "verbose": False, "force_variables": [], "force_slices": []},
        )
        summary = ProcessingManager().process(request)
        log(f"  计算 {len(summary['computed'])} 切片，安全审计跳过 {len(summary['skipped'])}")
        if before.exists() and report.exists():
            merge_report(report, before, set(gaps))
        still = missing_times(path)
        log(f"  补算后仍整时次缺失：{still if still else '无'}")


if __name__ == "__main__":
    main()
