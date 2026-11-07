#!/usr/bin/env python3
"""Migrate explicitly selected processed_v2 NPY slices to v3 NetCDF4."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from wrf_processing_v3.migration import migrate_tasks
from wrf_processing_v3.npy_storage import NpyStorage
from wrf_processing_v3.register import build_registry
from wrf_processing_v3.request import Task
from wrf_processing_v3.storage import NetCDFStorage


def _height(value: str) -> int | str | None:
    text = value.strip().lower()
    if text in {"none", "surface", "scalar"}:
        return None
    if text == "lowest":
        return text
    return int(text)


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--domain", type=int, required=True)
    parser.add_argument("--variable", action="append", required=True)
    parser.add_argument("--time-minute", action="append", type=int, required=True)
    parser.add_argument("--height-m", action="append", type=_height, required=True)
    parser.add_argument("--time-origin", required=True, help="WRF START_DATE")
    parser.add_argument("--source-root", type=Path, default=Path("/Volumes/land_1/processed_v2"))
    parser.add_argument("--output-root", type=Path, default=Path("/Volumes/land_1/processed_v3"))
    parser.add_argument("--radius-resolution-km", type=float, default=1.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-verify", action="store_true")
    args = parser.parse_args(argv)

    options = {"radius_resolution_km": args.radius_resolution_km}
    registry = build_registry(options, times_minutes=args.time_minute)
    tasks: list[Task] = []
    for variable in args.variable:
        spec = registry.resolve(variable)
        heights = (None,) if spec.dimensions in {"surface", "scalar"} else args.height_m
        tasks.extend(
            Task(args.experiment, args.domain, minute, variable, height)
            for minute in args.time_minute
            for height in heights
        )
    result = migrate_tasks(
        tasks,
        source=NpyStorage(args.source_root),
        target=NetCDFStorage(args.output_root, time_origin=args.time_origin),
        registry=registry,
        processing_options=options,
        force=args.force,
        verify=not args.no_verify,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    main()
