"""完成情况索引（2026-11-03）：<变量>.nc 旁的小文件 <变量>.nc.index.json。

为什么要它：每个切片的完成标记和参数记录在 NetCDF 里各占一个 HDF5 块（chunksize=1），
散落在几 GB 的大文件各处；外接机械硬盘上逐个读要一次次寻道，预览一段计划要几十秒。
索引把“哪些切片已完成、写入时用的科学参数是什么”集中记在一个小 JSON 里。

可靠性：
- 索引记录写好时 .nc 的 [大小, mtime_ns, ctime_ns, inode]；读取时四项必须与 .nc 当前一致才用，
  否则当作没有索引，回到逐个读取 NetCDF（并顺手重建）。所以别的程序改过 .nc，索引自动失效。
- 只在持有该 .nc 的文件锁时读写索引（写切片时持写锁，规划时持读锁），不会读到写了一半的状态。
- 索引只是加速用的副本：写不进去（只读盘、权限）时静默跳过，处理照常。
- 不改变 NetCDF 文件本身，也不改变任何数值。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np

INDEX_VERSION = 1
_MEMORY: dict[str, tuple[list[int], dict[str, Any]]] = {}
_MEMORY_LOCK = Lock()
_BUILD_LOCKS: dict[str, Lock] = {}


def index_path(path: Path) -> Path:
    path = Path(path)
    return path.with_name(path.name + ".index.json")


def signature(path: Path) -> list[int] | None:
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return [int(stat.st_size), int(stat.st_mtime_ns), int(stat.st_ctime_ns), int(stat.st_ino)]


def enabled() -> bool:
    return os.environ.get("WRF_PROCESSING_V3_COMPLETION_INDEX", "1").strip() not in {"0", "false", "off"}


def build_lock(path: Path) -> Lock:
    key = str(Path(path))
    with _MEMORY_LOCK:
        return _BUILD_LOCKS.setdefault(key, Lock())


def load_valid(path: Path) -> dict[str, Any] | None:
    """返回与 .nc 当前状态一致的索引；没有、损坏或已过期时返回 None。调用方应持有该 .nc 的文件锁。"""
    if not enabled():
        return None
    current = signature(path)
    if current is None:
        return None
    key = str(Path(path))
    with _MEMORY_LOCK:
        cached = _MEMORY.get(key)
    if cached is not None and cached[0] == current:
        return cached[1]
    target = index_path(path)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("version") != INDEX_VERSION or value.get("signature") != current:
        return None
    with _MEMORY_LOCK:
        if len(_MEMORY) > 4096:
            _MEMORY.clear()
        _MEMORY[key] = (current, value)
    return value


def write(path: Path, index: dict[str, Any] | None) -> None:
    """在 .nc 已关闭后调用：记下当前签名并原子替换索引文件。失败时删除旧索引的内存副本，不报错。"""
    if index is None or not enabled():
        return
    current = signature(path)
    key = str(Path(path))
    with _MEMORY_LOCK:
        _MEMORY.pop(key, None)
    if current is None:
        return
    value = {**index, "version": INDEX_VERSION, "signature": current}
    target = index_path(path)
    temporary = target.with_name(target.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, target)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        return
    with _MEMORY_LOCK:
        _MEMORY[key] = (current, value)


def profile_of(stored: Any) -> dict[str, Any]:
    """一条切片元数据里与兼容检查有关的部分；与 cache_compatibility 原来逐条读取时的取法相同。"""
    from .cache_compatibility import _science

    if not isinstance(stored, dict):
        return {"invalid": True}
    options = stored.get("request_options") or stored.get("options") or {}
    if not isinstance(options, dict):
        return {"invalid": True}
    return {
        "science": _science(options),
        "center_scheme": options.get("center_scheme", "base"),
        "algorithm": str(stored.get("algorithm_version") or ""),
    }


def _profile_from_raw(raw: Any) -> dict[str, Any]:
    text = str(raw.item() if isinstance(raw, np.ndarray) else raw)
    try:
        return profile_of(json.loads(text))
    except (ValueError, TypeError):
        return {"invalid": True}


def _profile_id(index: dict[str, Any], profile: dict[str, Any]) -> int:
    canonical = json.dumps(profile, sort_keys=True, ensure_ascii=False)
    for number, existing in enumerate(index["profiles"]):
        if json.dumps(existing, sort_keys=True, ensure_ascii=False) == canonical:
            return number
    index["profiles"].append(profile)
    return len(index["profiles"]) - 1


def _attrs(dataset) -> dict[str, Any]:
    names = ("schema_version", "experiment", "domain", "variable", "layout", "time_origin", "algorithm_version", "center_scheme")
    values = {}
    for name in names:
        if name in dataset.ncattrs():
            value = dataset.getncattr(name)
            values[name] = value.item() if isinstance(value, np.generic) else value
    return values


def build(dataset, read_completed_flags) -> dict[str, Any]:
    """从打开的 NetCDF 读出全部完成标记和已完成切片的参数（慢，只在没有有效索引时做一次）。"""
    index: dict[str, Any] = {"attrs": _attrs(dataset), "profiles": []}
    index["times"] = [int(value) for value in np.asarray(dataset["time_minutes"][:]).reshape(-1)]
    index["heights"] = (
        [int(value) for value in np.asarray(dataset["height_m"][:]).reshape(-1)]
        if "height_m" in dataset.variables else None
    )
    flags = np.asarray(read_completed_flags(dataset)).astype(int)
    profiles = np.full(flags.shape, -1, dtype=int)
    metadata = dataset["metadata_json"]
    for position in np.argwhere(flags == 1):
        profiles[tuple(position)] = _profile_id(index, _profile_from_raw(metadata[tuple(position)]))
    index["completed"], index["profile"] = flags.tolist(), profiles.tolist()
    index["lowest"] = None
    group = dataset.groups.get("lowest") if hasattr(dataset, "groups") else None
    if group is not None and {"completed", "metadata_json"}.issubset(group.variables):
        low = np.asarray(group["completed"][:]).astype(int).reshape(-1)
        low_profiles = np.full(low.shape, -1, dtype=int)
        for position in np.flatnonzero(low == 1):
            low_profiles[position] = _profile_id(index, _profile_from_raw(group["metadata_json"][position]))
        index["lowest"] = {"completed": low.tolist(), "profile": low_profiles.tolist()}
    return index


def updated(index: dict[str, Any], times: list[int], heights: list[int] | None, task, stored: dict[str, Any]) -> dict[str, Any] | None:
    """写完一个切片后的索引；stored 是刚写进 metadata_json 的那条记录（解析后）。
    坐标与旧索引不是“只在末尾追加”的关系时返回 None（调用方改为整体重建）。"""
    old_times, old_heights = index["times"], index["heights"]
    if times[: len(old_times)] != old_times:
        return None
    if (heights is None) != (old_heights is None):
        return None
    if heights is not None and heights[: len(old_heights)] != old_heights:
        return None
    new = json.loads(json.dumps(index))  # 深拷贝，失败时不影响内存缓存
    new["times"], new["heights"] = list(times), (list(heights) if heights is not None else None)
    rows, columns = len(times), (len(heights) if heights is not None else None)
    completed, profile = new["completed"], new["profile"]
    if columns is None:
        completed.extend([0] * (rows - len(completed)))
        profile.extend([-1] * (rows - len(profile)))
    else:
        for row in range(len(completed)):
            completed[row].extend([0] * (columns - len(completed[row])))
            profile[row].extend([-1] * (columns - len(profile[row])))
        for _ in range(rows - len(completed)):
            completed.append([0] * columns)
            profile.append([-1] * columns)
    if new.get("lowest") is not None:
        new["lowest"]["completed"].extend([0] * (rows - len(new["lowest"]["completed"])))
        new["lowest"]["profile"].extend([-1] * (rows - len(new["lowest"]["profile"])))
    number = _profile_id(new, profile_of(stored))
    record = times.index(int(task.time_minutes))
    if task.height_m == "lowest" and columns is not None:
        if new.get("lowest") is None:
            new["lowest"] = {"completed": [0] * rows, "profile": [-1] * rows}
        new["lowest"]["completed"][record] = 1
        new["lowest"]["profile"][record] = number
    elif columns is None:
        completed[record], profile[record] = 1, number
    else:
        column = heights.index(int(task.height_m))
        completed[record][column], profile[record][column] = 1, number
    return new


class IndexAttributes:
    """让 NetCDFStorage._validate_identity 能用索引里记下的全局属性做同样的核对。"""

    def __init__(self, attrs: dict[str, Any]):
        self.__dict__.update(attrs)


def lookup(index: dict[str, Any], task) -> bool:
    try:
        record = index["times"].index(int(task.time_minutes))
    except ValueError:
        return False
    if index["heights"] is None:
        return bool(index["completed"][record]) if record < len(index["completed"]) else False
    if task.height_m == "lowest":
        low = index.get("lowest")
        return bool(low and record < len(low["completed"]) and low["completed"][record])
    if not isinstance(task.height_m, int) or int(task.height_m) not in index["heights"]:
        return False
    column = index["heights"].index(int(task.height_m))
    row = index["completed"][record] if record < len(index["completed"]) else []
    return bool(column < len(row) and row[column])


def completed_profiles(index: dict[str, Any]):
    """根组（不含 lowest）已完成切片用到的各种参数组合，各给一次；与原兼容检查遍历的切片范围相同。"""
    flags = np.asarray(index["completed"], dtype=int)
    profiles = np.asarray(index["profile"], dtype=int)
    numbers = np.unique(profiles[flags == 1]) if flags.size else np.asarray([], dtype=int)
    for number in numbers.tolist():
        yield index["profiles"][number] if 0 <= number < len(index["profiles"]) else {"invalid": True}
