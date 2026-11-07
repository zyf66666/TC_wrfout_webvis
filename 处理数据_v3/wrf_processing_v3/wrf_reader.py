"""集中读取直接变量：文件内多个变量共享句柄，一个三维场服务多个高度。"""

from collections import defaultdict
from importlib import import_module

import numpy as np


def prepare_wrf_import():
    """兼容当前环境的旧 wrf-python；普通计划与缓存复用不导入 WRF。"""
    import os

    os.environ.setdefault("OMP_NUM_THREADS", "1")
    # 新 NumPy 去掉了旧别名，而部分 wrf-python 构建仍使用这些别名。
    for name, value in [("float", float), ("int", int), ("bool", bool)]:
        if name not in np.__dict__:
            setattr(np, name, value)


# 读取器一次写入的切片数上限（同一字段的多个高度）；16 个 805×805 float32 约 41 MB。
SAVE_BATCH = 16


class NetCDFBackend:
    def open(self, path):
        from netCDF4 import Dataset

        return Dataset(path, "r")

    def getvar(self, dataset, name, timeidx, **kwargs):
        prepare_wrf_import()
        from wrf import getvar, to_np

        return np.ma.asarray(
            to_np(getvar(dataset, name, timeidx=timeidx, meta=False, **kwargs))
        ).filled(np.nan)

    def interpolate(self, field, z, height):
        from wrf import interplevel, to_np

        values = np.ma.asarray(to_np(interplevel(field, z, float(height)))).filled(np.nan)
        return fill_exact_levels(values, field, z, height)

    # 2026-11-03 加速：一次插值多个高度。interplevel 每次调用都要把三维场和 z 转成 Fortran 连续的
    # 双精度副本（各约 280 MB），逐层调用 46 次就复制 46 遍；一次给多个高度时只复制一次，逐层算法相同。
    # 实测 315 陆地 4 h 的 rh 46 层：逐层 4.15 s，每 16 层一次 1.99 s，结果逐值相同（含缺测位置）。
    def interpolate_many(self, field, z, heights):
        from wrf import interplevel, to_np

        heights = [float(value) for value in heights]
        values = np.ma.asarray(to_np(interplevel(field, z, np.asarray(heights, dtype=float)))).filled(np.nan)
        if values.ndim == 2:
            values = values[np.newaxis]
        return [fill_exact_levels(level, field, z, height) for level, height in zip(values, heights)]

    # 2026-11-02 加速：只需要最低模式层的 ua/va 时，只读 U/V 的最低层再去交错，
    # 不必先把整个三维风场去交错。wrf-python 的 ua/va 就是 destagger(U, -1) / destagger(V, -2)，
    # destagger 逐层做 0.5*(a[0:n-1] + a[1:n])，所以最低层结果与 getvar(...)[0] 逐值相同。
    # 返回形状 (1, ny, nx)，读取器照旧取 [0]；不适用（别的变量、要换单位）时返回 None，由调用方走 getvar。
    def getvar_lowest(self, dataset, name, timeidx, **kwargs):
        if name not in ("ua", "va") or set(kwargs) - {"units"} or kwargs.get("units", "m s-1") != "m s-1":
            return None
        raw = dataset.variables["U" if name == "ua" else "V"][timeidx, 0]
        staggered = np.ma.asarray(raw).filled(np.nan)
        return destagger_level(staggered, -1 if name == "ua" else -2)[np.newaxis]


def fill_exact_levels(values, field, coordinate, level):
    """修正 wrf-python interplevel 的等值缺测（2026-11-03）。

    某格点某一模式层的坐标（高度 z 或气压）恰好等于目标值时，interplevel 在该格点返回缺测
    （z 由 PH/PHB 算出，2500 m、5500 m 这类整数高度时有发生；实测每个高度 0–12 个点）。
    这些点的正确值就是该层的原值，这里直接取回；其余缺测（地形以下、超出柱顶）原样保留，
    仍交给 common.fill_nan 填补。原来这些零星点也走 fill_nan：只缺一个点也要对全网格做一次
    三角剖分（约 5–60 s），而且得到的是周围格点的插值而非该层原值。
    """
    values = np.asarray(values)
    missing = np.isnan(values)
    if not missing.any():
        return values
    data = np.ma.asarray(field).filled(np.nan)
    coordinate = np.asarray(coordinate)
    if data.ndim != 3 or coordinate.shape != data.shape or values.shape != data.shape[1:]:
        return values
    rows, cols = np.nonzero(missing)
    equal = coordinate[:, rows, cols] == float(level)
    hit = equal.any(axis=0)
    if not hit.any():
        return values
    levels = equal.argmax(axis=0)
    fixed = values.copy()
    fixed[rows[hit], cols[hit]] = data[levels[hit], rows[hit], cols[hit]]
    return fixed


def destagger_level(values, axis):
    """与 wrf.destagger 相同的算式（0.5 * (前 n-1 个 + 后 n-1 个)），只用于一层二维场。"""
    values = np.asarray(values)
    size = values.shape[axis]
    first = [slice(None)] * values.ndim
    second = [slice(None)] * values.ndim
    first[axis] = slice(0, size - 1, 1)
    second[axis] = slice(1, size, 1)
    return .5 * (values[tuple(first)] + values[tuple(second)])


class WrfReader:
    def __init__(self, index=None, backend=None):
        self.index = index
        self._injected_index = index is not None
        self._index_request = None
        self.backend = backend or NetCDFBackend()

    def _lowest_reader(self, request):
        """可用时返回 backend.getvar_lowest；子类只换了 getvar（如测试里的假后端）时不用，免得绕过它。"""
        if not request.options.get("fast_source_reads", True):
            return None
        kind = type(self.backend)
        method = getattr(kind, "getvar_lowest", None)
        if method is None:
            return None
        if method is NetCDFBackend.getvar_lowest and kind.getvar is not NetCDFBackend.getvar:
            return None
        return getattr(self.backend, "getvar_lowest")

    def prepare(self, tasks, request, registry, storage, progress=None):
        if not tasks:
            return []
        if not self._injected_index and self._index_request is not request:
            from .source_index import SourceIndex

            self.index = SourceIndex(request)
            self._index_request = request
        grouped = defaultdict(list)
        for task in tasks:
            path, timeidx = self.index.resolve(task.experiment, task.domain, task.time)
            grouped[path].append((timeidx, task))
        outputs = []
        for path, entries in grouped.items():
            if request.options.get("verbose", False):
                print(f"读取 {path}：{len(entries)} 个输出任务", flush=True)
            with self.backend.open(path) as dataset:
                by_time = defaultdict(list)
                for timeidx, task in entries:
                    by_time[timeidx].append(task)
                for timeidx, time_tasks in by_time.items():
                    # 一个物理字段可能对应多个高度或气压层；按真实读取名合并。
                    # 只保留当前字段和坐标，避免全部三维变量同时占据内存。
                    height_coordinate = None
                    pressure_coordinate = None
                    field_groups = defaultdict(list)
                    for task in time_tasks:
                        spec = registry.resolve(task.variable)
                        kwargs = spec.options.get("getvar_kwargs", {})
                        key = (
                            spec.kind,
                            (
                                spec.handler
                                if spec.kind == "native"
                                else spec.wrf_name or spec.name
                            ),
                            tuple(sorted(kwargs.items())),
                        )
                        field_groups[key].append(task)
                    # 2026-11-02 加速：同一时次既要 dbz 又要 mdbz 时，mdbz 直接取已算好的 dbz 的柱最大值。
                    # wrf-python 的 mdbz 就是 max(get_dbz(...), axis=-3)（相同默认参数），结果相同，省一次三维 dbz 诊断。
                    fast = request.options.get("fast_source_reads", True)
                    # 同理先读 pressure：后面的气压面风场（_ua_upper 等）直接用它做插值坐标，不再重算三维气压。
                    reuse_dbz = fast and ("getvar", "mdbz", ()) in field_groups and ("getvar", "dbz", ()) in field_groups
                    first = {("getvar", "pressure", ()): 0, ("getvar", "dbz", ()): 1} if fast else {}
                    ordered_groups = sorted(
                        field_groups.items(), key=lambda entry: first.get(entry[0], 2),
                    )
                    lowest_reader = self._lowest_reader(request)
                    dbz_field = None
                    # 2026-11-03 批量写：同一字段的多个高度写的是同一个 .nc，攒够一批再一次写入（见 storage.save_many）。
                    batch = []
                    save_many = getattr(storage, "save_many", None)

                    def flush():
                        if not batch:
                            return
                        if save_many is not None:
                            paths = save_many([(task, values, spec, metadata) for task, values, spec, metadata in batch])
                        else:
                            paths = [
                                storage.save(task, values, spec, metadata=metadata, dtype=request.dtype)
                                for task, values, spec, metadata in batch
                            ]
                        for (task, *_), path in zip(batch, paths):
                            outputs.append(path)
                            if progress is not None:
                                progress(f"源切片 {len(outputs)}/{len(tasks)}: {task.variable} {task.time_minutes}min {task.height_m}")
                        batch.clear()
                    for (
                        kind,
                        source_name,
                        kwargs,
                    ), field_tasks in ordered_groups:
                        metadata = {
                            "source_file": str(path),
                            "source_time_index": timeidx,
                            "options": dict(request.options),
                            "request_options": dict(request.options),
                            "dtype": request.dtype,
                            "source_root": (
                                str(request.source_root.resolve())
                                if hasattr(request, "source_root")
                                else ""
                            ),
                        }
                        if hasattr(dataset, "DX") and hasattr(dataset, "DY"):
                            metadata["grid_spacing"] = {
                                "dx_km": float(dataset.DX) / 1000.0,
                                "dy_km": float(dataset.DY) / 1000.0,
                            }
                        if path.is_file():
                            stat = path.stat()
                            metadata["source_fingerprint"] = {
                                "size": stat.st_size,
                                "mtime_ns": stat.st_mtime_ns,
                            }
                        if kind == "native":
                            module, function = source_name.split(":")
                            data, details = getattr(import_module(module), function)(
                                dataset,
                                source_file=path,
                                timeidx=timeidx,
                                options=request.options,
                            )
                            metadata.update(details)
                        else:
                            data = None
                            if reuse_dbz and source_name == "mdbz" and dbz_field is not None:
                                data = np.max(dbz_field, axis=-3)
                                metadata["fast_read"] = "mdbz_from_dbz"
                            elif lowest_reader is not None and all(
                                task.height == "lowest"
                                and "pressure_hpa" not in registry.resolve(task.variable).options
                                for task in field_tasks
                            ):
                                data = lowest_reader(dataset, source_name, timeidx, **dict(kwargs))
                                if data is not None:
                                    metadata["fast_read"] = "lowest_level_only"
                            if data is None:
                                data = self.backend.getvar(
                                    dataset, source_name, timeidx, **dict(kwargs)
                                )
                            if reuse_dbz and source_name == "dbz" and not kwargs:
                                dbz_field = data
                        data = np.ma.asarray(data, dtype=float).filled(np.nan)
                        if source_name == "pressure":
                            pressure_coordinate = data
                        # 同一批要插值到高度的任务，先一次算好（见 NetCDFBackend.interpolate_many）。
                        many = getattr(self.backend, "interpolate_many", None)
                        prepared_heights = {}

                        def prepare_heights(start):
                            nonlocal height_coordinate
                            prepared_heights.clear()
                            if many is None or data.ndim != 3:
                                return
                            wanted = []
                            for member in field_tasks[start:start + SAVE_BATCH]:
                                member_spec = registry.resolve(member.variable)
                                if (
                                    "pressure_hpa" not in member_spec.options
                                    and member_spec.dimensions == "height"
                                    and member.height != "lowest"
                                    and member.height not in wanted
                                ):
                                    wanted.append(member.height)
                            if len(wanted) < 2:
                                return
                            if height_coordinate is None:
                                height_coordinate = self.backend.getvar(dataset, "z", timeidx, units="m")
                            prepared_heights.update(zip(wanted, many(data, height_coordinate, wanted)))

                        for position, task in enumerate(field_tasks):
                            if position % SAVE_BATCH == 0:
                                prepare_heights(position)
                            spec = registry.resolve(task.variable)
                            interpolated = True
                            if "pressure_hpa" in spec.options:
                                if pressure_coordinate is None:
                                    pressure_coordinate = self.backend.getvar(
                                        dataset, "pressure", timeidx
                                    )
                                values = self.backend.interpolate(
                                    data,
                                    pressure_coordinate,
                                    spec.options["pressure_hpa"],
                                )
                            elif spec.dimensions == "height":
                                if data.ndim != 3:
                                    raise ValueError(
                                        f"{task.variable} 需要三维模式层场，实际为 {data.shape}"
                                    )
                                if task.height == "lowest":
                                    values = data[0]
                                    interpolated = False
                                else:
                                    if task.height in prepared_heights:
                                        values = prepared_heights[task.height]
                                    else:
                                        if height_coordinate is None:
                                            height_coordinate = self.backend.getvar(
                                                dataset, "z", timeidx, units="m"
                                            )
                                        values = self.backend.interpolate(
                                            data, height_coordinate, task.height
                                        )
                            else:
                                # CAPE 诊断的第一个产品是 CAPE，其余 CIN/LCL/LFC 不混入输出。
                                interpolated = False
                                values = data[0] if task.variable == "cape" else data
                                if values.ndim != 2:
                                    raise ValueError(
                                        f"{task.variable} 需要二维场，实际为 {values.shape}"
                                    )
                            # 沿用旧版：普通二维/高度场保存前填补 NaN，PVO 保留插值缺测。
                            # 气压面辅助场也保留缺测，供风切变环带按有效样本求均值。
                            values = np.asarray(values, dtype=request.dtype)
                            if (
                                task.variable != "pvo"
                                and "pressure_hpa" not in spec.options
                            ):
                                from .common import fill_nan

                                values = fill_nan(values)
                            # 记下本切片用的是修正过等值缺测的插值（fill_exact_levels），便于与 11-03 之前写的切片区分。
                            task_metadata = {**metadata, "interp_exact_level_fix": "v1"} if interpolated else metadata
                            batch.append((task, values, spec, task_metadata))
                            if len(batch) >= SAVE_BATCH:
                                flush()
                        flush()
        return outputs
