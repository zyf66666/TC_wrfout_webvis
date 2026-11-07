from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from wrf_processing_v3.dependency_planner import DependencyPlanner
from wrf_processing_v3.processing_manager import ProcessingManager
from wrf_processing_v3.register import build_registry
from wrf_processing_v3.request import Request, Task
from wrf_processing_v3.storage import NetCDFStorage
from wrf_processing_v3.variables import (
    shear_200_850_direction,
    shear_200_850_magnitude,
    shear_200_850_u,
    shear_200_850_v,
)


SHEAR_NAMES = tuple(f"shear_200_850_{part}" for part in ("u", "v", "magnitude", "direction"))


def test_d03_shear_plans_d02_winds_and_center(tmp_path):
    options = {"shear_source_domain": 2}
    registry = build_registry(options, times_minutes=(0,))
    request = Request(
        experiments=("exp",),
        domains=(3,),
        times_minutes=(0,),
        heights_m=(100,),
        variables=SHEAR_NAMES,
        source_root=tmp_path / "source",
        output_root=tmp_path / "out",
        options=options,
    )
    storage = NetCDFStorage(request.output_root, time_origin="2023-09-01_00:00:00")
    plan = DependencyPlanner(registry, storage).plan(request)
    for name in SHEAR_NAMES:
        spec = registry.resolve(name)
        assert [(dep.name, dep.domain) for dep in spec.dependencies] == [
            ("_ua_upper", 2),
            ("_ua_lower", 2),
            ("_va_upper", 2),
            ("_va_lower", 2),
            ("slp_center", 2),
        ]
        assert spec.options["shear_radius_inner_km"] == 150.0
        assert spec.options["shear_radius_outer_km"] == 350.0
        assert any(item.task.variable == name and item.task.domain == 3 for item in plan)
    assert {item.task.domain for item in plan if item.task.variable == "slp_center"} == {2}


@pytest.mark.parametrize(
    ("module", "part"),
    [
        (shear_200_850_u, "u"),
        (shear_200_850_v, "v"),
        (shear_200_850_magnitude, "magnitude"),
        (shear_200_850_direction, "direction"),
    ],
)
def test_shear_uses_150_to_350_km_on_source_grid(module, part):
    yy, xx = np.indices((201, 201))
    distance_km = np.hypot(yy - 100, xx - 100) * 5.0
    data = {
        "slp_center": np.array([100.0, 100.0]),
        "_ua_upper": distance_km,
        "_ua_lower": np.zeros_like(distance_km),
        "_va_upper": np.full_like(distance_km, 8.0),
        "_va_lower": np.full_like(distance_km, 3.0),
    }

    class Context:
        task = SimpleNamespace(variable=f"shear_200_850_{part}", domain=3)
        spec = SimpleNamespace(
            options={
                "shear_source_domain": 2,
                "shear_radius_inner_km": 150.0,
                "shear_radius_outer_km": 350.0,
            }
        )

        def read(self, variable):
            return data[variable]

        def read_metadata(self, variable):
            assert variable == "_ua_upper"
            return {"grid_spacing": {"dx_km": 5.0, "dy_km": 5.0}}

        def save(self, variable, values, metadata=None):
            self.saved = (variable, values, metadata)
            return values

    ctx = Context()
    result = module.process(ctx)
    expected = {
        "u": 250.0,
        "v": 5.0,
        "magnitude": np.hypot(250.0, 5.0),
        "direction": -np.degrees(np.arctan2(5.0, 250.0)),
    }
    np.testing.assert_allclose(result, [expected[part]], atol=0.15, rtol=0)
    assert ctx.saved[0] == ctx.task.variable
    assert ctx.saved[2]["shear_source_domain"] == 2


def test_pipeline_reads_cached_d02_inputs_and_writes_d03_shear(tmp_path):
    options = {"time_origin": "2023-09-01_00:00:00", "shear_source_domain": 2}
    registry = build_registry(options, times_minutes=(0,))
    request = Request(
        experiments=("exp",),
        domains=(3,),
        times_minutes=(0,),
        heights_m=(100,),
        variables=SHEAR_NAMES,
        source_root=tmp_path / "source",
        output_root=tmp_path / "out",
        options=options,
    )
    storage = NetCDFStorage(request.output_root, time_origin=options["time_origin"])
    for name, value in {
        "_ua_upper": 20.0,
        "_ua_lower": 10.0,
        "_va_upper": 7.0,
        "_va_lower": 3.0,
    }.items():
        storage.save(
            Task("exp", 2, 0, name, None),
            np.full((201, 201), value),
            registry.resolve(name),
            metadata={"grid_spacing": {"dx_km": 5.0, "dy_km": 5.0}},
        )
    storage.save(
        Task("exp", 2, 0, "slp_center", None),
        np.array([100.0, 100.0]),
        registry.resolve("slp_center"),
    )

    class Reader:
        def prepare(self, tasks, req, reg, out):
            assert not tasks

    result = ProcessingManager(registry=registry, reader=Reader()).process(request)
    assert len(result["computed"]) == 4
    assert not result["skipped"]
    expected = {"u": 10.0, "v": 4.0, "magnitude": np.hypot(10.0, 4.0)}
    expected["direction"] = -np.degrees(np.arctan2(4.0, 10.0))
    for part, value in expected.items():
        task = Task("exp", 3, 0, f"shear_200_850_{part}", None)
        np.testing.assert_allclose(storage.load(task), [value], atol=1e-4)
        assert storage.load_metadata(task)["shear_source_domain"] == 2
