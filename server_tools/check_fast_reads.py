#!/usr/bin/env python3
"""只读核对（2026-11-02）：在一个真实 wrfout 时次上比较读取加速与原 getvar，并给出每帧各读取的耗时。

- 最低层 ua/va：只读最低层再去交错 vs getvar("ua"/"va")[0]
- mdbz：max(getvar("dbz"), axis=0) vs getvar("mdbz")
只打开 wrfout 读取，不写任何处理结果；报告写到 .runtime/fast_reads_check.json。

用法：python server_tools/check_fast_reads.py [试验名，默认 WRF_315_land] [domain，默认 3]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "处理数据_v3"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from netCDF4 import Dataset  # noqa: E402

import project_config  # noqa: E402
from wrf_processing_v3.wrf_reader import NetCDFBackend, prepare_wrf_import  # noqa: E402


def timed(function, repeat=1):
    started = time.perf_counter()
    for _ in range(repeat):
        value = function()
    return value, (time.perf_counter() - started) / repeat


def main(argv: list[str]) -> int:
    experiment = argv[1] if len(argv) > 1 else "WRF_315_land"
    domain = int(argv[2]) if len(argv) > 2 else 3
    directory = Path(project_config.SOURCE_DIRECTORIES[experiment])
    files = sorted(directory.glob(f"wrfout_d{domain:02d}_*"))
    if not files:
        print(f"{directory} 里没有 wrfout_d{domain:02d}_*（外接盘是否已接上？）")
        return 2
    path = files[len(files) // 2]  # 取中间一个文件，避开初始时刻
    prepare_wrf_import()
    from wrf import getvar, to_np

    backend = NetCDFBackend()
    report = {"file": str(path), "checks": {}, "seconds_per_frame": {}}
    with Dataset(path) as dataset:
        timeidx = 0

        def full(name, **kwargs):
            return np.ma.asarray(to_np(getvar(dataset, name, timeidx=timeidx, meta=False, **kwargs))).filled(np.nan)

        for name in ("ua", "va"):
            reference, slow = timed(lambda: full(name, units="m s-1")[0])
            fast_value, fast = timed(lambda: backend.getvar_lowest(dataset, name, timeidx, units="m s-1")[0])
            report["checks"][f"{name}_lowest"] = {
                "max_abs_diff": float(np.nanmax(np.abs(reference - fast_value))),
                "identical": bool(np.array_equal(reference, fast_value, equal_nan=True)),
            }
            report["seconds_per_frame"][f"{name}_full_getvar"] = slow
            report["seconds_per_frame"][f"{name}_lowest_only"] = fast
        dbz, dbz_seconds = timed(lambda: full("dbz"))
        mdbz, mdbz_seconds = timed(lambda: full("mdbz"))
        reuse, reuse_seconds = timed(lambda: np.max(dbz, axis=-3))
        report["checks"]["mdbz_from_dbz"] = {
            "max_abs_diff": float(np.nanmax(np.abs(mdbz - reuse))),
            "identical": bool(np.array_equal(mdbz, reuse, equal_nan=True)),
        }
        report["seconds_per_frame"].update({"dbz": dbz_seconds, "mdbz_getvar": mdbz_seconds, "mdbz_from_dbz": reuse_seconds})
        for name, kwargs in (("slp", {"units": "hPa"}), ("pressure", {}), ("z", {"units": "m"})):
            _, seconds = timed(lambda: full(name, **kwargs))
            report["seconds_per_frame"][name] = seconds
    passed = all(item["identical"] for item in report["checks"].values())
    report["all_identical"] = passed
    target = ROOT / ".runtime" / "fast_reads_check.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("FAST_READS_IDENTICAL" if passed else "FAST_READS_DIFFER")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
