"""绘图用的台风中心查找：读 processed_v3（slp_center / layer_center）；2026-11-01 起不再退回旧的 center.txt。

水平图的数据坐标、中心叠加标记都应通过这里取中心，保证同一张图只用一个中心。
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np

from ..core.centers import load_center_yx
from ..services.data_manager import DataManager


# 功能说明：返回某成员、某时刻的中心格点 (y, x)；找不到时发出警告并返回 None，由调用方决定退回值。
def load_center_grid(
    *,
    data_root: str | Path,
    member_name: str,
    domain: int,
    time_hour: int | float,
    height_m: int | float | None = None,
    center_path: str | Path | None = None,
    quiet: bool = False,
) -> tuple[float, float] | None:
    """Return the vortex center as raw grid indices ``(y, x)``.

    Order: an explicit ``center_path`` file; otherwise the processed_v3
    ``slp_center`` (``layer_center`` when ``height_m`` is given) through the
    same reader the data layer uses.  Every failure path warns so a plot never silently changes its reference center;
    ``quiet=True`` suppresses only the final "not found" warning (for callers
    that probe many heights and report once themselves).
    """
    variable = "slp_center" if height_m is None else "layer_center"
    label = f"{member_name} d{int(domain):02d} {float(time_hour):g} h {variable}" + (
        "" if height_m is None else f" {float(height_m):g} m"
    )

    if center_path:
        path = Path(center_path)
        if not path.exists():
            warnings.warn(f"指定的中心文件不存在：{path}（{label}）")
            return None
        try:
            return load_center_yx(path, time_hour)
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"指定的中心文件无法读取当前时次：{path}, {exc}")
            return None

    try:
        manager = DataManager(data_root)
        reference = manager.processed_reader.resolve(
            member_name=member_name,
            domain=domain,
            variable=variable,
            time_hour=time_hour,
            height_m=height_m,
        )
        if reference is not None:
            values = np.asarray(manager.processed_reader.load(reference), dtype=float).reshape(-1)
            if values.size >= 2 and np.isfinite(values[:2]).all():
                return float(values[0]), float(values[1])
            warnings.warn(f"processed_v3 中的中心不是有效的 [y, x]：{label}")
            return None
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"读取 processed_v3 中心失败：{label}, {exc}")
        return None

    if not quiet:
        warnings.warn(f"找不到中心：{label}（processed_v3 中没有该切片）")
    return None


# 功能说明：从 processed_v3 一次读出多个时次的地面中心轨迹（格点 y、x 与经纬度）；没有 slp_center 时返回 None。
def load_v3_center_series(
    *,
    data_root: str | Path,
    member_name: str,
    domain: int,
    times: list[float] | None = None,
) -> dict | None:
    """Read the processed_v3 ``slp_center`` track with one reader.

    Latitude/longitude come from ``center_geo``; when that slice is missing the
    moving-nest ``XLAT``/``XLONG`` of the same time are indexed at the center.
    Times without a completed ``slp_center`` slice are skipped with a warning.
    """
    from 处理数据_v3.wrf_processing_v3.request import Task

    reader = DataManager(data_root).processed_reader
    path = reader.variable_path(member_name, int(domain), "slp_center")
    if not path.is_file():
        return None

    if times:
        hours = [float(value) for value in times]
    else:
        minutes = reader.list_time_minutes(member_name, int(domain), "slp_center")
        hours = [value / 60.0 for value in minutes]
    hours = sorted(set(hours))

    def task(variable: str, hour: float) -> Task:
        return Task(member_name, int(domain), reader._minute(hour), variable, None)

    reader.prefetch([task(name, hour) for hour in hours for name in ("slp_center", "center_geo")])

    def read(variable: str, hour: float):
        reference = reader.resolve(
            member_name=member_name, domain=domain, variable=variable, time_hour=hour, height_m=None
        )
        return None if reference is None else np.asarray(reader.load(reference), dtype=float)

    rows: list[list[float]] = []
    missing: list[float] = []
    for hour in hours:
        center = read("slp_center", hour)
        if center is None or center.size < 2 or not np.isfinite(center.reshape(-1)[:2]).all():
            missing.append(hour)
            continue
        y, x = (float(value) for value in center.reshape(-1)[:2])
        lat = lon = float("nan")
        geo = read("center_geo", hour)
        if geo is not None and geo.size >= 2:
            lat, lon = (float(value) for value in geo.reshape(-1)[:2])
        if not (np.isfinite(lat) and np.isfinite(lon)):
            xlat, xlong = read("XLAT", hour), read("XLONG", hour)
            if xlat is not None and xlong is not None and xlat.ndim == 2 and xlat.shape == xlong.shape:
                iy, ix = int(round(y)), int(round(x))
                if 0 <= iy < xlat.shape[0] and 0 <= ix < xlat.shape[1]:
                    lat, lon = float(xlat[iy, ix]), float(xlong[iy, ix])
        rows.append([hour, y, x, lat, lon])

    if missing:
        warnings.warn(
            f"{member_name} d{int(domain):02d} 缺少 slp_center 的时次（已跳过）："
            + ", ".join(f"{value:g} h" for value in missing[:10])
            + (" …" if len(missing) > 10 else "")
        )
    if not rows:
        return None
    table = np.asarray(rows, dtype=float)
    if not np.isfinite(table[:, 3:5]).all():
        warnings.warn(
            f"{member_name} d{int(domain):02d} 部分时次没有 center_geo/XLAT/XLONG，"
            "轨迹可能退回格点坐标；移动嵌套网格下格点坐标不代表真实移动"
        )
    return {
        "path": path,
        "times": table[:, 0],
        "y": table[:, 1],
        "x": table[:, 2],
        "lat": table[:, 3],
        "lon": table[:, 4],
    }
