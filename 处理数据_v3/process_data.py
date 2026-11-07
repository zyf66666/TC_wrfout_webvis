#!/usr/bin/env python3
"""Process the configured v3 WRF experiments (dry-run unless --run is given)."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from wrf_processing_v3.processing_manager import ProcessingManager
from wrf_processing_v3.request import Request


if __package__:
    from .project_config import (
        EXPERIMENTS, DOMAINS, TIMES_MINUTES, EXPERIMENT_END_HOURS,
        TIMES_MINUTES_BY_EXPERIMENT, HEIGHTS_M, VARIABLES, SOURCE_ROOT,
        WARM_CORE_VARIABLES, WARM_CORE_HEIGHTS_M,
        SOURCE_DIRECTORIES, OUTPUT_ROOT, OPTIONS, CENTER_SCHEME, DATASETS,
    )
else:
    from project_config import (
        EXPERIMENTS, DOMAINS, TIMES_MINUTES, EXPERIMENT_END_HOURS,
        TIMES_MINUTES_BY_EXPERIMENT, HEIGHTS_M, VARIABLES, SOURCE_ROOT,
        WARM_CORE_VARIABLES, WARM_CORE_HEIGHTS_M,
        SOURCE_DIRECTORIES, OUTPUT_ROOT, OPTIONS, CENTER_SCHEME, DATASETS,
    )


# 功能说明：把变量按处理高度分组：暖心变量一组（到 15000 m），其余一组（到 10000 m），保持原有顺序。
def height_groups(variables) -> list[tuple[tuple[str, ...], tuple[int, ...]]]:
    names = tuple(variables)
    warm = tuple(name for name in names if name in WARM_CORE_VARIABLES)
    other = tuple(name for name in names if name not in WARM_CORE_VARIABLES)
    groups = []
    if other:
        groups.append((other, HEIGHTS_M))
    if warm:
        groups.append((warm, WARM_CORE_HEIGHTS_M))
    return groups


def build_request(
    experiment: str,
    *,
    dry_run: bool = True,
    output_root: str | Path = OUTPUT_ROOT,
    variables: tuple[str, ...] | None = None,
    force_variables: tuple[str, ...] = (),
    force_slices: tuple[dict[str, object], ...] = (),
    heights_m: tuple[int, ...] | None = None,
    center_scheme: str | None = CENTER_SCHEME,
    times_minutes: tuple[int, ...] | None = None,
) -> Request:
    selected = VARIABLES if variables is None else variables
    if heights_m is None:
        groups = height_groups(selected)
        if len(groups) > 1:
            raise ValueError("暖心变量和其他变量的处理高度不同，请用 build_requests 分组处理")
        heights_m = groups[0][1] if groups else HEIGHTS_M
    if experiment not in SOURCE_DIRECTORIES:
        raise ValueError(f"未配置试验源路径：{experiment}")
    options = {
        **OPTIONS,
        "wrfout_template": str(SOURCE_DIRECTORIES[experiment]),
        "force_variables": list(force_variables),
        "force_slices": [dict(item) for item in force_slices],
    }
    return Request(
        experiments=(experiment,),
        domains=DOMAINS,
        times_minutes=(
            tuple(times_minutes) if times_minutes is not None
            else tuple(TIMES_MINUTES_BY_EXPERIMENT.get(experiment, TIMES_MINUTES))
        ),
        heights_m=tuple(heights_m),
        variables=selected,
        source_root=SOURCE_ROOT,
        output_root=Path(output_root),
        dry_run=dry_run,
        options=options,
        dtype="float32",
        center_scheme=center_scheme,
    )


def build_requests(
    *,
    dry_run: bool = True,
    output_root: str | Path = OUTPUT_ROOT,
    variables: tuple[str, ...] | None = None,
    force_variables: tuple[str, ...] = (),
    force_slices: tuple[dict[str, object], ...] = (),
    center_scheme: str | None = CENTER_SCHEME,
) -> list[Request]:
    selected = VARIABLES if variables is None else variables
    return [
        build_request(
            experiment,
            dry_run=dry_run,
            output_root=output_root,
            variables=group,
            force_variables=force_variables,
            force_slices=force_slices,
            heights_m=heights,
            center_scheme=center_scheme,
        )
        for experiment in EXPERIMENTS
        for group, heights in height_groups(selected)
    ]


# 功能说明：处理套餐（2026-11-02，processing_presets.json）：为每个试验 × 每个启用的组生成一个请求；
# 每组按自己的时间步长从 0 取到该试验的结束时次（EXPERIMENT_END_HOURS），高度用组里写的高度。
def build_preset_requests(
    preset_id: str,
    *,
    dry_run: bool = True,
    output_root: str | Path = OUTPUT_ROOT,
    force_variables: tuple[str, ...] = (),
    center_scheme: str | None = CENTER_SCHEME,
    groups: tuple[str, ...] | None = None,
) -> list[Request]:
    from wrf_processing_v3.processing_presets import load_processing_presets, preset_minutes
    from wrf_processing_v3.register import build_registry

    presets = load_processing_presets(
        height_tokens={"standard": HEIGHTS_M, "warm_core": WARM_CORE_HEIGHTS_M},
        registry=build_registry(OPTIONS),
    )
    if preset_id not in presets:
        raise ValueError(f"没有处理套餐 {preset_id}（可用：{', '.join(sorted(presets)) or '无'}）")
    chosen = [
        group for group in presets[preset_id]["groups"]
        if (group["id"] in groups if groups else group["enabled"])
    ]
    if groups and len(chosen) != len(set(groups)):
        known = {group["id"] for group in presets[preset_id]["groups"]}
        raise ValueError(f"套餐 {preset_id} 没有这些组：{', '.join(sorted(set(groups) - known))}")
    return [
        build_request(
            experiment,
            dry_run=dry_run,
            output_root=output_root,
            variables=tuple(group["variables"]),
            force_variables=force_variables,
            heights_m=tuple(group["heights_m"]) or (None,),
            center_scheme=center_scheme,
            times_minutes=tuple(preset_minutes(0, EXPERIMENT_END_HOURS.get(experiment, 120) * 60, group["time_step_minutes"])),
        )
        for experiment in EXPERIMENTS
        for group in chosen
    ]


def _force_slice(value: str) -> dict[str, object]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) not in (3, 4):
        raise argparse.ArgumentTypeError(
            "--force-slice 格式为 VARIABLE,TIME_MINUTES,HEIGHT_M[,DOMAIN]"
        )
    variable, minute_text, height_text = parts[:3]
    result: dict[str, object] = {
        "variable": variable,
        "time_minutes": int(minute_text),
        "height_m": int(height_text),
    }
    if len(parts) == 4:
        result["domain"] = int(parts[3])
    return result


def process_request(request: Request) -> dict[str, list[str]]:
    experiment = request.experiments[0]
    mode = "实际计算" if not request.dry_run else "dry-run"
    scheme = f"，中心方案 {request.center_scheme}" if request.center_scheme not in (None, "base") else ""
    print(f"[{experiment}] 开始：{mode}{scheme}", flush=True)
    summary = ProcessingManager().process(request)
    mode = "计划" if request.dry_run else "完成"
    print(
        f"[{experiment}] {mode}：{len(summary['planned'])} 个节点，"
        f"{len(summary['computed'])} 个计算，{len(summary['reused'])} 个复用，"
        f"{len(summary['skipped'])} 个跳过",
        flush=True,
    )
    return summary


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true", help="执行试点计算")
    mode.add_argument("--dry-run", action="store_true", help="仅显示依赖计划（默认）")
    parser.add_argument("--output-root", type=Path, default=None, help="输出根目录；默认 OUTPUT_ROOT（processed_v3），或 --dataset 指定的根目录")
    parser.add_argument("--dataset", choices=sorted(DATASETS), help="按数据集选输出根目录（post = 网页处理专用目录，v3 = processed_v3）")
    parser.add_argument("--preset", help="处理套餐编号（processing_presets.json），按套餐的组分别处理；与 --variable 互斥")
    parser.add_argument("--preset-group", action="append", help="只处理套餐里的这些组（可重复）；默认处理 enabled 的组")
    parser.add_argument("--variable", action="append", help="仅计算指定变量，可重复；默认使用 project_config.VARIABLES")
    parser.add_argument("--force-variable", action="append", default=[])
    parser.add_argument("--force-slice", action="append", type=_force_slice, default=[])
    parser.add_argument(
        "--center-scheme", default=CENTER_SCHEME,
        help="中心方案编号（center_schemes.json），默认 project_config.CENTER_SCHEME；非 base 方案依赖中心的变量写到 OUTPUT_ROOT/centers/<方案>/",
    )
    args = parser.parse_args(argv)
    output_root = args.output_root or (DATASETS[args.dataset] if args.dataset else OUTPUT_ROOT)
    if args.preset:
        if args.variable:
            parser.error("--preset 与 --variable 不能同时使用")
        requests = build_preset_requests(
            args.preset, dry_run=not args.run, output_root=output_root,
            force_variables=tuple(args.force_variable), center_scheme=args.center_scheme,
            groups=tuple(args.preset_group) if args.preset_group else None,
        )
        return [process_request(request) for request in requests]
    requests = build_requests(
        dry_run=not args.run,
        output_root=output_root,
        variables=None if args.variable is None else tuple(args.variable),
        force_variables=tuple(args.force_variable),
        force_slices=tuple(args.force_slice),
        center_scheme=args.center_scheme,
    )
    return [process_request(request) for request in requests]


if __name__ == "__main__":
    main()
