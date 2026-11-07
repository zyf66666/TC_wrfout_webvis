"""测试用：往临时目录写 processed_v3 NetCDF 切片（2026-11-01，绘图去掉 v2 .npy 回退后，绘图测试统一用它造数据）。

用法::

    from v3_fixtures import save_v3            # wrf_webvis/tests 下的测试
    from wrf_webvis.tests.v3_fixtures import save_v3   # 其他目录的测试

    root = tmp_path / "processed_v3"
    save_v3(root, "WRF_315_land", "theta", field2d, time_hour=6, height_m=1000)

变量已在 v3 注册表登记时沿用其存储布局；没登记的测试变量按数组形状推断布局
（标量→scalar/height_scalar，二维→cartesian 或 polar（名字以 _polar 结尾））。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

TIME_ORIGIN = "2023-09-01_00:00:00"
_REGISTRY = None


def _registry():
    from 处理数据_v3.wrf_processing_v3.register import build_registry

    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = build_registry()
    return _REGISTRY


def _infer_layout(variable: str, values: np.ndarray, height_m: Any) -> str:
    layered = height_m is not None
    if values.size == 1 and values.ndim <= 1:
        return "height_scalar" if layered else "scalar"
    if values.ndim == 1:
        return "height_vector" if layered else "vector"
    if variable.endswith("_polar"):
        return "polar_3d" if layered else "polar_2d"
    return "cartesian_3d" if layered else "cartesian_2d"


def spec_for(
    variable: str,
    values: np.ndarray,
    height_m: Any = None,
    *,
    layout: str | None = None,
    units: str | None = None,
    radius_resolution_km: float = 1.0,
):
    """返回 NetCDFStorage.save 需要的变量说明。"""
    if layout is None:
        try:
            registered = _registry().resolve(variable)
            layout = registered.storage_layout
            units = registered.units if units is None else units
        except (KeyError, ValueError):
            layout = _infer_layout(variable, values, height_m)
    return SimpleNamespace(
        name=variable,
        storage_layout=layout,
        units=units or "",
        options={"algorithm_version": "test-fixture", "radius_resolution_km": float(radius_resolution_km)},
    )


def save_v3(
    root: str | Path,
    member: str,
    variable: str,
    values: Any,
    *,
    time_hour: float = 0.0,
    height_m: int | float | str | None = None,
    domain: int = 3,
    layout: str | None = None,
    units: str | None = None,
    radius_resolution_km: float = 1.0,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """写一个已完成的 v3 切片，返回 .nc 文件路径。"""
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    array = np.asarray(values, dtype=np.float32)
    if array.ndim == 0:
        array = array.reshape(1)
    minute = round(float(time_hour) * 60.0)
    height = height_m if height_m in (None, "lowest") else int(round(float(height_m)))
    spec = spec_for(variable, array, height, layout=layout, units=units,
                    radius_resolution_km=radius_resolution_km)
    if spec.storage_layout not in {"cartesian_3d", "polar_3d", "height_scalar", "height_vector"}:
        height = None
    # 2026-11-02：member 可写“试验@中心方案”，依赖中心的变量写到 <root>/centers/<方案>/（与绘图读取同一路径规则）。
    from 处理数据_v3.wrf_processing_v3.center_schemes import split_member

    experiment, scheme = split_member(member)
    task = Task(experiment, int(domain), int(minute), variable, height)
    storage = NetCDFStorage(
        Path(root), time_origin=TIME_ORIGIN, center_scheme=scheme,
        center_dependent=_registry().center_dependent if scheme != "base" else None,
    )
    return storage.save(task, array, spec, metadata=dict(metadata or {}))


def save_v3_series(
    root: str | Path,
    member: str,
    variable: str,
    values_by_hour: dict[float, Any] | list[tuple[float, Any]],
    **kwargs: Any,
) -> Path:
    """逐时次写同一变量；values_by_hour 为 {小时: 数值或数组}。"""
    items = values_by_hour.items() if isinstance(values_by_hour, dict) else values_by_hour
    path = None
    for hour, value in items:
        path = save_v3(root, member, variable, value, time_hour=hour, **kwargs)
    return path


def _hours_from_name(name: str) -> float:
    import re

    stem = name[:-4] if name.endswith(".npy") else name
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)h(?:(\d+(?:\.\d+)?)min)?", stem)
    if not match:
        raise ValueError(f"不是时次名：{name}")
    hours = float(match.group(1))
    minutes = float(match.group(2) or 0.0)
    return hours + (-1.0 if hours < 0 else 1.0) * minutes / 60.0


def save_at_legacy_path(path: str | Path, values: Any, **kwargs: Any) -> Path:
    """按旧 v2 路径写对应的 v3 切片，方便把旧测试里的 ``np.save(path, ...)`` 换成 v3 数据。

    认得两种写法：``<根>/<试验>/dXX/<变量>/<时次>.npy`` 和 ``<根>/<试验>/dXX/<变量>/<时次>/<高度>m.npy``；
    数据写到 ``<根>/<试验>/dXX/<变量>.nc``。
    """
    parts = Path(path).parts
    if parts[-1].endswith("m.npy") and not parts[-1].endswith("min.npy"):
        height = float(parts[-1][:-5])
        time_hour = _hours_from_name(parts[-2])
        variable, domain_dir, member = parts[-3], parts[-4], parts[-5]
        root = Path(*parts[:-5])
    else:
        height = kwargs.pop("height_m", None)
        time_hour = _hours_from_name(parts[-1])
        variable, domain_dir, member = parts[-2], parts[-3], parts[-4]
        root = Path(*parts[:-4])
    if not domain_dir.startswith("d"):
        raise ValueError(f"路径里没有 dXX：{path}")
    return save_v3(root, member, variable, values, time_hour=time_hour, height_m=height,
                   domain=int(domain_dir[1:]), **kwargs)
