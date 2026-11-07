from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset


REPO = Path(__file__).resolve().parents[2]
V3 = REPO / "处理数据_v3"
if str(V3) not in sys.path:
    sys.path.insert(0, str(V3))

from server_tools.plot_intensity import _figure, export_intensity, read_intensity


def _write_scalar(path: Path, times, values, completed, origin="2024-01-01_00:00:00"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with Dataset(path, "w") as ds:
        ds.createDimension("record", len(times))
        t = ds.createVariable("time_minutes", "i8", ("record",))
        t[:] = times
        d = ds.createVariable("data", "f4", ("record",), fill_value=np.float32(-9999))
        d[:] = np.ma.array(values, mask=np.isnan(values))
        c = ds.createVariable("completed", "u1", ("record",))
        c[:] = completed
        ds.time_origin = origin


def _write_vector(path: Path, times, values, completed, origin="2024-01-01_00:00:00"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with Dataset(path, "w") as ds:
        ds.createDimension("record", len(times))
        ds.createDimension("component", 2)
        t = ds.createVariable("time_minutes", "i8", ("record",))
        t[:] = times
        d = ds.createVariable("data", "f4", ("record", "component"), fill_value=np.float32(-9999))
        d[:] = values
        c = ds.createVariable("completed", "u1", ("record",))
        c[:] = completed
        ds.time_origin = origin


def _experiment(root: Path, name="exp1", origin="2024-01-01_00:00:00", *, both=True):
    base = root / name / "d03"
    _write_scalar(base / "axis_mean_max_wind.nc", [120, 0, 180], np.array([30, 20, 40.]), [1, 1, 1], origin)
    if both:
        _write_scalar(base / "center_slp_5km.nc", [0, 120, 180], np.array([1000, 990, 980.]), [1, 0, 1], origin)
    _write_vector(base / "center_geo.nc", [0, 120, 180], np.array([[10, 20], [11, 21], [12, 22.]], dtype="f4"), [1, 1, 1], origin)
    _write_vector(base / "slp_center.nc", [0, 120, 180], np.array([[30, 40], [31, 41], [32, 42.]], dtype="f4"), [1, 1, 1], origin)


def test_export_sorts_times_writes_gap_csv_and_panels(tmp_path):
    _experiment(tmp_path)
    result = export_intensity(tmp_path, ["exp1"])
    assert result == [tmp_path / "exp1" / "figures" / "intensity.png", tmp_path / "exp1" / "figures" / "intensity.pdf"]
    assert all(path.is_file() and path.stat().st_size > 0 for path in result)
    with (tmp_path / "exp1" / "figures" / "intensity.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert [row["time_minutes"] for row in rows] == ["0", "60", "120", "180"]
    assert rows[1]["axis_mean_max_wind_m_s"] == ""
    assert rows[1]["center_slp_5km_hpa"] == ""
    assert rows[2]["axis_mean_max_wind_m_s"] == "30.0"
    assert rows[2]["center_slp_5km_hpa"] == ""
    assert rows[2]["valid_time"].startswith("2024-01-01 02:00")


def test_read_rejects_duplicate_times_and_mismatched_origins(tmp_path):
    base = tmp_path / "exp" / "d03"
    _write_scalar(base / "axis_mean_max_wind.nc", [0, 0], np.array([1., 2.]), [1, 1])
    with pytest.raises(ValueError, match="duplicate"):
        read_intensity(tmp_path, "exp")
    _write_scalar(base / "axis_mean_max_wind.nc", [0, 60], np.array([1., 2.]), [1, 1], "different")
    _write_scalar(base / "center_slp_5km.nc", [0, 60], np.array([1., 2.]), [1, 1], "origin")
    with pytest.raises(ValueError, match="origin"):
        read_intensity(tmp_path, "exp")


def test_missing_both_metrics_fails_without_stale_outputs_and_continues(tmp_path):
    _experiment(tmp_path, "good")
    _experiment(tmp_path, "bad", both=False)
    _write_scalar(tmp_path / "bad" / "d03" / "axis_mean_max_wind.nc", [0], np.array([30.]), [0])
    with pytest.raises(ValueError, match="bad.*axis_mean_max_wind.*center_slp_5km"):
        export_intensity(tmp_path, ["good", "bad"])
    assert (tmp_path / "good" / "figures" / "intensity.png").is_file()
    assert not (tmp_path / "bad" / "figures" / "intensity.png").exists()


def test_multiple_experiments_write_comparison(tmp_path):
    _experiment(tmp_path, "a", "2024-01-01_00:00:00")
    _experiment(tmp_path, "b", "2024-01-02_00:00:00")
    outputs = export_intensity(tmp_path, ["a", "b"])
    assert outputs[-2:] == [tmp_path / "figures" / "intensity_comparison.png", tmp_path / "figures" / "intensity_comparison.pdf"]
    assert all(p.is_file() for p in outputs[-2:])


def test_completion_requires_exact_one_and_masks_nonfinite_values(tmp_path):
    path = tmp_path / "exp" / "d03" / "axis_mean_max_wind.nc"
    path.parent.mkdir(parents=True)
    with Dataset(path, "w") as ds:
        ds.createDimension("record", 4)
        t = ds.createVariable("time_minutes", "i8", ("record",))
        t[:] = [0, 60, 120, 180]
        values = ds.createVariable("data", "f4", ("record",), fill_value=-9999.0)
        values[:] = np.ma.array([10.0, np.nan, 30.0, 40.0], mask=[False, False, True, False])
        done = ds.createVariable("completed", "u1", ("record",), fill_value=255)
        done[:] = np.ma.array([2, 1, 1, 0], mask=[False, False, True, False])
        ds.time_origin = "2024-01-01_00:00:00"
    data = read_intensity(tmp_path, "exp")
    assert data.wind == {}


def test_every_nonempty_file_requires_valid_time_origin_and_layout(tmp_path):
    path = tmp_path / "exp" / "d03" / "axis_mean_max_wind.nc"
    _write_scalar(path, [0], np.array([10.0]), [1], origin="not-a-date")
    with pytest.raises(ValueError, match="time_origin"):
        read_intensity(tmp_path, "exp")

    with Dataset(path, "w") as ds:
        ds.createDimension("record", 1)
        ds.createDimension("y", 2)
        ds.createDimension("x", 2)
        ds.createVariable("time_minutes", "i8", ("record",))[:] = [0]
        ds.createVariable("data", "f4", ("record", "y", "x"))[:] = 1
        ds.createVariable("completed", "u1", ("record",))[:] = [1]
        ds.time_origin = "2024-01-01_00:00:00"
    with pytest.raises(ValueError, match="scalar"):
        read_intensity(tmp_path, "exp")


def test_figure_preserves_nan_gap_in_wind_line(tmp_path):
    _experiment(tmp_path)
    data = read_intensity(tmp_path, "exp1")
    axis = np.array([0, 60, 120, 180])
    fig = _figure(data, axis, "exp1")
    try:
        assert np.isnan(fig.axes[0].lines[0].get_ydata()[1])
        assert fig._suptitle.get_text() == "exp1"
    finally:
        import matplotlib.pyplot as plt

        plt.close(fig)
