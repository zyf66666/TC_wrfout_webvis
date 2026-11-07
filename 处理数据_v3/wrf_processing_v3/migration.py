"""Lossless migration from processed_v2 NPY slices to v3 NetCDF files."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable

import numpy as np

from .request import Task


def migrate_tasks(
    tasks: Iterable[Task],
    *,
    source: Any,
    target: Any,
    registry: Any,
    processing_options: dict[str, Any],
    force: bool = False,
    verify: bool = True,
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {"migrated": [], "skipped": [], "verified": []}
    for task in tasks:
        source_path = source.path_for(task)
        if not source.exists(task):
            raise FileNotFoundError(source_path)
        if target.exists(task) and not force:
            result["skipped"].append(str(source_path))
            continue
        values = np.asarray(source.load(task))
        source_metadata = source.load_metadata(task)
        spec = registry.resolve(task.variable)
        merged_options = {**dict(spec.options or {}), **dict(processing_options)}
        spec = replace(spec, options=merged_options)
        metadata = {
            **source_metadata,
            "migrated_from": str(source_path),
            "migration_source_fingerprint": source.fingerprint(task),
        }
        if "radius_resolution_km" not in metadata and merged_options.get(
            "radius_resolution_km"
        ) is not None:
            metadata["radius_resolution_km"] = merged_options[
                "radius_resolution_km"
            ]
        target.save(task, values, spec, metadata=metadata)
        result["migrated"].append(str(target.path_for(task)))
        if verify:
            migrated = target.load(task)
            if values.shape != migrated.shape or values.dtype != migrated.dtype:
                raise ValueError(f"迁移验证 shape/dtype 不一致：{task}")
            if not np.array_equal(values, migrated, equal_nan=True):
                raise ValueError(f"迁移验证数值不一致：{task}")
            result["verified"].append(str(target.path_for(task)))
    return result
