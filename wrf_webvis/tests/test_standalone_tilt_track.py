from pathlib import Path

import numpy as np
import pytest
from matplotlib.collections import LineCollection

from wrf_webvis.app.core.schemas import PlotRequest
from wrf_webvis.app.plotting import draw_main
from wrf_webvis.app.services.plot_service import PlotService
from v3_fixtures import save_v3


@pytest.mark.parametrize("variables", [[], [{"name": "slp", "enabled": False}]])
def test_standalone_tilt_track_renders_without_background(tmp_path, monkeypatch, variables):
    for name, values in {"tilt_x": [0, 10, 20], "tilt_y": [0, -5, 5]}.items():
        for hour, value in enumerate(values):
            save_v3(tmp_path, "member", name, value, time_hour=hour)

    request = PlotRequest(
        data_root=tmp_path, member_name="member", variables=variables,
        time={"values": [0, 1, 2]}, dpi=60,
        format={"common": {"fontname": "DejaVu Sans"}, "horizontal": {
            "draw_tilt_track": True, "tilt_track_time_range": [0, 2],
            "tilt_track_smooth_sigma": 0, "tilt_track_time_gradient": True,
            "tilt_track_colorbar": True, "xlim_km": 30, "tick_interval_km": 10,
        }},
    )
    save_figure = draw_main.save_figure
    captured = []

    def inspect_and_save(fig, path, **kwargs):
        tracks = [item for item in fig.axes[0].collections if isinstance(item, LineCollection)]
        assert len(tracks) == 1
        np.testing.assert_allclose(tracks[0].get_segments(), [
            [[0, 0], [10, -5]], [[10, -5], [20, 5]],
        ])
        assert fig.axes[0].get_xlabel() == "X (km)"
        assert len(fig.axes) == 2
        captured.append(path)
        save_figure(fig, path, **kwargs)

    monkeypatch.setattr(draw_main, "save_figure", inspect_and_save)
    result = PlotService(tmp_path / "figures").create_plot(request)
    assert captured
    assert Path(result["image_path"]).stat().st_size > 0


@pytest.mark.parametrize("fig_type, horizontal", [(0, {}), (1, {"draw_tilt_track": True})])
def test_empty_plot_without_supported_track_is_rejected(tmp_path, fig_type, horizontal):
    request = PlotRequest(data_root=tmp_path, fig_type=fig_type, format={"horizontal": horizontal})
    with pytest.raises(ValueError, match="没有启用任何"):
        PlotService(tmp_path / "figures").create_plot(request)


@pytest.mark.parametrize("gradient", [False, True])
def test_two_members_share_one_tilt_axis(tmp_path, monkeypatch, gradient):
    for member, sign in [("sea", 1), ("land", -1)]:
        for name in ["tilt_x", "tilt_y"]:
            for hour in range(4):
                save_v3(tmp_path, member, name, sign * hour, time_hour=hour)
    request = PlotRequest(
        data_root=tmp_path, member_names=["sea", "land"],
        time={"values": [0, 1, 2, 3]}, dpi=60,
        selection={"member_time_limits": {"land": 2}},
        format={"common": {"fontname": "DejaVu Sans"}, "horizontal": {
            "draw_tilt_track": True, "tilt_track_overlay_members": True,
            "tilt_track_time_range": [1, 3], "tilt_track_smooth_sigma": 0,
            "tilt_track_time_gradient": gradient, "tilt_track_colorbar": True,
        }},
    )
    original_save = draw_main.save_figure

    def inspect(fig, path, **kwargs):
        ax = fig.axes[0]
        assert len(fig.axes) == (2 if gradient else 1)
        assert [text.get_text() for text in ax.get_legend().get_texts()] == ["sea", "land"]
        if gradient:
            tracks = [item for item in ax.collections if isinstance(item, LineCollection)]
            assert len(tracks) == 2
            assert [(item.norm.vmin, item.norm.vmax) for item in tracks] == [(1, 3), (1, 3)]
            np.testing.assert_allclose(tracks[1].get_segments(), [[[-1, -1], [-2, -2]]])
            assert tracks[0].get_linestyle() != tracks[1].get_linestyle()
        else:
            assert len(ax.lines) == 2
            assert ax.lines[0].get_color() != ax.lines[1].get_color()
            np.testing.assert_allclose(ax.lines[1].get_xdata(), [-1, -2])
        original_save(fig, path, **kwargs)

    monkeypatch.setattr(draw_main, "save_figure", inspect)
    result = PlotService(tmp_path / "figures").create_plot(request)
    assert Path(result["image_path"]).stat().st_size > 0


def test_tilt_overlay_uses_experiment_colors_and_radius_circles(tmp_path):
    import matplotlib.pyplot as plt
    from matplotlib.colors import to_rgba
    from wrf_webvis.app.plotting.draw_format import draw_tilt_track_members
    from wrf_webvis.app.plotting.member_styles import resolve_experiment_color

    members = ["WRF_180_sea", "WRF_0_land"]
    for member in members:
        for variable in ["tilt_x", "tilt_y"]:
            for hour in [0, 1]:
                save_v3(tmp_path, member, variable, hour, time_hour=hour)
    fig, ax = plt.subplots()
    try:
        draw_tilt_track_members(ax, tmp_path, members, 3, {3: {"dx_km": 1}}, {
            "common": {"fontname": "DejaVu Sans"},
            "horizontal": {"draw_tilt_track": True, "tilt_track_time_gradient": False,
                           "radius_circles_km": [5, 10], "radius_circle_color": "purple",
                           "radius_circle_linewidth": 2, "radius_circle_linestyle": ":"},
        }, {})
        assert [line.get_color() for line in ax.lines] == [resolve_experiment_color(member, i, {}) for i, member in enumerate(members)]
        assert [patch.get_radius() for patch in ax.patches] == [5, 10]
        for patch in ax.patches:
            assert patch.center == (0, 0)
            assert patch.get_edgecolor() == to_rgba("purple")
            assert patch.get_linewidth() == 2
            assert patch.get_linestyle() == ":"
    finally:
        plt.close(fig)
