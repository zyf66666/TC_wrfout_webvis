"""Slice resolver for processed_v3 NetCDF (2026-11-01: v2 NPY fallback removed)."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import numpy as np
from netCDF4 import Dataset

from 处理数据_v3.wrf_processing_v3.center_schemes import BASE_CENTER_SCHEME, processed_path, split_member
from 处理数据_v3.wrf_processing_v3.locking import file_lock
from 处理数据_v3.wrf_processing_v3.request import Task
from 处理数据_v3.wrf_processing_v3.storage import HEIGHT_LAYOUTS, NetCDFStorage, read_completed_flags


class _CompletedFlags:
    """按需读取 completed 标记；请求较多时退回整表读取。"""

    def __init__(self, variable: Any, requested: int) -> None:
        self._variable = variable
        self._full: np.ndarray | None = None
        size = int(np.prod(variable.shape)) if variable.shape else 1
        # 一维标记（每个时次一个块）或请求量接近整表时，整表读取更省事。
        if variable.ndim < 2 or requested * 4 >= size:
            self._full = (
                np.asarray(variable[:])
                if variable.ndim < 2
                else np.column_stack(
                    [np.asarray(variable[:, height]) for height in range(variable.shape[1])]
                )
            )

    def flag(self, selection: tuple[int, ...]) -> int:
        if self._full is not None:
            return int(self._full[selection])
        return int(np.asarray(self._variable[selection]).reshape(-1)[0])


@dataclass(frozen=True, slots=True)
class DataReference:
    backend: str
    path: Path
    task: Task
    time_origin: str | None = None


_CENTER_DEPENDENT = None

# 2026-11-05 缺切片“留空”模式（format.common.missing_slices = "blank"）：只在画图时打开。
# 打开后，变量文件存在、但某时次/高度未完成的切片读成全 NaN（图上留白），不再因缺切片整张图失败。
# 中心类变量仍按缺失处理：缺中心时无法定位，留空会让后续裁剪/坐标出错。
_BLANK_MISSING: ContextVar[bool] = ContextVar("blank_missing_slices", default=False)
BLANK_STRICT_VARIABLES = frozenset({"slp_center", "layer_center", "center_geo", "center_slp_5km"})


@contextmanager
def blank_missing_slices(enabled: bool = True) -> Iterator[None]:
    """在此范围内，读取器把“文件存在但切片未完成”的切片当作全 NaN 返回。"""
    token = _BLANK_MISSING.set(bool(enabled))
    try:
        yield
    finally:
        _BLANK_MISSING.reset(token)


def blank_missing_enabled() -> bool:
    return _BLANK_MISSING.get()


def blankable_missing(variable: str, path: str | Path) -> bool:
    """留空模式下可以留白的缺失切片：变量文件存在，且不是中心类变量。"""
    return str(variable) not in BLANK_STRICT_VARIABLES and Path(path).is_file()


def plot_center_dependent(variable: str) -> bool:
    """绘图端判断变量是否依赖中心（与处理端同一张依赖图）。"""
    global _CENTER_DEPENDENT
    if _CENTER_DEPENDENT is None:
        from 处理数据_v3.wrf_processing_v3.register import build_registry

        _CENTER_DEPENDENT = build_registry({}).center_dependent
    try:
        return bool(_CENTER_DEPENDENT(variable))
    except (KeyError, ValueError):
        # 注册表里没有的名字（极少见）按依赖中心处理：宁可报缺数据，也不悄悄读 base 方案的结果。
        return True


class ProcessedDataReader:
    """只读 processed_v3：按“试验/dXX/变量.nc”里的 completed 标记判断切片是否可用。"""

    def __init__(self, processed_v3_root: str | Path) -> None:
        if processed_v3_root is None:
            raise ValueError("ProcessedDataReader 需要 processed_v3 根目录")
        self.processed_v3_root = Path(processed_v3_root)
        self._resolved: dict[Task, DataReference | None] = {}
        self._blank_shapes: dict[tuple[Path, bool], tuple[int, ...]] = {}

    def expected_path(self, task: Task) -> Path:
        """切片所在的 NetCDF 文件路径（不论是否已完成），用于缺失提示。

        2026-11-02：task.experiment 可以是“试验@中心方案”；依赖中心的变量在 <根>/centers/<方案>/ 下。
        """
        return self.variable_path(task.experiment, task.domain, task.variable)

    def variable_path(self, member_name: str, domain: int, variable: str) -> Path:
        experiment, scheme = split_member(member_name)
        return processed_path(
            self.processed_v3_root, experiment, int(domain), variable, center_scheme=scheme,
            center_dependent=plot_center_dependent if scheme != BASE_CENTER_SCHEME else None,
        )

    def _storage(self, task: Task, time_origin: str) -> tuple[NetCDFStorage, Task]:
        """把“试验@方案”拆开：返回按方案定位文件的存储对象，以及试验名为纯试验名的切片。"""
        experiment, scheme = split_member(task.experiment)
        storage = NetCDFStorage(
            self.processed_v3_root, time_origin=time_origin, center_scheme=scheme,
            center_dependent=plot_center_dependent if scheme != BASE_CENTER_SCHEME else None,
        )
        return storage, Task(experiment, task.domain, task.time_minutes, task.variable, task.height_m)

    def list_time_minutes(self, member_name: str, domain: int, variable: str) -> list[int]:
        """某变量文件里出现过的全部时次（分钟）；文件不存在时为空。"""
        storage, task = self._storage(Task(member_name, int(domain), 0, variable, None), "unused")
        return storage.list_time_minutes(task.experiment, task.domain, task.variable)

    def prefetch(self, tasks: list[Task]) -> None:
        """Check requested slices by variable file, opening each NetCDF once."""
        grouped: dict[Path, list[Task]] = {}
        for task in tasks:
            if task in self._resolved:
                continue
            grouped.setdefault(self.expected_path(task), []).append(task)
        for path, members in grouped.items():
            if path.is_file():
                with file_lock(path, exclusive=False, timeout_seconds=120.0):
                    with Dataset(path) as dataset:
                        dataset.set_auto_mask(False)
                        origin = str(dataset.getncattr("time_origin"))
                        times = [int(value) for value in dataset["time_minutes"][:]]
                        heights = [int(value) for value in dataset["height_m"][:]] if "height_m" in dataset.variables else []
                        if len(times) != len(set(times)) or len(heights) != len(set(heights)):
                            raise ValueError(f"NetCDF 坐标有重复值：{path}")
                        time_index = {value: index for index, value in enumerate(times)}
                        height_index = {value: index for index, value in enumerate(heights)}
                        # 只读取本次请求涉及的完成标记。completed 按 (1, 1) 分块存放，
                        # 整表读取要访问“时次×高度”个小数据块（例如 103×46≈4700 个），
                        # 在外接盘上需要数十秒；而一次绘图通常只用到其中几十到几百个。
                        completed = _CompletedFlags(dataset["completed"], len(members))
                        lowest = dataset.groups.get("lowest")
                        lowest_completed = (
                            _CompletedFlags(lowest["completed"], len(members))
                            if lowest is not None
                            and {"data", "completed", "metadata_json"}.issubset(lowest.variables)
                            and any(task.height_m == "lowest" for task in members)
                            else None
                        )
                        for task in members:
                            record = time_index.get(task.time_minutes)
                            if task.height_m == "lowest" and dataset.layout in HEIGHT_LAYOUTS:
                                if record is not None and lowest_completed is not None and lowest_completed.flag((record,)) == 1:
                                    self._resolved[task] = DataReference("netcdf4", path, task, origin)
                                continue
                            height = height_index.get(task.height_m) if heights else None
                            if record is None or (heights and height is None):
                                continue
                            selection = (record, height) if heights else (record,)
                            if completed.flag(selection) == 1:
                                self._resolved[task] = DataReference("netcdf4", path, task, origin)
            blank = _BLANK_MISSING.get()
            for task in members:
                if blank and self._resolved.get(task) is None and blankable_missing(task.variable, path):
                    self._resolved[task] = DataReference("blank", path, task, None)
                else:
                    self._resolved.setdefault(task, None)

    def resolve(
        self,
        *,
        member_name: str,
        domain: int,
        variable: str,
        time_hour: int | float,
        height_m: int | float | str | None,
    ) -> DataReference | None:
        minute = self._minute(time_hour)
        height = self._height(height_m)
        task = Task(member_name, int(domain), minute, variable, height)
        self.prefetch([task])
        return self._resolved[task]

    def load(self, reference: DataReference) -> np.ndarray:
        if reference.backend == "blank":
            return np.full(self._blank_shape(reference), np.nan, dtype=np.float32)
        if reference.backend != "netcdf4" or reference.time_origin is None:
            raise ValueError(f"不支持的 processed 数据引用：{reference.backend}（只读 processed_v3）")
        storage, task = self._storage(reference.task, reference.time_origin)
        return storage.load(task)

    def _blank_shape(self, reference: DataReference) -> tuple[int, ...]:
        """留空切片的形状：同一文件里一个切片的形状（去掉 record、height 维）。"""
        lowest = reference.task.height_m == "lowest"
        key = (reference.path, lowest)
        if key not in self._blank_shapes:
            with file_lock(reference.path, exclusive=False, timeout_seconds=120.0):
                with Dataset(reference.path) as dataset:
                    group = dataset.groups["lowest"] if lowest and "lowest" in dataset.groups else dataset
                    data = group["data"]
                    shape = tuple(
                        int(size) for dim, size in zip(data.dimensions, data.shape) if dim not in ("record", "height")
                    )
                    # 与 NetCDFStorage.load 一致：标量布局读出的是长度 1 的一维数组。
                    if str(getattr(dataset, "layout", "")) in {"scalar", "height_scalar"} and shape == ():
                        shape = (1,)
                    self._blank_shapes[key] = shape
        return self._blank_shapes[key]

    def load_metadata(self, reference: DataReference) -> dict[str, Any]:
        """读取切片的 metadata_json（单位、算法版本、中心参数等）。"""
        if reference.backend == "blank":
            return {"blank_missing_slice": True}
        storage, task = self._storage(reference.task, reference.time_origin or "unused")
        return storage.load_metadata(task)

    def grid_info(self, reference: DataReference) -> dict[str, Any]:
        """2026-11-04 绘图自动识别网格（见 grid_detection.py）：布局、笛卡尔格点数、极坐标径向间隔、
        以及该切片元数据里的网格间距 grid_spacing_km=(dx, dy)（处理时从 wrfout DX/DY 写入）。"""
        from .grid_detection import spacing_from_metadata

        storage, task = self._storage(reference.task, reference.time_origin or "unused")
        path = storage.path_for(task)
        info: dict[str, Any] = {"path": str(path)}
        with file_lock(path, exclusive=False, timeout_seconds=120.0):
            with Dataset(path) as dataset:
                dataset.set_auto_mask(False)
                info["layout"] = str(getattr(dataset, "layout", ""))
                if "y" in dataset.dimensions and "x" in dataset.dimensions:
                    info["ny"] = len(dataset.dimensions["y"])
                    info["nx"] = len(dataset.dimensions["x"])
                if "radius_km" in dataset.variables:
                    radius = np.asarray(dataset["radius_km"][:], dtype=float)
                    steps = np.diff(radius)
                    if steps.size and np.all(np.isfinite(steps)) and np.all(steps > 0):
                        info["radius_resolution_km"] = float(np.median(steps))
        spacing = spacing_from_metadata(self.load_metadata(reference))
        if spacing is not None:
            info["grid_spacing_km"] = spacing
        return info

    @staticmethod
    def _minute(time_hour: int | float) -> int:
        value = float(time_hour) * 60.0
        minute = round(value)
        if not np.isfinite(value) or abs(value - minute) > 1.0e-7:
            raise ValueError(f"time_hour={time_hour!r} 不能表示为整数分钟")
        return int(minute)

    @staticmethod
    def _height(value: int | float | str | None) -> int | str | None:
        if value in (None, "lowest"):
            return value
        number = float(value)
        height = round(number)
        if not np.isfinite(number) or abs(number - height) > 1.0e-7:
            raise ValueError(f"height_m={value!r} 不是整数米")
        return int(height)
