#!/usr/bin/env python3
"""Compare the approved WRF_315_land pilot slices between v2 and v3."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from wrf_processing_v3.comparison import compare_task
from wrf_processing_v3.npy_storage import NpyStorage
from wrf_processing_v3.register import build_registry
from wrf_processing_v3.request import Task
from wrf_processing_v3.storage import NetCDFStorage


EXPERIMENT = "WRF_315_land"
DOMAIN = 3
TIMES_MINUTES = (0, 60, 120)
HEIGHTS_M = (100, 1000, 15000)
VARIABLES = ("theta", "theta_anomaly", "theta_anomaly1_polar")
TIME_ORIGIN = "2023-09-01_00:00:00"


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("/Volumes/land_1/processed_v2"))
    parser.add_argument("--output-root", type=Path, default=Path("/Volumes/land_1/processed_v3"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    options = {
        "radius_resolution_km": 1.0,
        "theta_environment_domain": 2,
        "theta_environment_ring_km": [400.0, 500.0],
    }
    registry = build_registry(options, times_minutes=TIMES_MINUTES)
    source = NpyStorage(args.source_root)
    target = NetCDFStorage(args.output_root, time_origin=TIME_ORIGIN)
    reports = []
    for variable in VARIABLES:
        for minute in TIMES_MINUTES:
            for height in HEIGHTS_M:
                task = Task(EXPERIMENT, DOMAIN, minute, variable, height)
                reports.append(
                    compare_task(
                        task,
                        source=source,
                        target=target,
                        expected_units=registry.resolve(variable).units,
                        expected_radius_resolution_km=1.0,
                    )
                )
    required = (
        "shape_match",
        "dtype_match",
        "nan_mask_match",
        "values_match",
        "units_match",
        "coordinates_match",
    )
    payload = {
        "experiment": EXPERIMENT,
        "domain": DOMAIN,
        "time_origin": TIME_ORIGIN,
        "slice_count": len(reports),
        "all_match": all(all(report[name] for name in required) for report in reports),
        "reports": reports,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    return payload


if __name__ == "__main__":
    main()
