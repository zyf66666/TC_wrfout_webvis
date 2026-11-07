"""倾斜范围必须同时决定轨迹读取、绘图预检与保存请求。"""

from pathlib import Path

import numpy as np
import pytest

from wrf_webvis.app.core.schemas import PlotRequest
from wrf_webvis.app.plotting.draw_format import load_tilt_track
from wrf_webvis.app.services.plot_inputs import check_additional_plot_inputs
from wrf_webvis.app.services.plot_service import PlotService
from 处理数据_v3.wrf_processing_v3.register import build_registry
from 处理数据_v3.wrf_processing_v3.request import Task
from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage


LAYERS = {
    "2_4": ("tilt_x_2_4", "tilt_y_2_4"),
    "2_6": ("tilt_x_2_6", "tilt_y_2_6"),
    "2_8": ("tilt_x", "tilt_y"),
    "2_10": ("tilt_x_2_10", "tilt_y_2_10"),
}


def save_pair(root, layer, *, offset=0):
    storage = NetCDFStorage(root, time_origin="2023-09-01_00:00:00")
    registry = build_registry()
    for name, values in zip(LAYERS[layer], ([offset + 1, offset + 4], [offset - 2, offset + 3])):
        for minute, value in zip((0, 60), values):
            storage.save(Task("member", 3, minute, name, None), np.asarray([value]), registry.resolve(name))


def request_for(root, layer=None):
    horizontal = {"draw_tilt_track": True, "tilt_track_overlay_members": True,
                  "tilt_track_smooth_sigma": 0, "tilt_track_time_gradient": False,
                  "tilt_track_time_range": [0, 1], "xlim_km": 100}
    if layer is not None:
        horizontal["tilt_track_layer"] = layer
    return PlotRequest(processed_root=root, member_name="member", variables=[],
                       time={"values": [0, 1]}, dpi=60,
                       format={"common": {"fontname": "DejaVu Sans"}, "horizontal": horizontal})


@pytest.mark.parametrize("layer", list(LAYERS))
def test_selected_layer_reads_its_own_v3_components(tmp_path, layer):
    root = tmp_path / "processed_v3"
    for index, item in enumerate(LAYERS):
        save_pair(root, item, offset=index * 20)
    index = list(LAYERS).index(layer)
    track = load_tilt_track(root, "member", 3, {"tilt_track_layer": layer})
    np.testing.assert_allclose(track["time"], [0, 1])
    np.testing.assert_allclose(track["tilt_x"], [index * 20 + 1, index * 20 + 4])
    np.testing.assert_allclose(track["tilt_y"], [index * 20 - 2, index * 20 + 3])


def test_old_request_keeps_default_2_8_layer(tmp_path):
    root = tmp_path / "processed_v3"
    save_pair(root, "2_8", offset=40)
    track = load_tilt_track(root, "member", 3, {})
    np.testing.assert_allclose(track["tilt_x"], [41, 44])


@pytest.mark.parametrize("layer", list(LAYERS))
def test_preflight_requires_selected_component_ncs(tmp_path, layer):
    root = tmp_path / "processed_v3"
    save_pair(root, layer)
    checked = check_additional_plot_inputs(request_for(root, layer))
    assert set(checked.required_files) == {root / "member" / "d03" / f"{name}.nc" for name in LAYERS[layer]}
    assert checked.missing_files == []


def test_missing_selected_layer_cannot_fall_back_to_default(tmp_path):
    root = tmp_path / "processed_v3"
    save_pair(root, "2_8")
    with pytest.raises(FileNotFoundError, match="tilt_x_2_10|tilt_y_2_10"):
        load_tilt_track(root, "member", 3, {"tilt_track_layer": "2_10"})
    checked = check_additional_plot_inputs(request_for(root, "2_10"))
    assert {p.name for p in checked.missing_files} == {"tilt_x_2_10.nc", "tilt_y_2_10.nc"}


def test_invalid_layer_is_rejected_by_loader_and_preflight(tmp_path):
    root = tmp_path / "processed_v3"
    save_pair(root, "2_8")
    with pytest.raises(ValueError, match="tilt_track_layer"):
        load_tilt_track(root, "member", 3, {"tilt_track_layer": "2_5"})
    with pytest.raises(ValueError, match="tilt_track_layer"):
        check_additional_plot_inputs(request_for(root, "2_5"))


def test_selected_layer_survives_request_round_trip(tmp_path):
    request = request_for(tmp_path / "processed_v3", "2_10")
    restored = PlotRequest.model_validate_json(request.model_dump_json())
    assert restored.format.horizontal["tilt_track_layer"] == "2_10"


def test_standalone_plot_draws_selected_layer(tmp_path, monkeypatch):
    from wrf_webvis.app.plotting import draw_main

    root = tmp_path / "processed_v3"
    save_pair(root, "2_8", offset=40)
    save_pair(root, "2_4", offset=0)
    original = draw_main.save_figure
    captured = []

    def inspect(fig, path, **kwargs):
        ax = fig.axes[0]
        np.testing.assert_allclose(ax.lines[0].get_xdata(), [1, 4])
        np.testing.assert_allclose(ax.lines[0].get_ydata(), [-2, 3])
        captured.append(path)
        original(fig, path, **kwargs)

    monkeypatch.setattr(draw_main, "save_figure", inspect)
    result = PlotService(tmp_path / "figures").create_plot(request_for(root, "2_4"))
    assert captured and Path(result["image_path"]).is_file()
