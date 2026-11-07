from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from wrf_webvis.app.core.paths import PathManager


class StormMotionService:
    # 功能说明：保存处理根目录并创建路径管理器，用于定位风暴中心和移动速度文件。
    def __init__(self, processed_root: str | Path) -> None:
        self.path_manager = PathManager(processed_root)

    # 功能说明：读取中心轨迹中的经纬度，用相邻时次差分计算东西、南北速度、速度和方向；两个输出已存在时直接跳过，不核对来源。
    def compute(
        self,
        *,
        member_name: str,
        domain: int,
        center_path: str | Path | None = None,
        time_interval_seconds: float = 3600.0,
        force: bool = False,
    ) -> dict[str, Any]:
        source = Path(center_path) if center_path else self.path_manager.center_path(member_name, domain, "slp")
        if not source.exists():
            raise FileNotFoundError(f"Center file does not exist: {source}")
        out_root = self.path_manager.storm_motion_root(member_name, domain)
        npy_path = out_root / "storm_motion.npy"
        csv_path = out_root / "storm_motion.csv"
        if npy_path.exists() and csv_path.exists() and not force:
            return {"npy": str(npy_path), "csv": str(csv_path), "status": "skipped"}

        center = np.atleast_2d(np.loadtxt(source))
        lat = center[:, 3]
        lon = center[:, 4]
        if np.isnan(lat).any() or np.isnan(lon).any():
            raise ValueError("Center file must include lat/lon columns to compute storm motion")
        u, v = motion_components(lon, lat, time_interval_seconds)
        speed = np.hypot(u, v)
        direction = (np.degrees(np.arctan2(u, v)) + 360.0) % 360.0
        result = np.column_stack((center[:, 0], u, v, speed, direction))
        out_root.mkdir(parents=True, exist_ok=True)
        self._atomic_save(npy_path, result)
        header = "time_index,storm_u_motion,storm_v_motion,storm_speed,storm_direction"
        np.savetxt(csv_path, result, delimiter=",", header=header, comments="", fmt="%.8g")
        return {"npy": str(npy_path), "csv": str(csv_path), "status": "complete"}

    # 功能说明：把移动速度数组先写入临时文件，再替换正式文件，避免中断留下损坏结果。
    def _atomic_save(self, path: Path, array: np.ndarray) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("wb") as handle:
            np.save(handle, array)
        tmp.replace(path)


# 功能说明：根据相邻中心的经纬度和时间间隔计算风暴移动的东西、南北速度。
def motion_components(lon: np.ndarray, lat: np.ndarray, dt_seconds: float) -> tuple[np.ndarray, np.ndarray]:
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    n = lon.size
    u = np.zeros(n, dtype=float)
    v = np.zeros(n, dtype=float)
    for idx in range(n):
        if n == 1:
            continue
        if idx == 0:
            j0, j1, scale = 0, 1, 1.0
        elif idx == n - 1:
            j0, j1, scale = n - 2, n - 1, 1.0
        else:
            j0, j1, scale = idx - 1, idx + 1, 2.0
        east_m, north_m = local_displacement_m(lon[j0], lat[j0], lon[j1], lat[j1])
        u[idx] = east_m / (dt_seconds * scale)
        v[idx] = north_m / (dt_seconds * scale)
    return u, v


# 功能说明：读取指定时刻适用的风暴移动速度。
def load_motion_for_time(path: str | Path, time_hour: int | float) -> tuple[float, float]:
    data = np.atleast_2d(np.load(path))
    matches = np.where(np.isclose(data[:, 0].astype(float), float(time_hour)))[0]
    if matches.size == 0:
        available = ", ".join(f"{value:g}" for value in data[:, 0].astype(float)[:10])
        raise KeyError(f"storm motion has no time={float(time_hour):g}. Available times: {available}")
    row = data[int(matches[0])]
    return float(row[1]), float(row[2])


# 功能说明：检查移动速度文件是否包含请求的全部时刻。
def motion_file_has_times(path: str | Path, times: list[int]) -> bool:
    motion_path = Path(path)
    if not motion_path.exists():
        return False
    data = np.atleast_2d(np.load(motion_path))
    available = data[:, 0].astype(float)
    return all(np.isclose(available, float(time_hour)).any() for time_hour in times)


# 功能说明：把经纬度变化换算为局地东西、南北位移（米）。
def local_displacement_m(lon0: float, lat0: float, lon1: float, lat1: float) -> tuple[float, float]:
    radius = 6371000.0
    lat_mid = np.deg2rad((lat0 + lat1) * 0.5)
    east = np.deg2rad(lon1 - lon0) * radius * np.cos(lat_mid)
    north = np.deg2rad(lat1 - lat0) * radius
    return float(east), float(north)
