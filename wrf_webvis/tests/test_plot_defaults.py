from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pytest

from wrf_webvis.app.plotting.draw_variables import time_series_plot
from wrf_webvis.app.plotting.member_styles import DEFAULT_EXPERIMENT_COLORS


@pytest.mark.parametrize("direction", list(DEFAULT_EXPERIMENT_COLORS))
def test_single_member_curve_uses_predefined_experiment_color(direction):
    fig, ax = plt.subplots()
    try:
        payload = {
            "member_name": f"WRF_{direction}_sea", "variable": "tilt_1_6", "config": {},
            "x": np.asarray([0, 1]), "values": np.asarray([2, 3]),
        }
        time_series_plot(ax, {"tilt_1_6": payload})
        assert ax.lines[0].get_color() == DEFAULT_EXPERIMENT_COLORS[direction]
    finally:
        plt.close(fig)


def test_single_member_curve_uses_custom_palette_and_explicit_color_override():
    fig, ax = plt.subplots()
    try:
        payload = {
            "member_name": "WRF_315_land", "variable": "tilt_1_6", "config": {},
            "x": np.asarray([0, 1]), "values": np.asarray([2, 3]),
        }
        fmt = {"track": {"experiment_colors": {"315": "magenta"}}}
        time_series_plot(ax, {"tilt_1_6": payload}, format_config=fmt)
        assert ax.lines[0].get_color() == "magenta"
        payload["config"] = {"color": "black"}
        time_series_plot(ax, {"tilt_1_6": payload}, format_config=fmt)
        assert ax.lines[1].get_color() == "black"
    finally:
        plt.close(fig)


def test_unknown_member_uses_matplotlib_color_cycle():
    fig, ax = plt.subplots()
    try:
        time_series_plot(ax, {"tilt_1_6": {
            "member_name": "custom_experiment", "config": {},
            "x": np.asarray([0, 1]), "values": np.asarray([2, 3]),
        }})
        assert ax.lines[0].get_color() == "C0"
    finally:
        plt.close(fig)


def test_plot_spec_defaults_to_v3_root_and_build_preserves_custom_root(tmp_path):
    from wrf_webvis.app.core.schemas import PlotRequest, SelectionSpec, VariableConfig
    from wrf_webvis.app.plotting.plot_spec import PlotSpec, build_plot_spec

    assert PlotSpec().data_root == Path("/Volumes/land_1/processed_v3")
    request = PlotRequest(
        member_name="WRF_315_land", fig_type=5, data_root=str(tmp_path / "custom_data"),
        variables=[VariableConfig(name="tilt_1_6", plot_type="line")], time=SelectionSpec(values=[0]),
    )
    spec = build_plot_spec(request, tmp_path / "plot.png")
    assert spec.data_root == tmp_path / "custom_data"
    assert spec.output_path == tmp_path / "plot.png"
