"""Per-slice v2 NPY versus v3 NetCDF comparison reports."""

from __future__ import annotations

from typing import Any

import numpy as np
from netCDF4 import Dataset

from .locking import file_lock
from .request import Task


def compare_task(
    task: Task,
    *,
    source: Any,
    target: Any,
    expected_units: str | None = None,
    expected_radius_resolution_km: float | None = None,
) -> dict[str, Any]:
    old = np.asarray(source.load(task))
    new = np.asarray(target.load(task))
    old_nan = np.isnan(old) if np.issubdtype(old.dtype, np.floating) else np.zeros(old.shape, bool)
    new_nan = np.isnan(new) if np.issubdtype(new.dtype, np.floating) else np.zeros(new.shape, bool)
    same_shape = old.shape == new.shape
    finite = np.isfinite(old) & np.isfinite(new) if same_shape else np.asarray([], bool)
    maximum = (
        float(np.max(np.abs(old[finite].astype(np.float64) - new[finite].astype(np.float64))))
        if finite.any()
        else 0.0
    )
    source_metadata = source.load_metadata(task)
    target_metadata = target.load_metadata(task)
    old_units = source_metadata.get("units") or expected_units
    new_units = target_metadata.get("units")
    result: dict[str, Any] = {
        "task": str(task),
        "source": str(source.path_for(task)),
        "target": str(target.path_for(task)),
        "source_shape": tuple(old.shape),
        "target_shape": tuple(new.shape),
        "shape_match": same_shape,
        "source_dtype": str(old.dtype),
        "target_dtype": str(new.dtype),
        "dtype_match": old.dtype == new.dtype,
        "source_nan_count": int(old_nan.sum()),
        "target_nan_count": int(new_nan.sum()),
        "nan_mask_match": same_shape and np.array_equal(old_nan, new_nan),
        "values_match": same_shape and np.array_equal(old, new, equal_nan=True),
        "max_abs_difference": maximum,
        "source_units": old_units,
        "target_units": new_units,
        "units_match": old_units == new_units,
    }
    path = target.path_for(task)
    with file_lock(path, exclusive=False, timeout_seconds=target.lock_timeout_seconds):
        with Dataset(path) as dataset:
            dataset.set_auto_mask(False)
            times = np.asarray(dataset["time_minutes"][:], dtype=np.int64)
            time_match = int(np.sum(times == task.time_minutes)) == 1
            height_match = True
            if "height_m" in dataset.variables:
                if task.height_m == "lowest":
                    group = dataset.groups.get("lowest")
                    height_match = (
                        group is not None
                        and getattr(group, "model_level_index", None) == 0
                        and "height" not in group["data"].dimensions
                    )
                else:
                    heights = np.asarray(dataset["height_m"][:], dtype=np.int32)
                    height_match = (
                        isinstance(task.height_m, int)
                        and int(np.sum(heights == task.height_m)) == 1
                    )
            spatial_match = True
            if "y_index" in dataset.variables:
                y = np.asarray(dataset["y_index"][:], dtype=np.int32)
                x = np.asarray(dataset["x_index"][:], dtype=np.int32)
                spatial_match = np.array_equal(y, np.arange(old.shape[-2])) and np.array_equal(
                    x, np.arange(old.shape[-1])
                )
                result.update(
                    {
                        "y_coordinate_match": np.array_equal(y, np.arange(old.shape[-2])),
                        "x_coordinate_match": np.array_equal(x, np.arange(old.shape[-1])),
                    }
                )
            result.update(
                {
                    "time_coordinate_match": time_match,
                    "height_coordinate_match": height_match,
                }
            )
            if "radius_km" in dataset.variables:
                azimuth = np.asarray(dataset["azimuth_deg"][:], dtype=np.float32)
                azimuth_match = np.array_equal(
                    azimuth, np.arange(old.shape[-2], dtype=np.float32)
                )
                radius = np.asarray(dataset["radius_km"][:], dtype=np.float32)
                resolution = source_metadata.get(
                    "radius_resolution_km", expected_radius_resolution_km
                )
                expected = (
                    np.arange(radius.size, dtype=np.float32) * np.float32(resolution)
                    if resolution is not None
                    else radius
                )
                result.update(
                    {
                        "azimuth_match": azimuth_match,
                        "radius_match": np.array_equal(radius, expected),
                        "radius_count": int(radius.size),
                        "radius_min_km": float(radius[0]) if radius.size else None,
                        "radius_max_km": float(radius[-1]) if radius.size else None,
                    }
                )
                spatial_match = azimuth_match and bool(result["radius_match"])
            result["spatial_coordinates_match"] = spatial_match
            result["coordinates_match"] = time_match and height_match and spatial_match
    return result
