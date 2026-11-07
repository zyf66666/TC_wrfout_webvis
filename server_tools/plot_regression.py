#!/usr/bin/env python3
"""绘图回归对比（2026-11-01，Claude）：重构绘图代码前后，用看板里的真实出图请求重画并逐像素、逐数值比对。

    python server_tools/plot_regression.py select          # 从看板挑数据齐全、覆盖各图型的请求
    python server_tools/plot_regression.py capture --label before
    python server_tools/plot_regression.py compare --base before --label after_xxx

请求清单在 .runtime/绘图回归/requests.json（select 只读看板库，对每个请求做出图预检，只留数据齐全的）。
每次重画写到 .runtime/绘图回归/<label>/：图片、每张图的数值（npz）、结果摘要 summary.json。
不写看板、不动 static/figures、不处理 wrfout；只读 processed_v3。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[1]
BASE_DIR = REPO / ".runtime" / "绘图回归"


def _array(value: Any) -> np.ndarray | None:
    try:
        array = np.asarray(value, dtype=float)
    except (TypeError, ValueError):
        return None
    return array.copy() if array.size else None


class _PayloadRecorder:
    """记录每张图实际画上去的数值（填色/等值线/折线的 values、x、y）。"""

    def __init__(self) -> None:
        self.arrays: dict[str, np.ndarray] = {}
        self._count = 0

    def record(self, label: str, payload: dict[str, Any]) -> None:
        for key in ("values", "x", "y", "u", "v"):
            if isinstance(payload, dict) and key in payload:
                array = _array(payload[key])
                if array is not None:
                    self.arrays[f"{self._count:03d}_{label}_{key}"] = array
        self._count += 1


_CURRENT: dict[str, _PayloadRecorder | None] = {"recorder": None}
_HOOKED = {"done": False}


def _install_hooks() -> None:
    """只装一次：把画图入口包一层，按当前请求的记录器记下数值。"""
    if _HOOKED["done"]:
        return
    from wrf_webvis.app.plotting import draw_main, draw_variables

    original_plot_payload = draw_variables.plot_payload

    def plot_payload(ax, variable, payload, *args, **kwargs):
        if _CURRENT["recorder"] is not None:
            _CURRENT["recorder"].record(f"layer_{variable}", payload)
        return original_plot_payload(ax, variable, payload, *args, **kwargs)

    draw_variables.plot_payload = plot_payload

    original_time_series = draw_variables.time_series_plot

    def time_series_plot(ax, payloads, *args, **kwargs):
        if _CURRENT["recorder"] is not None:
            for name, payload in (payloads or {}).items():
                _CURRENT["recorder"].record(f"series_{name}", payload)
        return original_time_series(ax, payloads, *args, **kwargs)

    draw_variables.time_series_plot = time_series_plot
    if hasattr(draw_main, "time_series_plot"):
        draw_main.time_series_plot = time_series_plot
    _HOOKED["done"] = True


def capture(label: str) -> Path:
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.plot_service import PlotService

    entries = json.loads((BASE_DIR / "requests.json").read_text(encoding="utf-8"))
    out = BASE_DIR / label
    (out / "figures").mkdir(parents=True, exist_ok=True)
    (out / "values").mkdir(exist_ok=True)

    _install_hooks()
    service = PlotService(out / "figures")
    summary = []
    for index, entry in enumerate(entries):
        key = f"{index:02d}_{entry['image_id']}"
        raw = dict(entry["request"])
        raw["dashboard_id"] = None
        recorder = _PayloadRecorder()
        _CURRENT["recorder"] = recorder
        started = time.time()
        item: dict[str, Any] = {"key": key, "fig_type": raw.get("fig_type"), "title": entry.get("image_title")}
        try:
            result = service.create_plot(PlotRequest(**raw))
            image = Path(result["image_path"])
            target = out / "figures" / f"{key}{image.suffix}"
            image.replace(target)
            item.update(ok=True, image=target.name, warnings=result.get("warnings", []),
                        used_files=len(result.get("used_files", [])))
        except Exception as exc:  # noqa: BLE001
            item.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:2000],
                        traceback=traceback.format_exc()[-3000:])
        item["seconds"] = round(time.time() - started, 1)
        if recorder.arrays:
            np.savez_compressed(out / "values" / f"{key}.npz", **recorder.arrays)
        item["value_arrays"] = len(recorder.arrays)
        summary.append(item)
        status = "成功" if item.get("ok") else "失败"
        print(f"[{index + 1}/{len(entries)}] {status} {key} fig_type={item['fig_type']} ({item['seconds']} s)", flush=True)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


DASHBOARD_DB = REPO / "wrf_webvis" / "storage_data" / "dashboards.sqlite"


def _features(request: dict[str, Any]) -> set[str]:
    """一个请求用到的绘图特性，用来挑覆盖面广的请求。"""
    features = {f"fig:{request.get('fig_type')}"}
    for section, options in (request.get("format") or {}).items():
        if isinstance(options, dict):
            for key, value in options.items():
                if key.startswith(("draw_", "center_enabled", "tilt_track", "inner_core", "cloud_class", "overlay", "rmw_source"))\
                        and value not in (False, None, "", [], 0):
                    features.add(f"{section}.{key}")
    if any(item.get("enabled", True) for item in request.get("quiver") or []):
        features.add("quiver")
    if (request.get("row") or 1) * (request.get("col") or 1) > 1:
        features.add("panels")
    members = (request.get("selection") or {}).get("members") or request.get("member_names") or []
    if len(members) > 1:
        features.add("multi_member")
    for item in request.get("variables") or []:
        if item.get("enabled", True):
            name = str(item.get("name", ""))
            features.add(f"plot_type:{item.get('plot_type')}")
            if name.endswith("_polar") or name.rstrip("_polar").endswith("1"):
                features.add("layer_or_polar_name")
            if "dbz" in name:
                features.add("dbz")
    if (request.get("extra") or {}).get("analysis_type"):
        features.add("research_analysis")
    return features


def select(max_total: int = 24, max_slices: int = 3000) -> Path:
    """预检看板里的全部出图请求（不出图），在数据齐全的请求里按图型、特性挑一组。"""
    import sqlite3

    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.plot_service import PlotService

    connection = sqlite3.connect(f"file:{DASHBOARD_DB}?mode=ro", uri=True)
    rows = connection.execute(
        "select i.dashboard_id, i.id, d.title, i.data from images i join dashboards d on d.id = i.dashboard_id"
    ).fetchall()
    connection.close()
    service = PlotService(BASE_DIR / "_select_tmp")
    available = []
    for index, (dashboard, image_id, dashboard_title, data) in enumerate(rows):
        entry = json.loads(data)
        raw = dict(entry.get("request") or {})
        if not raw or int(raw.get("fig_type", 0)) == 9 or raw.get("custom_code"):
            continue
        raw["dashboard_id"] = None
        try:
            plan = service.dry_run(PlotRequest(**raw))["plan"]
        except Exception as exc:  # noqa: BLE001
            print(f"预检出错 {image_id}: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
            continue
        slices = len(plan.get("required_files", []))
        if plan.get("missing_files") or slices > max_slices:
            continue
        available.append({
            "dashboard": dashboard, "image_id": image_id, "dashboard_title": dashboard_title,
            "image_title": entry.get("title"), "request": entry["request"],
            "features": sorted(_features(entry["request"])), "slices": slices,
        })
    print(f"看板请求 {len(rows)} 个，数据齐全可重画 {len(available)} 个", flush=True)
    chosen: list[dict[str, Any]] = []
    covered: set[str] = set()
    pool = list(available)
    while pool and len(chosen) < max_total:
        best = max(pool, key=lambda item: (len(set(item["features"]) - covered), -item["slices"]))
        if not set(best["features"]) - covered:
            break
        covered |= set(best["features"])
        chosen.append(best)
        pool.remove(best)
    # 每种图型至少两张（有的话），覆盖更稳。
    for fig_type in sorted({item["request"].get("fig_type") for item in available}):
        while sum(1 for item in chosen if item["request"].get("fig_type") == fig_type) < 2 and len(chosen) < max_total:
            extra = [item for item in pool if item["request"].get("fig_type") == fig_type]
            if not extra:
                break
            pick = min(extra, key=lambda item: item["slices"])
            chosen.append(pick)
            pool.remove(pick)
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    path = BASE_DIR / "requests.json"
    path.write_text(json.dumps(chosen, ensure_ascii=False, indent=1), encoding="utf-8")
    for item in chosen:
        print(f"  选中 fig_type={item['request'].get('fig_type')} {item['image_id']} {(item.get('image_title') or '')[:30]}"
              f"（{item['slices']} 个切片）：{', '.join(item['features'])[:150]}", flush=True)
    return path


def _load_png(path: Path) -> np.ndarray:
    from PIL import Image

    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA"))


def compare(base: str, label: str) -> tuple[str, bool]:
    base_dir, new_dir = BASE_DIR / base, BASE_DIR / label
    before = {item["key"]: item for item in json.loads((base_dir / "summary.json").read_text(encoding="utf-8"))}
    after = {item["key"]: item for item in json.loads((new_dir / "summary.json").read_text(encoding="utf-8"))}
    lines = [f"对比 {base} → {label}", ""]
    all_same = True
    counts = {"相同": 0, "不同": 0, "两次都失败": 0}
    for key, old in before.items():
        new = after.get(key)
        title = (old.get("title") or "")[:40]
        if new is None:
            lines.append(f"✗ {key} 新结果里没有这张图")
            all_same = False
            counts["不同"] += 1
            continue
        if not old.get("ok") or not new.get("ok"):
            if not old.get("ok") and not new.get("ok") and old.get("error") == new.get("error"):
                counts["两次都失败"] += 1
                lines.append(f"· {key} 两次都失败（同样的错误，多半是缺数据）：{old.get('error', '')[:160]}")
            else:
                all_same = False
                counts["不同"] += 1
                lines.append(f"✗ {key} {title}：改前 {'成功' if old.get('ok') else '失败'}，改后 {'成功' if new.get('ok') else '失败'}")
                for side, item in (("改前", old), ("改后", new)):
                    if not item.get("ok"):
                        lines.append(f"    {side}错误：{item.get('error', '')[:400]}")
            continue
        problems = []
        a, b = _load_png(base_dir / "figures" / old["image"]), _load_png(new_dir / "figures" / new["image"])
        if a.shape != b.shape:
            problems.append(f"图片尺寸不同 {a.shape} → {b.shape}")
        else:
            diff = np.any(a != b, axis=-1)
            if diff.any():
                problems.append(f"像素不同：{diff.mean() * 100:.3f}% 的像素，最大差 {int(np.abs(a.astype(int) - b.astype(int)).max())}")
        va, vb = base_dir / "values" / f"{key}.npz", new_dir / "values" / f"{key}.npz"
        if va.exists() or vb.exists():
            if not (va.exists() and vb.exists()):
                problems.append("数值记录一边有一边没有")
            else:
                with np.load(va) as za, np.load(vb) as zb:
                    if sorted(za.files) != sorted(zb.files):
                        problems.append(f"数值数组名不同：{sorted(set(za.files) ^ set(zb.files))[:6]}")
                    for name in sorted(set(za.files) & set(zb.files)):
                        x, y = za[name], zb[name]
                        if x.shape != y.shape or not np.array_equal(x, y, equal_nan=True):
                            problems.append(f"数值不同：{name} {x.shape}→{y.shape}")
                            break
        if old.get("warnings") != new.get("warnings"):
            problems.append(f"警告不同：{old.get('warnings')} → {new.get('warnings')}")
        if problems:
            all_same = False
            counts["不同"] += 1
            lines.append(f"✗ {key} {title}：" + "；".join(problems))
        else:
            counts["相同"] += 1
            lines.append(f"✓ {key} {title}（{old.get('value_arrays', 0)} 组数值）")
    lines.insert(1, f"结论：{'全部一致' if all_same else '有差异'}　相同 {counts['相同']}、不同 {counts['不同']}、两次都失败 {counts['两次都失败']}")
    return "\n".join(lines), all_same


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("select")
    cap = sub.add_parser("capture")
    cap.add_argument("--label", required=True)
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("--base", required=True)
    cmp_.add_argument("--label", required=True)
    args = parser.parse_args(argv)
    if args.command == "select":
        print(f"已写入 {select()}")
        return 0
    if args.command == "capture":
        print(f"已写入 {capture(args.label)}")
        return 0
    report, same = compare(args.base, args.label)
    (BASE_DIR / f"对比_{args.label}.txt").write_text(report, encoding="utf-8")
    print(report)
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
