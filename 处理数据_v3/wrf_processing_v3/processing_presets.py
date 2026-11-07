"""处理套餐（2026-11-02）：一次选好几组“变量 + 时间步长 + 高度”，网页为每个试验 × 每组生成独立处理请求。

套餐写在 ``处理数据_v3/processing_presets.json``。本模块只负责读取、校验和展开高度令牌；
不做任何计算，也不写文件。高度令牌（standard、warm_core）的取值由调用方传入
（网页和命令行都取自 project_config），这样本包仍可单独导入。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

PRESET_FILE = Path(__file__).resolve().parents[1] / "processing_presets.json"
_ID = re.compile(r"[a-z][a-z0-9_]{1,47}")
_DIMENSIONS_WITH_HEIGHT = {"height", "height_scalar", "height_vector"}


def _heights(value: Any, tokens: Mapping[str, tuple[int, ...]], where: str) -> list[int] | None:
    if value is None:
        return None
    if isinstance(value, str):
        if value not in tokens:
            raise ValueError(f"{where}：未知高度写法 {value!r}（可用 {', '.join(sorted(tokens))}、null 或整数列表）")
        return [int(item) for item in tokens[value]]
    if isinstance(value, list) and value and all(isinstance(item, int) and not isinstance(item, bool) and item > 0 for item in value):
        return sorted(set(value))
    raise ValueError(f"{where}：heights 必须是 null、高度令牌或正整数列表（米）")


def parse_processing_presets(
    raw: Mapping[str, Any],
    *,
    height_tokens: Mapping[str, tuple[int, ...]],
    registry: Any = None,
) -> dict[str, dict[str, Any]]:
    """校验并展开套餐；registry 给出时检查每个变量都已登记、三维变量所在组有高度。"""
    presets: dict[str, dict[str, Any]] = {}
    for preset_id, entry in raw.items():
        if preset_id.startswith("_"):
            continue
        if not _ID.fullmatch(preset_id):
            raise ValueError(f"套餐编号只能用小写字母开头的小写字母、数字、下划线：{preset_id!r}")
        if not isinstance(entry, dict) or not isinstance(entry.get("groups"), list) or not entry["groups"]:
            raise ValueError(f"套餐 {preset_id} 缺少 groups")
        groups, seen = [], set()
        for group in entry["groups"]:
            group_id = str(group.get("id", ""))
            where = f"套餐 {preset_id} 的组 {group_id or '?'}"
            if not _ID.fullmatch(group_id) or group_id in seen:
                raise ValueError(f"{where}：组编号缺失、重复或含非法字符")
            seen.add(group_id)
            step = group.get("time_step_minutes")
            if not isinstance(step, int) or isinstance(step, bool) or step <= 0:
                raise ValueError(f"{where}：time_step_minutes 必须是正整数（分钟）")
            variables = group.get("variables")
            if not isinstance(variables, list) or not variables or not all(isinstance(name, str) and name for name in variables):
                raise ValueError(f"{where}：variables 必须是非空的变量名列表")
            if len(set(variables)) != len(variables):
                raise ValueError(f"{where}：variables 有重复")
            heights = _heights(group.get("heights"), height_tokens, where)
            if registry is not None:
                for name in variables:
                    try:
                        dimensions = registry.resolve(name).dimensions
                    except (KeyError, ValueError) as exc:
                        raise ValueError(f"{where}：变量 {name} 未登记（{exc}）") from exc
                    if dimensions in _DIMENSIONS_WITH_HEIGHT and not heights:
                        raise ValueError(f"{where}：{name} 随高度变化，组里必须给出 heights")
            groups.append({
                "id": group_id,
                "label": str(group.get("label") or group_id),
                "enabled": bool(group.get("enabled", True)),
                "time_step_minutes": step,
                "heights_m": heights or [],
                "variables": list(variables),
            })
        presets[preset_id] = {
            "id": preset_id,
            "label": str(entry.get("label") or preset_id),
            "description": str(entry.get("description") or ""),
            "dataset": str(entry.get("dataset") or ""),
            # 2026-11-03：false 时各组各自一个请求（不合并），默认 true。
            "merge_groups": bool(entry.get("merge_groups", True)),
            "groups": groups,
        }
    return presets


def load_processing_presets(
    path: str | Path | None = None,
    *,
    height_tokens: Mapping[str, tuple[int, ...]],
    registry: Any = None,
) -> dict[str, dict[str, Any]]:
    target = Path(path) if path is not None else PRESET_FILE
    if not target.is_file():
        return {}
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{target} 顶层必须是对象")
    return parse_processing_presets(raw, height_tokens=height_tokens, registry=registry)


def preset_minutes(start_minute: int, end_minute: int, step_minutes: int) -> list[int]:
    """包含两端的整数分钟序列：起点、起点+步长……不超过终点。"""
    if step_minutes <= 0 or start_minute < 0 or end_minute < start_minute:
        raise ValueError("套餐时间需要 0 <= 起点 <= 终点，步长为正")
    return list(range(int(start_minute), int(end_minute) + 1, int(step_minutes)))
