"""相对涡度：解析旋转场、平移不变性和登记口径。"""
import numpy as np


def test_relative_vorticity_solid_rotation_anisotropic_grid():
    from wrf_processing_v3.variables.vort_rel import calculate
    y, x = np.indices((9, 11), dtype=float)
    x *= 1000.0
    y *= 2000.0
    omega = 2.0e-4
    u, v = -omega*y + 7.0, omega*x - 5.0
    np.testing.assert_allclose(calculate(u, v, dx_m=1000, dy_m=2000), 2*omega,
                               atol=1e-15)


def test_relative_vorticity_irrotational_strain():
    from wrf_processing_v3.variables.vort_rel import calculate
    y, x = np.indices((9, 11), dtype=float)
    np.testing.assert_allclose(calculate(0.01*x, -0.01*y, dx_m=1000, dy_m=1000), 0, atol=1e-15)


def test_vort_rel_registry():
    from wrf_processing_v3.register import build_registry
    spec = build_registry().resolve("vort_rel")
    assert spec.units == "s-1"
    assert spec.dimensions == "height"
    assert {d.name for d in spec.dependencies} == {"ua", "va"}


def test_processor_saves_units_and_grid_spacing():
    from types import SimpleNamespace
    from wrf_processing_v3.variables.vort_rel import process
    y, x = np.indices((5, 7), dtype=float)
    fields = {"ua": -0.0002*y*2000, "va": 0.0002*x*1000}
    saved = []
    ctx = SimpleNamespace(options={"domain_config": {3: {"dx_km": 1, "dy_km": 2}}},
                          task=SimpleNamespace(domain=3, variable="vort_rel"),
                          spec=SimpleNamespace(units="s-1"), read=fields.__getitem__,
                          save=lambda name, values, metadata: saved.append((name, values, metadata)))
    process(ctx)
    assert saved[0][0] == "vort_rel"
    np.testing.assert_allclose(saved[0][1], 4e-4, atol=1e-15)
    assert saved[0][2]["units"] == "s-1"
