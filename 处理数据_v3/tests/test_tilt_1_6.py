from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from netCDF4 import Dataset


VARIABLES = ("tilt_1_6", "tilt_x_1_6", "tilt_y_1_6", "tilt_angle_1_6")


@pytest.mark.parametrize("name", VARIABLES)
def test_tilt_1_6_uses_fixed_heights_independent_of_legacy_options(name):
    from wrf_processing_v3.register import build_registry

    registry = build_registry({"tilt_lower_height_m": 2000, "tilt_upper_height_m": 8000})
    spec = registry.resolve(name)

    assert spec.storage_layout == "scalar"
    assert [(dep.name, dep.height_m) for dep in spec.dependencies] == [
        ("layer_center", 1000), ("layer_center", 6000),
    ]
    assert spec.units == ("degree" if name == "tilt_angle_1_6" else "km")
    assert [dep.height_m for dep in registry.resolve("tilt_magnitude").dependencies] == [2000, 8000]


def test_tilt_1_6_saves_all_components_and_reuses_cached_centers(tmp_path):
    from wrf_processing_v3.processing_manager import ProcessingManager
    from wrf_processing_v3.register import build_registry
    from wrf_processing_v3.request import Request, Task
    from wrf_processing_v3.storage import NetCDFStorage

    request = Request(
        experiments=("exp",), domains=(3,), times_minutes=(0, 60),
        heights_m=(1000,), variables=VARIABLES, source_root=tmp_path / "source",
        output_root=tmp_path / "out", options={"time_origin": "2023-09-01_00:00:00"},
    )
    registry = build_registry(request.options, times_minutes=request.times_minutes)
    storage = NetCDFStorage(request.output_root, time_origin=request.options["time_origin"])
    for minute in request.times_minutes:
        for height, center in ((1000, [10, 20]), (6000, [12, 23])):
            storage.save(
                Task("exp", 3, minute, "layer_center", height), np.asarray(center),
                registry.resolve("layer_center"),
                metadata={"grid_spacing": {"dx_km": 2.0, "dy_km": 3.0}},
            )
        storage.save(Task("exp", 3, minute, "tilt_magnitude", None), np.asarray(99.0), registry.resolve("tilt_magnitude"))

    class CachedOnlyReader:
        def prepare(self, tasks, *args, **kwargs):
            raise AssertionError(f"Cached centers should not require raw WRF fields: {tasks}")

    manager = ProcessingManager(registry=registry, reader=CachedOnlyReader())
    result = manager.process(request)
    assert len(result["computed"]) == 8
    expected = (np.sqrt(72.0), 6.0, 6.0, -45.0)
    for minute in request.times_minutes:
        for name, value in zip(VARIABLES, expected):
            task = Task("exp", 3, minute, name, None)
            np.testing.assert_allclose(storage.load(task), [value], rtol=1e-6)
            assert storage.path_for(task).name == f"{name}.nc"
            assert storage.load_metadata(task)["spec_options"]["lower_height"] == 1000
            assert storage.load_metadata(task)["spec_options"]["upper_height"] == 6000
            with Dataset(storage.path_for(task)) as dataset:
                assert dataset["data"].dimensions == ("record",)
                assert dataset.units == ("degree" if name == "tilt_angle_1_6" else "km")
        np.testing.assert_allclose(storage.load(Task("exp", 3, minute, "tilt_magnitude", None)), [99.0])

    assert manager.process(request)["computed"] == []
    forced = replace(request, force=True)
    assert len(manager.process(forced)["computed"]) == 8


def test_tilt_1_6_plans_continuous_center_chain_when_centers_are_missing(tmp_path):
    from wrf_processing_v3.dependency_planner import DependencyPlanner
    from wrf_processing_v3.register import build_registry
    from wrf_processing_v3.request import Request
    from wrf_processing_v3.storage import NetCDFStorage, DEFAULT_HEIGHTS_M

    request = Request(
        experiments=("exp",), domains=(3,), times_minutes=(0,), heights_m=(1000,),
        variables=("tilt_1_6",), source_root=tmp_path, output_root=tmp_path / "out",
        options={"center_options_by_domain": {3: {"layer_mode": "vertical"}}},
    )
    registry = build_registry(request.options)
    storage = NetCDFStorage(request.output_root, time_origin="2023-09-01_00:00:00")
    plan = DependencyPlanner(registry, storage).plan(request)
    centers = [item for item in plan if item.task.variable == "layer_center"]

    assert [item.task.height_m for item in centers] == [h for h in DEFAULT_HEIGHTS_M if h <= 6000]
    assert next(item for item in centers if item.task.height_m == 6000).spec.dependencies[-1].height_m == 5750
    assert len([item for item in plan if item.task.variable == "tilt_1_6"]) == 1


def test_tilt_1_6_is_available_for_web_time_series_and_processing():
    from wrf_webvis.app.core.registry import get_variable_item, plotting_variable_type_registry
    from wrf_webvis.app.plotting.draw_variables import infer_time_series_units
    from wrf_webvis.app.services.v3_management import V3ManagementService

    catalog = V3ManagementService().catalog()
    for name in VARIABLES:
        assert name in catalog["variables"]
        assert plotting_variable_type_registry()[name] == "1d"
        assert get_variable_item(name).derived
        assert infer_time_series_units(name) == ("degree" if name == "tilt_angle_1_6" else "km")


def test_cli_can_select_only_tilt_1_6_without_editing_project_defaults(monkeypatch):
    from 处理数据_v3 import process_data

    requests = []
    monkeypatch.setattr(process_data, "process_request", lambda request: requests.append(request))
    argv = [argument for name in VARIABLES for argument in ("--variable", name)]
    process_data.main(argv)

    assert requests
    assert all(request.variables == VARIABLES and request.dry_run for request in requests)
    requests.clear()
    process_data.main(["--run", *argv])
    assert all(request.variables == VARIABLES and not request.dry_run for request in requests)
    assert process_data.build_request("WRF_315_land").variables == tuple(process_data.VARIABLES)
