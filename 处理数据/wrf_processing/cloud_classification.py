from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.schemas import ProcessingRequest, VariableDimension
from .processing_utils import atomic_save, build_processing_metadata


CLOUD_CLASSIFICATION_VARIABLES = {"classification", "cloud_classification"}
CLOUD_CLASSIFICATION_HEIGHTS_M = [900, 1000, 1250, 1500, 1750, 2000, 3000, 4000, 8000]

CLASS_LABELS = {
    0: "no_rain",
    1: "other",
    2: "stratiform",
    3: "shallow_convective",
    4: "moderate_convective",
    5: "deep_convective",
}


# 功能说明：读取指定时刻多个高度的反射率和垂直风，按阈值为每个水平格点分配云雨类别，保存一张类别图及分类依据。
def process_cloud_classification(
    *,
    request: ProcessingRequest,
    time_hour: int,
    dtype: str | None = None,
    source_file: Path | None = None,
    source_time_index: int | None = None,
) -> str:
    path_manager = PathManager(request.processed_root)
    out_path = path_manager.variable_path(
        member_name=request.member_name,
        domain=request.domain,
        variable="classification",
        time_hour=time_hour,
        dimension=VariableDimension.TWO_D,
    )
    if out_path.exists() and not request.force:
        return str(out_path)

    dbz_stack, dbz_paths = load_per_height_stack(
        path_manager=path_manager,
        member_name=request.member_name,
        domain=request.domain,
        variable="dbz",
        time_hour=time_hour,
        heights_m=CLOUD_CLASSIFICATION_HEIGHTS_M,
    )
    wa_stack, wa_paths = load_per_height_stack(
        path_manager=path_manager,
        member_name=request.member_name,
        domain=request.domain,
        variable="wa",
        time_hour=time_hour,
        heights_m=CLOUD_CLASSIFICATION_HEIGHTS_M,
    )
    class_map = classify_cloud_types(dbz_stack, wa_stack, CLOUD_CLASSIFICATION_HEIGHTS_M)
    atomic_save(out_path, class_map.astype(np.int8, copy=False))

    source_paths = dbz_paths + wa_paths
    metadata = build_processing_metadata(
        request=request,
        variable="classification",
        source_file=source_file or source_paths[0],
        time_hour=time_hour,
        height_m=None,
        source_time_index=source_time_index,
    )
    metadata.update(
        {
            "derived_from": [str(path) for path in source_paths],
            "classification_labels": CLASS_LABELS,
            "classification_heights_m": CLOUD_CLASSIFICATION_HEIGHTS_M,
            "algorithm": "dbz_wa_thresholds",
            "thresholds": {
                "convective": "dbz_900m > 46 or dbz_3000m > 46 or mean_wa_900_2000m > 0.5",
                "stratiform": "not convective and dbz_3000m > 20",
                "other": "not convective and not stratiform and dbz_3000m > 0",
                "deep_convective": "convective and dbz_8000m > 20",
                "moderate_convective": "convective and not deep_convective and dbz_4000m >= 20",
                "shallow_convective": "convective and not deep/moderate_convective and dbz_2000m < 20",
            },
        }
    )
    path_manager.write_metadata(out_path, metadata)
    return str(out_path)


# 功能说明：读取多个高度层的云分类输入，并沿高度堆叠成统一数组。
def load_per_height_stack(
    *,
    path_manager: PathManager,
    member_name: str,
    domain: int,
    variable: str,
    time_hour: int,
    heights_m: list[float],
) -> tuple[np.ndarray, list[Path]]:
    arrays: list[np.ndarray] = []
    paths: list[Path] = []
    for height_m in heights_m:
        existing = path_manager.existing_variable(
            member_name=member_name,
            domain=domain,
            variable=variable,
            time_hour=time_hour,
            dimension=VariableDimension.THREE_D,
            height_m=height_m,
        )
        if existing is None:
            raise FileNotFoundError(f"Missing {variable} dependency for cloud classification: {time_hour}h {height_m:g}m")
        arrays.append(np.asarray(np.load(existing.path, mmap_mode="r"), dtype=float))
        paths.append(existing.path)
    shapes = {array.shape for array in arrays}
    if len(shapes) != 1:
        raise ValueError(f"{variable} cloud-classification dependency shapes differ: {sorted(shapes)}")
    return np.stack(arrays, axis=0), paths


# 功能说明：用低层反射率和上升速度先判断是否对流，再用2、4、8公里反射率分出浅、中、深对流；返回0至5的类别编号二维图。
def classify_cloud_types(dbz: np.ndarray, wa: np.ndarray, heights_m: list[float]) -> np.ndarray:
    """Classify cloud/precipitation type from reflectivity and vertical velocity.

    Class values match the original notebook:
    0 no rain, 1 other, 2 stratiform, 3 shallow convective,
    4 moderate convective, 5 deep convective.
    """
    data = np.asarray(dbz, dtype=float)
    data_w = np.asarray(wa, dtype=float)
    heights = np.asarray(heights_m, dtype=float)
    if data.shape != data_w.shape:
        raise ValueError(f"dbz/wa shapes differ: {data.shape} vs {data_w.shape}")
    if data.ndim != 3:
        raise ValueError(f"cloud classification expects (height, y, x), got {data.shape}")
    if data.shape[0] != heights.size:
        raise ValueError(f"height count {heights.size} does not match data levels {data.shape[0]}")

    idx_900 = _exact_height_index(heights, 900)
    idx_2000 = _exact_height_index(heights, 2000)
    idx_3000 = _exact_height_index(heights, 3000)
    idx_4000 = _nearest_height_index(heights, 4000)
    idx_8000 = _nearest_height_index(heights, 8000)

    low_w_mask = (heights >= 900) & (heights <= 2000)
    if not low_w_mask.any():
        raise ValueError("cloud classification requires at least one wa level between 900m and 2000m")
    w_avg_low = np.nanmean(data_w[low_w_mask, :, :], axis=0)

    z_900 = np.nan_to_num(data[idx_900, :, :], nan=-999)
    z_2000 = np.nan_to_num(data[idx_2000, :, :], nan=-999)
    z_3000 = np.nan_to_num(data[idx_3000, :, :], nan=-999)
    z_4000 = np.nan_to_num(data[idx_4000, :, :], nan=-999)
    z_8000 = np.nan_to_num(data[idx_8000, :, :], nan=-999)
    w_avg = np.nan_to_num(w_avg_low, nan=-999)

    convective = (z_900 > 46.0) | (z_3000 > 46.0) | (w_avg > 0.5)
    stratiform = (~convective) & (z_3000 > 20.0)
    other = (~convective) & (~stratiform) & (z_3000 > 0.0)

    class_map = np.zeros(z_3000.shape, dtype=np.int8)
    class_map[other] = 1
    class_map[stratiform] = 2

    deep_convective = convective & (z_8000 > 20.0)
    moderate_convective = convective & (~deep_convective) & (z_4000 >= 20.0)
    shallow_convective = convective & (~deep_convective) & (~moderate_convective) & (z_2000 < 20.0)

    class_map[deep_convective] = 5
    class_map[moderate_convective] = 4
    class_map[shallow_convective] = 3
    return class_map


# 功能说明：在高度列表中查找与目标高度相符的层，允许很小的数值误差；没有相符层时提示缺少所需高度。
def _exact_height_index(heights: np.ndarray, target_m: float) -> int:
    matches = np.where(np.isclose(heights, target_m))[0]
    if matches.size == 0:
        raise ValueError(f"cloud classification requires exact {target_m:g}m level")
    return int(matches[0])


# 功能说明：在高度数组中找到距离目标高度最近的层索引。
def _nearest_height_index(heights: np.ndarray, target_m: float) -> int:
    return int(np.argmin(np.abs(heights - float(target_m))))
