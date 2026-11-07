import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from wrf_webvis.app.core.paths import PathManager, metadata_path
from wrf_webvis.app.core.schemas import ProcessingRequest
from wrf_webvis.app.plotting.draw_format import apply_time_series_numeric_ticks, load_tilt_track, maybe_draw_tilt_track
from wrf_processing.diagnostics import vortex_tilt


# 功能说明：用两个层中心和不同 x/y 网格间距，确认四种倾斜量及高度元数据都被写入。
def test_vortex_tilt_uses_xy_grid_spacing_and_writes_all_outputs(tmp_path: Path):
    source = tmp_path / "wrfout_d03_test"
    source.touch()
    manager = PathManager(tmp_path)
    request = ProcessingRequest(
        processed_root=tmp_path,
        member_name="member",
        domain=3,
        variables=["tilt_magnitude"],
        options={"tilt_lower_height_m": 2000, "tilt_upper_height_m": 8000},
    )

    # 功能说明：模拟层中心准备器，为 2000 和 8000 米写入指定时次的不同中心位置。
    def ensure_layer_centers(req, times, heights, _progress):
        assert times == [5 / 60]
        assert heights == [2000.0, 8000.0]
        lower = manager.layer_center_path(req.member_name, req.domain, 2000)
        upper = manager.layer_center_path(req.member_name, req.domain, 8000)
        lower.parent.mkdir(parents=True, exist_ok=True)
        upper.parent.mkdir(parents=True, exist_ok=True)
        np.savetxt(lower, [[5 / 60, 10, 20]])
        np.savetxt(upper, [[5 / 60, 12, 23]])
        return [lower, upper]

    out = vortex_tilt.process(
        request=request,
        path_manager=manager,
        variable="tilt_magnitude",
        source_file=source,
        source_time_index=1,
        time_hour=5 / 60,
        dtype="float32",
        domain_config={"dx_km": 2.0, "dy_km": 3.0},
        ensure_layer_centers=ensure_layer_centers,
    )

    assert Path(out).name == "0h5min.npy"
    expected = {
        "tilt_x": 6.0,
        "tilt_y": 6.0,
        "tilt_magnitude": np.sqrt(72.0),
        "tilt_angle": -45.0,
    }
    for variable, value in expected.items():
        path = manager.variable_path(
            member_name="member", domain=3, variable=variable,
            time_hour=5 / 60, dimension="1d",
        )
        np.testing.assert_allclose(np.load(path), [value], rtol=1e-6)
        metadata = json.loads(metadata_path(path).read_text(encoding="utf-8"))
        assert metadata["lower_height_m"] == 2000.0
        assert metadata["upper_height_m"] == 8000.0


# 功能说明：写入倾斜 x/y 时序，确认加载器返回轨迹且水平图叠加渐变线和指定时次标记。
def test_tilt_track_loader_and_horizontal_overlay(tmp_path: Path):
    # 2026-11-01：倾斜轨迹只读 processed_v3 的 tilt_x / tilt_y 标量。
    from v3_fixtures import save_v3

    for variable, values in {"tilt_x": [0.0, 10.0, 20.0], "tilt_y": [0.0, -5.0, 5.0]}.items():
        for time, value in zip([0, 1, 2], values):
            save_v3(tmp_path, "member", variable, value, time_hour=time)

    fmt = {
        "draw_tilt_track": True,
        "coordinate_system": "center_km",
        "tilt_track_smooth_sigma": 0,
        "tilt_track_time_gradient": True,
        "tilt_track_mark_times": [1],
    }
    track = load_tilt_track(tmp_path, "member", 3, fmt)
    np.testing.assert_allclose(track["time"], [0, 1, 2])
    np.testing.assert_allclose(track["tilt_x"], [0, 10, 20])

    fig, ax = plt.subplots()
    try:
        maybe_draw_tilt_track(
            ax, tmp_path, "member", 3, [0, 1, 2],
            center_x=0.0, center_y=0.0, dx_km=1.0, dy_km=1.0, fmt=fmt,
        )
        # One gradient LineCollection plus the start/end/marked-time scatters.
        assert len(ax.collections) >= 4
        np.testing.assert_allclose(ax.collections[0].get_segments()[0], [[0, 0], [10, -5]])
    finally:
        plt.close(fig)


# 功能说明：同时提供通用和变量级纵轴设置，确认变量级范围与刻度优先生效。
def test_tilt_variable_axis_options_override_generic_intensity_limits():
    fig, ax = plt.subplots()
    try:
        ax.plot([0, 1], [5, 100])
        apply_time_series_numeric_ticks(
            ax,
            {"left_ylim": [20, 60], "left_yticks": [20, 40, 60]},
            "left",
            12,
            "DejaVu Sans",
            allow_generic=True,
            variable_config={"ylim": [0, 120], "yticks": [0, 40, 80, 120]},
        )
        assert tuple(ax.get_ylim()) == (0.0, 120.0)
        np.testing.assert_allclose(ax.get_yticks(), [0, 40, 80, 120])
    finally:
        plt.close(fig)
