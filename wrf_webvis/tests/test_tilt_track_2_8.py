"""2026-11-04：2–8 km 倾斜轨迹改读 tilt_x_2_8 / tilt_y_2_8；新名缺文件时退回同一量的旧名 tilt_x / tilt_y。"""
from __future__ import annotations

import pytest

from wrf_webvis.app.core.registry import VARIABLE_REGISTRY
from wrf_webvis.app.core.tilt_track import tilt_track_components


def test_2_8_uses_new_names_by_default():
    assert tilt_track_components({"tilt_track_layer": "2_8"}) == ("tilt_x_2_8", "tilt_y_2_8")
    assert tilt_track_components({}) == ("tilt_x_2_8", "tilt_y_2_8")


def test_2_8_falls_back_to_legacy_names_only_when_new_files_missing():
    legacy_only = {"tilt_x", "tilt_y"}.__contains__
    assert tilt_track_components({"tilt_track_layer": "2_8"}, exists=legacy_only) == ("tilt_x", "tilt_y")
    both = {"tilt_x", "tilt_y", "tilt_x_2_8", "tilt_y_2_8"}.__contains__
    assert tilt_track_components({"tilt_track_layer": "2_8"}, exists=both) == ("tilt_x_2_8", "tilt_y_2_8")
    nothing = set().__contains__
    assert tilt_track_components({"tilt_track_layer": "2_8"}, exists=nothing) == ("tilt_x_2_8", "tilt_y_2_8")


@pytest.mark.parametrize("layer", ["2_4", "2_6", "2_10"])
def test_other_layers_never_fall_back(layer):
    legacy_only = {"tilt_x", "tilt_y"}.__contains__
    assert tilt_track_components({"tilt_track_layer": layer}, exists=legacy_only) == (f"tilt_x_{layer}", f"tilt_y_{layer}")


@pytest.mark.parametrize("name", ["tilt_2_8", "tilt_x_2_8", "tilt_y_2_8", "tilt_angle_2_8",
                                  "shear_core_200_850_magnitude", "shear_core_200_850_direction"])
def test_new_scalar_names_registered_for_plotting(name):
    item = VARIABLE_REGISTRY[name]
    assert item.dimension.name == "ONE_D"
