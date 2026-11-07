"""把 processed_v3 格式的 <试验>/dXX/*.nc 读成逐时次的一张小表（超算导出和本地画图共用）。

只依赖 numpy + netCDF4，不导入处理管线。读法遵守 SCHEMA.md：只取 completed == 1 的记录，
按 time_minutes 坐标定位，不假定记录顺序；大文件（slp.nc）逐记录读，不整体载入。
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np


# 表格列（CSV 列顺序固定，画图脚本按列名取值）
COLUMNS = (
    "time_h",                 # 积分小时
    "time_minutes",           # 相对 START_DATE 的分钟
    "datetime",               # 模式时间
    "vmax_axis_mean_ms",      # axis_mean_max_wind：最低模式层切向风方位平均的最大值（以 slp_center 为圆心）
    "vmax_local_ms",          # local_max_wind：最低模式层全场最大风速
    "rmw_km",                 # rmw：上面方位平均切向风最大值所在半径
    "slp_min_hPa",            # d03 全场海平面气压最小值（由 slp.nc 现算）
    "slp_min_y",              # 最小值所在格点 (y, x)
    "slp_min_x",
    "slp_min_offset_km",      # 最小气压格点到 slp_center 的距离
    "slp_at_center_hPa",      # slp_center 取整格点上的 SLP（slp_center 元数据 pressure_hpa）
    "slp_center_5km_hPa",     # center_slp_5km：中心 5 km 圆盘平均 SLP
    "center_y",               # slp_center（d03 网格下标，Bezier PCC v1）
    "center_x",
    "center_lat",             # center_geo：中心格点的纬度、经度（移动嵌套当时的网格）
    "center_lon",
)

# 与 wrf_processing_v3/common.DEFAULT_DOMAIN_CONFIG 一致
GRID_KM = {1: 25.0, 2: 5.0, 3: 1.0}

# 结果 .nc 文件（下载包里带的）
RESULT_VARIABLES = (
    "slp_center", "center_geo", "center_slp_5km",
    "axis_mean_max_wind", "local_max_wind", "rmw",
)


def _default_open(path: Path):
    from netCDF4 import Dataset

    return Dataset(str(path), "r")


def _completed(dataset) -> np.ndarray:
    flags = np.ma.filled(np.ma.asarray(dataset["completed"][:]), 0)
    return np.asarray(flags).astype(np.uint8)


def _time_index(dataset) -> dict[int, int]:
    """time_minutes -> record，只保留 completed == 1 的记录。"""
    minutes = np.asarray(np.ma.filled(np.ma.asarray(dataset["time_minutes"][:]), -10**12)).astype(np.int64)
    if len(set(minutes.tolist())) != len(minutes):
        raise ValueError("time_minutes 有重复值，文件不符合 v3 约定")
    completed = _completed(dataset)
    if completed.ndim != 1:
        raise ValueError("只支持无高度维的变量（completed 应为一维）")
    return {int(m): i for i, m in enumerate(minutes) if i < completed.size and completed[i] == 1}


def _attr(dataset, name: str, default=None):
    try:
        return dataset.getncattr(name)
    except (AttributeError, KeyError):
        return default


def read_records(
    path: Path,
    *,
    with_metadata: bool = False,
    opener: Callable[[Path], Any] = _default_open,
) -> tuple[dict[int, Any], str | None]:
    """读标量/向量变量：返回 ({time_minutes: 数值或数组[, 元数据]}, time_origin)。文件不存在返回空表。"""
    path = Path(path)
    if not path.exists():
        return {}, None
    dataset = opener(path)
    try:
        index = _time_index(dataset)
        data = dataset["data"]
        result: dict[int, Any] = {}
        for minute, record in index.items():
            value = np.asarray(np.ma.filled(np.ma.asarray(data[record]), np.nan), dtype=float)
            if value.ndim == 0:
                value = float(value)  # scalar 布局：data(record)
            if with_metadata:
                raw = dataset["metadata_json"][record]
                try:
                    meta = json.loads(str(raw)) if raw not in (None, "") else {}
                except ValueError:
                    meta = {}
                result[minute] = (value, meta)
            else:
                result[minute] = value
        return result, _attr(dataset, "time_origin")
    finally:
        dataset.close()


def read_slp_minimum(
    path: Path,
    minutes: Iterable[int],
    *,
    opener: Callable[[Path], Any] = _default_open,
) -> dict[int, tuple[float, int, int]]:
    """slp.nc（cartesian_2d）逐记录求全场最小值与位置；只读需要的时次。"""
    path = Path(path)
    if not path.exists():
        return {}
    dataset = opener(path)
    try:
        index = _time_index(dataset)
        data = dataset["data"]
        result = {}
        for minute in minutes:
            record = index.get(int(minute))
            if record is None:
                continue
            field = np.asarray(np.ma.filled(np.ma.asarray(data[record]), np.nan), dtype=float)
            if not np.isfinite(field).any():
                continue
            flat = int(np.nanargmin(field))
            y, x = np.unravel_index(flat, field.shape)
            result[int(minute)] = (float(field[y, x]), int(y), int(x))
        return result
    finally:
        dataset.close()


def _domain_from_dir(directory: Path) -> int | None:
    name = Path(directory).name
    if len(name) == 3 and name[0] == "d" and name[1:].isdigit():
        return int(name[1:])
    return None


def _format_time(origin: str | None, minute: int) -> str:
    if not origin:
        return ""
    try:
        start = datetime.strptime(str(origin).strip().replace("T", "_"), "%Y-%m-%d_%H:%M:%S")
    except ValueError:
        return ""
    return (start + timedelta(minutes=int(minute))).strftime("%Y-%m-%d_%H:%M:%S")


def build_table(
    domain_dir: Path,
    *,
    step_minutes: int | None = 60,
    start_minutes: int | None = None,
    end_minutes: int | None = None,
    with_slp_min: bool = True,
    opener: Callable[[Path], Any] = _default_open,
) -> list[dict[str, Any]]:
    """读 <试验>/dXX/ 目录，返回按时间排序的行（字典，键见 COLUMNS）。缺的量填 NaN。"""
    domain_dir = Path(domain_dir)
    grid_km = GRID_KM.get(_domain_from_dir(domain_dir) or 3, 1.0)

    axis, origin_a = read_records(domain_dir / "axis_mean_max_wind.nc", opener=opener)
    local, origin_b = read_records(domain_dir / "local_max_wind.nc", opener=opener)
    rmw, _ = read_records(domain_dir / "rmw.nc", opener=opener)
    center, origin_c = read_records(domain_dir / "slp_center.nc", with_metadata=True, opener=opener)
    geo, _ = read_records(domain_dir / "center_geo.nc", opener=opener)
    slp5, _ = read_records(domain_dir / "center_slp_5km.nc", opener=opener)
    origin = origin_a or origin_b or origin_c

    minutes = sorted(set(axis) | set(local) | set(center))
    if step_minutes:
        minutes = [m for m in minutes if m % int(step_minutes) == 0]
    if start_minutes is not None:
        minutes = [m for m in minutes if m >= start_minutes]
    if end_minutes is not None:
        minutes = [m for m in minutes if m <= end_minutes]

    slp_min = read_slp_minimum(domain_dir / "slp.nc", minutes, opener=opener) if with_slp_min else {}

    nan = float("nan")
    rows = []
    for minute in minutes:
        row: dict[str, Any] = {name: nan for name in COLUMNS}
        row["time_minutes"] = minute
        row["time_h"] = minute / 60.0
        row["datetime"] = _format_time(origin, minute)
        row["vmax_axis_mean_ms"] = axis.get(minute, nan)
        row["vmax_local_ms"] = local.get(minute, nan)
        row["rmw_km"] = rmw.get(minute, nan)
        row["slp_center_5km_hPa"] = slp5.get(minute, nan)
        if minute in center:
            value, meta = center[minute]
            yx = np.asarray(value, dtype=float).ravel()
            if yx.size >= 2:
                row["center_y"], row["center_x"] = float(yx[-2]), float(yx[-1])
            pressure = meta.get("pressure_hpa") if isinstance(meta, dict) else None
            if pressure is not None:
                row["slp_at_center_hPa"] = float(pressure)
        if minute in geo:
            latlon = np.asarray(geo[minute], dtype=float).ravel()
            if latlon.size >= 2:
                row["center_lat"], row["center_lon"] = float(latlon[0]), float(latlon[1])
        if minute in slp_min:
            value, y, x = slp_min[minute]
            row["slp_min_hPa"], row["slp_min_y"], row["slp_min_x"] = value, y, x
            if np.isfinite(row["center_y"]):
                row["slp_min_offset_km"] = float(
                    np.hypot(y - row["center_y"], x - row["center_x"]) * grid_km
                )
        rows.append(row)
    return rows


def write_csv(rows: list[dict[str, Any]], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            out = {}
            for key in COLUMNS:
                value = row.get(key, "")
                if isinstance(value, float):
                    out[key] = "" if not np.isfinite(value) else (
                        f"{value:.0f}" if key in ("slp_min_y", "slp_min_x") else f"{value:.6g}"
                    )
                else:
                    out[key] = value
            writer.writerow(out)
    return path


def read_csv(source) -> list[dict[str, Any]]:
    """读 write_csv 写的表；source 可以是路径，也可以是已打开的文本流（如从 tar.gz 里读出的）。"""
    if hasattr(source, "read"):
        return _parse_csv(source)
    with Path(source).open(newline="", encoding="utf-8") as handle:
        return _parse_csv(handle)


def _parse_csv(handle) -> list[dict[str, Any]]:
    rows = []
    for raw in csv.DictReader(handle):
        row: dict[str, Any] = {}
        for key in COLUMNS:
            text = (raw.get(key) or "").strip()
            if key == "datetime":
                row[key] = text
            else:
                try:
                    row[key] = float(text) if text else float("nan")
                except ValueError:
                    row[key] = float("nan")
        rows.append(row)
    return rows


def column(rows: list[dict[str, Any]], key: str) -> np.ndarray:
    return np.array([row.get(key, np.nan) for row in rows], dtype=float)
