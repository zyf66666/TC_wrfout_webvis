#!/usr/bin/env python3
"""中心参数核查（2026-11-02，Claude）：只读 processed_v3，按试验/domain/变量汇总“依赖中心”的切片当时用的中心参数。

    python server_tools/center_audit.py                       # 默认根目录 /Volumes/land_1/processed_v3（含 profiles/）
    python server_tools/center_audit.py --root <目录> --experiment WRF_315_land

依赖中心的变量由注册表依赖图判断（slp_center、layer_center 及逐级依赖它们的变量）。
每个时次的“中心签名”= 该 domain 的 center_options_by_domain（来自 request_options；随高度文件抽查底、中、顶三层），
中心文件本身另加 algorithm / layer_center_mode。签名与当前默认（project_config）不同、
或同一文件里混有多个签名的，在报告里标出。结果写到 .runtime/中心核查/：
summary.txt（给人看）和 audit.json（逐文件明细）。不改任何数据、不触发处理。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / ".runtime" / "中心核查"
for extra in (REPO / "处理数据_v3", REPO):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _canonical(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, dict):
            return {str(key): normalize(entry) for key, entry in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(entry) for entry in item]
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item

    return json.dumps(normalize(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _domain_entry(options: dict[str, Any], domain: int) -> Any:
    table = options.get("center_options_by_domain") or {}
    if isinstance(table, dict):
        return table.get(str(domain), table.get(domain))
    return None


def _signature(metadata: dict[str, Any], variable: str, domain: int) -> str:
    options = metadata.get("request_options") or metadata.get("options") or {}
    signature: dict[str, Any] = {"center_options": _domain_entry(options, domain)}
    if "center_scheme" in metadata:
        signature["center_scheme"] = metadata["center_scheme"]
    if variable in ("slp_center", "layer_center"):
        for key in ("algorithm", "layer_center_mode"):
            if key in metadata:
                signature[key] = metadata[key]
    return _canonical(signature)


def _hours_text(minutes: list[int]) -> str:
    if not minutes:
        return "-"
    hours = sorted({value / 60 for value in minutes})
    first, last = hours[0], hours[-1]
    fmt = lambda h: f"{h:g}"
    return f"{fmt(first)}–{fmt(last)} h（{len(hours)} 个时次）" if len(hours) > 1 else f"{fmt(first)} h"


def audit_file(path: Path, variable: str, domain: int) -> dict[str, Any]:
    """按时次汇总中心签名。为控制外接盘读取量：不读 completed（按 (1,1) 分块，逐块读非常慢），
    只整列读取少数几个高度的 metadata_json（未完成的切片元数据为空），每个时次取第一个非空的。"""
    from netCDF4 import Dataset

    groups: dict[str, dict[str, Any]] = {}
    with Dataset(path) as dataset:
        dataset.set_auto_mask(False)
        times = [int(value) for value in dataset["time_minutes"][:]]
        metadata = dataset["metadata_json"]
        if metadata.ndim == 1:
            columns = [np.asarray(metadata[:], dtype=object)]
        else:
            count = metadata.shape[1]
            picks = sorted({0, count // 2, count - 1}) if count else []
            columns = [np.asarray(metadata[:, index], dtype=object) for index in picks]
        for record, minute in enumerate(times):
            text = ""
            for column in columns:
                value = column[record] if record < len(column) else ""
                text = str(value.item() if isinstance(value, np.ndarray) else value or "")
                if text:
                    break
            if not text:
                continue
            try:
                meta = json.loads(text)
            except json.JSONDecodeError:
                meta = {}
            key = _signature(meta, variable, domain) if meta else '"缺少元数据"'
            group = groups.setdefault(key, {"minutes": [], "slices": 0})
            group["minutes"].append(minute)
            group["slices"] += 1
    return {
        "path": str(path),
        "signatures": [
            {"signature": key, "slices": value["slices"], "hours": _hours_text(value["minutes"]), "minutes": sorted(value["minutes"])}
            for key, value in groups.items()
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="/Volumes/land_1/processed_v3")
    parser.add_argument("--experiment", action="append", help="只查这些试验（可多次给出）")
    args = parser.parse_args(argv)

    from wrf_processing_v3.register import build_registry
    import project_config

    default_options = dict(getattr(project_config, "OPTIONS", {}))
    registry = build_registry(default_options)
    root = Path(args.root)
    roots = [("default", root)]
    profiles = root / "profiles"
    if profiles.is_dir():
        roots += [(item.name, item) for item in sorted(profiles.iterdir()) if item.is_dir()]

    started = time.time()
    rows: list[dict[str, Any]] = []
    for profile_id, base in roots:
        for experiment_dir in sorted(p for p in base.iterdir() if p.is_dir() and p.name not in ("profiles", "centers")):
            if args.experiment and experiment_dir.name not in args.experiment:
                continue
            for domain_dir in sorted(experiment_dir.glob("d[0-9][0-9]")):
                domain = int(domain_dir.name[1:])
                for path in sorted(domain_dir.glob("*.nc")):
                    variable = path.stem
                    if path.name.startswith("._"):
                        continue
                    try:
                        if not registry.center_dependent(variable):
                            continue
                    except (KeyError, ValueError):
                        continue
                    try:
                        detail = audit_file(path, variable, domain)
                    except Exception as error:  # 单个坏文件不影响其余核查
                        detail = {"path": str(path), "error": f"{type(error).__name__}: {error}", "signatures": []}
                    detail.update(profile=profile_id, experiment=experiment_dir.name, domain=domain, variable=variable)
                    rows.append(detail)
                    print(f"[{time.time() - started:6.0f}s] {profile_id} {experiment_dir.name} d{domain:02d} {variable}", flush=True)

    current = {domain: _canonical({"center_options": _domain_entry(default_options, domain)}) for domain in (1, 2, 3)}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "audit.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = [
        f"中心参数核查  {time.strftime('%Y-%m-%d %H:%M')}  根目录 {root}",
        "当前默认中心参数（project_config）：",
        *[f"  d{domain:02d}: {current[domain]}" for domain in (2, 3)],
        "",
    ]
    tally: dict[str, int] = defaultdict(int)
    by_experiment: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_experiment[(row["profile"], row["experiment"], row["domain"])].append(row)
    for (profile_id, experiment, domain), items in sorted(by_experiment.items()):
        lines.append(f"== {profile_id} / {experiment} / d{domain:02d}")
        for row in items:
            if row.get("error"):
                lines.append(f"  !! {row['variable']}: 读取失败 {row['error']}")
                tally["读取失败"] += 1
                continue
            signatures = row["signatures"]
            mixed = len(signatures) > 1
            for item in signatures:
                base_signature = json.loads(item["signature"]) if item["signature"].startswith("{") else {}
                same = base_signature.get("center_options") == json.loads(current[domain])["center_options"]
                mark = "  " if same else "≠ "
                if not same:
                    tally["与当前默认不同的签名"] += 1
                lines.append(f"  {mark}{row['variable']}{' [同一文件混有多套参数]' if mixed else ''}: {item['hours']}")
                if not same:
                    lines.append(f"      参数 {item['signature']}")
            if mixed:
                tally["混有多套参数的文件"] += 1
    lines += ["", "汇总：", f"  依赖中心的文件 {len(rows)} 个", *[f"  {key} {value}" for key, value in tally.items()],
              f"  用时 {time.time() - started:.0f} s", "（≠ 表示该签名的中心参数与当前默认不同）"]
    (OUT_DIR / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[-8:]))
    print(f"报告：{OUT_DIR / 'summary.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
