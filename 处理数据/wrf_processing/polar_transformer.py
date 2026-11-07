from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from wrf_webvis.app.core.centers import load_center_yx
from wrf_webvis.app.core.paths import PathManager, metadata_path
from wrf_webvis.app.core.registry import DEFAULT_DOMAIN_CONFIG
from wrf_webvis.app.core.schemas import VariableDimension


POLAR_ALGORITHM_VERSION = "fixed_radius_linear_v1"


class PolarTransformer:
    # 功能说明：保存处理根目录并创建路径管理器，用于读取中心文件和写入极坐标结果。
    def __init__(self, processed_root: str | Path) -> None:
        self.path_manager = PathManager(processed_root)

    # 功能说明：按变量和中心模式生成统一的极坐标输出名称。
    @staticmethod
    def polar_variable_name(variable: str, center_mode: str) -> str:
        if center_mode == "slp":
            return f"{variable}_polar"
        if center_mode == "layer":
            return f"{variable}1_polar"
        raise ValueError("center_mode must be 'slp' or 'layer'")

    # 功能说明：检查一个二维源场和对应极坐标文件是否存在，再用当前中心、半径和分辨率核对输出元数据及数组形状。
    def is_2d_output_current(
        self,
        *,
        member_name: str,
        domain: int,
        variable: str,
        time_hour: int | float,
        center_mode: str = "slp",
        radius_km: float | None = None,
        radius_resolution_km: float | None = None,
        domain_config: dict[int, dict[str, Any]] | None = None,
    ) -> bool:
        return self._output_is_current(
            member_name=member_name,
            domain=domain,
            variable=variable,
            time_hour=time_hour,
            dimension=VariableDimension.TWO_D,
            center_mode=center_mode,
            radius_km=radius_km,
            radius_resolution_km=radius_resolution_km,
            domain_config=domain_config,
        )

    # 功能说明：对请求的每个高度执行同样的源场、中心和极坐标输出核对；任一高度不满足就返回否。
    def are_3d_outputs_current(
        self,
        *,
        member_name: str,
        domain: int,
        variable: str,
        time_hour: int | float,
        heights_m: list[float],
        center_mode: str = "slp",
        radius_km: float | None = None,
        radius_resolution_km: float | None = None,
        domain_config: dict[int, dict[str, Any]] | None = None,
    ) -> bool:
        return bool(heights_m) and all(
            self._output_is_current(
                member_name=member_name,
                domain=domain,
                variable=variable,
                time_hour=time_hour,
                dimension=VariableDimension.THREE_D,
                height_m=height_m,
                center_mode=center_mode,
                radius_km=radius_km,
                radius_resolution_km=radius_resolution_km,
                domain_config=domain_config,
            )
            for height_m in heights_m
        )

    # 功能说明：加载源场形状并读取当前中心，重新计算极坐标半径设置，再检查已有输出的元数据和数组形状能否复用。
    def _output_is_current(
        self,
        *,
        member_name: str,
        domain: int,
        variable: str,
        time_hour: int | float,
        dimension: VariableDimension,
        height_m: float | None = None,
        center_mode: str,
        radius_km: float | None,
        radius_resolution_km: float | None,
        domain_config: dict[int, dict[str, Any]] | None,
    ) -> bool:
        source = self.path_manager.existing_variable(
            member_name=member_name,
            domain=domain,
            variable=variable,
            time_hour=time_hour,
            dimension=dimension,
            height_m=height_m,
        )
        output = self.path_manager.existing_variable(
            member_name=member_name,
            domain=domain,
            variable=self.polar_variable_name(variable, center_mode),
            time_hour=time_hour,
            dimension=dimension,
            height_m=height_m,
        )
        if source is None or output is None:
            return False
        try:
            center_y, center_x = self._center(
                member_name,
                domain,
                time_hour,
                center_mode,
                height_m,
            )
            source_data = np.load(source.path, mmap_mode="r")
            source_shape = tuple(source_data.shape)
            del source_data
            geometry = polar_geometry(
                source_shape,
                center_y=center_y,
                center_x=center_x,
                domain=domain,
                radius_km=radius_km,
                radius_resolution_km=radius_resolution_km,
                domain_config=domain_config,
            )
            return polar_output_is_current(
                output.path,
                geometry=geometry,
                source_path=source.path,
                center_mode=center_mode,
            )
        except (FileNotFoundError, IndexError, OSError, TypeError, ValueError):
            return False

    # 功能说明：读取一个时次的二维处理场和中心，采样成 360 方位的半径数组；已有输出通过元数据核对时直接复用。
    def transform_2d(
        self,
        *,
        member_name: str,
        domain: int,
        variable: str,
        time_hour: int | float,
        center_mode: str = "slp",
        radius_km: float | None = None,
        radius_resolution_km: float | None = None,
        domain_config: dict[int, dict[str, Any]] | None = None,
        force: bool = False,
    ) -> Path:
        source = self.path_manager.existing_variable(
            member_name=member_name,
            domain=domain,
            variable=variable,
            time_hour=time_hour,
            dimension=VariableDimension.TWO_D,
        )
        if source is None:
            raise FileNotFoundError(f"Missing source variable for polar transform: {variable} {time_hour}h")

        out_var = self.polar_variable_name(variable, center_mode)
        out_path = self.path_manager.variable_path(
            member_name=member_name,
            domain=domain,
            variable=out_var,
            time_hour=time_hour,
            dimension=VariableDimension.TWO_D,
        )
        center_y, center_x = self._center(member_name, domain, time_hour, center_mode)
        data = np.load(source.path, mmap_mode="r")
        geometry = polar_geometry(
            data.shape, center_y=center_y, center_x=center_x, domain=domain,
            radius_km=radius_km, radius_resolution_km=radius_resolution_km,
            domain_config=domain_config,
        )
        if out_path.exists() and not force and polar_output_is_current(
            out_path, geometry=geometry, source_path=source.path, center_mode=center_mode
        ):
            return out_path
        polar, metadata = self._to_polar(data, center_y, center_x, domain, radius_km, radius_resolution_km, domain_config)
        self._atomic_save(out_path, polar.astype(np.float32, copy=False))
        metadata.update(
            {
                "source": str(source.path),
                "source_mtime_ns": source.path.stat().st_mtime_ns,
                "center_mode": center_mode,
            }
        )
        self.path_manager.write_metadata(out_path, metadata)
        return out_path

    # 功能说明：逐高度读取已处理的二维切片和对应中心，将每片转换成 360 方位极坐标数组并写盘或复用有效输出。
    def transform_3d_per_height(
        self,
        *,
        member_name: str,
        domain: int,
        variable: str,
        time_hour: int | float,
        heights_m: list[float],
        center_mode: str = "slp",
        radius_km: float | None = None,
        radius_resolution_km: float | None = None,
        domain_config: dict[int, dict[str, Any]] | None = None,
        force: bool = False,
        source_variable: str | None = None,
    ) -> list[Path]:
        out_var = self.polar_variable_name(variable, center_mode)
        outputs: list[Path] = []
        for height_m in heights_m:
            source = self.path_manager.existing_variable(
                member_name=member_name,
                domain=domain,
                variable=source_variable or variable,
                time_hour=time_hour,
                dimension=VariableDimension.THREE_D,
                height_m=height_m,
            )
            if source is None:
                raise FileNotFoundError(f"Missing source variable for polar transform: {source_variable or variable} {time_hour}h {height_m}m")
            out_path = self.path_manager.variable_path(
                member_name=member_name,
                domain=domain,
                variable=out_var,
                time_hour=time_hour,
                dimension=VariableDimension.THREE_D,
                height_m=height_m,
            )
            center_y, center_x = self._center(member_name, domain, time_hour, center_mode, height_m)
            data = np.load(source.path, mmap_mode="r")
            geometry = polar_geometry(
                data.shape, center_y=center_y, center_x=center_x, domain=domain,
                radius_km=radius_km, radius_resolution_km=radius_resolution_km,
                domain_config=domain_config,
            )
            if out_path.exists() and not force and polar_output_is_current(
                out_path, geometry=geometry, source_path=source.path, center_mode=center_mode
            ):
                outputs.append(out_path)
                continue
            polar, metadata = self._to_polar(data, center_y, center_x, domain, radius_km, radius_resolution_km, domain_config)
            self._atomic_save(out_path, polar.astype(np.float32, copy=False))
            metadata.update({"source": str(source.path), "center_mode": center_mode, "height_m": height_m})
            self.path_manager.write_metadata(
                out_path,
                metadata,
            )
            outputs.append(out_path)
        return outputs

    # 功能说明：按中心模式选择海平面或指定高度层的中心文件，并读取该时次 y、x；文件缺失会直接报错，不会回退。
    def _center(
        self,
        member_name: str,
        domain: int,
        time_hour: int | float,
        center_mode: str,
        height_m: float | None = None,
    ) -> tuple[float, float]:
        if center_mode == "layer" and height_m is not None:
            path = self.path_manager.layer_center_path(member_name, domain, height_m)
        else:
            path = self.path_manager.center_path(member_name, domain, "slp")
        if not path.exists():
            raise FileNotFoundError(f"Center file does not exist: {path}")
        return load_center_yx(path, time_hour)

    # 功能说明：按半径和方位角坐标从原二维网格双线性采样，返回风暴中心坐标系的场。
    def _to_polar(
        self,
        data: np.ndarray,
        center_y: float,
        center_x: float,
        domain: int,
        radius_km: float | None,
        radius_resolution_km: float | None,
        domain_config: dict[int, dict[str, Any]] | None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        geometry = polar_geometry(
            data.shape,
            center_y=center_y,
            center_x=center_x,
            domain=domain,
            radius_km=radius_km,
            radius_resolution_km=radius_resolution_km,
            domain_config=domain_config,
        )
        dx_km = geometry["dx_km"]
        dy_km = geometry["dy_km"]
        resolution = geometry["radius_resolution_km"]
        radius_values = np.arange(geometry["radial_count"], dtype=float) * resolution
        angles = np.arange(360.0)
        theta = np.deg2rad(angles)[:, None]
        radius_grid_km = radius_values[None, :]
        yy = center_y + (radius_grid_km * np.sin(theta)) / dy_km
        xx = center_x + (radius_grid_km * np.cos(theta)) / dx_km
        try:
            from scipy.ndimage import map_coordinates

            coords = np.vstack([yy.ravel(), xx.ravel()])
            values = map_coordinates(np.asarray(data, dtype=float), coords, order=1, mode="nearest")
            polar = values.reshape(angles.size, radius_values.size)
        except ImportError:
            polar = bilinear_sample(np.asarray(data, dtype=float), yy, xx)
        metadata = {
            "algorithm_version": POLAR_ALGORITHM_VERSION,
            "source_shape": list(np.asarray(data).shape),
            "center_y": float(center_y),
            "center_x": float(center_x),
            "dx_km": dx_km,
            "dy_km": dy_km,
            "requested_radius_km": geometry["requested_radius_km"],
            "radius_km": geometry["radius_km"],
            "safe_radius_km": geometry["safe_radius_km"],
            "radius_resolution_km": resolution,
            "radial_count": geometry["radial_count"],
            "angle_count": int(angles.size),
            "angle_resolution_deg": 1.0,
        }
        return polar, metadata

    # 功能说明：用临时文件保存极坐标数组并原子替换目标文件，防止并发写入产生半文件。
    def _atomic_save(self, path: Path, array: np.ndarray) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("wb") as handle:
            np.save(handle, array)
        tmp.replace(path)


# 功能说明：在没有 SciPy 时，将坐标夹在网格内并用上下左右四个格点的线性权重估计采样值。
def bilinear_sample(data: np.ndarray, yy: np.ndarray, xx: np.ndarray) -> np.ndarray:
    max_y, max_x = data.shape[0] - 1, data.shape[1] - 1
    y = np.clip(yy, 0, max_y)
    x = np.clip(xx, 0, max_x)
    y0 = np.floor(y).astype(int)
    x0 = np.floor(x).astype(int)
    y1 = np.clip(y0 + 1, 0, max_y)
    x1 = np.clip(x0 + 1, 0, max_x)
    wy = y - y0
    wx = x - x0
    top = data[y0, x0] * (1.0 - wx) + data[y0, x1] * wx
    bottom = data[y1, x0] * (1.0 - wx) + data[y1, x1] * wx
    return top * (1.0 - wy) + bottom * wy


# 功能说明：根据源场形状、中心和网格间距检查安全半径，计算有效的径向格点数，并返回极坐标转换所需的几何参数字典。
def polar_geometry(
    shape: tuple[int, ...],
    *,
    center_y: float,
    center_x: float,
    domain: int,
    radius_km: float | None,
    radius_resolution_km: float | None,
    domain_config: dict[int, dict[str, Any]] | None,
) -> dict[str, Any]:
    config = (domain_config or DEFAULT_DOMAIN_CONFIG).get(domain, {})
    dx_km = float(config.get("dx_km") or 1.0)
    dy_km = float(config.get("dy_km") or dx_km)
    resolution = float(radius_resolution_km if radius_resolution_km is not None else dx_km)
    if resolution <= 0:
        raise ValueError(f"极坐标径向分辨率必须大于 0 km，当前为 {resolution:g}")
    safe_radius = default_polar_radius_km(
        shape, center_y=center_y, center_x=center_x, dx_km=dx_km, dy_km=dy_km
    )
    requested = safe_radius if radius_km is None else float(radius_km)
    if requested < 0:
        raise ValueError(f"极坐标最大半径不能为负数，当前为 {requested:g} km")
    if requested > safe_radius + 1.0e-8:
        raise ValueError(
            f"请求极坐标半径 {requested:g} km 超出当前中心到网格边界的安全半径 "
            f"{safe_radius:g} km；请减小 POLAR_MAX_RADIUS_KM"
        )
    radial_count = int(np.floor(requested / resolution + 1.0e-10)) + 1
    effective_radius = float((radial_count - 1) * resolution)
    return {
        "algorithm_version": POLAR_ALGORITHM_VERSION,
        "source_shape": [int(value) for value in shape],
        "center_y": float(center_y),
        "center_x": float(center_x),
        "dx_km": dx_km,
        "dy_km": dy_km,
        "requested_radius_km": None if radius_km is None else float(radius_km),
        "radius_km": effective_radius,
        "safe_radius_km": float(safe_radius),
        "radius_resolution_km": resolution,
        "radial_count": radial_count,
        "angle_count": 360,
    }


# 功能说明：核对算法版本、源文件修改时间、中心方式、中心坐标和径向设置，并检查数组为 360×径向格点数后决定能否复用。
def polar_output_is_current(
    path: Path,
    *,
    geometry: dict[str, Any],
    source_path: Path,
    center_mode: str,
) -> bool:
    meta_path = metadata_path(path)
    if not meta_path.exists():
        return False
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        if not (
            metadata.get("algorithm_version") == POLAR_ALGORITHM_VERSION
            and metadata.get("source") == str(source_path)
            and int(metadata.get("source_mtime_ns", -1)) == source_path.stat().st_mtime_ns
            and metadata.get("center_mode") == center_mode
            and metadata.get("requested_radius_km") == geometry["requested_radius_km"]
            and int(metadata.get("radial_count", -1)) == geometry["radial_count"]
            and float(metadata.get("radius_km", np.nan)) == geometry["radius_km"]
            and float(metadata.get("radius_resolution_km", np.nan)) == geometry["radius_resolution_km"]
            and float(metadata.get("center_y", np.nan)) == geometry["center_y"]
            and float(metadata.get("center_x", np.nan)) == geometry["center_x"]
        ):
            return False
        stored = np.load(path, mmap_mode="r")
        stored_shape = tuple(stored.shape)
        del stored
        return stored_shape == (geometry["angle_count"], geometry["radial_count"])
    except (OSError, TypeError, ValueError):
        return False


# 功能说明：计算中心到上下左右网格边界的物理距离，返回其中最小的安全极坐标半径。
def default_polar_radius_km(
    shape: tuple[int, ...],
    *,
    center_y: float,
    center_x: float,
    dx_km: float,
    dy_km: float,
) -> float:
    ny, nx = int(shape[-2]), int(shape[-1])
    distances = [
        float(center_y) * float(dy_km),
        float(max(ny - 1 - center_y, 0.0)) * float(dy_km),
        float(center_x) * float(dx_km),
        float(max(nx - 1 - center_x, 0.0)) * float(dx_km),
    ]
    positive = [value for value in distances if value > 0.0]
    if not positive:
        return min(ny * float(dy_km), nx * float(dx_km)) / 2.0
    return min(positive)
