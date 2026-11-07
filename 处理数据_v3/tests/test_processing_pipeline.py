from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from netCDF4 import Dataset


def _write_surface_wrf(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with Dataset(path, "w") as dataset:
        for name, size in [
            ("Time", 2),
            ("DateStrLen", 19),
            ("south_north", 3),
            ("west_east", 4),
        ]:
            dataset.createDimension(name, size)
        times = dataset.createVariable("Times", "S1", ("Time", "DateStrLen"))
        times[:] = np.asarray(
            [list("2023-09-01_00:00:00"), list("2023-09-01_00:05:00")],
            dtype="S1",
        )
        dataset.START_DATE = "2023-09-01_00:00:00"
        dataset.SIMULATION_START_DATE = "2023-08-31_23:00:00"
        dataset.DX = 1000.0
        dataset.DY = 1000.0
        dataset.MAP_PROJ = 0
        for name, value in [("U10", 3.0), ("V10", 4.0)]:
            field = dataset.createVariable(
                name, "f4", ("Time", "south_north", "west_east")
            )
            field[:] = value


def test_real_netcdf_pipeline_batches_minutes_and_reuses_completed_slices(tmp_path, capsys):
    try:
        from wrf_processing_v3.processing_manager import ProcessingManager
        from wrf_processing_v3.request import Request, Task
        from wrf_processing_v3.storage import NetCDFStorage
    except ImportError:
        pytest.fail("v3 execution pipeline has not been implemented")

    source = tmp_path / "source" / "exp" / "wrfout_d03_sample"
    _write_surface_wrf(source)
    request = Request(
        experiments=("exp",),
        domains=(3,),
        times_minutes=(0, 5),
        heights_m=(100,),
        variables=("wind_speed_10",),
        source_root=tmp_path / "source",
        output_root=tmp_path / "out",
        options={"verbose": True},
    )

    manager = ProcessingManager()
    first = manager.process(request)
    assert len(first["computed"]) == 6
    storage = NetCDFStorage(
        request.output_root, time_origin="2023-09-01_00:00:00"
    )
    for minute in request.times_minutes:
        task = Task("exp", 3, minute, "wind_speed_10", None)
        np.testing.assert_allclose(storage.load(task), 5.0)
    second = manager.process(request)
    assert len(second["reused"]) == 2
    assert second["computed"] == []
    output = capsys.readouterr().out
    assert "5min wind_speed_10" in output
    assert "5h wind_speed_10" not in output


def test_lowest_wind_diagnostic_uses_model_level_and_reuses_its_dependencies(tmp_path):
    from wrf_processing_v3.processing_manager import ProcessingManager
    from wrf_processing_v3.request import Request, Task
    from wrf_processing_v3.storage import NetCDFStorage
    from wrf_processing_v3.wrf_reader import NetCDFBackend, WrfReader

    source = tmp_path / "source" / "exp" / "wrfout_d03_sample"
    _write_surface_wrf(source)

    class Backend(NetCDFBackend):
        def getvar(self, dataset, name, timeidx, **kwargs):
            lower, upper = {"ua": (3, 30), "va": (4, 40), "z": (100, 1000)}[name]
            return np.stack([np.full((3, 4), lower), np.full((3, 4), upper)])

        def interpolate(self, field, z, height):
            assert height == 1000
            return field[1]

    request = Request(
        experiments=("exp",), domains=(3,), times_minutes=(0, 5),
        heights_m=(1000,), variables=("local_max_wind", "ua"),
        source_root=tmp_path / "source", output_root=tmp_path / "out",
        options={"time_origin": "2023-09-01_00:00:00"},
    )
    manager = ProcessingManager(reader=WrfReader(backend=Backend()))
    first = manager.process(request)
    assert len(first["computed"]) == 8
    storage = NetCDFStorage(request.output_root, time_origin=request.options["time_origin"])
    for minute in request.times_minutes:
        np.testing.assert_allclose(storage.load(Task("exp", 3, minute, "local_max_wind", None)), [5.0])
        np.testing.assert_allclose(storage.load(Task("exp", 3, minute, "ua", "lowest")), 3.0)
        np.testing.assert_allclose(storage.load(Task("exp", 3, minute, "ua", 1000)), 30.0)

    second = manager.process(request)
    assert second["computed"] == []
    assert len(second["reused"]) == 4
    forced = manager.process(replace(request, options={**request.options, "force_variables": ["local_max_wind"]}))
    assert len(forced["computed"]) == 2
    assert len(forced["reused"]) == 6


def test_cross_domain_theta_anomaly_polar_executes_existing_variable_modules(tmp_path):
    try:
        from wrf_processing_v3.processing_manager import ProcessingManager
        from wrf_processing_v3.register import build_registry
        from wrf_processing_v3.request import Request, Task
        from wrf_processing_v3.storage import NetCDFStorage
    except ImportError:
        pytest.fail("v3 context/executor integration has not been implemented")

    options = {
        "time_origin": "2023-09-01_00:00:00",
        "domain_config": {
            2: {"dx_km": 1.0, "dy_km": 1.0},
            3: {"dx_km": 1.0, "dy_km": 1.0},
        },
        "center_options_by_domain": {
            2: {
                "search_radius_km": 3.0,
                "cross_radius_grid": 3,
                "pcc_radius_grid": 1,
                "boundary_width": 2,
                "max_iterations": 2,
            },
            3: {
                "search_radius_km": 3.0,
                "cross_radius_grid": 3,
                "pcc_radius_grid": 1,
                "boundary_width": 2,
                "max_iterations": 2,
            },
        },
        "radius_km_by_domain": {2: 8.0, 3: 3.0},
        "radius_resolution_km": 1.0,
        "theta_environment_domain": 2,
        "theta_environment_ring_km": [4.0, 5.0],
        "layer_polar_boundary_policy": "off",
    }
    request = Request(
        experiments=("exp",),
        domains=(3,),
        times_minutes=(0,),
        heights_m=(100, 1000),
        variables=("theta_anomaly1_polar",),
        source_root=tmp_path / "source",
        output_root=tmp_path / "out",
        options=options,
    )
    registry = build_registry(options, times_minutes=request.times_minutes)

    class Reader:
        def prepare(self, tasks, req, reg, storage):
            yy, xx = np.mgrid[:31, :31]
            bowl = 1000.0 + (yy - 15) ** 2 + (xx - 15) ** 2
            for task in tasks:
                if task.variable == "theta":
                    value = 300.0 if task.domain == 2 else 320.0
                    data = np.full((31, 31), value, dtype=np.float32)
                elif task.variable in {"pressure", "slp"}:
                    data = bowl.astype(np.float32)
                else:
                    raise AssertionError(task)
                storage.save(
                    task,
                    data,
                    reg.resolve(task.variable),
                    metadata={"grid_spacing": {"dx_km": 1.0, "dy_km": 1.0}},
                )

    manager = ProcessingManager(registry=registry, reader=Reader())
    result = manager.process(request)
    assert result["computed"]
    storage = NetCDFStorage(
        request.output_root, time_origin="2023-09-01_00:00:00"
    )
    for height in request.heights_m:
        cartesian = storage.load(Task("exp", 3, 0, "theta_anomaly", height))
        polar = storage.load(
            Task("exp", 3, 0, "theta_anomaly1_polar", height)
        )
        np.testing.assert_allclose(cartesian, 20.0, atol=1.0e-5)
        assert polar.shape == (360, 4)
        np.testing.assert_allclose(polar, 20.0, atol=1.0e-5)
