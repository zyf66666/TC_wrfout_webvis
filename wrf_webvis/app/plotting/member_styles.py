from __future__ import annotations

import re
from typing import Any


DEFAULT_EXPERIMENT_ORDER = ["0", "45", "90", "135", "180", "225", "270", "315"]
DEFAULT_EXPERIMENT_COLORS = {
    "0": "#d34a38",
    "45": "#7000df",
    "90": "#2c3cff",
    "135": "#5d99ff",
    "180": "#67c7c5",
    "225": "#b8e34e",
    "270": "#7cd933",
    "315": "#d4aa3a",
}


# 2026-11-02 中心方案：绘图成员键“试验@方案”。同一试验不同方案用相同颜色、不同线型，图例写成“试验 [方案]”。
# 非 base 方案的默认线型按 center_schemes.json 里的顺序取（base 保持原来的海/陆线型）。
DEFAULT_SCHEME_LINESTYLES = [":", "-.", (0, (5, 1, 1, 1, 1, 1)), (0, (8, 3)), (0, (1, 3))]


def split_member_key(member_name: str) -> tuple[str, str]:
    """“试验@方案” → (试验, 方案)；没有 @ 时方案为 base。只做字符串拆分，不校验方案是否登记。"""
    text = str(member_name)
    if "@" not in text:
        return text, "base"
    experiment, scheme = text.split("@", 1)
    return experiment, scheme or "base"


def member_display_name(member_name: str) -> str:
    experiment, scheme = split_member_key(member_name)
    return experiment if scheme == "base" else f"{experiment} [{scheme}]"


# 功能说明：非 base 中心方案的线型：先查格式里的 center_scheme_linestyles，再按方案目录顺序取默认线型；base 返回 None。
def scheme_linestyle(member_name: str, fmt: dict[str, Any] | None = None) -> Any:
    _, scheme = split_member_key(member_name)
    if scheme == "base":
        return None
    configured = (fmt or {}).get("center_scheme_linestyles")
    if isinstance(configured, dict) and scheme in configured:
        return configured[scheme]
    try:
        from 处理数据_v3.wrf_processing_v3.center_schemes import load_center_schemes

        order = [key for key in load_center_schemes() if key != "base"]
    except Exception:  # noqa: BLE001 - 方案目录读不到时仍能出图
        order = []
    index = order.index(scheme) if scheme in order else len(order)
    return DEFAULT_SCHEME_LINESTYLES[index % len(DEFAULT_SCHEME_LINESTYLES)]


# 功能说明：从试验名称识别实验编号，优先查自定义配色，再查默认配色，最后尝试按顺序取颜色，帮助同一试验在不同图里保持一致。
def resolve_experiment_color(member_name: str, index: int, fmt: dict[str, Any]) -> str | None:
    """Return the configured/default color for an experiment encoded in a member name."""
    color_map = fmt.get("experiment_colors", fmt.get("member_colors"))
    if isinstance(color_map, dict) and member_name in color_map:
        return str(color_map[member_name])
    member_name = split_member_key(member_name)[0]  # 颜色只看试验，不看中心方案
    tokens = {token.lower() for token in re.split(r"[^A-Za-z0-9.]+", member_name) if token}
    color_map = fmt.get("experiment_colors", fmt.get("member_colors"))
    if isinstance(color_map, dict):
        color = _color_from_map(member_name, tokens, color_map, fmt.get("experiment_order") or list(color_map))
        if color is not None:
            return color

    color = _color_from_map(member_name, tokens, DEFAULT_EXPERIMENT_COLORS, DEFAULT_EXPERIMENT_ORDER)
    if color is not None:
        return color

    colors = fmt.get("colors")
    if colors:
        return str(colors[index % len(colors)])
    return None


# 功能说明：在配色表中先找完整试验名，再按试验名拆出的片段寻找匹配颜色；找不到返回空值。
def _color_from_map(member_name: str, tokens: set[str], color_map: dict[Any, Any], order: Any) -> str | None:
    if member_name in color_map:
        return str(color_map[member_name])
    for experiment in order:
        key = str(experiment)
        if key.lower() in tokens and key in color_map:
            return str(color_map[key])
    for key, value in color_map.items():
        if str(key).lower() in tokens:
            return str(value)
    return None
