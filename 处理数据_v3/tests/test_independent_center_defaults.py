"""默认使用已复现的独立 Bezier/PCC；显式请求时仍保留逐层连续模式。"""

from types import SimpleNamespace

import numpy as np
import pytest

from wrf_processing_v3.dependency_planner import DependencyPlanner
from wrf_processing_v3.register import build_registry
from wrf_processing_v3.request import Request
from wrf_processing_v3.storage import NetCDFStorage
from wrf_processing_v3.variables._center_options import slp_center_algorithm


def _request(tmp_path, options=None):
    return Request(
        experiments=("exp",), domains=(3,), times_minutes=(0,),
        heights_m=(2000, 8000), variables=("layer_center",),
        source_root=tmp_path / "source", output_root=tmp_path / "out",
        options=options or {},
    )


def test_project_web_and_cli_default_to_old_independent_centers(tmp_path):
    from 处理数据_v3 import project_config, process_data
    from wrf_webvis.app.services.v3_management import V3ManagementService

    for options in (
        project_config.OPTIONS,
        process_data.build_request("WRF_315_land").options,
        V3ManagementService(
            output_root=tmp_path, source_directories={"exp": str(tmp_path)},
            default_options=project_config.OPTIONS,
        ).default_options,
    ):
        name, params = slp_center_algorithm(options, 3)
        assert name == "bezier_pcc_v1"
        assert params["search_radius_km"] == 60
        assert params["pcc_radius_grid"] == 44


def test_default_sparse_layer_request_needs_only_requested_pressure_fields(tmp_path):
    request = _request(tmp_path)
    registry = build_registry(request.options)
    storage = NetCDFStorage(request.output_root, time_origin="2023-09-01_00:00:00")
    plan = DependencyPlanner(registry, storage).plan(request)
    centers = [item for item in plan if item.task.variable == "layer_center"]
    assert [item.task.height_m for item in centers] == [2000, 8000]
    assert {item.task.variable for item in plan} == {"pressure", "layer_center"}
    assert all([d.name for d in item.spec.dependencies] == ["pressure"] for item in centers)
    assert all(item.spec.options["algorithm_version"] == "v2.1" for item in centers)


def test_layer_handler_default_does_not_read_previous_center():
    from wrf_processing_v3._center_algorithm import find_pressure_center
    from wrf_processing_v3.variables.layer_center import process

    yy, xx = np.mgrid[:501, :501]
    field = 1000.0 - 20 * np.exp(-((yy - 230) ** 2 + (xx - 170) ** 2) / (2 * 40 ** 2))
    saved = {}

    def read(name, **kwargs):
        assert name == "pressure", "独立定位不应读取地面或上一层中心"
        return field

    ctx = SimpleNamespace(
        task=SimpleNamespace(domain=3, variable="layer_center"),
        options={"domain_config": {3: {"dx_km": 1, "dy_km": 1}}},
        spec=SimpleNamespace(dependencies=(SimpleNamespace(name="layer_center", height=1000),)),
        read=read,
        save=lambda name, values, metadata: saved.update(values=values, metadata=metadata),
    )
    process(ctx)
    y, x, _ = find_pressure_center(field, search_radius_km=60)
    np.testing.assert_array_equal(saved["values"], [y, x])
    assert saved["metadata"]["algorithm"] == "bezier_pcc_v1"
    assert saved["metadata"]["initial_center_yx"] is None


def test_explicit_vertical_mode_keeps_previous_center_chain(tmp_path):
    request = _request(tmp_path, {"center_options_by_domain": {3: {"layer_mode": "vertical"}}})
    registry = build_registry(request.options)
    storage = NetCDFStorage(request.output_root, time_origin="2023-09-01_00:00:00")
    plan = DependencyPlanner(registry, storage).plan(request)
    top = next(item for item in plan if item.task.variable == "layer_center" and item.task.height_m == 8000)
    assert top.spec.options["algorithm_version"] == "bezier_pcc_vertical_v1"
    assert top.spec.dependencies[-1].name == "layer_center"
    assert top.spec.dependencies[-1].height_m == 7750


def test_unknown_layer_mode_is_rejected():
    with pytest.raises(ValueError, match="layer_mode"):
        slp_center_algorithm({"center_options_by_domain": {3: {"layer_mode": "unknown"}}}, 3)


def test_pipeline_saves_independent_centers_from_cached_pressure_and_reuses_them(tmp_path):
    from wrf_processing_v3._center_algorithm import find_pressure_center
    from wrf_processing_v3.processing_manager import ProcessingManager
    from wrf_processing_v3.request import Task

    request = _request(tmp_path, {"time_origin": "2023-09-01_00:00:00"})
    registry = build_registry(request.options)
    storage = NetCDFStorage(request.output_root, time_origin=request.options["time_origin"])
    yy, xx = np.mgrid[:501, :501]
    expected = {}
    for height, cy, cx in ((2000, 230, 170), (8000, 260, 310)):
        field = 1000.0 - 20 * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * 40 ** 2))
        storage.save(
            Task("exp", 3, 0, "pressure", height), field, registry.resolve("pressure"),
            metadata={"grid_spacing": {"dx_km": 1.0, "dy_km": 1.0}},
        )
        # Storage is float32; compare the handler against the actual cached field.
        y, x, _ = find_pressure_center(field.astype(np.float32), search_radius_km=60)
        expected[height] = [y, x]

    class CachedOnlyReader:
        def prepare(self, tasks, *args, **kwargs):
            raise AssertionError(f"已有气压缓存不应读取 wrfout: {tasks}")

    manager = ProcessingManager(registry=registry, reader=CachedOnlyReader())
    result = manager.process(request)
    assert len(result["computed"]) == 2
    for height, center in expected.items():
        task = Task("exp", 3, 0, "layer_center", height)
        np.testing.assert_array_equal(storage.load(task), center)
        metadata = storage.load_metadata(task)
        assert metadata["algorithm"] == "bezier_pcc_v1"
        assert metadata["layer_center_mode"] == "independent"
        assert metadata["initial_center_yx"] is None
        assert storage.path_for(task).name == "layer_center.nc"
    assert manager.process(request)["computed"] == []
