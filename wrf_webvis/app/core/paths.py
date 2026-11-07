from __future__ import annotations

from .config import absolute_path

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Iterable
from uuid import uuid4

from .registry import DEFAULT_DOMAIN_CONFIG
from .schemas import DomainConfig, VariableDimension


_metadata_write_locks: dict[Path, Lock] = {}
_metadata_write_locks_guard = Lock()


# 功能说明：把模拟区域编号转换成文件夹名，例如3变为 d03。
def domain_dir(domain: int) -> str:
    return f"d0{int(domain)}"


# 功能说明：把模拟小时数转换成带小时、必要时带分钟的目录名，让同一时刻有统一的存储名称。
def time_dir_name(time_hour: int | float | str) -> str:
    value = str(time_hour).strip()
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?h(?:\d+(?:\.\d+)?min)?", value, re.IGNORECASE):
        return value
    try:
        number = float(value)
        if number.is_integer():
            return f"{int(number)}h"
        total_minutes = number * 60.0
        rounded_minutes = round(total_minutes)
        if abs(total_minutes - rounded_minutes) < 1e-7:
            sign = "-" if rounded_minutes < 0 else ""
            absolute_minutes = abs(rounded_minutes)
            hours, minutes = divmod(absolute_minutes, 60)
            return f"{sign}{hours}h{minutes}min"
        return f"{number:g}h"
    except ValueError:
        return f"{value}h"


# 功能说明：把时次统一格式化为对应的 .npy 文件名。
def time_file_name(time_hour: int | float | str) -> str:
    value = str(time_hour).strip()
    if value.endswith(".npy"):
        return value
    if value.endswith("h"):
        return f"{value}.npy"
    return f"{time_dir_name(value)}.npy"


# 功能说明：把高度统一格式化为对应的 .npy 文件名。
def height_file_name(height_m: int | float | str) -> str:
    value = str(height_m).strip()
    if value.endswith(".npy"):
        return value
    if value.endswith("m"):
        return f"{value}.npy"
    number = float(value)
    if number.is_integer():
        return f"{int(number)}m.npy"
    return f"{number:g}m.npy"


# 功能说明：依据当前元数据布局，计算一个数据文件应使用的元数据路径。
def metadata_path(data_path: Path) -> Path:
    if data_path.suffix == ".npy" and _variable_metadata_only():
        return variable_metadata_path(data_path)
    return data_path.with_suffix(data_path.suffix + ".json")


# 功能说明：定位并返回某变量共用的来源元数据文件路径。
def variable_metadata_path(data_path: Path) -> Path:
    """Return the shared provenance file for a processed variable."""
    variable_root = data_path.parent.parent if _is_time_directory(data_path.parent.name) else data_path.parent
    return variable_root / "source_metadata.json"


# 功能说明：读取程序启动环境的开关，判断数据说明是每个变量共用一份，还是按旧方式分别存放。
def _variable_metadata_only() -> bool:
    return os.environ.get("WRF_WEBVIS_METADATA_LAYOUT", "").strip().lower() == "variable"


# 功能说明：判断目录名是否是合法的时次格式。
def _is_time_directory(name: str) -> bool:
    return bool(re.fullmatch(r"[+-]?\d+(?:\.\d+)?h(?:\d+(?:\.\d+)?min)?", name, re.IGNORECASE))


# 功能说明：加锁后先写临时 JSON 文件，再替换目标文件，避免并发写坏内容。
def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write cache metadata safely when independent web jobs share a target."""
    target = path.resolve(strict=False)
    with _metadata_write_lock(target):
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)


# 功能说明：为同一个数据说明文件取得共用的写入保护，让多个任务修改它时依次进行。
def _metadata_write_lock(path: Path) -> Lock:
    with _metadata_write_locks_guard:
        return _metadata_write_locks.setdefault(path, Lock())


@dataclass(frozen=True)
class ExistingData:
    path: Path
    metadata: Path | None
    layout: str


class WrfoutDomainReader:
    """Read grid dimensions and resolution from WRF output files."""

    # 功能说明：在根目录中筛选指定域的 WRF 输出文件，并按名称排序返回。
    @staticmethod
    def find_wrfout_files(wrfout_root: str | Path, domain: int) -> list[Path]:
        root = Path(wrfout_root)
        if root.is_file():
            return [root] if WrfoutDomainReader.matches_domain(root, domain) else []
        if not root.exists():
            return []
        pattern = f"wrfout_{domain_dir(domain)}_*"
        files = sorted(root.glob(pattern))
        # Never borrow another domain's wrfout as a fallback.  Grid spacing is
        # domain-specific; for example, reading d01 while processing d03 would
        # silently turn the configured 1 km d03 grid into a 25 km grid.  An
        # empty result lets ``read_domain_config`` use DEFAULT_DOMAIN_CONFIG
        # for the requested domain instead.
        return files

    # 功能说明：检查 WRF 文件名是否属于指定域。
    @staticmethod
    def matches_domain(path: str | Path, domain: int) -> bool:
        return Path(path).name.lower().startswith(f"wrfout_{domain_dir(domain)}_")

    # 功能说明：优先从指定 WRF 文件读取网格配置；不可用时返回备用配置。
    @staticmethod
    def read_domain_config(
        *,
        domain: int,
        wrfout_root: str | Path | None = None,
        wrfout_file: str | Path | None = None,
        fallback: dict[int, dict[str, Any]] | None = None,
    ) -> DomainConfig:
        source_path: Path | None = None
        if wrfout_file is not None:
            candidate = Path(wrfout_file)
            source_path = candidate if WrfoutDomainReader.matches_domain(candidate, domain) else None
        elif wrfout_root is not None:
            files = WrfoutDomainReader.find_wrfout_files(wrfout_root, domain)
            source_path = files[0] if files else None

        if source_path is not None and source_path.exists():
            try:
                return WrfoutDomainReader._read_from_file(source_path, domain)
            except Exception as exc:  # noqa: BLE001
                config = WrfoutDomainReader._fallback(domain, fallback)
                config.source = f"fallback_after_wrfout_error: {exc}"
                config.wrfout_path = source_path
                return config

        return WrfoutDomainReader._fallback(domain, fallback)

    # 功能说明：从 WRF 文件读取网格间距和尺寸，组装并返回域配置。
    @staticmethod
    def _read_from_file(path: Path, domain: int) -> DomainConfig:
        from netCDF4 import Dataset

        with Dataset(path, "r") as dataset:
            dx_m = _read_number_attr(dataset, "DX")
            dy_m = _read_number_attr(dataset, "DY")
            nx = _read_dimension(dataset, "west_east") or _read_int_attr(dataset, "WEST-EAST_GRID_DIMENSION")
            ny = _read_dimension(dataset, "south_north") or _read_int_attr(dataset, "SOUTH-NORTH_GRID_DIMENSION")

        return DomainConfig(
            domain=domain,
            dx_km=(dx_m / 1000.0) if dx_m is not None else None,
            dy_km=(dy_m / 1000.0) if dy_m is not None else None,
            nx=nx,
            ny=ny,
            source="wrfout",
            wrfout_path=path,
        )

    # 功能说明：从给定或默认配置表取出指定域的备用网格配置。
    @staticmethod
    def _fallback(domain: int, fallback: dict[int, dict[str, Any]] | None = None) -> DomainConfig:
        table = fallback or DEFAULT_DOMAIN_CONFIG
        raw = table.get(domain, {})
        return DomainConfig(
            domain=domain,
            dx_km=raw.get("dx_km"),
            dy_km=raw.get("dy_km", raw.get("dx_km")),
            nx=raw.get("nx", raw.get("grid_number")),
            ny=raw.get("ny", raw.get("grid_number")),
            source="fallback",
        )


# 功能说明：安全读取数据集属性并转换为小数；无效时返回空。
def _read_number_attr(dataset: Any, name: str) -> float | None:
    value = getattr(dataset, name, None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# 功能说明：安全读取数据集属性并转换为整数；无效时返回空。
def _read_int_attr(dataset: Any, name: str) -> int | None:
    value = getattr(dataset, name, None)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# 功能说明：读取指定维度的长度；维度不存在时返回空。
def _read_dimension(dataset: Any, name: str) -> int | None:
    dim = dataset.dimensions.get(name)
    return len(dim) if dim is not None else None


class PathManager:
    # 功能说明：保存绝对化后的预处理数据根目录，供后续拼接路径。
    def __init__(self, processed_root: str | Path) -> None:
        self.processed_root = absolute_path(processed_root)

    # 功能说明：拼出并返回某成员、某域的数据根目录。
    def domain_root(self, member_name: str, domain: int) -> Path:
        return self.processed_root / member_name / domain_dir(domain)

    # 功能说明：按成员、变量、时次、维度和高度拼出标准数据文件路径。
    def variable_path(
        self,
        *,
        member_name: str,
        domain: int,
        variable: str,
        time_hour: int | float | str,
        dimension: VariableDimension | str,
        height_m: int | float | str | None = None,
    ) -> Path:
        dim = _dimension_value(dimension)
        base = self.domain_root(member_name, domain) / variable
        if dim == VariableDimension.THREE_D.value:
            if height_m is None:
                raise ValueError("height_m is required for per-height 3D output")
            return base / time_dir_name(time_hour) / height_file_name(height_m)
        return base / time_file_name(time_hour)

    # 功能说明：列出兼容旧数据布局时可能存在的变量文件路径。
    def legacy_variable_candidates(
        self,
        *,
        member_name: str,
        domain: int,
        variable: str,
        time_hour: int | float | str,
        height_m: int | float | str | None = None,
    ) -> list[Path]:
        base = self.domain_root(member_name, domain) / variable
        candidates = [base / time_file_name(time_hour)]
        if height_m is not None:
            candidates.insert(0, base / time_dir_name(time_hour) / height_file_name(height_m))
            candidates.append(base / time_dir_name(time_hour) / time_file_name(height_m))
        return candidates

    # 功能说明：检查新旧布局中的候选路径，找到时返回文件、元数据和布局信息。
    def existing_variable(
        self,
        *,
        member_name: str,
        domain: int,
        variable: str,
        time_hour: int | float | str,
        dimension: VariableDimension | str,
        height_m: int | float | str | None = None,
        include_metadata: bool = True,
    ) -> ExistingData | None:
        candidates: list[tuple[str, Path]] = []
        dim = _dimension_value(dimension)
        try:
            candidates.append(
                (
                    "per_height" if dim == VariableDimension.THREE_D.value else "standard",
                    self.variable_path(
                        member_name=member_name,
                        domain=domain,
                        variable=variable,
                        time_hour=time_hour,
                        dimension=dimension,
                        height_m=height_m,
                    ),
                )
            )
        except ValueError:
            pass
        for path in self.legacy_variable_candidates(
            member_name=member_name,
            domain=domain,
            variable=variable,
            time_hour=time_hour,
            height_m=height_m,
        ):
            candidates.append(("legacy", path))

        seen: set[Path] = set()
        for layout, path in candidates:
            if path in seen:
                continue
            seen.add(path)
            if path.exists():
                if not include_metadata:
                    return ExistingData(path=path, metadata=None, layout=layout)
                meta = metadata_path(path)
                return ExistingData(path=path, metadata=meta if meta.exists() else None, layout=layout)
        return None

    # 功能说明：拼出并返回中心位置文件路径。
    def center_path(self, member_name: str, domain: int, center_name: str = "slp") -> Path:
        return self.processed_root / member_name / "center" / domain_dir(domain) / center_name / "center.txt"

    # 功能说明：拼出并返回指定高度层的中心位置文件路径。
    def layer_center_path(self, member_name: str, domain: int, height_m: int | float) -> Path:
        return self.center_path(member_name, domain, f"{int(height_m)}m")

    # 功能说明：拼出并返回成员的陆地边界数据路径。
    def land_boundary_path(self, member_name: str) -> Path:
        return self.processed_root / member_name / "d01" / "land_boundary" / "land_boundary.npy"

    # 功能说明：拼出并返回距陆地数据的目录路径。
    def land_distance_root(self, member_name: str, domain: int) -> Path:
        return self.domain_root(member_name, domain) / "land_distance"

    # 功能说明：拼出并返回风暴移动数据的目录路径。
    def storm_motion_root(self, member_name: str, domain: int) -> Path:
        return self.domain_root(member_name, domain) / "motion"

    # 功能说明：依据当前布局写入元数据，并返回实际写入的文件路径。
    def write_metadata(self, data_path: Path, payload: dict[str, Any]) -> Path:
        meta_path = metadata_path(data_path)
        if data_path.suffix == ".npy" and _variable_metadata_only():
            if meta_path.exists():
                return meta_path
            variable_name = variable_metadata_path(data_path).parent.name
            shared_payload = {
                key: value
                for key, value in payload.items()
                if key not in {"time", "height", "source_wrfout", "source_time_index", "created_at"}
            }
            shared_payload.update(
                {
                    "variable": shared_payload.get("variable") or variable_name,
                    "time_height_layout": "variable-level provenance",
                    "metadata_layout": "variable",
                }
            )
            atomic_write_json(meta_path, shared_payload)
            return meta_path
        atomic_write_json(meta_path, payload)
        return meta_path


# 功能说明：从文件名提取时和分，返回用于时间排序的键。
def stable_time_sort_key(path: Path) -> tuple[int, int]:
    match = re.search(r"(\d+)h(?:(\d+)min)?", path.name)
    return (int(match.group(1)), int(match.group(2) or 0)) if match else (999999, 0)


# 功能说明：按给定顺序返回第一个实际存在的路径。
def first_existing(paths: Iterable[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


# 功能说明：把维度枚举或文字统一成字符串。
def _dimension_value(dimension: VariableDimension | str) -> str:
    return dimension.value if isinstance(dimension, VariableDimension) else str(dimension)
