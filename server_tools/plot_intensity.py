"""Standalone intensity time-series plots for processed v3 NetCDF results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import csv
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from netCDF4 import Dataset

_V3 = Path(__file__).resolve().parents[1] / "处理数据_v3"
if str(_V3) not in sys.path:
    sys.path.insert(0, str(_V3))
from wrf_processing_v3.locking import file_lock


@dataclass
class IntensityData:
    times: np.ndarray
    wind: dict[int, float]
    slp: dict[int, float]
    center_y: dict[int, float]
    center_x: dict[int, float]
    latitude: dict[int, float]
    longitude: dict[int, float]
    origin: str


def _read(path: Path, kind: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
    if not path.is_file():
        return np.array([], dtype=np.int64), np.array([]), np.array([], dtype=bool), ""
    with file_lock(path, exclusive=False):
        with Dataset(path) as ds:
            ds.set_auto_maskandscale(True)
            if not {"time_minutes", "data", "completed"}.issubset(ds.variables):
                raise ValueError(f"{path}: requires time_minutes, data, completed")
            times = np.asarray(ds["time_minutes"][:], dtype=np.int64)
            if len(np.unique(times)) != len(times):
                raise ValueError(f"{path}: duplicate time_minutes coordinates")
            data_variable = ds["data"]
            if kind == "scalar" and data_variable.ndim != 1:
                raise ValueError(f"{path}: scalar data must be one-dimensional")
            if kind == "vector" and (data_variable.ndim != 2 or data_variable.shape[1] < 2):
                raise ValueError(f"{path}: vector data must be two-dimensional with two components")
            completed_raw = np.ma.asarray(ds["completed"][:]).reshape(-1)
            completed = np.zeros(completed_raw.size, dtype=bool)
            completion_mask = np.ma.getmaskarray(completed_raw)
            completed[~completion_mask] = np.asarray(completed_raw.data)[~completion_mask] == 1
            data = np.ma.asarray(data_variable[:])
            if data.shape[0] != times.size or completed.size != times.size:
                raise ValueError(f"{path}: inconsistent record dimensions")
            origin = str(getattr(ds, "time_origin", ""))
            if times.size:
                try:
                    datetime.fromisoformat(origin.replace("_", " ").replace("Z", ""))
                except (TypeError, ValueError):
                    raise ValueError(f"{path}: invalid time_origin {origin!r}") from None
            return times, data, completed, origin


def _finite(value: Any) -> bool:
    return bool(np.isfinite(np.asarray(value, dtype=float)).all())


def read_intensity(output_root: Path, experiment: str) -> IntensityData:
    """Read completed, finite records for one experiment under domain d03."""
    base = Path(output_root) / experiment / "d03"
    paths = {name: base / f"{name}.nc" for name in ("axis_mean_max_wind", "center_slp_5km", "center_geo", "slp_center")}
    loaded = {
        "axis_mean_max_wind": _read(paths["axis_mean_max_wind"], "scalar"),
        "center_slp_5km": _read(paths["center_slp_5km"], "scalar"),
        "center_geo": _read(paths["center_geo"], "vector"),
        "slp_center": _read(paths["slp_center"], "vector"),
    }
    origins = {item[3] for item in loaded.values() if item[0].size and item[3]}
    if len(origins) > 1:
        raise ValueError(f"{experiment}: time_origin values do not match")
    origin = next(iter(origins), "")

    def scalar(name: str) -> dict[int, float]:
        times, values, done, _ = loaded[name]
        result = {}
        for index, minute in enumerate(times):
            value = values[index]
            if done[index] and not np.ma.is_masked(value) and _finite(value):
                result[int(minute)] = float(value)
        return result

    def vector(name: str) -> dict[int, tuple[float, float]]:
        times, values, done, _ = loaded[name]
        result = {}
        for index, minute in enumerate(times):
            value = np.ma.asarray(values[index]).reshape(-1)
            if done[index] and value.size >= 2 and not np.ma.is_masked(value[:2]) and _finite(value[:2]):
                result[int(minute)] = (float(value[0]), float(value[1]))
        return result

    wind, slp = scalar("axis_mean_max_wind"), scalar("center_slp_5km")
    yx, latlon = vector("slp_center"), vector("center_geo")
    # The chart's time extent is defined by scalar diagnostics.  Coordinate
    # files may legitimately contain a broader or different set of records.
    available = set(wind) | set(slp)
    return IntensityData(
        times=np.array(sorted(available), dtype=np.int64),
        wind=wind,
        slp=slp,
        center_y={k: v[0] for k, v in yx.items()},
        center_x={k: v[1] for k, v in yx.items()},
        latitude={k: v[0] for k, v in latlon.items()},
        longitude={k: v[1] for k, v in latlon.items()},
        origin=origin,
    )


def _regular(data: IntensityData, interval: int) -> np.ndarray:
    if interval <= 0:
        raise ValueError("interval_minutes must be positive")
    if data.times.size == 0:
        return np.array([], dtype=np.int64)
    low = int(np.floor(data.times.min() / interval) * interval)
    high = int(np.ceil(data.times.max() / interval) * interval)
    return np.arange(low, high + interval, interval, dtype=np.int64)


def _value(mapping: dict[int, float], axis: np.ndarray) -> np.ndarray:
    return np.array([mapping.get(int(t), np.nan) for t in axis], dtype=float)


def _valid_time(origin: str, minute: int) -> str:
    if not origin:
        return ""
    text = origin.replace("_", " ").replace("Z", "")
    try:
        return (datetime.fromisoformat(text) + timedelta(minutes=int(minute))).isoformat(sep=" ")
    except ValueError:
        return ""


def _atomic_plot(fig, target: Path, *, dpi: int | None = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=target.suffix, dir=target.parent)
    os.close(fd)
    try:
        fig.savefig(temporary, dpi=dpi, format=target.suffix.lstrip("."))
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_csv(path: Path, data: IntensityData, axis: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".csv", dir=path.parent)
    os.close(fd)
    fields = ["time_minutes", "time_hours", "valid_time", "axis_mean_max_wind_m_s", "center_slp_5km_hpa", "center_y", "center_x", "latitude", "longitude"]
    try:
        with open(temporary, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for minute in axis:
                i = int(minute)
                row = {"time_minutes": i, "time_hours": i / 60, "valid_time": _valid_time(data.origin, i)}
                for key, mapping in (("axis_mean_max_wind_m_s", data.wind), ("center_slp_5km_hpa", data.slp), ("center_y", data.center_y), ("center_x", data.center_x), ("latitude", data.latitude), ("longitude", data.longitude)):
                    row[key] = mapping.get(i, "")
                writer.writerow(row)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _figure(data: IntensityData, axis: np.ndarray, label: str = ""):
    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True, constrained_layout=True)
    if label:
        fig.suptitle(label)
    axes[0].plot(axis / 60, _value(data.wind, axis), marker="o", label=label or None)
    axes[0].set_ylabel("Maximum axis-mean wind (m/s)")
    axes[0].grid(True, alpha=0.3)
    axes[1].plot(axis / 60, _value(data.slp, axis), marker="o", label=label or None)
    axes[1].set_ylabel("Center 5 km mean SLP (hPa)")
    axes[1].set_xlabel("Hours since time origin")
    axes[1].grid(True, alpha=0.3)
    if label:
        axes[0].legend()
        axes[1].legend()
    return fig


def export_intensity(output_root: Path, experiments: list[str], interval_minutes: int = 60) -> list[Path]:
    """Export per-experiment intensity artifacts and an optional comparison plot."""
    root = Path(output_root)
    successes: list[tuple[str, IntensityData, np.ndarray]] = []
    failures: list[str] = []
    outputs: list[Path] = []
    for experiment in experiments:
        try:
            data = read_intensity(root, experiment)
            if not data.wind and not data.slp:
                raise ValueError(f"{experiment}: both axis_mean_max_wind and center_slp_5km are unavailable or incomplete")
            axis = _regular(data, interval_minutes)
            figures = root / experiment / "figures"
            _write_csv(figures / "intensity.csv", data, axis)
            fig = _figure(data, axis, experiment)
            try:
                png, pdf = figures / "intensity.png", figures / "intensity.pdf"
                _atomic_plot(fig, png, dpi=300)
                _atomic_plot(fig, pdf)
            finally:
                plt.close(fig)
            outputs.extend([png, pdf])
            successes.append((experiment, data, axis))
        except Exception as exc:
            failures.append(f"{experiment}: {exc}")
    if len(successes) > 1:
        fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True, constrained_layout=True)
        try:
            for name, data, axis in successes:
                axes[0].plot(axis / 60, _value(data.wind, axis), marker="o", label=name)
                axes[1].plot(axis / 60, _value(data.slp, axis), marker="o", label=name)
            axes[0].set_ylabel("Maximum axis-mean wind (m/s)")
            axes[1].set_ylabel("Center 5 km mean SLP (hPa)")
            axes[1].set_xlabel("Hours since each experiment's time origin")
            for ax in axes:
                ax.grid(True, alpha=0.3)
                ax.legend()
            comparison = root / "figures"
            _atomic_plot(fig, comparison / "intensity_comparison.png", dpi=300)
            _atomic_plot(fig, comparison / "intensity_comparison.pdf")
            outputs.extend([comparison / "intensity_comparison.png", comparison / "intensity_comparison.pdf"])
        finally:
            plt.close(fig)
    if failures:
        raise ValueError("; ".join(failures))
    return outputs


__all__ = ["IntensityData", "export_intensity", "read_intensity"]
