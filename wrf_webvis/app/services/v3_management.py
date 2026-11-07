"""Metadata-only discovery and request planning for the v3 WRF pipeline."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import OrderedDict, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from functools import wraps
from threading import RLock, Thread
from typing import Any

import numpy as np
from netCDF4 import Dataset

from 处理数据_v3.wrf_processing_v3.dependency_planner import DependencyPlanner
from 处理数据_v3.wrf_processing_v3.locking import file_lock
from 处理数据_v3.wrf_processing_v3.processing_manager import ProcessingManager
from 处理数据_v3.wrf_processing_v3.register import build_registry
from 处理数据_v3.wrf_processing_v3.request import Request, Task, storage_scheme_options, with_center_scheme
from 处理数据_v3.wrf_processing_v3.center_schemes import (
    BASE_CENTER_SCHEME, CENTERS_DIRNAME, load_center_schemes, member_key, processed_path, split_member,
)
from 处理数据_v3.wrf_processing_v3.source_index import SourceIndex
from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage, read_completed_flags
from 处理数据_v3.wrf_processing_v3.cache_compatibility import (
    OPTIONAL_SCIENCE_KEYS, SCIENCE_KEYS, _canonical, _science, check_cache_compatibility,
)


MAX_REQUEST_TIMES = 2000
# 2026-11-02：网页“其他可处理变量”默认不列出的变量（仍可在输入框里手写，也能勾选“显示全部”看到）：
# 以下划线开头的内部辅助场（如 _va_lower）、只是别名的兼容名称、网格坐标与海岸线点集。
PICKER_HIDDEN_ALIASES = frozenset({
    "center", "height_center", "pressure_center", "cloud_classification", "motion", "land_distance", "u", "v",
})
PICKER_HIDDEN_INTERNAL = frozenset({"XLAT", "XLONG", "XLAND", "land_boundary"})


def picker_hidden_variables(names) -> list[str]:
    return sorted(
        name for name in names
        if name.startswith("_") or name in PICKER_HIDDEN_ALIASES or name in PICKER_HIDDEN_INTERNAL
    )

EDITABLE_KEYS = (
    "radius_km_by_domain", "radius_resolution_km", "center_options_by_domain",
    "theta_environment_domain", "theta_environment_ring_km",
    "shear_source_domain", "shear_radius_inner_km", "shear_radius_outer_km",
    # 2026-11-04 非对称度参数（网页“通用处理选项”可改；偏离基准值时自动用独立参数配置，见 cache_compatibility）
    "asymmetry_radius_km", "dbz_asymmetry_height_m",
)

# 2026-11-04：HDF5 库（netCDF4 底层）不是线程安全的。网页进程里多个请求线程同时打开/读取 NetCDF 会让整个
# 网页进程段错误退出（11-04 13:55 实例：连点“已处理数据”的变量，7 个线程同时在 HDF5 里），正在跑的处理任务随之中断。
# 网页进程里所有会打开 NetCDF / wrfout 的入口都先拿这把锁，一次只让一个线程读；处理子进程是单线程，不受影响。
NETCDF_LOCK = RLock()


def netcdf_serialized(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        with NETCDF_LOCK:
            return function(*args, **kwargs)
    return wrapper


_SOURCE_CACHE: dict[tuple[str, str, int, tuple], tuple[dict, Any]] = {}
_SOURCE_CACHE_LOCK = RLock()
_CUSTOM_EXPERIMENTS_LOCK = RLock()
_SUMMARY_LOCK = RLock()
_SUMMARY_QUEUE: Queue = Queue()
_SUMMARY_PENDING: set[str] = set()
_SUMMARY_THREAD: Thread | None = None
_EXPERIMENT_NAME = re.compile(r"[A-Za-z0-9_]{1,64}")
_REFERENCE_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|(.+)\|\s*$")
_REFERENCE_CACHE: dict[str, tuple[float, dict[str, dict[str, str]]]] = {}
DIMENSION_LABELS = {
    "height": "三维：随高度变化（每个时次 × 每个高度一个切片）",
    "surface": "二维：地面/单层（每个时次一个切片）",
    "scalar": "标量：每个时次一个数",
    "height_scalar": "随高度的标量：每个时次 × 每个高度一个数",
    "vector": "向量：每个时次几个分量",
    "height_vector": "随高度的向量：每个时次 × 每个高度几个分量",
}
LAYOUT_LABELS = {
    "cartesian_3d": "格点场 · 随高度",
    "polar_3d": "极坐标 · 随高度",
    "cartesian_2d": "格点场 · 单层",
    "polar_2d": "极坐标 · 单层",
    "scalar": "标量时间序列",
    "height_scalar": "随高度的标量",
    "vector": "向量时间序列",
    "height_vector": "随高度的向量",
}


def default_custom_experiments_path() -> Path:
    """网页上添加的试验（名称 → wrfout 目录）保存在这里，不改 project_config.py。"""
    configured = os.environ.get("WRF_WEBVIS_CUSTOM_EXPERIMENTS", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / "storage_data" / "custom_experiments.json"


def default_summary_cache_path() -> Path:
    configured = os.environ.get("WRF_WEBVIS_INVENTORY_SUMMARY_CACHE", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / "storage_data" / "inventory_summary_cache.json"


def variables_reference_path() -> Path:
    return Path(__file__).resolve().parents[3] / "处理数据_v3" / "VARIABLES_REFERENCE.md"


def _reference_table(path: Path) -> dict[str, dict[str, str]]:
    """解析 VARIABLES_REFERENCE.md 的变量表（按文件修改时间缓存）。"""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _REFERENCE_CACHE.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    table: dict[str, dict[str, str]] = {}
    section = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = re.sub(r"^[0-9.\s]+", "", line[3:]).strip()
            continue
        match = _REFERENCE_ROW.match(line)
        if not match:
            continue
        cells = [cell.strip() for cell in match.group(2).split("|")]
        if match.group(1) in table or len(cells) < 4:
            continue
        table[match.group(1)] = {
            "units": cells[0], "dependencies_text": cells[1].replace("`", ""),
            "meaning": cells[2].replace("`", "").replace("**", ""),
            "method": cells[3].replace("`", "").replace("**", ""), "category": section,
        }
    _REFERENCE_CACHE[str(path)] = (mtime, table)
    return table


def _dependency_text(dependency: Any) -> str:
    text = dependency.name
    extras = []
    if dependency.height == "lowest":
        extras.append("最低模式层")
    elif dependency.height not in ("same", None):
        extras.append(f"{dependency.height} m")
    if dependency.domain:
        extras.append(f"d{int(dependency.domain):02d}")
    return f"{text}（{'，'.join(extras)}）" if extras else text


def _range_text(values: list[int], unit: str, scale: float = 1.0) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "max": None, "step": None}
    ordered = sorted(values)
    steps = {b - a for a, b in zip(ordered, ordered[1:])}
    return {
        "count": len(ordered), "min": ordered[0] / scale, "max": ordered[-1] / scale,
        "step": (steps.pop() / scale) if len(steps) == 1 else None, "unit": unit,
    }


_RECENT_PLANS: OrderedDict[str, dict[str, Any]] = OrderedDict()
_RECENT_PLANS_LOCK = RLock()


def _plan_key(output_root: Path, payload: dict[str, Any]) -> str:
    """同一数据集、除 plan_hash / label（只是任务标题）外完全相同的请求。"""
    return _canonical([str(output_root), {key: value for key, value in payload.items() if key not in ("plan_hash", "label")}])


def _plan_fingerprints(source_paths, planned_paths) -> dict[str, str] | None:
    """计划指纹：用到的 wrfout 与已存在的输出文件的大小和修改时间（与计划编号里的算法相同）。"""
    try:
        outputs = sorted(Path(path) for path in planned_paths if Path(path).is_file())
        return {
            "source": hashlib.sha256(_canonical([
                (str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in sorted(Path(item) for item in source_paths)
            ]).encode()).hexdigest(),
            "output": hashlib.sha256(_canonical([
                (str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in outputs
            ]).encode()).hexdigest(),
        }
    except OSError:
        return None


def _remember_plan(output_root: Path, payload: dict[str, Any], summary: dict[str, Any], source_paths, planned_paths) -> None:
    with _RECENT_PLANS_LOCK:
        _RECENT_PLANS[summary["plan_hash"]] = {
            "key": _plan_key(output_root, payload), "summary": summary,
            "sources": sorted(str(path) for path in source_paths), "outputs": sorted(str(path) for path in planned_paths),
        }
        _RECENT_PLANS.move_to_end(summary["plan_hash"])
        while len(_RECENT_PLANS) > 256:
            _RECENT_PLANS.popitem(last=False)


class CachedSourceIndex(SourceIndex):
    """Reuse Times scans while the wrfout file list and file stats are unchanged."""

    def _scan(self, experiment: str, domain: int):
        paths = self._paths(experiment, domain)
        signature = tuple((str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in paths)
        key = (str(self.source_root), experiment, int(domain), signature)
        with _SOURCE_CACHE_LOCK:
            cached = _SOURCE_CACHE.get(key)
        if cached is not None:
            mapping, origin = cached
            self._origins[(experiment, int(domain))] = origin
            return dict(mapping)
        mapping = super()._scan(experiment, domain)
        with _SOURCE_CACHE_LOCK:
            if len(_SOURCE_CACHE) >= 32:
                _SOURCE_CACHE.pop(next(iter(_SOURCE_CACHE)))
            _SOURCE_CACHE[key] = (dict(mapping), self._origins[(experiment, int(domain))])
        return mapping


class V3ManagementService:
    def __init__(
        self,
        output_root: str | Path | None = None,
        source_directories: dict[str, str | Path] | None = None,
        default_options: dict[str, Any] | None = None,
        custom_experiments_path: str | Path | None = None,
        summary_cache_path: str | Path | None = None,
    ) -> None:
        # 只有使用 project_config 默认试验表时才合并网页上添加的试验；
        # 测试和处理子进程显式传入 source_directories，不读取自定义文件。
        uses_project_sources = source_directories is None
        if output_root is None or source_directories is None or default_options is None:
            from 处理数据_v3 import project_config

            output_root = output_root or project_config.OUTPUT_ROOT
            source_directories = source_directories or project_config.SOURCE_DIRECTORIES
            default_options = default_options or project_config.OPTIONS
        if custom_experiments_path is None and uses_project_sources:
            custom_experiments_path = default_custom_experiments_path()
        self.custom_experiments_path = Path(custom_experiments_path).expanduser() if custom_experiments_path else None
        # 测试和处理子进程不传路径时同步统计；网页默认服务用后台统计 + 磁盘缓存。
        if summary_cache_path is None and uses_project_sources:
            summary_cache_path = default_summary_cache_path()
        self.summary_cache_path = Path(summary_cache_path).expanduser() if summary_cache_path else None
        self._persistent: dict[str, Any] | None = None
        self.output_root = Path(output_root).expanduser().resolve()
        self._base_sources = {
            name: Path(path).expanduser().resolve()
            for name, path in source_directories.items()
        }
        self.default_options = json.loads(_canonical(default_options))
        self._coverage_cache: OrderedDict[tuple, dict[str, Any]] = OrderedDict()
        self._coverage_cache_lock = RLock()
        self._summary_cache: OrderedDict[tuple, dict[str, Any]] = OrderedDict()

    # ---- 试验表：project_config 固定试验 + 网页添加的试验 ----
    def _read_custom(self) -> dict[str, str]:
        path = self.custom_experiments_path
        if path is None or not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        entries = value.get("experiments", {}) if isinstance(value, dict) else {}
        return {
            str(name): str(entry.get("path") if isinstance(entry, dict) else entry)
            for name, entry in entries.items()
            if _EXPERIMENT_NAME.fullmatch(str(name)) and name not in self._base_sources
        }

    def _write_custom(self, entries: dict[str, str]) -> None:
        path = self.custom_experiments_path
        if path is None:
            raise ValueError("当前服务没有配置自定义试验文件")
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"experiments": {name: {"path": entries[name]} for name in sorted(entries)}}
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    @property
    def source_directories(self) -> dict[str, Path]:
        merged = dict(self._base_sources)
        for name, value in self._read_custom().items():
            merged[name] = Path(value).expanduser()
        return merged

    @netcdf_serialized
    def experiment_details(self) -> list[dict[str, Any]]:
        custom = self._read_custom()
        rows = [{"name": name, "path": str(path), "custom": False} for name, path in self._base_sources.items()]
        rows += [{"name": name, "path": value, "custom": True} for name, value in custom.items()]
        for row in rows:
            row["available"] = Path(row["path"]).is_dir()
        return sorted(rows, key=lambda row: row["name"])

    @staticmethod
    def _wrfout_domains(directory: Path) -> list[int]:
        domains: set[int] = set()
        for pattern in ("wrfout_d*", "*/wrfout_d*"):
            for path in directory.glob(pattern):
                match = re.match(r"wrfout_d(\d{2})", path.name)
                if match:
                    domains.add(int(match.group(1)))
            if domains:
                break
        return sorted(domains)

    def add_experiment(self, name: str, path: str) -> dict[str, Any]:
        name = str(name or "").strip()
        if not _EXPERIMENT_NAME.fullmatch(name):
            raise ValueError("试验名称只能包含英文字母、数字和下划线（最多 64 个字符），例如 WRF_315_land_test")
        if name in self._base_sources:
            raise ValueError(f"{name} 是 project_config.py 里的固定试验，不能在网页上修改")
        directory = Path(str(path or "").strip()).expanduser()
        if not str(path or "").strip() or not directory.is_absolute():
            raise ValueError("请填写 wrfout 所在目录的绝对路径，例如 /Volumes/sea_2/WRF_315_new/run")
        if not directory.is_dir():
            raise FileNotFoundError(f"目录不存在：{directory}（外接盘是否已接上？）")
        domains = self._wrfout_domains(directory)
        if not domains:
            raise FileNotFoundError(f"{directory} 及其下一级目录里没有找到 wrfout_d* 文件")
        with _CUSTOM_EXPERIMENTS_LOCK:
            entries = self._read_custom()
            entries[name] = str(directory)
            self._write_custom(entries)
        return {"name": name, "path": str(directory), "custom": True, "available": True, "domains": domains}

    def remove_experiment(self, name: str) -> dict[str, Any]:
        """只从网页试验表移除登记，不删除 wrfout 或已处理数据。"""
        if name in self._base_sources:
            raise ValueError(f"{name} 是 project_config.py 里的固定试验，不能在网页上移除")
        with _CUSTOM_EXPERIMENTS_LOCK:
            entries = self._read_custom()
            if name not in entries:
                raise KeyError(f"没有名为 {name} 的自定义试验")
            removed = entries.pop(name)
            self._write_custom(entries)
        return {"name": name, "path": removed, "removed": True}

    # ---- 变量说明：VARIABLES_REFERENCE.md + 注册表 ----
    def variable_info(self, names: list[str] | None = None) -> dict[str, dict[str, Any]]:
        from 处理数据_v3.wrf_processing_v3._scientific_registry import build_registry as build_scientific_registry

        registry = build_scientific_registry(self.default_options)
        reference = _reference_table(variables_reference_path())
        result: dict[str, dict[str, Any]] = {}
        for name in names if names is not None else self.catalog_variables(registry):
            info: dict[str, Any] = {"name": name}
            try:
                spec = registry.resolve(name)
            except (KeyError, ValueError):
                spec = None
            if spec is not None:
                info.update({
                    "units": spec.units, "dimension": spec.dimensions,
                    "dimension_label": DIMENSION_LABELS.get(spec.dimensions, spec.dimensions),
                    "dependencies": [_dependency_text(dependency) for dependency in spec.dependencies],
                })
                if spec.kind == "getvar":
                    info["dependencies"] = [f"WRF 原始输出（wrf.getvar {spec.wrf_name or name}）"]
            entry = reference.get(name)
            if entry:
                info.update({key: entry[key] for key in ("meaning", "method", "category")})
                info.setdefault("units", entry["units"])
                if not info.get("dependencies"):
                    info["dependencies"] = [entry["dependencies_text"]]
            elif spec is not None and spec.options.get("source"):
                source = spec.options["source"]
                base = reference.get(source, {})
                center = spec.options.get("center")
                if name.endswith("_asy_polar") or spec.options.get("mode") == "asymmetry":
                    info["meaning"] = f"{source} 的方位非对称分量（每个半径减去方位平均）"
                elif re.search(r"_asy\d+w_polar$", name):
                    info["meaning"] = f"{source} 的第 {spec.options.get('wavenumber')} 波分量"
                elif name.endswith("_polar"):
                    where = "逐高度 layer_center" if center == "layer_center" else "slp_center（海平面气压中心）"
                    info["meaning"] = f"以 {where} 为原点的 {source} 极坐标场（方位角 × 半径）" + (f"：{base['meaning']}" if base.get("meaning") else "")
                else:
                    info["meaning"] = f"{source} 的兼容名称" + (f"：{base['meaning']}" if base.get("meaning") else "")
                info["category"] = "由命名规则自动生成"
            if name.endswith("_polar"):
                info["dimension_label"] = info.get("dimension_label", "") + "；保存为极坐标（方位角 × 半径）"
            result[name] = info
        return result

    def catalog_variables(self, registry=None) -> list[str]:
        if registry is None:
            from 处理数据_v3.wrf_processing_v3._scientific_registry import build_registry as build_scientific_registry

            registry = build_scientific_registry(self.default_options)
        from 处理数据_v3.wrf_processing_v3._scientific_registry import LAYER_CENTER_WINDS

        variables = set(registry.specs)
        for name, spec in registry.specs.items():
            if name.endswith("_polar") or spec.dimensions not in {"height", "surface"}:
                continue
            candidates = [f"{name}_polar"]
            # 已按各层中心分解的（vr1、vr_sr1、inflow_angle_sr1 等）其 X_polar 已是各层中心版本，不再列 X1_polar（11-03）。
            if spec.dimensions == "height" and name not in LAYER_CENTER_WINDS:
                candidates.append(f"{name}1_polar")
            for candidate in candidates:
                try:
                    registry.resolve(candidate)
                except (KeyError, ValueError):
                    continue
                variables.add(candidate)
        return sorted(variables)

    # ---- 已处理文件的维度摘要 ----
    # 坐标（时间、高度轴、空间维度）只读一维小变量，0.1 s 内返回；
    # completed 标记每个格子一个 HDF5 块，大文件要读十几秒，所以放到后台线程统计，
    # 结果按 文件路径 + 大小 + 修改时间 存进 storage_data/inventory_summary_cache.json，文件不变就不再重读。
    @netcdf_serialized
    def _quick_summary(self, path: Path) -> dict[str, Any]:
        with file_lock(path, exclusive=False, timeout_seconds=2):
            with Dataset(path) as dataset:
                dataset.set_auto_mask(False)
                data = dataset["data"]
                dims = [[name, int(size)] for name, size in zip(data.dimensions, data.shape)]
                times = [int(value) for value in dataset["time_minutes"][:]]
                heights = [int(value) for value in dataset["height_m"][:]] if "height_m" in dataset.variables else []
                spatial: dict[str, Any] = {}
                if "radius_km" in dataset.variables:
                    radius = np.asarray(dataset["radius_km"][:], dtype=float)
                    spatial = {"azimuth": int(dataset.dimensions["azimuth"].size), "radius": int(radius.size),
                               "radius_max_km": float(radius.max()) if radius.size else None,
                               "radius_step_km": float(radius[1] - radius[0]) if radius.size > 1 else None}
                elif "y" in dataset.dimensions and "x" in dataset.dimensions:
                    spatial = {"y": int(dataset.dimensions["y"].size), "x": int(dataset.dimensions["x"].size)}
                elif "component" in dataset.dimensions:
                    spatial = {"component": int(dataset.dimensions["component"].size)}
                layout = str(getattr(dataset, "layout", ""))
                return {
                    "layout": layout, "layout_label": LAYOUT_LABELS.get(layout, layout or "未知"),
                    "units": str(getattr(dataset, "units", "")), "dims": dims, "spatial": spatial,
                    "has_lowest_level": "lowest" in getattr(dataset, "groups", {}),
                    "file_times": sorted(times), "file_heights": sorted(heights),
                }

    @staticmethod
    @netcdf_serialized
    def _completed_summary(path: Path) -> dict[str, Any]:
        with file_lock(path, exclusive=False, timeout_seconds=30):
            with Dataset(path) as dataset:
                dataset.set_auto_mask(False)
                times = [int(value) for value in dataset["time_minutes"][:]]
                heights = [int(value) for value in dataset["height_m"][:]] if "height_m" in dataset.variables else []
                flags = np.asarray(read_completed_flags(dataset))
        done = flags == 1
        if flags.ndim == 2:
            done_times = sorted(times[i] for i in np.flatnonzero(done.any(axis=1)))
            done_heights = sorted(heights[j] for j in np.flatnonzero(done.any(axis=0)))
        else:
            done_times, done_heights = sorted(times[i] for i in np.flatnonzero(done)), []
        return {"done_times": done_times, "done_heights": done_heights,
                "completed_slices": int(done.sum()), "total_slots": int(done.size)}

    def _persistent_summaries(self) -> dict[str, Any]:
        if self.summary_cache_path is None:
            return {}
        with _SUMMARY_LOCK:
            if self._persistent is None:
                try:
                    self._persistent = json.loads(self.summary_cache_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    self._persistent = {}
            return self._persistent

    def _store_completed(self, path: Path, signature: list[int], value: dict[str, Any]) -> None:
        cache = self._persistent_summaries()
        with _SUMMARY_LOCK:
            cache[str(path)] = {"signature": signature, "completed": value}
            target = self.summary_cache_path
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            temporary.replace(target)

    @staticmethod
    def _summary_worker() -> None:
        while True:
            self, path, signature = _SUMMARY_QUEUE.get()
            try:
                stat = path.stat()
                if [stat.st_size, stat.st_mtime_ns] == signature:
                    try:
                        value = self._completed_summary(path)
                    except Exception as exc:  # noqa: BLE001
                        value = {"error": f"统计完成标记失败：{exc}"}
                    self._store_completed(path, signature, value)
            except OSError:
                pass
            finally:
                with _SUMMARY_LOCK:
                    _SUMMARY_PENDING.discard(str(path))
                _SUMMARY_QUEUE.task_done()

    def _enqueue_completed(self, path: Path, signature: list[int]) -> None:
        global _SUMMARY_THREAD
        with _SUMMARY_LOCK:
            if str(path) in _SUMMARY_PENDING:
                return
            _SUMMARY_PENDING.add(str(path))
            if _SUMMARY_THREAD is None or not _SUMMARY_THREAD.is_alive():
                _SUMMARY_THREAD = Thread(target=V3ManagementService._summary_worker, name="inventory-summary", daemon=True)
                _SUMMARY_THREAD.start()
        _SUMMARY_QUEUE.put((self, path, signature))

    def file_summary(self, profile_id: str, experiment: str, domain: int, variable: str) -> dict[str, Any]:
        path = self._data_path(profile_id, experiment, domain, variable)
        base = {"profile_id": profile_id, "experiment": experiment, "domain": int(domain), "variable": variable, "path": str(path)}
        if not path.is_file():
            return {**base, "error": "文件不存在"}
        try:
            stat = path.stat()
            signature = [stat.st_size, stat.st_mtime_ns]
            key = (str(path), stat.st_ino, *signature)
            with self._coverage_cache_lock:
                quick = self._summary_cache.get(key)
            if quick is None:
                quick = self._quick_summary(path)
                with self._coverage_cache_lock:
                    self._summary_cache[key] = quick
                    if len(self._summary_cache) > 2048:
                        self._summary_cache.popitem(last=False)
        except Exception as exc:  # noqa: BLE001 - 单个文件出错不影响整页清单
            return {**base, "error": f"读取失败：{exc}"}
        quick = deepcopy(quick)
        file_times, file_heights = quick.pop("file_times"), quick.pop("file_heights")
        summary = {**base, **quick, "file_time_count": len(file_times), "file_height_count": len(file_heights)}
        completed = None
        if self.summary_cache_path is None:
            try:
                completed = self._completed_summary(path)
            except Exception as exc:  # noqa: BLE001
                completed = {"error": f"统计完成标记失败：{exc}"}
        else:
            entry = self._persistent_summaries().get(str(path))
            if entry and entry.get("signature") == signature:
                completed = entry["completed"]
            else:
                self._enqueue_completed(path, signature)
        if completed is None or "error" in completed:
            # 统计还没完成：时间先用文件里已有的记录（有记录的时次至少算过一个切片），高度待统计。
            summary.update({
                "completed_pending": completed is None, "completed_error": (completed or {}).get("error"),
                "time_hours": _range_text(file_times, "h", 60.0), "heights_m": None,
                "completed_slices": None, "total_slots": None, "height_values": None,
            })
        else:
            summary.update({
                "completed_pending": False,
                "time_hours": _range_text(completed["done_times"], "h", 60.0),
                "heights_m": _range_text(completed["done_heights"], "m"),
                "height_values": completed["done_heights"],
                "completed_slices": completed["completed_slices"], "total_slots": completed["total_slots"],
            })
        return summary

    def inventory(self) -> dict[str, Any]:
        """List output files using directory entries and stat data only."""
        roots = [("default", self.output_root)]
        profiles_dir = self.output_root / "profiles"
        if profiles_dir.is_dir():
            for directory in sorted(profiles_dir.iterdir()):
                if not directory.is_dir() or directory.is_symlink():
                    continue
                try:
                    self._manifest(directory.name)
                except (OSError, ValueError, KeyError):
                    continue
                roots.append((directory.name, directory))
        items = []
        # 2026-11-02 中心方案：<根>/centers/<方案>/<试验>/... 里的文件以“试验@方案”列出。
        scheme_roots = []
        for profile_id, root in roots:
            centers = root / CENTERS_DIRNAME
            if centers.is_dir() and not centers.is_symlink():
                for directory in sorted(centers.iterdir()):
                    if directory.is_dir() and not directory.is_symlink() and re.fullmatch(r"[a-z][a-z0-9_]{1,31}", directory.name):
                        scheme_roots.append((profile_id, directory, directory.name))
        for profile_id, root, scheme in [(pid, r, BASE_CENTER_SCHEME) for pid, r in roots] + scheme_roots:
            if not root.is_dir():
                continue
            for experiment_dir in root.iterdir():
                if experiment_dir.name in ("profiles", CENTERS_DIRNAME):
                    continue
                if not experiment_dir.is_dir() or experiment_dir.is_symlink() or not re.fullmatch(r"[A-Za-z0-9_]+", experiment_dir.name):
                    continue
                for domain_dir in experiment_dir.iterdir():
                    if not domain_dir.is_dir() or domain_dir.is_symlink() or not re.fullmatch(r"d\d{2}", domain_dir.name):
                        continue
                    domain = int(domain_dir.name[1:])
                    if not 1 <= domain <= 99:
                        continue
                    for path in domain_dir.iterdir():
                        if path.suffix != ".nc" or path.name.startswith("._") or path.is_symlink() or not re.fullmatch(r"[A-Za-z0-9_]+", path.stem):
                            continue
                        try:
                            if not path.is_file():
                                continue
                            stat = path.stat()
                        except OSError:
                            continue
                        items.append({
                            "profile_id": profile_id, "experiment": member_key(experiment_dir.name, scheme),
                            "center_scheme": scheme,
                            "domain": domain, "variable": path.stem, "path": str(path),
                            "size_bytes": stat.st_size,
                            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                        })
        items.sort(key=lambda row: (row["profile_id"] != "default", row["profile_id"], row["experiment"], row["domain"], row["variable"]))
        return {"items": items}

    def catalog(self) -> dict[str, Any]:
        from 处理数据_v3.wrf_processing_v3._scientific_registry import build_registry as build_scientific_registry

        registry = build_scientific_registry(self.default_options)
        variables = self.catalog_variables(registry)
        profiles = [{"id": "default", "root": str(self.output_root), "options": _science(self.default_options),
                     "label": "默认配置"}]
        directory = self.output_root / "profiles"
        if directory.is_dir():
            for manifest in sorted(directory.glob("*/profile.json")):
                try:
                    value = self._manifest(manifest.parent.name)
                    profiles.append({"id": value["id"], "root": str(manifest.parent), "options": value["options"],
                                     "label": self.profile_label(value["options"])})
                except (OSError, ValueError, KeyError):
                    continue
        return {
            "experiments": sorted(sources := self.source_directories),
            "source_directories": {key: str(value) for key, value in sources.items()},
            "experiment_details": self.experiment_details(),
            "variables": variables,
            "hidden_variables": picker_hidden_variables(variables),
            "variable_info": self.variable_info(variables),
            "profiles": profiles,
            "center_schemes": self.center_schemes(),
            **self.processing_presets(registry),
            "experiment_end_hours": self.experiment_end_hours(sources),
            "default_options": {key: self.default_options.get(key) for key in EDITABLE_KEYS if key in self.default_options},
        }

    # 功能说明：参数配置的可读名字（2026-11-02）：列出与默认配置不同的科学参数，例如“d03 极坐标半径 120 km，径向间隔 2 km”。
    # 编号 p-xxxx 是这组参数的哈希指纹，目录名不变；名字只用于网页显示。
    def profile_label(self, options: dict[str, Any]) -> str:
        default, current = _science(self.default_options), _science(options)

        def per_domain(value: Any) -> dict[str, Any]:
            return {str(key): entry for key, entry in (value or {}).items()} if isinstance(value, dict) else {}

        def km(value: Any) -> str:
            return f"{float(value):g} km"

        parts: list[str] = []
        for key in (*SCIENCE_KEYS, *OPTIONAL_SCIENCE_KEYS):
            old, new = default.get(key), current.get(key)
            if _canonical(old) == _canonical(new):
                continue
            if key == "radius_km_by_domain":
                before, after = per_domain(old), per_domain(new)
                for domain in sorted(set(before) | set(after)):
                    if _canonical(before.get(domain)) != _canonical(after.get(domain)) and after.get(domain) is not None:
                        parts.append(f"d{int(domain):02d} 极坐标半径 {km(after[domain])}")
            elif key == "center_options_by_domain":
                before, after = per_domain(old), per_domain(new)
                for domain in sorted(set(before) | set(after)):
                    entry_old, entry_new = before.get(domain) or {}, after.get(domain) or {}
                    for name in sorted(set(entry_old) | set(entry_new)):
                        if _canonical(entry_old.get(name)) == _canonical(entry_new.get(name)) or name not in entry_new:
                            continue
                        label = {"search_radius_km": "中心搜索半径", "algorithm": "中心算法", "layer_mode": "层中心模式"}.get(name, name)
                        value = km(entry_new[name]) if name.endswith("_km") else entry_new[name]
                        parts.append(f"d{int(domain):02d} {label} {value}")
            elif key == "radius_km":
                parts.append(f"极坐标半径 {km(new)}")
            elif key == "radius_resolution_km":
                parts.append(f"径向间隔 {km(new)}")
            elif key == "theta_environment_domain":
                parts.append(f"环境参考 d{int(new):02d}")
            elif key == "theta_environment_ring_km" and new:
                parts.append(f"环境环 {float(new[0]):g}–{float(new[1]):g} km")
            elif key == "shear_source_domain":
                parts.append(f"风切变来源 d{int(new):02d}")
            elif key in ("shear_radius_inner_km", "shear_radius_outer_km"):
                parts.append(f"风切变{'内' if 'inner' in key else '外'}半径 {km(new)}")
            elif key in ("shear_upper_hpa", "shear_lower_hpa"):
                parts.append(f"风切变{'上' if 'upper' in key else '下'}层 {float(new):g} hPa")
            elif key == "asymmetry_radius_km":
                parts.append(f"非对称度圆盘半径 {km(new if new is not None else OPTIONAL_SCIENCE_KEYS[key])}")
            elif key == "dbz_asymmetry_height_m":
                parts.append(f"dbz 非对称度高度 {float(new if new is not None else OPTIONAL_SCIENCE_KEYS[key]):g} m")
            else:
                parts.append(f"{key}={new}")
        return "，".join(parts) or "与默认配置相同"

    # 功能说明：各试验的模拟时长（小时，project_config.EXPERIMENT_END_HOURS）。处理套餐的结束时间留空时按它取，
    # 而不是按 wrfout 最后时次（2026-11-03：315 的 wrfout 输出到 120 h，但只用 0–102 h，之后中心靠近 d03 边界）。
    # 网页上添加的试验不在表里，留空时由前端按 120 h + “自动截断”处理。
    @staticmethod
    def experiment_end_hours(experiments) -> dict[str, float]:
        try:
            from 处理数据_v3 import project_config

            table = dict(getattr(project_config, "EXPERIMENT_END_HOURS", {}) or {})
        except Exception:  # noqa: BLE001
            return {}
        return {name: float(table[name]) for name in experiments if name in table}

    # 功能说明：处理套餐（2026-11-02，处理数据_v3/processing_presets.json）：已校验、高度已展开。
    # 套餐文件写错时不影响整页目录，只在网页套餐区显示错误。
    @staticmethod
    def processing_presets(registry=None) -> dict[str, Any]:
        from 处理数据_v3 import project_config
        from 处理数据_v3.wrf_processing_v3.processing_presets import load_processing_presets

        tokens = {"standard": tuple(project_config.HEIGHTS_M), "warm_core": tuple(project_config.WARM_CORE_HEIGHTS_M)}
        try:
            if registry is None:
                registry = build_registry(project_config.OPTIONS)
            presets = load_processing_presets(height_tokens=tokens, registry=registry)
        except (OSError, ValueError, KeyError) as exc:
            return {"processing_presets": [], "processing_presets_error": f"处理套餐文件有误：{exc}"}
        return {"processing_presets": list(presets.values()), "processing_presets_error": None}

    @staticmethod
    def center_schemes() -> list[dict[str, Any]]:
        """中心方案目录（处理数据_v3/center_schemes.json），base 在前。"""
        schemes = load_center_schemes()
        ordered = [BASE_CENTER_SCHEME] + sorted(key for key in schemes if key != BASE_CENTER_SCHEME)
        return [
            {
                "id": key, "label": schemes[key]["label"], "description": schemes[key]["description"],
                "center_options_by_domain": {str(domain): entry for domain, entry in schemes[key]["center_options_by_domain"].items()},
            }
            for key in ordered
        ]

    def _center_dependent(self, profile_id: str):
        cache = self.__dict__.setdefault("_center_dependent_cache", {})
        if profile_id not in cache:
            cache[profile_id] = build_registry(self.options_for(profile_id)).center_dependent
        return cache[profile_id]

    def options_for(self, profile_id: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        overrides = overrides or {}
        unknown = set(overrides) - set(EDITABLE_KEYS)
        if unknown:
            raise ValueError(f"不支持的处理参数：{', '.join(sorted(unknown))}")
        if profile_id == "default":
            options = json.loads(_canonical(self.default_options))
        else:
            manifest = self._manifest(profile_id)
            options = json.loads(_canonical(manifest["options"]))
            if overrides and _canonical(self._merged(options, overrides)) != _canonical(options):
                raise ValueError("配置不可修改；请从默认配置新建参数配置")
        return self._merged(options, overrides)

    @staticmethod
    def _merged(options: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
        merged = json.loads(_canonical(options))
        for key, value in overrides.items():
            if key in {"radius_km_by_domain", "center_options_by_domain"}:
                base = dict(merged.get(key) or {})
                for domain, entry in value.items():
                    if key == "center_options_by_domain":
                        base[str(domain)] = {**(base.get(str(domain)) or {}), **entry}
                    else:
                        base[str(domain)] = entry
                merged[key] = base
            else:
                merged[key] = value
        V3ManagementService._validate_options(merged)
        return merged

    @staticmethod
    def _validate_options(options: dict[str, Any]) -> None:
        positive = ("radius_resolution_km", "shear_radius_inner_km", "shear_radius_outer_km", "asymmetry_radius_km", "dbz_asymmetry_height_m")
        for key in positive:
            if key in options and (not math.isfinite(float(options[key])) or float(options[key]) <= 0):
                raise ValueError(f"{key} 必须为正数")
        if "dbz_asymmetry_height_m" in options and not float(options["dbz_asymmetry_height_m"]).is_integer():
            raise ValueError("dbz 非对称度高度必须是整数米")
        inner = float(options.get("shear_radius_inner_km", 150))
        outer = float(options.get("shear_radius_outer_km", 350))
        if inner >= outer:
            raise ValueError("风切变内半径必须小于外半径")
        ring = options.get("theta_environment_ring_km", [400, 500])
        if len(ring) != 2 or not 0 <= float(ring[0]) < float(ring[1]):
            raise ValueError("环境环带必须为递增的两个非负半径")
        for value in (options.get("radius_km_by_domain") or {}).values():
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError("极坐标最大半径必须为正数")
        for entry in (options.get("center_options_by_domain") or {}).values():
            if "search_radius_km" in entry and float(entry["search_radius_km"]) <= 0:
                raise ValueError("中心搜索半径必须为正数")

    def profile_for(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        options = self.options_for("default", overrides)
        if _canonical(_science(options)) == _canonical(_science(self.default_options)):
            return {"id": "default", "root": self.output_root, "options": options}
        digest = hashlib.sha256(_canonical(_science(options)).encode()).hexdigest()[:16]
        return {"id": f"p-{digest}", "root": self.output_root / "profiles" / f"p-{digest}", "options": options}

    def ensure_profile(self, profile: dict[str, Any]) -> None:
        if profile["id"] == "default":
            return
        root = Path(profile["root"])
        root.mkdir(parents=True, exist_ok=True)
        path = root / "profile.json"
        data = {"id": profile["id"], "options": profile["options"]}
        if path.exists():
            if json.loads(path.read_text(encoding="utf-8")) != data:
                raise ValueError("配置不可修改：已有相同编号但参数不同")
            return
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(_canonical(data), encoding="utf-8")
        temporary.replace(path)

    def _manifest(self, profile_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"p-[0-9a-f]{16}", profile_id):
            raise ValueError("无效参数配置编号")
        path = self.output_root / "profiles" / profile_id / "profile.json"
        if not path.is_file():
            raise KeyError(f"参数配置不存在：{profile_id}")
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("id") != profile_id:
            raise ValueError("参数配置文件编号不匹配")
        if not isinstance(value.get("options"), dict):
            raise ValueError("参数配置文件缺少参数")
        digest = hashlib.sha256(_canonical(_science(value["options"])).encode()).hexdigest()[:16]
        if profile_id != f"p-{digest}":
            raise ValueError("参数配置文件与编号不匹配")
        return value

    def root_for(self, profile_id: str) -> Path:
        if profile_id == "default":
            return self.output_root
        self._manifest(profile_id)
        return self.output_root / "profiles" / profile_id

    def _data_path(self, profile_id: str, experiment: str, domain: int, variable: str) -> Path:
        # experiment 可写成“试验@中心方案”（与绘图成员键相同），base 方案不写后缀。
        name, scheme = split_member(experiment)
        if not re.fullmatch(r"[A-Za-z0-9_]+", name) or not re.fullmatch(r"[A-Za-z0-9_]+", variable):
            raise ValueError("无效试验或变量名称")
        if not 1 <= int(domain) <= 99:
            raise ValueError("domain 必须介于 1 和 99")
        return processed_path(
            self.root_for(profile_id), name, int(domain), variable, center_scheme=scheme,
            center_dependent=self._center_dependent(profile_id) if scheme != BASE_CENTER_SCHEME else None,
        )

    def _request(self, payload: dict[str, Any], *, dry_run: bool) -> tuple[Request, dict[str, Any]]:
        experiment, member_scheme = split_member(payload["experiment"])
        # 2026-11-02 中心方案：payload.center_scheme 或 “试验@方案”。不给时沿用旧行为（中心参数取自参数配置）。
        center_scheme = payload.get("center_scheme") or (member_scheme if "@" in str(payload["experiment"]) else None)
        if payload.get("center_scheme") and "@" in str(payload["experiment"]) and member_scheme != payload["center_scheme"]:
            raise ValueError("试验名里的中心方案与 center_scheme 不一致")
        sources = self.source_directories
        if experiment not in sources:
            raise ValueError(f"试验未登记：{experiment}")
        domain = int(payload["domain"])
        if domain <= 0 or domain > 99:
            raise ValueError("domain 必须介于 1 和 99")
        times = tuple(int(value) for value in payload["times_minutes"])
        heights = tuple(int(value) for value in payload.get("heights_m", ()))
        variables = tuple(payload["variables"])
        # 2026-11-02：时次上限 1000 → 2000，容纳 120 h 的 5 min 序列（1441 个时次，处理套餐的中心与强度组）。
        if not times or not variables or len(times) > MAX_REQUEST_TIMES or len(heights) > 100:
            raise ValueError(f"请选择变量和时次，且限制在 {MAX_REQUEST_TIMES} 个时次、100 个高度内")
        if any(value < 0 for value in times) or any(value <= 0 for value in heights):
            raise ValueError("时次不能为负，高度必须为正")
        profile_id = payload.get("profile_id", "default")
        overrides = payload.get("options") or {}
        if profile_id == "default":
            profile = self.profile_for(overrides)
        else:
            options = self.options_for(profile_id, overrides)
            profile = {"id": profile_id, "root": self.root_for(profile_id), "options": options}
        options = {**profile["options"], "wrfout_template": str(sources[experiment]), "verbose": False}
        if center_scheme is not None:
            if "center_options_by_domain" in overrides:
                raise ValueError("选了中心方案时不能再改中心参数；换中心参数请在 处理数据_v3/center_schemes.json 新增方案")
            base_options = load_center_schemes()[BASE_CENTER_SCHEME]["center_options_by_domain"]
            if _canonical(options.get("center_options_by_domain") or base_options) != _canonical(base_options):
                raise ValueError(f"参数配置 {profile['id']} 的中心参数不是 base；不能与中心方案同时使用")
            options.pop("center_options_by_domain", None)  # 由中心方案提供
        registry = build_registry(options, times_minutes=times)
        dimensions = {registry.resolve(name).dimensions for name in variables}
        if "height" in dimensions and not heights:
            raise ValueError("三维变量必须选择高度")
        request = Request(
            experiments=(experiment,), domains=(domain,), times_minutes=times,
            heights_m=heights or (None,), variables=variables,
            source_root=sources[experiment], output_root=profile["root"],
            dry_run=dry_run, force=bool(payload.get("force", False)), options=options,
            center_scheme=center_scheme,
        )
        profile = {**profile, "clipped_minutes": [], "source_end_minute": None}
        if payload.get("clip_to_source_end"):
            # 各试验模拟结束时间不同：去掉晚于该试验 wrfout 最后时次的请求时次。
            index = CachedSourceIndex(request)
            index.time_origin(experiment, domain)
            source_end = max(index._cache[(experiment, domain)])
            kept = tuple(value for value in times if value <= source_end)
            if not kept:
                raise ValueError(f"所选时次都晚于 {experiment} 的 wrfout 最后时次 {source_end} min")
            profile["clipped_minutes"] = [value for value in times if value > source_end]
            profile["source_end_minute"] = source_end
            if len(kept) != len(times):
                request = Request(
                    experiments=(experiment,), domains=(domain,), times_minutes=kept,
                    heights_m=heights or (None,), variables=variables,
                    source_root=sources[experiment], output_root=profile["root"],
                    dry_run=dry_run, force=bool(payload.get("force", False)), options=options,
                    center_scheme=center_scheme,
                )
        request = with_center_scheme(request)
        if center_scheme is not None:
            profile["center_scheme"] = center_scheme
        return request, profile

    @netcdf_serialized
    def source_coverage(self, experiment: str, domain: int) -> dict[str, Any]:
        request, _ = self._request({
            "experiment": experiment, "domain": domain, "variables": ["U10"],
            "times_minutes": [0], "heights_m": [],
        }, dry_run=True)
        index = CachedSourceIndex(request)
        origin = index.time_origin(experiment, domain)
        return {"time_origin": origin, "time_minutes": sorted(index._cache[(experiment, domain)])}

    @netcdf_serialized
    def plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        request, profile = self._request(payload, dry_run=True)
        experiment, domain = request.experiments[0], request.domains[0]
        from 处理数据_v3.wrf_processing_v3.cached_planning import cached_plan

        registry = build_registry(request.options, times_minutes=request.times_minutes)
        cached = None if payload.get("clip_to_source_end") else cached_plan(request, registry)
        index = None
        if cached is None:
            index = CachedSourceIndex(request)
            origin = index.time_origin(experiment, domain)
            storage = NetCDFStorage(request.output_root, time_origin=origin, **storage_scheme_options(request, registry))
            planned = DependencyPlanner(registry, storage).plan(request)
        else:
            storage, planned = cached
            origin = storage.time_origin
        check_cache_compatibility(planned, storage, request.options)
        missing_source = set()
        for item in planned:
            if index is not None and item.should_compute and item.spec.kind in {"native", "getvar"}:
                try:
                    index.resolve(item.task.experiment, item.task.domain, item.task.time_minutes)
                except KeyError:
                    missing_source.add(item.task.time_minutes)
                except FileNotFoundError:
                    missing_source.add(item.task.time_minutes)
        rows = [{
            "experiment": item.task.experiment, "domain": item.task.domain,
            "time_minutes": item.task.time_minutes, "height_m": item.task.height_m,
            "variable": item.task.variable, "status": "compute" if item.should_compute else "reuse",
            "path": str(storage.path_for(item.task)), "dependency": not item.explicit,
        } for item in planned]
        counts = {"compute": sum(item.should_compute for item in planned), "reuse": sum(not item.should_compute for item in planned)}
        source_paths = {
            path for mapping in (index._cache.values() if index is not None else ()) for path, _ in mapping.values()
        }
        planned_paths = {storage.path_for(item.task) for item in planned}
        fingerprints = _plan_fingerprints(source_paths, planned_paths)
        summary = {
            "profile_id": profile["id"], "root": str(profile["root"]),
            # 按请求实际使用的参数显示（选了中心方案时，中心参数是方案的）；不选方案时与参数配置相同。
            "options": _science(request.options), "time_origin": origin,
            "source_minutes": sorted(index._cache[(experiment, domain)]) if index is not None else [],
            "source_index_skipped": index is None,
            "missing_source_minutes": sorted(missing_source),
            "clipped_minutes": profile["clipped_minutes"],
            "source_end_minute": profile["source_end_minute"],
            "counts": counts, "tasks": rows, "fingerprints": fingerprints,
        }
        if request.center_scheme is not None:
            summary["center_scheme"] = request.center_scheme  # 不选方案时不加此键，旧计划编号不变
        summary["plan_hash"] = hashlib.sha256(_canonical(summary).encode()).hexdigest()
        _remember_plan(self.output_root, payload, summary, source_paths, planned_paths)
        return summary

    # 功能说明（2026-11-03）：提交前核对计划。刚预览过、请求相同、且计划涉及的 wrfout 与输出文件
    # 大小/修改时间都没变时，计划必然相同，直接用预览结果；否则照旧重新建立计划再比较。
    # 以前提交时总是重做一遍计划，外接机械硬盘上 12 段要再等好几分钟。
    @netcdf_serialized
    def verify_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        with _RECENT_PLANS_LOCK:
            entry = _RECENT_PLANS.get(str(payload.get("plan_hash") or ""))
        if entry is not None and entry["key"] == _plan_key(self.output_root, payload):
            if _plan_fingerprints(entry["sources"], entry["outputs"]) == entry["summary"]["fingerprints"]:
                return entry["summary"]
        return self.plan(payload)

    @netcdf_serialized
    def coverage(self, profile_id: str, experiment: str, domain: int, variable: str) -> dict[str, Any]:
        path = self._data_path(profile_id, experiment, domain, variable)
        result = {"profile_id": profile_id, "path": str(path), "dimension": "surface", "time_minutes": [], "heights_m": [], "completed": []}
        if not path.is_file():
            try:
                result["dimension"] = build_registry(self.options_for(profile_id)).resolve(variable).dimensions
            except (KeyError, ValueError):
                pass
            return result
        with file_lock(path, exclusive=False, timeout_seconds=120):
            stat = path.stat()
            key = (str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns)
            with self._coverage_cache_lock:
                cached = self._coverage_cache.get(key)
                if cached is not None:
                    self._coverage_cache.move_to_end(key)
                    return deepcopy(cached)
            with Dataset(path) as dataset:
                dataset.set_auto_mask(False)
                times = [int(value) for value in dataset["time_minutes"][:]]
                heights = [int(value) for value in dataset["height_m"][:]] if "height_m" in dataset.variables else []
                flags = read_completed_flags(dataset)
                result.update({
                    "dimension": "height" if heights else "surface",
                    "time_minutes": sorted(times), "heights_m": sorted(heights),
                    "layout": str(getattr(dataset, "layout", "")),
                    "units": str(getattr(dataset, "units", "")),
                    "algorithm_version": str(getattr(dataset, "algorithm_version", "")),
                    "completed": [
                        {"time_minutes": times[index[0]], "height_m": heights[index[1]] if len(index) > 1 else None}
                        for index in np.argwhere(flags == 1)
                    ],
                })
            result["completed"].sort(key=lambda item: (item["time_minutes"], item["height_m"] or -1))
            with self._coverage_cache_lock:
                self._coverage_cache[key] = deepcopy(result)
                if len(self._coverage_cache) > 32:
                    self._coverage_cache.popitem(last=False)
        return result

    @netcdf_serialized
    def slice_metadata(self, profile_id: str, experiment: str, domain: int, variable: str, time_minutes: int, height_m: int | None) -> dict[str, Any]:
        path = self._data_path(profile_id, experiment, domain, variable)
        if not path.is_file():
            raise KeyError("切片不存在")
        with file_lock(path, exclusive=False, timeout_seconds=120):
            with Dataset(path) as dataset:
                dataset.set_auto_mask(False)
                times = [int(value) for value in dataset["time_minutes"][:]]
                heights = [int(value) for value in dataset["height_m"][:]] if "height_m" in dataset.variables else []
                if time_minutes not in times or (heights and height_m not in heights):
                    raise KeyError("切片不存在")
                selection = (times.index(time_minutes), heights.index(height_m)) if heights else (times.index(time_minutes),)
                if int(dataset["completed"][selection]) != 1:
                    raise KeyError("切片未完成")
                value = json.loads(str(dataset["metadata_json"][selection]))
                return {
                    **value, "path": str(path), "units": str(getattr(dataset, "units", "")),
                    "algorithm_version": str(getattr(dataset, "algorithm_version", "")),
                    "time_minutes": time_minutes, "height_m": height_m,
                }

    def run(self, payload: dict[str, Any], progress=None) -> dict[str, Any]:
        request, profile = self._request(payload, dry_run=False)
        self.ensure_profile(profile)
        result = ProcessingManager().process(request, progress=progress)
        extra = {"center_scheme": request.center_scheme} if request.center_scheme is not None else {}
        return {"profile_id": profile["id"], "root": str(profile["root"]), **extra, **result}
