"""网页和命令行共用的科学参数/算法版本兼容检查，只读取元数据。"""
from __future__ import annotations

import json
import math
from typing import Any
from threading import RLock

import numpy as np
from netCDF4 import Dataset

from .locking import file_lock
from .storage import read_completed_flags

_COMPATIBILITY_CACHE: set[tuple] = set()
_COMPATIBILITY_CACHE_LOCK = RLock()

SCIENCE_KEYS = (
    "radius_km", "radius_km_by_domain", "radius_resolution_km",
    "center_options_by_domain", "theta_environment_domain",
    "theta_environment_ring_km", "shear_source_domain",
    "shear_radius_inner_km", "shear_radius_outer_km",
    "shear_upper_hpa", "shear_lower_hpa", "layer_polar_boundary_policy",
)


def _canonical(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: normalize(entry) for key, entry in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(entry) for entry in item]
        if isinstance(item, float) and math.isfinite(item) and item.is_integer():
            return int(item)
        return item

    return json.dumps(normalize(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# 2026-11-04 网页可改的非对称度参数（四象限指数 mdbz/dbz/precipitation_asymmetry 的圆盘半径、dbz_asymmetry 的高度）。
# 只有偏离下面的基准值时才计入科学参数：这样已有参数配置的编号（按 _science 取哈希）和完成情况索引都不变；
# 改了它们的请求会得到独立的参数配置目录（profiles/p-…），不会写进按基准值算好的文件。
# 基准值与 project_config.OPTIONS 一致（100 km、3000 m）；元数据里没有这两项的旧切片按基准值看待。
OPTIONAL_SCIENCE_KEYS = {"asymmetry_radius_km": 100.0, "dbz_asymmetry_height_m": 3000.0}


def _science(options: dict[str, Any]) -> dict[str, Any]:
    result = {key: options[key] for key in SCIENCE_KEYS if key in options}
    for key, baseline in OPTIONAL_SCIENCE_KEYS.items():
        if key in options and _canonical(options[key]) != _canonical(baseline):
            result[key] = options[key]
    return result


def _with_baselines(actual: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    """已存切片的科学参数里没有可选键时，补上基准值再比较（见 OPTIONAL_SCIENCE_KEYS）。"""
    filled = dict(actual)
    for key, baseline in OPTIONAL_SCIENCE_KEYS.items():
        if key in expected and key not in filled:
            filled[key] = baseline
    return filled


def _relevant_science_keys(variable: str, center_dependent=None) -> set[str]:
    keys: set[str] = set()
    # 2026-11-02：非 base 中心方案的目录按依赖图严格判断（vt、tilt、theta_e_anomaly 等也比较中心参数）；
    # base 目录暂沿用下面按名称的判断，待“中心参数核查”结果确认旧切片元数据齐全后再收紧。
    if center_dependent is not None and center_dependent(variable):
        keys.add("center_options_by_domain")
    if "center" in variable or variable.startswith("rmw"):
        keys.add("center_options_by_domain")
    if "_polar" in variable or variable.endswith("_asy") or variable.startswith("rmw"):
        keys.update(("radius_km_by_domain", "radius_resolution_km", "center_options_by_domain", "layer_polar_boundary_policy"))
    if variable.startswith("theta_anomaly"):
        keys.update(("theta_environment_domain", "theta_environment_ring_km"))
    if variable.startswith("shear_"):
        keys.update(("shear_source_domain", "shear_radius_inner_km", "shear_radius_outer_km", "shear_upper_hpa", "shear_lower_hpa"))
    if variable in ("mdbz_asymmetry", "dbz_asymmetry", "precipitation_asymmetry"):
        keys.add("asymmetry_radius_km")
    if variable == "dbz_asymmetry":
        keys.add("dbz_asymmetry_height_m")
    return keys


def _science_value_for_domain(key: str, value: Any, domain: int) -> Any:
    if key in {"radius_km_by_domain", "center_options_by_domain"} and isinstance(value, dict):
        return value.get(str(domain), value.get(domain))
    return value


def check_cache_compatibility(planned, storage, options):
    """同一文件的已完成切片必须与请求的相关科学参数和算法版本一致。

    无效/缺失元数据或不一致时只报错，不修复文件或触发重新处理。
    原始格点变量不要求与其无关的极坐标参数；运行时开关不参与比较。
    """
    by_path = {storage.path_for(item.task): item for item in planned}
    scheme = getattr(storage, "center_scheme", "base")
    for path, item in by_path.items():
        if not path.is_file():
            continue
        in_scheme = scheme != "base" and storage.in_scheme_directory(item.task.variable)
        relevant = _relevant_science_keys(item.task.variable, storage.center_dependent if in_scheme else None)
        expected = {
            key: _science_value_for_domain(key, options[key], item.task.domain)
            for key in relevant if key in options
        }
        algorithm = str(item.spec.options.get("algorithm_version") or "")
        with file_lock(path, exclusive=False, timeout_seconds=storage.lock_timeout_seconds):
            stat = path.stat()
            cache_key = (
                str(path.resolve()), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
                _canonical(expected), algorithm,
            )
            with _COMPATIBILITY_CACHE_LOCK:
                if cache_key in _COMPATIBILITY_CACHE:
                    continue
            # 2026-11-03：有完成情况索引时用它（每种参数组合只核对一次），不再逐条读 metadata_json。
            index_reader = getattr(storage, "_completion_index", None)
            index = index_reader(path) if index_reader is not None else None
            if index is not None:
                _check_with_index(index, path, item, relevant, expected, algorithm, in_scheme, scheme)
                with _COMPATIBILITY_CACHE_LOCK:
                    if len(_COMPATIBILITY_CACHE) >= 256:
                        _COMPATIBILITY_CACHE.clear()
                    _COMPATIBILITY_CACHE.add(cache_key)
                continue
            with Dataset(path) as dataset:
                dataset.set_auto_mask(False)
                stored_algorithm = str(getattr(dataset, "algorithm_version", ""))
                if algorithm and stored_algorithm != algorithm:
                    raise ValueError(f"{path} 已有切片的算法版本不一致；请使用独立输出目录或独立参数配置；不会自动重算")
                if relevant:
                    completed = read_completed_flags(dataset)
                    metadata = dataset["metadata_json"]
                    for index in np.argwhere(completed == 1):
                        raw = metadata[tuple(index)]
                        try:
                            stored = json.loads(str(raw))
                            if not isinstance(stored, dict):
                                raise ValueError("metadata_json 必须是对象")
                            actual = _with_baselines(_science(stored.get("request_options") or stored.get("options") or {}), expected)
                        except (ValueError, TypeError):
                            raise ValueError(f"{path} 的旧切片缺少有效参数元数据，不能在原目录续算")
                        for key, value in actual.items():
                            if key in expected and _canonical(_science_value_for_domain(key, value, item.task.domain)) != _canonical(expected[key]):
                                raise ValueError(f"{path} 已有切片的 {key} 参数不一致；请使用独立输出目录或独立参数配置；不会自动重算")
                        if any(key not in actual for key in expected):
                            raise ValueError(f"{path} 的旧切片缺少科学参数元数据，不能在原目录续算")
                        stored_scheme = (stored.get("request_options") or stored.get("options") or {}).get("center_scheme", "base")
                        if in_scheme and stored_scheme != scheme:
                            raise ValueError(f"{path} 已有切片属于中心方案 {stored_scheme!r}，与本次 {scheme!r} 不一致；不会自动重算")
                        if algorithm and str(stored.get("algorithm_version") or stored_algorithm) != algorithm:
                            raise ValueError(f"{path} 已有切片的算法版本不一致；请使用独立输出目录或独立参数配置；不会自动重算")
            with _COMPATIBILITY_CACHE_LOCK:
                if len(_COMPATIBILITY_CACHE) >= 256:
                    _COMPATIBILITY_CACHE.clear()
                _COMPATIBILITY_CACHE.add(cache_key)



def _check_with_index(index, path, item, relevant, expected, algorithm, in_scheme, scheme):
    """与上面逐条读取 NetCDF 的核对逐项相同，只是参数来自完成情况索引（每种参数组合核对一次）。"""
    from .completion_index import completed_profiles

    stored_algorithm = str(index.get("attrs", {}).get("algorithm_version", ""))
    if algorithm and stored_algorithm != algorithm:
        raise ValueError(f"{path} 已有切片的算法版本不一致；请使用独立输出目录或独立参数配置；不会自动重算")
    if not relevant:
        return
    for profile in completed_profiles(index):
        if profile.get("invalid"):
            raise ValueError(f"{path} 的旧切片缺少有效参数元数据，不能在原目录续算")
        actual = _with_baselines(profile.get("science") or {}, expected)
        for key, value in actual.items():
            if key in expected and _canonical(_science_value_for_domain(key, value, item.task.domain)) != _canonical(expected[key]):
                raise ValueError(f"{path} 已有切片的 {key} 参数不一致；请使用独立输出目录或独立参数配置；不会自动重算")
        if any(key not in actual for key in expected):
            raise ValueError(f"{path} 的旧切片缺少科学参数元数据，不能在原目录续算")
        if in_scheme and profile.get("center_scheme", "base") != scheme:
            raise ValueError(f"{path} 已有切片属于中心方案 {profile.get('center_scheme')!r}，与本次 {scheme!r} 不一致；不会自动重算")
        if algorithm and str(profile.get("algorithm") or stored_algorithm) != algorithm:
            raise ValueError(f"{path} 已有切片的算法版本不一致；请使用独立输出目录或独立参数配置；不会自动重算")
