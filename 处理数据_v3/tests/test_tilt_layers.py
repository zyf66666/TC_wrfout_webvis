"""2026-10-26：固定层次倾斜 tilt_*_2_4 / 2_6 / 2_10（与 tilt_1_6 同算法）。"""
from __future__ import annotations

import numpy as np
import pytest

# 2026-11-04 加 2_8（取代旧名 tilt_magnitude 等）。
LAYERS = {"2_4": (2000, 4000), "2_6": (2000, 6000), "2_8": (2000, 8000), "2_10": (2000, 10000)}
PREFIXES = ("tilt", "tilt_x", "tilt_y", "tilt_angle")


@pytest.mark.parametrize("suffix", LAYERS)
@pytest.mark.parametrize("prefix", PREFIXES)
def test_layer_tilt_registered_with_fixed_heights(prefix, suffix):
    from wrf_processing_v3.register import build_registry

    registry = build_registry({"tilt_lower_height_m": 2000, "tilt_upper_height_m": 8000})
    spec = registry.resolve(f"{prefix}_{suffix}")
    lower, upper = LAYERS[suffix]
    assert spec.storage_layout == "scalar"
    assert [(dep.name, dep.height_m) for dep in spec.dependencies] == [
        ("layer_center", lower), ("layer_center", upper),
    ]
    assert spec.units == ("degree" if prefix == "tilt_angle" else "km")
    # 默认 2–8 km 倾斜不受影响
    assert [dep.height_m for dep in registry.resolve("tilt_magnitude").dependencies] == [2000, 8000]


@pytest.mark.parametrize("suffix", LAYERS)
def test_layer_tilt_values_from_cached_centers(tmp_path, suffix):
    from wrf_processing_v3.processing_manager import ProcessingManager
    from wrf_processing_v3.register import build_registry
    from wrf_processing_v3.request import Request, Task
    from wrf_processing_v3.storage import NetCDFStorage

    lower, upper = LAYERS[suffix]
    names = tuple(f"{p}_{suffix}" for p in PREFIXES)
    request = Request(
        experiments=("exp",), domains=(3,), times_minutes=(0, 5),
        heights_m=(lower,), variables=names, source_root=tmp_path / "source",
        output_root=tmp_path / "out", options={"time_origin": "2023-09-01_00:00:00"},
    )
    registry = build_registry(request.options, times_minutes=request.times_minutes)
    storage = NetCDFStorage(request.output_root, time_origin=request.options["time_origin"])
    for minute in request.times_minutes:
        for height, center in ((lower, [10, 20]), (upper, [12, 23])):
            storage.save(
                Task("exp", 3, minute, "layer_center", height), np.asarray(center),
                registry.resolve("layer_center"),
                metadata={"grid_spacing": {"dx_km": 2.0, "dy_km": 3.0}},
            )

    class CachedOnlyReader:
        def prepare(self, tasks, *args, **kwargs):
            raise AssertionError(f"Cached centers should not require raw WRF fields: {tasks}")

    result = ProcessingManager(registry=registry, reader=CachedOnlyReader()).process(request)
    assert len(result["computed"]) == 8
    expected = (np.sqrt(72.0), 6.0, 6.0, -45.0)
    for minute in request.times_minutes:
        for name, value in zip(names, expected):
            task = Task("exp", 3, minute, name, None)
            np.testing.assert_allclose(storage.load(task), [value], rtol=1e-6)
            meta = storage.load_metadata(task)["spec_options"]
            assert (meta["lower_height"], meta["upper_height"]) == (lower, upper)


def test_layer_tilt_available_for_web_time_series():
    from wrf_webvis.app.core.registry import get_variable_item, plotting_variable_type_registry
    from wrf_webvis.app.plotting.draw_variables import infer_time_series_units
    from wrf_webvis.app.services.v3_management import V3ManagementService

    catalog = V3ManagementService().catalog()
    for suffix in LAYERS:
        for prefix in PREFIXES:
            name = f"{prefix}_{suffix}"
            assert name in catalog["variables"]
            assert plotting_variable_type_registry()[name] == "1d"
            assert get_variable_item(name).derived
            assert infer_time_series_units(name) == ("degree" if prefix == "tilt_angle" else "km")
