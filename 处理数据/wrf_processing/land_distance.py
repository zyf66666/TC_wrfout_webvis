from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from wrf_webvis.app.core.paths import PathManager, WrfoutDomainReader


class LandDistanceService:
    # 功能说明：保存处理根目录并创建路径管理器，用于定位海岸边界和距离输出。
    def __init__(self, processed_root: str | Path) -> None:
        self.path_manager = PathManager(processed_root)

    # 功能说明：读取 d01 的陆海掩膜，找出相邻列之间陆海类别变化两侧的经纬度格点，并保存为距离计算的边界点文件。
    def compute_land_boundary(
        self,
        *,
        member_name: str,
        wrfout_root: str | Path | None = None,
        wrfout_file: str | Path | None = None,
        force: bool = False,
    ) -> Path:
        out_path = self.path_manager.land_boundary_path(member_name)
        if out_path.exists() and not force:
            return out_path
        source = Path(wrfout_file) if wrfout_file else None
        if source is None:
            files = WrfoutDomainReader.find_wrfout_files(wrfout_root or ".", 1)
            source = files[0] if files else None
        if source is None or not source.exists():
            raise FileNotFoundError("No d01 wrfout file found for land boundary")

        from netCDF4 import Dataset

        with Dataset(source, "r") as dataset:
            xland = np.asarray(dataset.variables["XLAND"][0], dtype=float)
            lat = np.asarray(dataset.variables["XLAT"][0], dtype=float)
            lon = np.asarray(dataset.variables["XLONG"][0], dtype=float)

        mask = np.zeros_like(xland, dtype=bool)
        mask[:, 1:] |= xland[:, 1:] != xland[:, :-1]
        mask[:, :-1] |= xland[:, 1:] != xland[:, :-1]
        points = np.column_stack((lat[mask], lon[mask]))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        with tmp.open("wb") as handle:
            np.save(handle, points)
        tmp.replace(out_path)
        return out_path

    # 功能说明：逐中心经纬度计算到最近海岸边界点的球面距离，并按中心经度是否小于海岸平均经度附加正负号，保存 NPY 和文本文件。
    def compute_distance(
        self,
        *,
        member_name: str,
        domain: int,
        center_path: str | Path | None = None,
        boundary_path: str | Path | None = None,
        force: bool = False,
    ) -> dict[str, str]:
        boundary_file = Path(boundary_path) if boundary_path else self.path_manager.land_boundary_path(member_name)
        center_file = Path(center_path) if center_path else self.path_manager.center_path(member_name, domain, "slp")
        if not boundary_file.exists():
            raise FileNotFoundError(f"Land boundary file does not exist: {boundary_file}")
        if not center_file.exists():
            raise FileNotFoundError(f"Center file does not exist: {center_file}")
        out_root = self.path_manager.land_distance_root(member_name, domain)
        npy_path = out_root / "land_distance.npy"
        txt_path = out_root / "land_distance.txt"
        if npy_path.exists() and txt_path.exists() and not force:
            return {"npy": str(npy_path), "txt": str(txt_path), "status": "skipped"}

        boundary = np.asarray(np.load(boundary_file), dtype=float)
        center = np.atleast_2d(np.loadtxt(center_file))
        rows: list[list[float]] = []
        for record in center:
            time_index = record[0]
            lat = record[3]
            lon = record[4]
            if np.isnan(lat) or np.isnan(lon):
                rows.append([time_index, np.nan])
                continue
            distances = haversine_km(lon, lat, boundary[:, 1], boundary[:, 0])
            min_distance = float(np.nanmin(distances))
            sign = 1.0
            if lon < np.nanmean(boundary[:, 1]):
                sign = -1.0
            rows.append([time_index, sign * min_distance])

        result = np.asarray(rows, dtype=float)
        out_root.mkdir(parents=True, exist_ok=True)
        tmp = npy_path.with_suffix(npy_path.suffix + ".tmp")
        with tmp.open("wb") as handle:
            np.save(handle, result)
        tmp.replace(npy_path)
        np.savetxt(txt_path, result, fmt="%.8g", header="time_index signed_distance_km")
        return {"npy": str(npy_path), "txt": str(txt_path), "status": "complete"}


# 功能说明：用球面距离公式计算经纬度点之间的地表距离（公里）。
def haversine_km(lon0: float, lat0: float, lon1: np.ndarray, lat1: np.ndarray) -> np.ndarray:
    radius_km = 6371.0
    lon0_r = np.deg2rad(lon0)
    lat0_r = np.deg2rad(lat0)
    lon1_r = np.deg2rad(lon1)
    lat1_r = np.deg2rad(lat1)
    dlon = lon1_r - lon0_r
    dlat = lat1_r - lat0_r
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat0_r) * np.cos(lat1_r) * np.sin(dlon / 2.0) ** 2
    return 2.0 * radius_km * np.arcsin(np.sqrt(a))
