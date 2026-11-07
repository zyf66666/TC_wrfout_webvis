from __future__ import annotations

import numpy as np
import pytest
from netCDF4 import Dataset

from wrf_processing_v3.processing_manager import ProcessingManager
from wrf_processing_v3.register import build_registry
from wrf_processing_v3.request import Request, Task
from wrf_processing_v3.storage import NetCDFStorage


@pytest.mark.parametrize(
    "variable",
    [
        "vr1_ventilation_polar", "vr_ventilation_polar",
        "w1_ventilation_polar", "w_ventilation_polar",
    ],
)
@pytest.mark.parametrize("configured_resolution", [None, 0.5, 1.0])
def test_ventilation_inherits_source_polar_coordinates_and_resumes(
    tmp_path, variable, configured_resolution
):
    options = {"time_origin": "2023-09-01_00:00:00"}
    if configured_resolution is not None:
        options["radius_resolution_km"] = configured_resolution
    request = Request(
        experiments=("exp",), domains=(3,), times_minutes=(0,),
        heights_m=(10000,), variables=(variable,),
        source_root=tmp_path / "source", output_root=tmp_path / "out",
        options=options,
    )
    registry = build_registry(options, times_minutes=request.times_minutes)
    storage = NetCDFStorage(request.output_root, time_origin=options["time_origin"])
    spec = registry.resolve(variable)
    geometry = {
        "radius_resolution_km": 0.5, "radius_km": 1.0,
        "angle_count": 4, "source_shape": [31, 31],
        "center_mode": "layer_center" if "1_" in variable else "slp_center",
    }
    fields = [
        np.full((4, 3), 2.0, dtype=np.float32),
        np.full((4, 3), 3.0, dtype=np.float32),
        np.tile([-2.0, 0.0, 3.0], (4, 1)).astype(np.float32),
        np.tile([-1.0, 2.0, 4.0], (4, 1)).astype(np.float32),
    ]
    for dependency, field in zip(spec.dependencies, fields):
        task = Task("exp", 3, 0, dependency.name, 10000)
        storage.save(task, field, registry.resolve(dependency.name), {**geometry, "request_options": {"radius_resolution_km": 0.5}})

    class CachedReader:
        def prepare(self, *args, **kwargs):
            raise AssertionError("Completed polar dependencies should be reused")

    manager = ProcessingManager(registry=registry, reader=CachedReader())
    if configured_resolution == 1.0:
        # 输入缓存实为 0.5 km，统一校验后不能在显式 1 km 请求中静默复用。
        with pytest.raises(ValueError, match="参数不一致"):
            manager.process(request)
        assert not storage.path_for(Task("exp", 3, 0, variable, 10000)).exists()
        return
    result = manager.process(request)
    assert len(result["computed"]) == 1
    assert len(result["reused"]) == 4
    target = Task("exp", 3, 0, variable, 10000)
    np.testing.assert_allclose(storage.load(target), np.tile([-6.0, 12.0, 0.0], (4, 1)))
    metadata = storage.load_metadata(target)
    for key, value in geometry.items():
        assert metadata[key] == value
    with Dataset(storage.path_for(target)) as dataset:
        np.testing.assert_allclose(dataset["radius_km"][:], [0.0, 0.5, 1.0])
        assert dataset["data"].units == "kg K m-2 s-1"
    resumed = manager.process(request)
    assert resumed["computed"] == []
    assert len(resumed["reused"]) == 1
