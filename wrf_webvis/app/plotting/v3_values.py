"""绘图叠加要素（RMW 圈、R34 线、陆地距离、云分类、路径图背景等）读取 processed_v3 单个切片的小工具。

2026-11-01：绘图不再读 processed_v2 的 .npy / .txt；这些要素统一通过同一个切片读取器取已完成的 v3 切片，
没有就返回 None，由调用方给出警告。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np

from ..services.data_manager import DataManager
from ..services.processed_data import ProcessedDataReader


# 功能说明：按绘图数据根目录（旧 v1/v2 目录会改指 v3）建立切片读取器。
def processed_reader(data_root: str | Path) -> ProcessedDataReader:
    return DataManager(data_root).processed_reader


# 功能说明：读取一个已完成的 v3 切片；没有该切片时返回 None。
def read_slice(
    reader: ProcessedDataReader,
    member_name: str,
    domain: int,
    variable: str,
    time_hour: float,
    height_m: float | str | None = None,
) -> np.ndarray | None:
    reference = reader.resolve(
        member_name=member_name, domain=int(domain), variable=variable,
        time_hour=float(time_hour), height_m=height_m,
    )
    if reference is None:
        return None
    return np.asarray(reader.load(reference), dtype=float)


# 功能说明：读取标量切片（如 rmw、r_34、center_land_distance）的数值；没有切片返回 None，缺测返回 NaN。
def read_scalar(
    reader: ProcessedDataReader,
    member_name: str,
    domain: int,
    variable: str,
    time_hour: float,
    height_m: float | str | None = None,
) -> float | None:
    values = read_slice(reader, member_name, domain, variable, time_hour, height_m)
    if values is None:
        return None
    flat = values.reshape(-1)
    return float(flat[0]) if flat.size else float("nan")


# 功能说明：逐时次读取标量序列，只保留已完成且有限的时次；返回 (时次, 数值) 两个数组。
def read_scalar_series(
    reader: ProcessedDataReader,
    member_name: str,
    domain: int,
    variable: str,
    times: Iterable[float],
    height_m: float | str | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    from 处理数据_v3.wrf_processing_v3.request import Task

    hours = [float(value) for value in times]
    reader.prefetch([
        Task(member_name, int(domain), reader._minute(hour), variable, reader._height(height_m))
        for hour in hours
    ])
    kept_times: list[float] = []
    kept_values: list[float] = []
    for hour in hours:
        value = read_scalar(reader, member_name, domain, variable, hour, height_m)
        if value is not None and np.isfinite(value):
            kept_times.append(hour)
            kept_values.append(value)
    return np.asarray(kept_times, dtype=float), np.asarray(kept_values, dtype=float)


# 功能说明：列出某变量在 v3 文件里出现过的全部时次（小时）；文件不存在时返回空列表。
def available_hours(reader: ProcessedDataReader, member_name: str, domain: int, variable: str) -> list[float]:
    minutes = reader.list_time_minutes(member_name, int(domain), variable)
    return [value / 60.0 for value in minutes]
