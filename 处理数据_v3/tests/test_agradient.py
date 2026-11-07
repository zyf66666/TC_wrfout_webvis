"""超梯度力诊断 pgf_r / cen_r / cor_r / motf_r / agf_r（2026-10-26，Claude）。

核对思路：构造 f 平面上严格梯度风平衡的轴对称涡旋（p 由 dp/dr = ρ(v²/r + f v) 积分得到），
再叠加匀速平移 c 和与之地转平衡的环境气压场。风暴相对、加上 f·c_t 修正后，四项之和应为 0；
把风放大 1.2 倍则得到解析可算的超梯度力。
"""

from __future__ import annotations

import numpy as np
import pytest

F = 4.9881e-05
RHO = 1.1
DX_KM = 0.5
N = 161            # 80 km × 80 km，中心在 (80, 80)
CENTER = 80.0
VM, RM = 30.0, 10_000.0
T = 24 * 60        # 目标时次（分钟）
H = 1000           # 高度（m）
EARTH = 6371000.0


def _vortex_profile(r):
    return VM * (r / RM) * np.exp(0.5 * (1.0 - (r / RM) ** 2))


def _fields(scale=1.0, motion=(0.0, 0.0)):
    yy, xx = np.indices((N, N), dtype=float)
    x = (xx - CENTER) * DX_KM * 1000.0
    y = (yy - CENTER) * DX_KM * 1000.0
    r = np.hypot(x, y)
    cos = np.divide(x, r, out=np.zeros_like(x), where=r > 0)
    sin = np.divide(y, r, out=np.zeros_like(y), where=r > 0)
    # 平衡气压：在细的一维网格上积分 dp/dr，再插值到二维距离。
    rr = np.linspace(0.0, r.max() + 1000.0, 200_001)
    vv = _vortex_profile(rr)
    integrand = RHO * (np.divide(vv ** 2, rr, out=np.zeros_like(rr), where=rr > 0) + F * vv)
    pp = np.concatenate([[0.0], np.cumsum(0.5 * (integrand[1:] + integrand[:-1]) * np.diff(rr))])
    p_pa = 95_000.0 + np.interp(r, rr, pp)
    cx, cy = motion
    # 环境地转气压：∇p_env = ρ f (c_y, −c_x)。
    p_pa = p_pa + RHO * F * (cy * x - cx * y)
    vt = scale * _vortex_profile(r)
    u = -vt * sin + cx
    v = vt * cos + cy
    return dict(u=u, v=v, p_hpa=p_pa / 100.0, rho=np.full_like(r, RHO), r=r)


def _request(tmp_path, variables=("agf_r",), options=None):
    from wrf_processing_v3.request import Request

    return Request(
        experiments=("exp",), domains=(3,), times_minutes=(T,), heights_m=(H,), variables=variables,
        source_root=tmp_path / "source", output_root=tmp_path / "out",
        options={"time_origin": "2023-09-01_00:00:00",
                 "domain_config": {3: {"dx_km": DX_KM, "dy_km": DX_KM}},
                 "coriolis_f": F, **(options or {})},
    )


def _run_all(tmp_path, fields, motion):
    """把输入写进 v3 存储，按依赖顺序运行 5 个处理器，返回各项结果。"""
    from wrf_processing_v3.context import ProcessingContext
    from wrf_processing_v3.register import build_registry
    from wrf_processing_v3.request import Task
    from wrf_processing_v3.storage import NetCDFStorage
    from wrf_processing_v3.variables import agf_r, cen_r, cor_r, motf_r, pgf_r

    request = _request(tmp_path)
    registry = build_registry(request.options, times_minutes=request.times_minutes)
    storage = NetCDFStorage(request.output_root, time_origin=request.options["time_origin"])
    save = lambda name, minute, height, value: storage.save(  # noqa: E731
        Task("exp", 3, minute, name, height), np.asarray(value), registry.resolve(name))
    save("slp_center", T, None, [CENTER, CENTER])
    save("slp", T, None, fields["p_hpa"])
    for name, key in (("ua", "u"), ("va", "v"), ("pressure", "p_hpa"), ("density", "rho")):
        save(name, T, H, fields[key])
    # center_geo 在 t ± 3 h：位移 = 移速 × 6 h（与 motion_from_centers 的公式互逆）。
    lat0, lon0, dt = 20.0, -60.0, 6 * 3600.0
    lat1 = lat0 + np.rad2deg(motion[1] * dt / EARTH)
    lon1 = lon0 + np.rad2deg(motion[0] * dt / (EARTH * np.cos(np.deg2rad(0.5 * (lat0 + lat1)))))
    save("center_geo", T - 180, None, [lat0, lon0])
    save("center_geo", T + 180, None, [lat1, lon1])
    out = {}
    for module, name, height in ((pgf_r, "pgf_r", H), (cen_r, "cen_r", H), (cor_r, "cor_r", H),
                                 (motf_r, "motf_r", None), (agf_r, "agf_r", H)):
        task = Task("exp", 3, T, name, height)
        module.process(ProcessingContext(task, request, registry.resolve(name), storage, registry))
        out[name] = np.asarray(storage.load(task), dtype=float)
        out[name + "_meta"] = storage.load_metadata(task)
    return out


def _annulus(r, lo_km=3.0, hi_km=30.0):
    return (r > lo_km * 1000) & (r < hi_km * 1000)


# 功能说明：静止的梯度风平衡涡旋：PGF 与 离心+科氏 相抵，合力≈0。
def test_balanced_vortex_has_zero_agradient_force(tmp_path):
    fields = _fields()
    out = _run_all(tmp_path, fields, (0.0, 0.0))
    ring = _annulus(fields["r"])
    scale = np.nanmax(np.abs(out["pgf_r"][ring]))
    assert scale > 0.05                                   # PGF 量级约 VM²/RM + f VM
    assert np.nanmax(np.abs(out["agf_r"][ring])) < 0.01 * scale
    assert np.nanmax(np.abs(out["motf_r"])) == 0.0
    assert np.all(np.isnan(out["cen_r"][fields["r"] < 2000]))


# 功能说明：平移的平衡涡旋 + 环境地转气压：必须先减移速、再加 f·c_t，合力才为 0。
def test_translating_balanced_vortex_needs_storm_relative_wind_and_motion_term(tmp_path):
    motion = (2.0, -4.0)
    fields = _fields(motion=motion)
    out = _run_all(tmp_path, fields, motion)
    ring = _annulus(fields["r"])
    scale = np.nanmax(np.abs(out["pgf_r"][ring]))
    np.testing.assert_allclose(out["cen_r_meta"]["storm_motion_u_v_m_s"], motion, atol=1e-3)  # center_geo 以 float32 存
    assert out["cen_r_meta"]["motion_half_window_h"] == 3.0
    assert np.nanmax(np.abs(out["agf_r"][ring])) < 0.01 * scale
    # f·c_t 的幅度 = f |c|，若漏掉它，合力会留下这一大小的一波数残差。
    assert np.nanmax(np.abs(out["motf_r"])) == pytest.approx(F * np.hypot(*motion), rel=1e-3)


# 功能说明：风放大 1.2 倍（气压不变）→ 超梯度，RMW 处合力 = (1.44−1)Vm²/Rm + 0.2 f Vm，且向外。
def test_supergradient_value_at_rmw(tmp_path):
    fields = _fields(scale=1.2)
    out = _run_all(tmp_path, fields, (0.0, 0.0))
    yy, xx = np.indices((N, N))
    at_rmw = (yy == int(CENTER)) & (xx == int(CENTER + RM / (DX_KM * 1000)))
    expected = 0.44 * VM ** 2 / RM + 0.2 * F * VM
    assert out["agf_r"][at_rmw][0] == pytest.approx(expected, rel=0.01)
    assert expected > 0


# 功能说明：登记的单位、维度、存储布局与依赖（含 center_geo 的 ±3 h 偏移）。
def test_registry_entries_and_dependencies(tmp_path):
    from wrf_processing_v3.register import build_registry

    registry = build_registry(_request(tmp_path).options, times_minutes=(T,))
    for name in ("pgf_r", "cen_r", "cor_r", "agf_r"):
        spec = registry.resolve(name)
        assert spec.units == "m s-2" and spec.dimensions == "height" and spec.storage_layout == "cartesian_3d"
    assert registry.resolve("motf_r").storage_layout == "cartesian_2d"
    assert registry.resolve("agf_r_polar").storage_layout == "polar_3d"
    cen = registry.resolve("cen_r")
    assert [(d.name, d.height_m, d.time_offset_minutes) for d in cen.dependencies] == [
        ("ua", "same", 0), ("va", "same", 0), ("slp_center", None, 0),
        ("center_geo", None, -180), ("center_geo", None, 180),
    ]
    assert cen.options["coriolis_f"] == F
    assert [d.name for d in registry.resolve("pgf_r").dependencies] == ["pressure", "density", "slp_center"]
    assert [d.name for d in registry.resolve("agf_r").dependencies] == ["pgf_r", "cen_r", "cor_r", "motf_r"]


# 功能说明：配置已对 ua/va 做移动订正时，改读地球相对风，避免重复减移速；窗口可配置。
def test_motion_corrected_profile_uses_earth_relative_wind(tmp_path):
    from wrf_processing_v3.register import build_registry

    options = _request(tmp_path, options={"motion_correct": True, "agradient_motion_half_window_h": 2.0}).options
    cen = build_registry(options, times_minutes=(T,)).resolve("cen_r")
    names = [d.name for d in cen.dependencies]
    assert names[:2] == ["_ua_earth", "_va_earth"]
    assert [d.time_offset_minutes for d in cen.dependencies if d.name == "center_geo"] == [-120, 120]


# 功能说明：依赖规划会安排 t ± 3 h 的 center_geo 与同高度的 pressure、density、ua、va。
def test_planner_schedules_offset_centers(tmp_path):
    from wrf_processing_v3.dependency_planner import DependencyPlanner
    from wrf_processing_v3.register import build_registry
    from wrf_processing_v3.request import Task
    from wrf_processing_v3.storage import NetCDFStorage

    request = _request(tmp_path)
    registry = build_registry(request.options, times_minutes=request.times_minutes)
    storage = NetCDFStorage(request.output_root, time_origin=request.options["time_origin"])
    tasks = {item.task for item in DependencyPlanner(registry, storage).plan(request)}
    for name in ("agf_r", "pgf_r", "cen_r", "cor_r", "ua", "va", "pressure", "density"):
        assert Task("exp", 3, T, name, H) in tasks
    assert Task("exp", 3, T, "motf_r", None) in tasks
    assert Task("exp", 3, T - 180, "center_geo", None) in tasks
    assert Task("exp", 3, T + 180, "center_geo", None) in tasks
