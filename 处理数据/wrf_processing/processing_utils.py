from __future__ import annotations

from wrf_webvis.app.core.config import DEFAULT_EXPERIMENTS_ROOT, absolute_path

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

import numpy as np

from wrf_webvis.app.core.paths import PathManager, WrfoutDomainReader
from wrf_webvis.app.core.registry import DEFAULT_DOMAIN_CONFIG
from wrf_webvis.app.core.schemas import ProcessingRequest


STORM_RELATIVE_WIND_VARIABLES = {"ua": "u", "va": "v"}
_atomic_save_locks: dict[Path, Lock] = {}
_atomic_save_locks_guard = Lock()


# 功能说明：优先使用明确指定的原始数据路径，否则按标准试验名称推导挂载路径，无法识别名称时用默认试验目录；这里只决定路径，不检查磁盘是否挂载。
def resolve_wrfout_root(member_name: str, wrfout_root: str | Path | None = None) -> Path:
    text = "" if wrfout_root is None else str(wrfout_root).strip()
    if text:
        return absolute_path(text)
    standard_root = _standard_member_wrfout_root(member_name)
    if standard_root is not None:
        return standard_root
    return DEFAULT_EXPERIMENTS_ROOT / member_name / "run"


# 功能说明：识别标准 WRF 成员的海域或陆地挂载卷，并返回对应 wrfout 根目录。
def _standard_member_wrfout_root(member_name: str) -> Path | None:
    """Return the mounted source path for a standard sea/land experiment."""
    parts = member_name.split("_")
    if len(parts) != 3 or parts[0] != "WRF":
        return None
    try:
        direction = int(parts[1])
    except ValueError:
        return None
    surface = parts[2]
    if direction not in {0, 45, 90, 135, 180, 225, 270, 315} or surface not in {"sea", "land"}:
        return None
    first_half = direction in {0, 45, 90, 135}
    volume = {
        ("sea", True): "sea_1",
        ("sea", False): "sea_2",
        ("land", True): "land_2",
        ("land", False): "land_1",
    }[(surface, first_half)]
    return Path("/Volumes") / volume / member_name / "run"


# 功能说明：先写临时文件再一次性替换目标文件，避免中途失败留下半个数组。
def atomic_save(path: Path, array: np.ndarray) -> None:
    target = path.resolve(strict=False)
    with _atomic_save_lock(target):
        _atomic_save_unlocked(target, array)


# 功能说明：把每个格点的纬度、经度分别保存为 grid/lat.npy 和 grid/lon.npy，供绘图定位使用，并返回这两个路径。
def save_grid_coordinates(
    path_manager: PathManager,
    member_name: str,
    domain: int,
    latitude: np.ndarray,
    longitude: np.ndarray,
) -> list[Path]:
    """Persist plot-ready grid coordinates beside processed variables."""

    grid_root = path_manager.domain_root(member_name, domain) / "grid"
    outputs = [grid_root / "lat.npy", grid_root / "lon.npy"]
    atomic_save(outputs[0], np.asarray(latitude, dtype=np.float32))
    atomic_save(outputs[1], np.asarray(longitude, dtype=np.float32))
    return outputs


# 功能说明：为每个目标路径取得共享锁，防止并发写入同一文件。
def _atomic_save_lock(path: Path) -> Lock:
    with _atomic_save_locks_guard:
        return _atomic_save_locks.setdefault(path, Lock())


# 功能说明：创建临时文件写入 NumPy 数组，再替换正式文件并清理临时文件。
def _atomic_save_unlocked(path: Path, array: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with tmp.open("wb") as handle:
            np.save(handle, array)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


# 功能说明：收集请求、源文件、时间和高度等信息，生成输出元数据。
def build_processing_metadata(
    request: ProcessingRequest,
    variable: str,
    source_file: str | Path,
    time_hour: int,
    height_m: float | None,
    source_time_index: int | None = None,
) -> dict[str, Any]:
    return {
        "source_wrfout": str(source_file),
        "source_time_index": source_time_index,
        "variable": variable,
        "domain": request.domain,
        "time": time_hour,
        "height": height_m,
        "member_name": request.member_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "processing_options": request.options,
    }


# 功能说明：尝试读取原始 WRF 文件的网格间距和大小，必要时使用默认值，再整理成按模拟区域编号查找的配置表。
def domain_config_dict(request: ProcessingRequest) -> dict[int, dict[str, Any]]:
    domain = WrfoutDomainReader.read_domain_config(
        domain=request.domain,
        wrfout_root=resolve_wrfout_root(request.member_name, request.wrfout_root),
        wrfout_file=request.wrfout_file,
        fallback=DEFAULT_DOMAIN_CONFIG,
    )
    return {
        request.domain: {
            "dx_km": domain.dx_km or 1.0,
            "dy_km": domain.dy_km or domain.dx_km or 1.0,
            "grid_number": domain.nx or domain.ny or 0,
            "nx": domain.nx,
            "ny": domain.ny,
        }
    }


# 功能说明：处理已有 NaN 缺测：二维场先线性插值再用最近点补齐；没有有效点、不是二维或缺插值库时用0替代。若原数据没有NaN则直接返回；这与网页绘图的缺测策略不同。
def fill_nan(array: np.ndarray) -> np.ndarray:
    values = np.asarray(array)
    if not np.isnan(values).any():
        return values
    if values.ndim != 2:
        return np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
    try:
        from scipy.interpolate import griddata
    except ImportError:
        return np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)

    yy, xx = np.indices(values.shape)
    valid = ~np.isnan(values)
    if not valid.any():
        return np.zeros_like(values)
    points = np.column_stack((yy[valid], xx[valid]))
    samples = values[valid]
    missing = np.column_stack((yy[~valid], xx[~valid]))
    filled = values.copy()
    linear = griddata(points, samples, missing, method="linear")
    filled[~valid] = linear
    remaining = np.isnan(filled)
    if remaining.any():
        nearest = griddata(points, samples, np.column_stack((yy[remaining], xx[remaining])), method="nearest")
        filled[remaining] = nearest
    return np.nan_to_num(filled, nan=0.0, posinf=0.0, neginf=0.0)


# 功能说明：尝试加载地图投影库 Cartopy；失败时放入不做实际投影的占位模块，让只需读取和插值 WRF 数据的流程仍能导入依赖。
def prepare_wrf_import() -> None:
    """Avoid optional Cartopy/Shapely import failures when only getvar/interplevel are needed."""
    try:
        import cartopy  # noqa: F401
    except Exception:  # noqa: BLE001
        import sys
        import types

        class _DummyCRS:
            # 功能说明：接受创建投影对象时传入的任意参数，但不执行操作，作为缺少地图投影库时的空占位对象。
            def __init__(self, *args, **kwargs):
                pass

        class _CRSModule(types.ModuleType):
            # 功能说明：当依赖库询问某个投影名称时，一律交回空的投影占位类，使不需要实际地图投影的数据处理可以继续加载。
            def __getattr__(self, name):
                return _DummyCRS

        cartopy_module = types.ModuleType("cartopy")
        crs_module = _CRSModule("cartopy.crs")
        cartopy_module.crs = crs_module
        sys.modules.setdefault("cartopy", cartopy_module)
        sys.modules.setdefault("cartopy.crs", crs_module)


# 功能说明：把读取失败的文件、正在做的操作和原始错误拼成一段提示，并给出文件损坏、被占用或读取库不兼容等排查方向。
def wrfout_read_error(*, path: Path, action: str, original: Exception) -> str:
    return (
        f"WRF file read failed while trying to {action}: {path}. "
        f"Original error: {type(original).__name__}: {original}. "
        "Check whether this wrfout file is incomplete, corrupted, locked by another program, "
        "or unreadable by the current netCDF/HDF5 libraries."
    )


# 功能说明：需要时核对数据说明中的原始文件名和时次编号；风暴相对风还必须有移动订正标记。未要求核对来源的普通变量会直接视为可复用。
def processed_file_is_current(
    variable: str,
    metadata: Path | None,
    *,
    source_file: str | Path | None = None,
    source_time_index: int | None = None,
) -> bool:
    check_source = source_file is not None
    if variable not in STORM_RELATIVE_WIND_VARIABLES and not check_source:
        return True
    if metadata is None or not metadata.exists():
        return False
    try:
        payload = json.loads(metadata.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    if check_source:
        stored_source = payload.get("source_wrfout")
        if stored_source is None or not _same_wrfout_source(stored_source, source_file):
            return False
        try:
            if int(payload.get("source_time_index")) != int(source_time_index):
                return False
        except (TypeError, ValueError):
            return False
    if variable in STORM_RELATIVE_WIND_VARIABLES:
        return payload.get("motion_corrected") is True
    return True


# 功能说明：比较原始文件路径或去掉冒号后的文件名，允许同一时刻的文件移动到其他挂载目录；不比较文件内容或修改时间。
def _same_wrfout_source(stored_source: str | Path, current_source: str | Path) -> bool:
    """Compare a WRF source across stable filenames but relocatable mount roots."""
    stored = Path(stored_source)
    current = Path(current_source)
    if stored == current:
        return True
    # Cache paths already partition data by member and domain.  A source file
    # can move from the cluster archive to a local mounted volume without
    # changing its WRF timestamp.  WRF commonly writes either ``02:00:00`` or
    # ``020000``; normalizing colons preserves that identity without treating a
    # different timestamp as current.
    return stored.name.replace(":", "") == current.name.replace(":", "")
