"""按层中心极坐标变量的固定半径边界安全审计。"""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .common import grid_km


def _requested_radius(context: Any) -> float:
    """按按域配置、通用配置和旧配置的优先级取得请求半径。"""
    # 第一段：读取按域半径，整数和字符串域编号均与 ProcessingContext 配置兼容。
    # 与插值（variables/polar.py）共用同一取值规则：变量 > domain > 全局。
    from .common import polar_radius_km

    by_domain = context.options.get("radius_km_by_domain", {})
    if not isinstance(by_domain, dict):
        raise ValueError("radius_km_by_domain 必须是字典")
    radius = polar_radius_km(context.options, context.task.variable, context.task.domain)
    # 第三段：审计固定半径前验证数值，避免无请求半径时错误标记为安全。
    if radius is None:
        raise ValueError("按层极坐标安全检查需要 radius_km 或 polar_max_radius_km")
    radius = float(radius)
    if not math.isfinite(radius) or radius < 0.0:
        raise ValueError("请求极坐标半径必须是有限非负数")
    return radius


def _center_yx(context: Any) -> tuple[float, float]:
    """读取按层中心的 y、x 网格索引。"""
    # 第一段：确认变量规格明确要求按层中心，避免把 SLP 中心混入同一审计组。
    center_mode = context.spec.options.get("center")
    if center_mode != "layer_center":
        raise ValueError("按层极坐标安全检查要求 center='layer_center'")
    # 第二段：通过 Context 读取已声明的中心依赖，并展平兼容长度为二的一维存储。
    values = np.asarray(context.read("layer_center"), dtype=float).ravel()
    if values.size < 2:
        raise ValueError("layer_center 必须保存 [y, x]")
    # 第三段：取最后两个元素，沿用层中心处理器的 [y, x] 存储约定。
    return float(values[-2]), float(values[-1])


def _safe_radius_km(
    shape: tuple[int, ...], center_y: float, center_x: float, dx_km: float, dy_km: float
) -> float:
    """计算中心到四条网格边界的最小物理距离，不允许边界外推。"""
    # 第一段：验证二维源网格及中心、间距的有限性。
    if len(shape) != 2 or shape[0] < 1 or shape[1] < 1:
        raise ValueError(f"按层极坐标源变量必须是非空二维网格，实际形状为 {shape}")
    if not all(math.isfinite(value) for value in (center_y, center_x, dx_km, dy_km)):
        return 0.0
    # 第二段：中心若在网格外，没有可用的径向范围，返回零以禁止任何外推。
    ny, nx = shape
    if center_y < 0.0 or center_y > ny - 1 or center_x < 0.0 or center_x > nx - 1:
        return 0.0
    # 第三段：含首尾零索引边界计算四侧距离；中心贴边时安全半径严格为零。
    return float(
        min(
            center_y * dy_km,
            (ny - 1 - center_y) * dy_km,
            center_x * dx_km,
            (nx - 1 - center_x) * dx_km,
        )
    )


def audit_group(contexts: Iterable[Any]) -> dict[str, Any]:
    """审计同一试验、域、时次和变量的多个高度层，任一层越界即整体不安全。"""
    # 第一段：物化并验证组身份，防止不同时间或变量的层误合并。
    group = list(contexts)
    if not group:
        raise ValueError("按层极坐标安全审计至少需要一个上下文")
    first = group[0]
    identity = (
        first.task.experiment,
        first.task.domain,
        first.task.time,
        first.task.variable,
    )
    if any(
        (item.task.experiment, item.task.domain, item.task.time, item.task.variable)
        != identity
        for item in group
    ):
        raise ValueError("安全审计组中的试验、域、时次和变量必须一致")

    # 第二段：逐层读取规格声明的源变量和 layer_center，计算四边最小安全半径。
    rows: list[dict[str, Any]] = []
    for context in sorted(group, key=lambda item: float(item.task.height)):
        source = context.spec.options.get("source")
        if not isinstance(source, str) or not source:
            raise ValueError("按层极坐标变量规格必须声明 source")
        center_y, center_x = _center_yx(context)
        read_shape = getattr(context, "read_shape", None)
        shape = read_shape(source) if read_shape is not None else np.shape(context.read(source))
        dx_km, dy_km = grid_km(context)
        requested_radius = _requested_radius(context)
        safe_radius = _safe_radius_km(shape, center_y, center_x, dx_km, dy_km)
        rows.append(
            {
                "height": float(context.task.height),
                "safe_radius": safe_radius,
                "requested_radius": requested_radius,
                "safe": bool(safe_radius + 1.0e-8 >= requested_radius),
            }
        )

    # 第三段：汇总逐层结果；任一高度不安全即整个时次不安全。
    experiment, domain, time, variable = identity
    return {
        "experiment": experiment,
        "domain": domain,
        "time": time,
        "variable": variable,
        "heights": rows,
        "all_safe": all(row["safe"] for row in rows),
    }


def select_times(
    audits: Iterable[dict[str, Any]], policy: str = "all_safe"
) -> list[Any]:
    """按全部安全、首次中断前连续安全或关闭筛选三种策略选择时次。"""
    # 第一段：按物理时次排序，保证连续策略不受调用方传入顺序影响。
    ordered = sorted(list(audits), key=lambda audit: audit["time"])
    if policy not in {"all_safe", "continuous", "off"}:
        raise ValueError("安全时次策略只能是 all_safe、continuous 或 off")
    # 第二段：off 保留全部请求时次，all_safe 保留每个整体安全的时次。
    if policy == "off":
        return [audit["time"] for audit in ordered]
    if policy == "all_safe":
        return [audit["time"] for audit in ordered if audit["all_safe"]]
    # 第三段：continuous 从首个请求开始，第一次不安全之后即使恢复也不再续接。
    selected = []
    for audit in ordered:
        if not audit["all_safe"]:
            break
        selected.append(audit["time"])
    return selected


def write_report(
    output_root: Path | str,
    experiment: str,
    domain: int,
    variable: str,
    audits: Iterable[dict[str, Any]],
    selected: Iterable[Any],
) -> Path:
    """将安全审计和筛选时次原子写入变量目录，返回报告路径。"""
    # 第一段：确定兼容 Storage 目录的报告路径及可 JSON 序列化内容。
    root = Path(output_root)
    path = root / experiment / f"d{int(domain):02d}" / f"{variable}_safety.json"
    report = {
        "experiment": experiment,
        "domain": int(domain),
        "variable": variable,
        "audits": list(audits),
        "selected_times": list(selected),
    }
    # 第二段：在目标目录建立临时 JSON 文件，完整写入并刷新到磁盘。
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, suffix=".json", mode="w", encoding="utf-8", delete=False
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(report, handle, ensure_ascii=False, sort_keys=True, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        # 第三段：以 replace 原子替换报告，避免执行器读取到部分 JSON。
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return path
