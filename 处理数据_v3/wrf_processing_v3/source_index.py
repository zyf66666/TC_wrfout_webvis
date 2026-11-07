"""Index WRF records by integer minutes after START_DATE."""

from __future__ import annotations

from datetime import datetime
import glob
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def _default_cache_dir() -> Path:
    configured = os.environ.get("WRF_PROCESSING_V3_SOURCE_CACHE")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "wrf_processing_v3" / "source_index"


class SourceIndex:
    def __init__(self, request: Any) -> None:
        self.request = request
        self.source_root = Path(request.source_root)
        self.options = dict(request.options or {})
        self._cache: dict[tuple[str, int], dict[int, tuple[Path, int]]] = {}
        self._origins: dict[tuple[str, int], datetime] = {}

    def resolve(
        self, experiment: str, domain: int, time_minutes: int
    ) -> tuple[Path, int]:
        if not isinstance(time_minutes, int) or isinstance(time_minutes, bool):
            raise ValueError("time_minutes 必须是整数分钟")
        key = (experiment, int(domain))
        if key not in self._cache:
            self._cache[key] = self._scan(*key)
        try:
            return self._cache[key][time_minutes]
        except KeyError as exc:
            available = ", ".join(str(value) for value in sorted(self._cache[key]))
            raise KeyError(
                f"source time_minutes={time_minutes} not found for "
                f"experiment={experiment!r}, domain={domain}; available=[{available}]"
            ) from exc

    def time_origin(self, experiment: str, domain: int) -> str:
        key = (experiment, int(domain))
        if key not in self._cache:
            self._cache[key] = self._scan(*key)
        return self._origins[key].strftime("%Y-%m-%d_%H:%M:%S")

    def _scan(self, experiment: str, domain: int) -> dict[int, tuple[Path, int]]:
        from netCDF4 import Dataset, chartostring

        paths = self._paths(experiment, domain)
        if not paths:
            raise FileNotFoundError(
                f"No wrfout files found for experiment={experiment!r}, domain={domain}"
            )
        records: list[tuple[datetime, Path, int]] = []
        start_dates: list[datetime] = []
        simulation_starts: list[datetime] = []
        cache_path, cache = self._load_file_cache(experiment, domain)
        fresh: dict[str, list] = {}
        for path in paths:
            stat = path.stat()
            cached = cache.get(str(path))
            if cached and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns:
                entry = cached
            else:
                with Dataset(path) as dataset:
                    if "Times" not in dataset.variables:
                        raise ValueError(f"WRF source {path} has no Times variable")
                    raw = dataset.variables["Times"][:]
                    texts = chartostring(raw) if raw.dtype.kind in "SU" else raw
                    entry = [
                        stat.st_size, stat.st_mtime_ns,
                        [str(value).strip() for value in texts],
                        str(getattr(dataset, "START_DATE", "") or ""),
                        str(getattr(dataset, "SIMULATION_START_DATE", "") or ""),
                    ]
            fresh[str(path)] = entry
            _, _, texts, start, simulation_start = entry
            times = [self._parse_time(value, path) for value in texts]
            records.extend((value, path, index) for index, value in enumerate(times))
            if start:
                start_dates.append(self._parse_time(start, path))
            if simulation_start:
                simulation_starts.append(self._parse_time(simulation_start, path))
        if fresh != cache:
            self._save_file_cache(cache_path, fresh)
        origin = self._configured_origin(experiment)
        if origin is None:
            candidates = start_dates or simulation_starts or [value[0] for value in records]
            origin = min(candidates)
        key = (experiment, int(domain))
        self._origins[key] = origin
        result: dict[int, tuple[Path, int]] = {}
        for timestamp, path, record_index in records:
            seconds = (timestamp - origin).total_seconds()
            minute = round(seconds / 60.0)
            if abs(seconds - minute * 60) > 1e-7:
                raise ValueError(
                    f"源时间 {timestamp} 相对 START_DATE 不是整数分钟：{path}"
                )
            if minute in result:
                raise ValueError(
                    f"重复源时间 {minute} 分钟：{result[minute][0]} 与 {path}"
                )
            result[minute] = (path, record_index)
        return result

    def _load_file_cache(self, experiment: str, domain: int) -> tuple[Path | None, dict[str, list]]:
        """Per-file Times cache keyed by path, size and mtime (survives restarts)."""
        if self.options.get("source_index_cache", True) is False:
            return None, {}
        key = hashlib.sha256(
            f"{self.source_root.resolve()}|{self.options.get('wrfout_template', '')}|{experiment}|{int(domain)}".encode()
        ).hexdigest()[:20]
        path = _default_cache_dir() / f"{experiment}_d{int(domain):02d}_{key}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return path, value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return path, {}

    @staticmethod
    def _save_file_cache(path: Path | None, value: dict[str, list]) -> None:
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            pass  # 缓存只是加速，写不进去时照常运行

    def _configured_origin(self, experiment: str) -> datetime | None:
        configured = self.options.get("time_origin")
        if isinstance(configured, dict):
            configured = configured.get(experiment)
        return (
            self._parse_time(str(configured), Path("configured_time_origin"))
            if configured
            else None
        )

    def _paths(self, experiment: str, domain: int) -> list[Path]:
        domain_name = f"d{domain:02d}"
        template = self.options.get("wrfout_template")
        if template:
            pattern = str(template).format(
                root=self.source_root,
                experiment=experiment,
                domain=domain_name,
            )
            candidates = (
                [Path(value) for value in glob.glob(pattern, recursive=True)]
                if any(character in pattern for character in "*?[")
                else [Path(pattern)]
            )
            paths: list[Path] = []
            for candidate in candidates:
                if candidate.is_dir():
                    paths.extend(candidate.glob(f"**/wrfout_{domain_name}*"))
                elif candidate.name.startswith(f"wrfout_{domain_name}"):
                    paths.append(candidate)
        else:
            paths = list(
                (self.source_root / experiment).glob(f"**/wrfout_{domain_name}*")
            )
        return sorted({path for path in paths if path.is_file()})

    @staticmethod
    def _parse_time(value: str, path: Path) -> datetime:
        try:
            return datetime.strptime(value.strip().replace("T", "_"), "%Y-%m-%d_%H:%M:%S")
        except ValueError as exc:
            raise ValueError(f"Invalid WRF time {value!r} in {path}") from exc
