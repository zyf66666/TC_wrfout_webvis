"""Read-only adapter for processed_v2 NPY files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .request import Task


class NpyStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for(self, task: Task) -> Path:
        base = (
            self.root
            / task.experiment
            / f"d{int(task.domain):02d}"
            / task.variable
            / self.time_name(task.time_minutes)
        )
        if task.height_m is None:
            return Path(f"{base}.npy")
        height = "lowest" if task.height_m == "lowest" else f"{int(task.height_m)}m"
        return base / f"{height}.npy"

    @staticmethod
    def time_name(minutes: int) -> str:
        if not isinstance(minutes, int) or isinstance(minutes, bool):
            raise ValueError("time_minutes 必须是整数分钟")
        sign = "-" if minutes < 0 else ""
        hours, remainder = divmod(abs(minutes), 60)
        return f"{sign}{hours}h" if remainder == 0 else f"{sign}{hours}h{remainder}min"

    def exists(self, task: Task) -> bool:
        return self.path_for(task).is_file()

    def load(self, task: Task) -> np.ndarray:
        values = np.load(self.path_for(task), allow_pickle=False)
        values.setflags(write=False)
        return values

    def load_metadata(self, task: Task) -> dict[str, Any]:
        path = self.path_for(task)
        candidates = [
            Path(f"{path}.json"),
            path.with_suffix(".json"),
            path.parent.parent / "source_metadata.json"
            if task.height_m is not None
            else path.parent / "source_metadata.json",
        ]
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    def fingerprint(self, task: Task) -> dict[str, Any]:
        path = self.path_for(task)
        try:
            stat = path.stat()
        except FileNotFoundError:
            return {"data": None, "metadata": None}
        metadata_path = Path(f"{path}.json")
        metadata = None
        if metadata_path.is_file():
            metadata_stat = metadata_path.stat()
            metadata = {
                "size": metadata_stat.st_size,
                "mtime_ns": metadata_stat.st_mtime_ns,
            }
        return {
            "data": {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
            "metadata": metadata,
        }

    def save(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("NpyStorage 是只读兼容层")
