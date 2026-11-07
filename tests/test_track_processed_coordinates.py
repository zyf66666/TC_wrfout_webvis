from __future__ import annotations

from pathlib import Path

import numpy as np

from wrf_webvis.app.plotting import track_plot


# 功能说明：在降雨数组旁放入已处理的纬度和经度网格，确认轨迹底图从这些缓存读取坐标。
def test_track_background_reads_coordinates_from_processed_data(tmp_path: Path) -> None:
    data_path = tmp_path / "member/d01/RAINNC/120h.npy"
    data_path.parent.mkdir(parents=True)
    np.save(data_path, np.ones((2, 3), dtype=np.float32))
    grid_root = tmp_path / "member/d01/grid"
    grid_root.mkdir()
    expected_lat = np.arange(6, dtype=float).reshape(2, 3)
    expected_lon = expected_lat + 100
    np.save(grid_root / "lat.npy", expected_lat)
    np.save(grid_root / "lon.npy", expected_lon)

    lat, lon = track_plot.load_processed_grid_coordinates(data_path, {"member_name": "member", "domain": 1})

    np.testing.assert_array_equal(lat, expected_lat)
    np.testing.assert_array_equal(lon, expected_lon)


# 功能说明：直接检查轨迹绘图源码，确认它不依赖 netCDF4 或回查原始 wrfout 的函数。
def test_track_plotting_module_has_no_netcdf_dependency() -> None:
    source = (Path(__file__).parents[1] / "wrf_webvis/app/plotting/track_plot.py").read_text(encoding="utf-8")

    assert "netCDF4" not in source
    assert "resolve_local_source_wrfout" not in source
