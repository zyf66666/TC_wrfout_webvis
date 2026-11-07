"""Slice-oriented NetCDF4/HDF5 processed-data storage."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from netCDF4 import Dataset

from . import completion_index
from .center_schemes import BASE_CENTER_SCHEME, processed_path, validate_scheme_id
from .locking import file_lock
from .request import Task


SCHEMA_VERSION = "3.0"
DEFAULT_HEIGHTS_M = tuple(range(100, 1001, 100)) + tuple(
    range(1250, 15001, 250)
)

HEIGHT_LAYOUTS = {"cartesian_3d", "polar_3d", "height_vector", "height_scalar"}
SPATIAL_LAYOUTS = {
    "cartesian_3d",
    "polar_3d",
    "cartesian_2d",
    "polar_2d",
    "scalar",
    "height_scalar",
    "vector",
    "height_vector",
}


def read_completed_flags(dataset: Dataset) -> np.ndarray:
    """Read completion metadata reliably for NetCDF's unlimited record axis.

    The netCDF4 build used by the web environment can return fill values for
    later records when a two-dimensional variable is read with ``[:]``.
    Reading whole height columns avoids that error while still batching times.
    """
    variable = dataset["completed"]
    if variable.ndim == 1:
        return np.asarray(variable[:])
    if variable.ndim == 2:
        return np.column_stack([np.asarray(variable[:, height]) for height in range(variable.shape[1])])
    raise ValueError("completed 必须是一维或二维")


class NetCDFStorage:
    def __init__(
        self,
        root: str | Path,
        *,
        time_origin: str,
        default_heights_m: Iterable[int] = DEFAULT_HEIGHTS_M,
        lock_timeout_seconds: float = 120.0,
        center_scheme: str | None = None,
        center_dependent: Any = None,
    ) -> None:
        self.root = Path(root)
        # 2026-11-02 中心方案：非 base 方案下“依赖中心”的变量存到 <root>/centers/<方案>/，其余仍在 <root>/。
        self.center_scheme = validate_scheme_id(center_scheme or BASE_CENTER_SCHEME)
        if self.center_scheme != BASE_CENTER_SCHEME and center_dependent is None:
            raise ValueError("非 base 中心方案的存储需要注册表的 center_dependent 判断")
        self.center_dependent = center_dependent
        self.time_origin = str(time_origin)
        heights = tuple(int(value) for value in default_heights_m)
        if len(set(heights)) != len(heights) or any(value < 0 for value in heights):
            raise ValueError("default_heights_m 必须是唯一的非负整数米")
        self.default_heights_m = heights
        self.lock_timeout_seconds = float(lock_timeout_seconds)

    def path_for(self, task: Task) -> Path:
        return self.path_for_variable(task.experiment, task.domain, task.variable)

    def path_for_variable(self, experiment: str, domain: int, variable: str) -> Path:
        return processed_path(
            self.root, experiment, domain, variable,
            center_scheme=self.center_scheme, center_dependent=self.center_dependent,
        )

    def in_scheme_directory(self, variable: str) -> bool:
        """该变量是否存放在本方案自己的 centers/<方案>/ 目录（base 方案恒为否）。"""
        return self.center_scheme != BASE_CENTER_SCHEME and bool(self.center_dependent(variable))

    def save(
        self,
        task: Task,
        array: np.ndarray,
        spec: Any | dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        dtype: str | None = None,
    ) -> Path:
        if isinstance(spec, dict) and metadata is None:
            metadata, spec = spec, None
        return self.save_many([(task, array, spec, metadata)])[0]

    def save_many(self, items: Iterable[tuple[Task, np.ndarray, Any, dict[str, Any] | None]]) -> list[Path]:
        """写一批切片；同一个 .nc 的切片一次加锁、一次打开、一次写盘（2026-11-03 加速）。

        原来每个切片单独加锁、打开几 GB 的文件、强制写盘 3–4 次再关闭，外接机械硬盘上约 1 s/切片。
        崩溃安全的顺序不变：先写数据和元数据并写盘，再把完成标记置 1（关闭文件时落盘）；
        覆盖已完成的切片时，先把它的完成标记清零并写盘，所以中途断电不会留下“已完成但数据只写了一半”的切片。
        写入的数值、坐标、元数据与逐个 save 完全相同。
        """
        prepared = []
        for task, array, spec, metadata in items:
            if isinstance(spec, dict) and metadata is None:
                metadata, spec = spec, None
            values = np.asarray(array, dtype=np.float32)
            layout = self._layout(spec, values, task)
            units = str(getattr(spec, "units", "") or (metadata or {}).get("units", ""))
            options = dict(getattr(spec, "options", {}) or {})
            details = {
                "units": units,
                "spec_options": options,
                "algorithm_version": options.get("algorithm_version"),
                **(metadata or {}),
            }
            prepared.append((task, values, layout, units, options, details))
        groups: dict[Path, list[tuple]] = {}
        for entry in prepared:
            groups.setdefault(self.path_for(entry[0]), []).append(entry)
        for path, members in groups.items():
            self._save_group(path, members)
        return [self.path_for(entry[0]) for entry in prepared]

    def _save_group(self, path: Path, members: list[tuple]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(
            path,
            exclusive=True,
            timeout_seconds=self.lock_timeout_seconds,
        ):
            # 2026-11-03 完成情况索引：写之前取有效索引（与当前文件一致才算），写完后增量更新。
            before = completion_index.load_valid(path) if path.exists() else None
            new_index = None
            if not path.exists():
                task, values, layout, units, options, details = members[0]
                self._create(path, task, values, layout, units, options, details)
            with Dataset(path, "r+") as dataset:
                dataset.set_auto_mask(False)
                writes = []
                for task, values, layout, units, options, details in members:
                    self._validate_identity(dataset, task, layout)
                    expected_algorithm = str(options.get("algorithm_version") or "")
                    actual_algorithm = str(getattr(dataset, "algorithm_version", ""))
                    if expected_algorithm and actual_algorithm and expected_algorithm != actual_algorithm:
                        raise ValueError(
                            f"NetCDF 算法版本不匹配：{actual_algorithm!r}，期望 {expected_algorithm!r}"
                        )
                    self._validate_coordinates(dataset, values, layout, options, details)
                    record_index = self._coordinate_index(
                        dataset, "time_minutes", task.time_minutes, append=True
                    )
                    height_index = None
                    target = dataset
                    if layout in HEIGHT_LAYOUTS:
                        if task.height_m == "lowest":
                            target = self._create_lowest_group(dataset)
                        elif isinstance(task.height_m, int):
                            height_index = self._coordinate_index(
                                dataset, "height_m", task.height_m, append=True
                            )
                        else:
                            raise ValueError(f"{layout} 任务必须指定整数 height_m")
                    selection = (
                        (record_index, height_index)
                        if height_index is not None
                        else (record_index,)
                    )
                    metadata_text = json.dumps(
                        self._canonicalize(details),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    writes.append((task, target, selection, layout, values, metadata_text))
                # 覆盖已完成的切片：先清零完成标记并写盘，再动数据。新切片的标记本来就是 0（填充值）。
                reset = False
                for _, target, selection, *_ in writes:
                    if int(np.asarray(target["completed"][selection])) != 0:
                        target["completed"][selection] = np.uint8(0)
                        reset = True
                if reset:
                    dataset.sync()
                for _, target, selection, layout, values, metadata_text in writes:
                    self._write_data(target, layout, selection, values)
                    target["metadata_json"][selection] = metadata_text
                dataset.sync()
                for _, target, selection, *_ in writes:
                    target["completed"][selection] = np.uint8(1)
                new_index = self._index_after_save(
                    dataset, before, [(task, text) for task, _, _, _, _, text in writes]
                )
            # .nc 关闭后再写索引（索引记的是关闭后的文件签名），仍在写锁内。
            completion_index.write(path, new_index)

    @staticmethod
    def _index_after_save(dataset, before, written):
        """写完一批切片后的完成情况索引；written 为 [(task, metadata_json 文本)]。
        出任何问题都返回 None（旧索引随文件变化自动失效）。"""
        if not completion_index.enabled():
            return None
        try:
            times = NetCDFStorage._unique_coordinate(dataset, "time_minutes")
            heights = NetCDFStorage._unique_coordinate(dataset, "height_m") if "height_m" in dataset.variables else None
            index = before
            for task, metadata_text in written:
                if index is None:
                    break
                index = completion_index.updated(index, times, heights, task, json.loads(metadata_text))
            return index if index is not None else completion_index.build(dataset, read_completed_flags)
        except Exception:  # noqa: BLE001 - 索引只是加速用的副本
            return None

    def _completion_index(self, path: Path) -> dict[str, Any] | None:
        """调用方须持有 path 的文件锁。有效索引直接返回；没有时从 NetCDF 整体读一次并写出索引。"""
        if not completion_index.enabled():
            return None
        index = completion_index.load_valid(path)
        if index is not None:
            return index
        with completion_index.build_lock(path):
            index = completion_index.load_valid(path)
            if index is not None:
                return index
            try:
                with Dataset(path) as dataset:
                    dataset.set_auto_mask(False)
                    index = completion_index.build(dataset, read_completed_flags)
            except Exception:  # noqa: BLE001 - 读不了就回到逐个读取
                return None
            completion_index.write(path, index)
            return index

    def exists(self, task: Task) -> bool:
        path = self.path_for(task)
        if not path.is_file():
            return False
        with file_lock(
            path,
            exclusive=False,
            timeout_seconds=self.lock_timeout_seconds,
        ):
            index = completion_index.load_valid(path)
            if index is not None:
                attrs = index.get("attrs", {})
                self._validate_identity(completion_index.IndexAttributes(attrs), task, attrs.get("layout"))
                return completion_index.lookup(index, task)
            with Dataset(path) as dataset:
                dataset.set_auto_mask(False)
                selection = self._selection(dataset, task, require_coordinates=False)
                if selection is None:
                    return False
                return bool(int(self._slice_group(dataset, task)["completed"][selection]))

    def existing(self, tasks: Iterable[Task]) -> set[Task]:
        """Return completed tasks while opening each variable file only once."""
        grouped: dict[Path, list[Task]] = defaultdict(list)
        for task in tasks:
            grouped[self.path_for(task)].append(task)
        result: set[Task] = set()
        for path, members in grouped.items():
            if not path.is_file():
                continue
            with file_lock(
                path,
                exclusive=False,
                timeout_seconds=self.lock_timeout_seconds,
            ):
                # 2026-11-03：先用完成情况索引（一个小 JSON），不在大文件里逐个寻道读完成标记。
                index = self._completion_index(path)
                if index is not None:
                    attrs = index.get("attrs", {})
                    self._validate_identity(completion_index.IndexAttributes(attrs), members[0], attrs.get("layout"))
                    result.update(task for task in members if completion_index.lookup(index, task))
                    continue
                with Dataset(path) as dataset:
                    dataset.set_auto_mask(False)
                    layout = dataset.getncattr("layout")
                    self._validate_identity(dataset, members[0], layout)
                    time_index = {
                        value: index
                        for index, value in enumerate(
                            self._unique_coordinate(dataset, "time_minutes")
                        )
                    }
                    height_index = None
                    if layout in HEIGHT_LAYOUTS:
                        height_index = {
                            value: index
                            for index, value in enumerate(
                                self._unique_coordinate(dataset, "height_m")
                            )
                        }
                    completed = dataset["completed"]
                    for task in members:
                        record = time_index.get(task.time_minutes)
                        if record is None:
                            continue
                        if height_index is None:
                            selection = (record,)
                        elif task.height_m == "lowest":
                            target = self._slice_group(dataset, task)
                            if target is not None and "completed" in target.variables:
                                if bool(int(target["completed"][record])):
                                    result.add(task)
                            continue
                        elif isinstance(task.height_m, int):
                            height = height_index.get(task.height_m)
                            if height is None:
                                continue
                            selection = (record, height)
                        else:
                            continue
                        if bool(int(completed[selection])):
                            result.add(task)
        return result

    def load(self, task: Task) -> np.ndarray:
        path = self.path_for(task)
        if not path.is_file():
            raise FileNotFoundError(path)
        with file_lock(
            path,
            exclusive=False,
            timeout_seconds=self.lock_timeout_seconds,
        ):
            with Dataset(path) as dataset:
                dataset.set_auto_mask(False)
                selection = self._selection(dataset, task, require_coordinates=True)
                target = self._slice_group(dataset, task)
                if not int(target["completed"][selection]):
                    raise KeyError(f"切片未完成：{task}")
                layout = dataset.getncattr("layout")
                value = self._read_data(target, layout, selection)
                if layout in {"scalar", "height_scalar"}:
                    return np.atleast_1d(np.asarray(value, dtype=np.float32))
                return np.asarray(value, dtype=np.float32)

    def shape(self, task: Task) -> tuple[int, ...]:
        """核对已完成切片并读取尺寸；不解压/读取 data 变量的数值。"""
        path = self.path_for(task)
        if not path.is_file():
            raise FileNotFoundError(path)
        with file_lock(path, exclusive=False, timeout_seconds=self.lock_timeout_seconds):
            with Dataset(path) as dataset:
                dataset.set_auto_mask(False)
                selection = self._selection(dataset, task, require_coordinates=True)
                target = self._slice_group(dataset, task)
                if not int(target["completed"][selection]):
                    raise KeyError(f"切片未完成：{task}")
                return self._slice_shape(dataset, target, selection)

    @staticmethod
    def _slice_shape(dataset, target, selection):
        if dataset.layout in {"scalar", "height_scalar"}:
            return (1,)
        return tuple(target["data"].shape[len(selection):])

    def load_metadata(self, task: Task) -> dict[str, Any]:
        path = self.path_for(task)
        if not path.is_file():
            return {}
        with file_lock(
            path,
            exclusive=False,
            timeout_seconds=self.lock_timeout_seconds,
        ):
            with Dataset(path) as dataset:
                dataset.set_auto_mask(False)
                selection = self._selection(dataset, task, require_coordinates=False)
                if selection is None:
                    return {}
                target = self._slice_group(dataset, task)
                if not int(target["completed"][selection]):
                    return {}
                remember_shape = getattr(getattr(self, "_coordinate_cache", None), "remember_shape", None)
                if remember_shape is not None:
                    remember_shape(task, self._slice_shape(dataset, target, selection))
                raw = target["metadata_json"][selection]
                text = str(raw.item() if isinstance(raw, np.ndarray) else raw)
                try:
                    return json.loads(text) if text else {}
                except json.JSONDecodeError:
                    return {}

    def list_time_minutes(self, experiment: str, domain: int, variable: str) -> list[int]:
        path = self.path_for_variable(experiment, domain, variable)
        if not path.is_file():
            return []
        with file_lock(
            path,
            exclusive=False,
            timeout_seconds=self.lock_timeout_seconds,
        ):
            with Dataset(path) as dataset:
                dataset.set_auto_mask(False)
                return sorted(self._unique_coordinate(dataset, "time_minutes"))

    def list_heights_m(self, experiment: str, domain: int, variable: str) -> list[int]:
        path = self.path_for_variable(experiment, domain, variable)
        if not path.is_file():
            return []
        with file_lock(
            path,
            exclusive=False,
            timeout_seconds=self.lock_timeout_seconds,
        ):
            with Dataset(path) as dataset:
                dataset.set_auto_mask(False)
                if "height_m" not in dataset.variables:
                    return []
                return sorted(self._unique_coordinate(dataset, "height_m"))

    def fingerprint(self, task: Task) -> dict[str, Any]:
        path = self.path_for(task)
        try:
            stat = path.stat()
        except FileNotFoundError:
            return {"data": None}
        return {
            "data": {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
            "slice": {
                "time_minutes": task.time_minutes,
                "height_m": task.height_m,
                "completed": self.exists(task),
            },
        }

    def _create(
        self,
        path: Path,
        task: Task,
        values: np.ndarray,
        layout: str,
        units: str,
        options: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        with Dataset(path, "w", format="NETCDF4") as dataset:
            dataset.set_auto_mask(False)
            dataset.setncatts(
                {
                    "schema_version": SCHEMA_VERSION,
                    "experiment": task.experiment,
                    "domain": int(task.domain),
                    "variable": task.variable,
                    "layout": layout,
                    "units": units,
                    "time_origin": self.time_origin,
                    "algorithm_version": str(options.get("algorithm_version") or ""),
                }
            )
            if self.in_scheme_directory(task.variable):
                dataset.setncattr("center_scheme", self.center_scheme)
            dataset.createDimension("record", None)
            time_variable = dataset.createVariable("time_minutes", "i8", ("record",))
            time_variable.setncatts(
                {
                    "long_name": "integer minutes after time_origin",
                    "units": f"minutes since {self.time_origin.replace('_', ' ')}",
                }
            )
            data_dimensions, completed_dimensions = self._create_layout_coordinates(
                dataset, layout, values, options, metadata
            )
            chunks = self._chunks(layout, values.shape)
            data = dataset.createVariable(
                "data",
                "f4",
                data_dimensions,
                zlib=True,
                complevel=2,
                shuffle=True,
                fletcher32=True,
                chunksizes=chunks,
                fill_value=np.float32(np.nan),
            )
            data.setncatts({"units": units, "coordinates": self._coordinates(layout)})
            completed = dataset.createVariable(
                "completed",
                "u1",
                completed_dimensions,
                fill_value=np.uint8(0),
                chunksizes=(1,) * len(completed_dimensions),
            )
            completed.setncatts(
                {
                    "long_name": "slice completion state",
                    "flag_values": np.asarray([0, 1], dtype=np.uint8),
                    "flag_meanings": "incomplete complete",
                }
            )
            dataset.createVariable(
                "metadata_json",
                str,
                completed_dimensions,
                chunksizes=(1,) * len(completed_dimensions),
            )
            dataset.sync()

    @staticmethod
    def _create_lowest_group(dataset: Dataset) -> Any:
        """Keep the lowest model level separate from physical metre heights.

        The group shares the root record/spatial dimensions. It can be added
        to existing files without changing any height coordinate or slice.
        """
        group = dataset.groups.get("lowest")
        if group is None:
            group = dataset.createGroup("lowest")
        group.setncatts({"vertical_level": "lowest_model_level", "model_level_index": 0})
        if "data" not in group.variables:
            parent_data = dataset["data"]
            dimensions = tuple(name for name in parent_data.dimensions if name != "height")
            chunks = tuple(
                size for name, size in zip(parent_data.dimensions, parent_data.chunking())
                if name != "height"
            )
            data = group.createVariable(
                "data", "f4", dimensions,
                zlib=True, complevel=2, shuffle=True, fletcher32=True,
                chunksizes=chunks, fill_value=np.float32(np.nan),
            )
            data.setncatts({
                "units": parent_data.units,
                "coordinates": " ".join(
                    name for name in parent_data.coordinates.split() if name != "height_m"
                ),
            })
        if "completed" not in group.variables:
            completed = group.createVariable(
                "completed", "u1", ("record",),
                fill_value=np.uint8(0), chunksizes=(1,),
            )
            completed.setncatts({
                "long_name": "lowest model level slice completion state",
                "flag_values": np.asarray([0, 1], dtype=np.uint8),
                "flag_meanings": "incomplete complete",
            })
        if "metadata_json" not in group.variables:
            group.createVariable("metadata_json", str, ("record",), chunksizes=(1,))
        group.set_auto_mask(False)
        return group

    @staticmethod
    def _slice_group(dataset: Dataset, task: Task) -> Any:
        if task.height_m == "lowest" and dataset.getncattr("layout") in HEIGHT_LAYOUTS:
            return dataset.groups.get("lowest")
        return dataset

    def _create_layout_coordinates(
        self,
        dataset: Dataset,
        layout: str,
        values: np.ndarray,
        options: dict[str, Any],
        metadata: dict[str, Any],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        height_prefix: tuple[str, ...] = ()
        completed_dimensions = ("record",)
        if layout in HEIGHT_LAYOUTS:
            dataset.createDimension("height", None)
            height = dataset.createVariable("height_m", "i4", ("height",))
            height.setncatts({"long_name": "height above mean sea level", "units": "m"})
            height[:] = np.asarray(self.default_heights_m, dtype=np.int32)
            height_prefix = ("height",)
            completed_dimensions = ("record", "height")
        if layout in {"cartesian_3d", "cartesian_2d"}:
            if values.ndim != 2:
                raise ValueError(f"{layout} 数据必须是二维切片")
            ny, nx = values.shape
            dataset.createDimension("y", ny)
            dataset.createDimension("x", nx)
            y = dataset.createVariable("y_index", "i4", ("y",))
            x = dataset.createVariable("x_index", "i4", ("x",))
            y[:] = np.arange(ny, dtype=np.int32)
            x[:] = np.arange(nx, dtype=np.int32)
            return ("record", *height_prefix, "y", "x"), completed_dimensions
        if layout in {"polar_3d", "polar_2d"}:
            if values.ndim != 2:
                raise ValueError(f"{layout} 数据必须是二维切片")
            nazimuth, nradius = values.shape
            resolution = self._radius_resolution(options, metadata)
            dataset.createDimension("azimuth", nazimuth)
            dataset.createDimension("radius", nradius)
            azimuth = dataset.createVariable("azimuth_deg", "f4", ("azimuth",))
            radius = dataset.createVariable("radius_km", "f4", ("radius",))
            azimuth[:] = np.arange(nazimuth, dtype=np.float32)
            radius[:] = np.arange(nradius, dtype=np.float32) * np.float32(resolution)
            azimuth.setncatts({"units": "degree", "axis": "A"})
            radius.setncatts({"units": "km", "axis": "R"})
            return (
                "record",
                *height_prefix,
                "azimuth",
                "radius",
            ), completed_dimensions
        if layout in {"scalar", "height_scalar"}:
            if values.size != 1:
                raise ValueError(f"{layout} 数据必须只有一个值")
            return ("record", *height_prefix), completed_dimensions
        if layout in {"vector", "height_vector"}:
            flat = values.reshape(-1)
            dataset.createDimension("component", flat.size)
            names = metadata.get("columns") or options.get("components") or [
                str(index) for index in range(flat.size)
            ]
            if len(names) != flat.size:
                raise ValueError("component 名称数量与数据不一致")
            component = dataset.createVariable("component_name", str, ("component",))
            component[:] = np.asarray([str(value) for value in names], dtype=object)
            return ("record", *height_prefix, "component"), completed_dimensions
        raise ValueError(f"不支持的 storage_layout：{layout}")

    @staticmethod
    def _chunks(layout: str, shape: tuple[int, ...]) -> tuple[int, ...]:
        if layout == "cartesian_3d":
            return 1, 1, min(shape[0], 256), min(shape[1], 256)
        if layout == "cartesian_2d":
            return 1, min(shape[0], 256), min(shape[1], 256)
        if layout == "polar_3d":
            return 1, 1, min(shape[0], 90), min(shape[1], 256)
        if layout == "polar_2d":
            return 1, min(shape[0], 90), min(shape[1], 256)
        if layout == "height_vector":
            return 1, 1, max(1, int(np.prod(shape)))
        if layout == "height_scalar":
            return 1, 1
        if layout == "vector":
            return 1, max(1, int(np.prod(shape)))
        return (256,)

    @staticmethod
    def _coordinates(layout: str) -> str:
        values = ["time_minutes"]
        if layout in HEIGHT_LAYOUTS:
            values.append("height_m")
        if layout.startswith("cartesian"):
            values.extend(["y_index", "x_index"])
        elif layout.startswith("polar"):
            values.extend(["azimuth_deg", "radius_km"])
        elif layout in {"vector", "height_vector"}:
            values.append("component_name")
        return " ".join(values)

    @staticmethod
    def _layout(spec: Any, values: np.ndarray, task: Task) -> str:
        layout = getattr(spec, "storage_layout", None)
        if layout is None and spec is not None:
            layout = (getattr(spec, "options", {}) or {}).get("storage_layout")
        if layout not in SPATIAL_LAYOUTS:
            raise ValueError(f"变量 {task.variable} 缺少有效 storage_layout")
        return str(layout)

    def _validate_identity(self, dataset: Dataset, task: Task, layout: str) -> None:
        expected = {
            "schema_version": SCHEMA_VERSION,
            "experiment": task.experiment,
            "domain": int(task.domain),
            "variable": task.variable,
            "layout": layout,
            "time_origin": self.time_origin,
        }
        for name, value in expected.items():
            if getattr(dataset, name, None) != value:
                raise ValueError(
                    f"NetCDF schema 不匹配：{name}={getattr(dataset, name, None)!r}，期望 {value!r}"
                )

    def _validate_coordinates(
        self,
        dataset: Dataset,
        values: np.ndarray,
        layout: str,
        options: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        self._unique_coordinate(dataset, "time_minutes")
        if "height_m" in dataset.variables:
            self._unique_coordinate(dataset, "height_m")
        expected_shape = None
        if layout in {"cartesian_3d", "cartesian_2d"}:
            expected_shape = (
                len(dataset.dimensions["y"]),
                len(dataset.dimensions["x"]),
            )
        elif layout in {"polar_3d", "polar_2d"}:
            expected_shape = (
                len(dataset.dimensions["azimuth"]),
                len(dataset.dimensions["radius"]),
            )
        if expected_shape is not None and values.shape != expected_shape:
            raise ValueError(
                f"{layout} shape 与既有文件不一致：{values.shape} != {expected_shape}"
            )
        if layout.startswith("polar"):
            resolution = self._radius_resolution(options, metadata)
            expected = np.arange(values.shape[1], dtype=np.float32) * np.float32(
                resolution
            )
            if not np.array_equal(np.asarray(dataset["radius_km"][:]), expected):
                raise ValueError("radius_km 与既有文件不一致")

    @staticmethod
    def _radius_resolution(options: dict[str, Any], metadata: dict[str, Any]) -> float:
        value = metadata.get(
            "radius_resolution_km",
            options.get(
                "radius_resolution_km", options.get("polar_radius_resolution_km")
            ),
        )
        if value is None or not np.isfinite(float(value)) or float(value) <= 0:
            raise ValueError("极坐标变量必须提供正的 radius_resolution_km")
        return float(value)

    def _selection(
        self, dataset: Dataset, task: Task, *, require_coordinates: bool
    ) -> tuple[int, ...] | None:
        self._validate_identity(dataset, task, dataset.getncattr("layout"))
        record = self._coordinate_index(
            dataset, "time_minutes", task.time_minutes, append=False
        )
        if record is None:
            if require_coordinates:
                raise KeyError(f"time_minutes={task.time_minutes} 不存在")
            return None
        if dataset.getncattr("layout") in HEIGHT_LAYOUTS:
            if task.height_m == "lowest":
                group = self._slice_group(dataset, task)
                if group is None or not {"data", "completed", "metadata_json"}.issubset(group.variables):
                    if require_coordinates:
                        raise KeyError("最低模式层切片不存在")
                    return None
                return (record,)
            if not isinstance(task.height_m, int):
                if require_coordinates:
                    raise KeyError("高度变量必须指定整数 height_m")
                return None
            height = self._coordinate_index(
                dataset, "height_m", task.height_m, append=False
            )
            if height is None:
                if require_coordinates:
                    raise KeyError(f"height_m={task.height_m} 不存在")
                return None
            return record, height
        return (record,)

    def _coordinate_index(
        self,
        dataset: Dataset,
        name: str,
        value: int,
        *,
        append: bool,
    ) -> int | None:
        coordinate_cache = getattr(self, "_coordinate_cache", None)
        values = (
            coordinate_cache.coordinate_values(dataset, name)
            if coordinate_cache is not None and not append
            else self._unique_coordinate(dataset, name)
        )
        matches = [index for index, item in enumerate(values) if item == int(value)]
        if matches:
            return matches[0]
        if not append:
            return None
        index = len(values)
        dataset[name][index] = int(value)
        # 不在这里单独写盘：save_many 写完数据后统一 sync（新坐标处的完成标记仍是 0，中途断电也只是“未完成”）。
        return index

    @staticmethod
    def _unique_coordinate(dataset: Dataset, name: str) -> list[int]:
        values = [int(value) for value in np.asarray(dataset[name][:]).reshape(-1)]
        if len(values) != len(set(values)):
            raise ValueError(f"检测到重复的 {name} 坐标")
        return values

    @staticmethod
    def _write_data(
        dataset: Dataset,
        layout: str,
        selection: tuple[int, ...],
        values: np.ndarray,
    ) -> None:
        if layout in {"scalar", "height_scalar"}:
            dataset["data"][selection] = np.float32(values.reshape(-1)[0])
        elif layout in {"vector", "height_vector"}:
            dataset["data"][(*selection, slice(None))] = values.reshape(-1)
        else:
            dataset["data"][(*selection, slice(None), slice(None))] = values

    @staticmethod
    def _read_data(
        dataset: Dataset, layout: str, selection: tuple[int, ...]
    ) -> np.ndarray:
        if layout in {"scalar", "height_scalar"}:
            return np.asarray(dataset["data"][selection])
        if layout in {"vector", "height_vector"}:
            return np.asarray(dataset["data"][(*selection, slice(None))])
        return np.asarray(
            dataset["data"][(*selection, slice(None), slice(None))]
        )

    @staticmethod
    def _canonicalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): NetCDFStorage._canonicalize(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [NetCDFStorage._canonicalize(item) for item in value]
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, np.generic):
            return value.item()
        return value


# Existing contexts annotate the storage boundary with this historical name.
Storage = NetCDFStorage
