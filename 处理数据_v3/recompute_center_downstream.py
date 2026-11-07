#!/usr/bin/env python3
"""中心算法改变后，重算某些试验的 slp_center 及其全部下游变量，并保留旧文件。

步骤（每个试验）：
1. 在 OUTPUT_ROOT/<试验>/d<domain> 中找出依赖 slp_center / layer_center 的已有变量
   （只沿同一 domain 的依赖追溯；例如经 d02 中心计算的 shear_200_850_* 不在其中）；
2. 读出每个文件已完成的 (时次, 高度) 切片，写入备份目录的 manifest.json；
3. 把这些文件（含 .nc.lock、*_safety.json）移到 BACKUP_ROOT/<tag>/<试验>/d<domain>/；
   与 OUTPUT_ROOT 同盘时只是改名，不复制数据；
4. 按依赖顺序逐个变量提交请求，只计算 manifest 中原有的切片；
5. 全部结束后核对新文件的完成切片与 manifest，并写 verify.json。

中断后重新运行同一命令即可继续：已移走的文件不会再移，已完成的切片按
cache_policy=exists 复用。默认 dry-run，只打印计划；加 --run 才会移动和计算。

可以分批：--only 只重算指定变量（slp_center、layer_center 总是包括在内），
--stage 给这一批起名（manifest_<stage>.json）。之后的批次会自动跳过备份目录里
已有旧文件的变量（说明已经重算过），不会把新结果当成旧结果再移走。

示例：
  python 处理数据_v3/recompute_center_downstream.py --experiment WRF_315_land
  python 处理数据_v3/recompute_center_downstream.py --experiment WRF_315_land --run
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import traceback

PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

import numpy as np
from netCDF4 import Dataset

from project_config import (
    OPTIONS, OUTPUT_ROOT, SOURCE_DIRECTORIES, SOURCE_ROOT, TIMES_MINUTES_BY_EXPERIMENT,
)
from wrf_processing_v3.processing_manager import ProcessingManager
from wrf_processing_v3.register import build_registry
from wrf_processing_v3.request import Request
from wrf_processing_v3.storage import read_completed_flags
from wrf_processing_v3.variables._center_options import slp_center_algorithm

CENTER_VARIABLES = ("slp_center", "layer_center")
DEFAULT_BACKUP_ROOT = Path(OUTPUT_ROOT).parent / f"{Path(OUTPUT_ROOT).name}_backup"


def log(message: str) -> None:
    print(f"[{time.strftime('%m-%d %H:%M:%S')}] {message}", flush=True)


def center_dependent(registry, domain: int) -> callable:
    memo: dict[str, bool] = {}

    def depends(name: str) -> bool:
        if name in CENTER_VARIABLES:
            return True
        if name in memo:
            return memo[name]
        memo[name] = False
        try:
            spec = registry.resolve(name)
        except (KeyError, ValueError):
            return False
        result = False
        for dependency in getattr(spec, "dependencies", ()):
            if dependency.domain not in (None, domain):
                continue
            if depends(dependency.name):
                result = True
                break
        memo[name] = result
        return result

    return depends


def ancestors(registry, name: str, pool: set[str], domain: int, memo: dict) -> set[str]:
    """``name`` 在 ``pool`` 内的全部（同 domain）上游变量。"""
    if name in memo:
        return memo[name]
    memo[name] = set()
    found: set[str] = set()
    try:
        spec = registry.resolve(name)
    except (KeyError, ValueError):
        return found
    for dependency in getattr(spec, "dependencies", ()):
        if dependency.domain not in (None, domain):
            continue
        if dependency.name in pool and dependency.name != name:
            found.add(dependency.name)
        found |= ancestors(registry, dependency.name, pool, domain, memo)
    memo[name] = found
    return found


def read_coverage(path: Path) -> dict:
    """返回 {"cells": {时次: [高度...] 或 None}}；高度含 'lowest'。"""
    with Dataset(path) as dataset:
        dataset.set_auto_mask(False)
        times = [int(v) for v in dataset["time_minutes"][:]]
        heights = [int(v) for v in dataset["height_m"][:]] if "height_m" in dataset.variables else None
        flags = np.asarray(read_completed_flags(dataset))
        lowest = dataset.groups.get("lowest")
        lowest_flags = (
            np.asarray(lowest["completed"][:]).reshape(-1)
            if lowest is not None and "completed" in lowest.variables
            else None
        )
    cells: dict[int, list | None] = {}
    for index, minute in enumerate(times):
        if heights is None:
            if index < flags.shape[0] and int(flags[index]) == 1:
                cells[minute] = None
            continue
        row = flags[index] if index < flags.shape[0] else np.zeros(0)
        done = [heights[j] for j in range(min(len(heights), row.shape[0])) if int(row[j]) == 1]
        if lowest_flags is not None and index < lowest_flags.shape[0] and int(lowest_flags[index]) == 1:
            done.append("lowest")
        if done:
            cells[minute] = done
    return {"surface": heights is None, "cells": cells}


def build_manifest(
    experiment: str, domain: int, exclude: set[str], extra: list[str],
    only: set[str] | None = None, backup_dir: Path | None = None,
) -> dict:
    data_dir = Path(OUTPUT_ROOT) / experiment / f"d{domain:02d}"
    registry = build_registry(dict(OPTIONS))
    depends = center_dependent(registry, domain)
    names = sorted(p.name[:-3] for p in data_dir.glob("*.nc") if not p.name.startswith("._"))
    selected = [n for n in names if depends(n) and n not in exclude]
    if only:
        unknown = sorted(only - set(selected))
        if unknown:
            raise ValueError(f"--only 中的变量不存在或不依赖 d{domain:02d} 中心：{', '.join(unknown)}")
        selected = [n for n in selected if n in only or n in CENTER_VARIABLES]
    if backup_dir is not None:
        # 已重算过：备份中有旧文件，或出现在之前批次的 manifest 里（例如原来没有、新算的变量）。
        earlier: set[str] = set()
        for path in backup_dir.glob("manifest_*.json"):
            try:
                earlier |= set(json.loads(path.read_text(encoding="utf-8")).get("variables", {}))
            except (OSError, ValueError):
                continue
        redone = [n for n in selected if (backup_dir / f"{n}.nc").exists() or n in earlier]
        if redone:
            log(f"  已重算过（备份中已有旧文件），本批跳过：{', '.join(redone)}")
        selected = [n for n in selected if n not in redone]
    variables = {}
    for name in selected:
        log(f"  读取覆盖范围 {name}")
        coverage = read_coverage(data_dir / f"{name}.nc")
        variables[name] = {
            "surface": coverage["surface"],
            "cells": {str(k): v for k, v in coverage["cells"].items()},
            "existing": True,
        }
    for name in extra:
        spec = registry.resolve(name)
        surface = spec.dimensions in {"surface", "scalar"}
        times = TIMES_MINUTES_BY_EXPERIMENT[experiment]
        if name in variables:
            # 已有变量：地面/标量变量补全到试验结束时次（原有切片照常重算）。
            if variables[name]["surface"]:
                before = len(variables[name]["cells"])
                for t in times:
                    variables[name]["cells"].setdefault(str(t), None)
                log(f"  {name}：时次由 {before} 补全到 {len(variables[name]['cells'])}")
            continue
        variables[name] = {
            "surface": surface,
            "cells": {str(t): None for t in times} if surface else {},
            "existing": False,
        }
    pool = set(variables)
    memo: dict = {}
    order = sorted(pool, key=lambda n: (len(ancestors(registry, n, pool, domain, memo)), n))
    return {
        "experiment": experiment,
        "domain": domain,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "slp_center_algorithm": slp_center_algorithm(dict(OPTIONS), domain),
        "order": order,
        "variables": variables,
    }


def requests_for(name: str, entry: dict, experiment: str, domain: int) -> list[Request]:
    """按“同一组高度”的时次把一个变量拆成若干请求。"""
    groups: dict[tuple, list[int]] = {}
    for minute, heights in entry["cells"].items():
        key = (None,) if entry["surface"] or heights is None else tuple(heights)
        groups.setdefault(key, []).append(int(minute))
    options = {
        **OPTIONS,
        "wrfout_template": str(SOURCE_DIRECTORIES[experiment]),
        "verbose": False,
        "force_variables": [],
        "force_slices": [],
    }
    return [
        Request(
            experiments=(experiment,),
            domains=(domain,),
            times_minutes=tuple(sorted(times)),
            heights_m=heights,
            variables=(name,),
            source_root=SOURCE_ROOT,
            output_root=Path(OUTPUT_ROOT),
            dry_run=False,
            options=options,
            dtype="float32",
        )
        for heights, times in sorted(groups.items(), key=lambda item: min(item[1]))
    ]


def slice_count(entry: dict) -> int:
    return sum(1 if v is None else len(v) for v in entry["cells"].values())


def move_old_files(manifest: dict, backup_dir: Path, resuming: bool = False) -> None:
    data_dir = Path(OUTPUT_ROOT) / manifest["experiment"] / f"d{manifest['domain']:02d}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name, entry in manifest["variables"].items():
        if not entry["existing"]:
            continue
        for suffix in (".nc", ".nc.lock", "_safety.json"):
            source = data_dir / f"{name}{suffix}"
            target = backup_dir / f"{name}{suffix}"
            if source.exists() and not target.exists():
                os.rename(source, target)
            elif source.exists() and target.exists() and suffix == ".nc":
                if resuming:
                    # 续算：旧文件上次已移入备份，现在数据目录里的是本次新算的文件，保留并接着算。
                    log(f"  续算：{name} 已有新文件（旧文件在备份中），保留新文件")
                    continue
                raise RuntimeError(f"旧文件和备份同时存在，请人工检查：{source} / {target}")
    log(f"  旧文件已移到 {backup_dir}")


def verify(manifest: dict) -> dict:
    data_dir = Path(OUTPUT_ROOT) / manifest["experiment"] / f"d{manifest['domain']:02d}"
    report = {}
    for name in manifest["order"]:
        entry = manifest["variables"][name]
        expected = {(int(t), h) for t, hs in entry["cells"].items() for h in ([None] if hs is None else hs)}
        path = data_dir / f"{name}.nc"
        got = set()
        if path.exists():
            for t, hs in read_coverage(path)["cells"].items():
                for h in ([None] if hs is None else hs):
                    got.add((int(t), h))
        missing = sorted(expected - got, key=str)
        report[name] = {"expected": len(expected), "completed": len(expected & got), "missing": [list(m) for m in missing[:50]]}
    return report


def run_experiment(experiment: str, domain: int, args) -> None:
    backup_dir = Path(args.backup_root) / args.tag / experiment / f"d{domain:02d}"
    manifest_path = backup_dir / f"manifest_{args.stage}.json"
    algorithm = slp_center_algorithm(dict(OPTIONS), domain)
    log(f"{experiment} d{domain:02d}：slp_center 算法 {algorithm[0]} {algorithm[1]}")
    resuming = manifest_path.exists()
    if resuming:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        log(f"  使用已有 manifest：{manifest_path}（续算）")
    else:
        manifest = build_manifest(
            experiment, domain, set(args.exclude), list(args.add),
            only=set(args.only) or None, backup_dir=backup_dir,
        )
        if args.run:
            backup_dir.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    total = sum(slice_count(manifest["variables"][n]) for n in manifest["order"])
    log(f"  变量 {len(manifest['order'])} 个，切片 {total} 个；顺序：{', '.join(manifest['order'])}")
    if not args.run:
        for name in manifest["order"]:
            entry = manifest["variables"][name]
            log(f"    {name}: {slice_count(entry)} 切片，{len(requests_for(name, entry, experiment, domain))} 个请求"
                + ("" if entry["existing"] else "（新增）"))
        log("  dry-run：未移动文件，未计算")
        return
    move_old_files(manifest, backup_dir, resuming=resuming)

    done = 0
    started = time.time()
    for name in manifest["order"]:
        entry = manifest["variables"][name]
        count = slice_count(entry)
        t0 = time.time()
        computed = skipped = 0
        for request in requests_for(name, entry, experiment, domain):
            last = [time.time()]

            def progress(message: str) -> None:
                if message.startswith("派生切片") and time.time() - last[0] > 60:
                    last[0] = time.time()
                    log(f"    {name}: {message}")

            summary = ProcessingManager().process(request, progress=progress)
            computed += len(summary["computed"])
            skipped += len(summary["skipped"])
        done += count
        elapsed = time.time() - started
        rate = elapsed / max(done, 1)
        log(
            f"  完成 {name}：{count} 切片（本次计算 {computed}，安全审计跳过 {skipped}），"
            f"{time.time() - t0:.0f}s；总进度 {done}/{total}，预计还需 {rate * (total - done) / 3600:.1f} h"
        )
    log("  核对新文件的完成切片...")
    report = verify(manifest)
    (backup_dir / f"verify_{args.stage}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    short = {n: r for n, r in report.items() if r["completed"] != r["expected"]}
    if short:
        for name, r in short.items():
            log(f"  ⚠ {name}: {r['completed']}/{r['expected']}，缺 {r['missing'][:5]}")
    else:
        log("  全部切片与原覆盖范围一致")
    log(f"{experiment} 完成，用时 {(time.time() - started) / 3600:.2f} h")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--experiment", action="append", required=True)
    parser.add_argument("--domain", type=int, default=3)
    parser.add_argument("--run", action="store_true", help="实际移动旧文件并计算（默认 dry-run）")
    parser.add_argument("--tag", default="center_bezier_pcc_v1_20261013", help="备份子目录名")
    parser.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT))
    parser.add_argument("--exclude", action="append", default=[], help="不重算的变量，可重复")
    parser.add_argument("--add", action="append", default=[], help="顺便新算的地面/标量变量（按试验结束时次）；已有的则补全到试验结束时次，可重复")
    parser.add_argument("--only", action="append", default=[], help="本批只重算这些变量（另加 slp_center、layer_center），可重复")
    parser.add_argument("--stage", default="all", help="本批名称，用于 manifest_<stage>.json / verify_<stage>.json")
    args = parser.parse_args(argv)
    for experiment in args.experiment:
        try:
            run_experiment(experiment, args.domain, args)
        except Exception:
            log(f"{experiment} 出错：\n{traceback.format_exc()}")
            raise


if __name__ == "__main__":
    main()
