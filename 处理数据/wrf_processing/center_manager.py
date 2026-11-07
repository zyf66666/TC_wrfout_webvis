from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from wrf_webvis.app.core.centers import CENTER_ALGORITHM, clear_center_cache, find_pressure_center, read_new_center_rows, read_wrf_lat_lon
from wrf_webvis.app.core.paths import PathManager, metadata_path
from wrf_webvis.app.core.schemas import VariableDimension


class CenterManager:
    # 功能说明：保存处理根目录并创建路径管理器，后续中心文件都通过它定位和写入。
    def __init__(self, processed_root: str | Path) -> None:
        self.path_manager = PathManager(processed_root)

    # 功能说明：对每个时次的海平面气压场运行 PCC/Bezier 迭代定位，保存取整后的中心格点、经纬度和该点气压。
    def compute_slp_centers(
        self,
        *,
        member_name: str,
        domain: int,
        times: list[int],
        variable: str = "slp",
        force: bool = False,
        domain_config: dict[int, dict[str, Any]] | None = None,
        algorithm_options: dict[str, Any] | None = None,
    ) -> Path:
        out_path = self.path_manager.center_path(member_name, domain, "slp")
        cache_valid = not _center_options_explicit(algorithm_options) or center_output_is_current(out_path, algorithm_options)
        rows_by_time = {} if force or not cache_valid else read_new_center_rows(out_path)
        dx_km, dy_km = _grid_spacing_km(domain, domain_config)
        changed = False
        for time_hour in times:
            existing = self.path_manager.existing_variable(
                member_name=member_name,
                domain=domain,
                variable=variable,
                time_hour=time_hour,
                dimension=VariableDimension.TWO_D,
            )
            time_key = _time_key(time_hour)
            current_row = rows_by_time.get(time_key)
            if current_row is not None and not force:
                continue
            if existing is None and current_row is None:
                raise FileNotFoundError(f"Missing pressure field for center calculation: {variable} {time_hour}h")
            if existing is not None:
                # Copy out of the memory map so Windows/HPC files are closed as
                # soon as loading finishes and can be replaced or cleaned up.
                data = np.array(np.load(existing.path, mmap_mode="r"), dtype=float, copy=True)
                y, x, value = find_pressure_center(
                    data,
                    dx_km=dx_km,
                    dy_km=dy_km,
                    **_center_algorithm_kwargs(algorithm_options),
                )
                y, x, value = _integer_center(data, y, x)
            else:
                y, x = float(current_row[1]), float(current_row[2])
                value = float(current_row[5]) if len(current_row) > 5 else np.nan
                y, x = _round_yx(y, x)
            lat, lon = self._lat_lon_from_metadata(existing, y=y, x=x) if existing is not None else (np.nan, np.nan)
            rows_by_time[time_key] = [float(time_hour), float(y), float(x), lat, lon, float(value)]
            changed = True
        if changed or not out_path.exists():
            self._write_center(out_path, [rows_by_time[key] for key in sorted(rows_by_time)])
            self._write_center_metadata(out_path, algorithm_options)
        return out_path

    # 功能说明：对指定时次和每个高度层的二维场运行 PCC/Bezier 迭代定位，并分别写入层中心文件。
    def compute_layer_centers_from_minimum(
        self,
        *,
        member_name: str,
        domain: int,
        variable: str,
        time_hour: int,
        heights_m: list[float],
        force: bool = False,
        domain_config: dict[int, dict[str, Any]] | None = None,
        algorithm_options: dict[str, Any] | None = None,
    ) -> list[Path]:
        outputs: list[Path] = []
        dx_km, dy_km = _grid_spacing_km(domain, domain_config)
        for height_m in heights_m:
            out_path = self.path_manager.layer_center_path(member_name, domain, height_m)
            cache_valid = not _center_options_explicit(algorithm_options) or center_output_is_current(out_path, algorithm_options)
            rows_by_time = {} if force or not cache_valid else read_new_center_rows(out_path)
            time_key = _time_key(time_hour)
            current_row = rows_by_time.get(time_key)
            if current_row is not None and not force:
                outputs.append(out_path)
                continue
            existing = self.path_manager.existing_variable(
                member_name=member_name,
                domain=domain,
                variable=variable,
                time_hour=time_hour,
                dimension=VariableDimension.THREE_D,
                height_m=height_m,
            )
            if existing is None and current_row is None:
                raise FileNotFoundError(f"Missing field for layer center calculation: {variable} {time_hour}h {height_m}m")
            if existing is not None:
                data = np.array(np.load(existing.path, mmap_mode="r"), dtype=float, copy=True)
                y, x, value = find_pressure_center(
                    data,
                    dx_km=dx_km,
                    dy_km=dy_km,
                    **_center_algorithm_kwargs(algorithm_options),
                )
                y, x, value = _integer_center(data, y, x)
            else:
                y, x = float(current_row[1]), float(current_row[2])
                value = float(current_row[5]) if len(current_row) > 5 else np.nan
                y, x = _round_yx(y, x)
            lat, lon = self._lat_lon_from_metadata(existing, y=y, x=x) if existing is not None else (np.nan, np.nan)
            rows_by_time[time_key] = [float(time_hour), float(y), float(x), lat, lon, float(value)]
            self._write_center(out_path, [rows_by_time[key] for key in sorted(rows_by_time)])
            self._write_center_metadata(out_path, algorithm_options)
            outputs.append(out_path)
        return outputs

    # 功能说明：把按时次排列的中心 y、x、纬度、经度和气压写入文本文件，并清除旧的读取缓存。
    def _write_center(self, path: Path, rows: list[list[float]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        np.savetxt(tmp, np.asarray(rows, dtype=float), fmt="%.8g")
        tmp.replace(path)
        clear_center_cache()

    # 功能说明：把中心定位算法版本和实际使用的算法参数写入中心文件对应的 JSON 元数据。
    def _write_center_metadata(self, path: Path, algorithm_options: dict[str, Any] | None) -> None:
        payload = {"algorithm_version": CENTER_ALGORITHM, **_center_algorithm_kwargs(algorithm_options)}
        meta_path = metadata_path(path)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = meta_path.with_suffix(meta_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(meta_path)

    # 功能说明：从当前气压场的来源元数据取 wrfout 文件和时次，在新中心格点读取经纬度；失败时返回 NaN。
    def _lat_lon_from_metadata(self, existing: Any, *, y: float, x: float) -> tuple[float, float]:
        if existing is None or existing.metadata is None or not existing.metadata.exists():
            return np.nan, np.nan
        try:
            metadata = json.loads(existing.metadata.read_text(encoding="utf-8"))
            wrfout = metadata.get("source_wrfout")
            if not wrfout:
                return np.nan, np.nan
            time_index = int(metadata.get("source_time_index", 0) or 0)
            return read_wrf_lat_lon(wrfout, y=y, x=x, time_index=time_index)
        except Exception:
            return np.nan, np.nan


# 功能说明：从指定域配置取东西和南北网格间距；缺省时使用 1 公里，供中心定位算法换算搜索半径。
def _grid_spacing_km(domain: int, domain_config: dict[int, dict[str, Any]] | None) -> tuple[float, float]:
    raw = (domain_config or {}).get(domain, {})
    dx = float(raw.get("dx_km") or 1.0)
    dy = float(raw.get("dy_km") or dx)
    return dx, dy


# 功能说明：把中心算法选项筛出为计算中心函数可接受的关键字参数。
def _center_algorithm_kwargs(options: dict[str, Any] | None) -> dict[str, Any]:
    config = options or {}
    return {
        "search_radius_km": float(config.get("center_search_radius_km", 60.0)),
        "cross_radius_grid": int(config.get("center_cross_radius_grid", 121)),
        "pcc_radius_grid": int(config.get("center_pcc_radius_grid", 44)),
        "boundary_width": int(config.get("center_boundary_width", 20)),
        "max_iterations": int(config.get("center_max_iterations", 10)),
    }


_CENTER_OPTION_KEYS = {
    "center_search_radius_km",
    "center_cross_radius_grid",
    "center_pcc_radius_grid",
    "center_boundary_width",
    "center_max_iterations",
}


# 功能说明：判断请求是否明确指定了中心算法选项，以决定能否复用旧中心文件。
def _center_options_explicit(options: dict[str, Any] | None) -> bool:
    return bool(options and _CENTER_OPTION_KEYS.intersection(options))


# 功能说明：检查中心文本及其元数据是否存在，且算法版本和五项定位参数仍匹配；本函数不检查中心文件包含哪些时次。
def center_output_is_current(path: Path, options: dict[str, Any] | None) -> bool:
    meta_path = metadata_path(path)
    if not path.exists() or not meta_path.exists():
        return False
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        expected = _center_algorithm_kwargs(options)
        return (
            metadata.get("algorithm_version") == CENTER_ALGORITHM
            and float(metadata.get("search_radius_km", np.nan)) == expected["search_radius_km"]
            and int(metadata.get("cross_radius_grid", -1)) == expected["cross_radius_grid"]
            and int(metadata.get("pcc_radius_grid", -1)) == expected["pcc_radius_grid"]
            and int(metadata.get("boundary_width", -1)) == expected["boundary_width"]
            and int(metadata.get("max_iterations", -1)) == expected["max_iterations"]
        )
    except (OSError, TypeError, ValueError):
        return False


# 功能说明：把时间值转换成可稳定比较和排序的标准键。
def _time_key(value: int | float) -> int | float:
    number = float(value)
    rounded = round(number)
    return int(rounded) if abs(number - rounded) < 1e-10 else round(number, 10)


# 功能说明：将 y、x 浮点格点坐标四舍五入为整数，供中心文本文件作为可复用的网格索引保存。
def _round_yx(y: float, x: float) -> tuple[int, int]:
    return int(round(float(y))), int(round(float(x)))


# 功能说明：将中心坐标限制在数组范围内并转换成整数网格索引。
def _integer_center(data: np.ndarray, y: float, x: float) -> tuple[int, int, float]:
    """Convert a floating-point centroid to the saved integer grid center.

    The center-finding algorithm can return a sub-grid centroid, but the rest of
    this workflow stores and reuses center files as WRF grid-point indices.
    Rounding here makes ``center.txt`` stable and easy to inspect:

        time y x lat lon value

    where ``y`` and ``x`` are integers.  ``value`` is then read from the rounded
    grid point, so it matches the saved center location.
    """
    iy, ix = _round_yx(y, x)
    iy = min(max(iy, 0), data.shape[0] - 1)
    ix = min(max(ix, 0), data.shape[1] - 1)
    return iy, ix, float(data[iy, ix])
