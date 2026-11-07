import json
from pathlib import Path

import numpy as np
import pytest
from netCDF4 import Dataset

from wrf_webvis.app.core.paths import metadata_path
from wrf_webvis.app.core.registry import VARIABLE_REGISTRY
from wrf_webvis.app.core.schemas import ProcessingRequest, VariableDimension
from wrf_processing.processing_manager import ProcessingManager
from wrf_processing.potential_vorticity import calculate_pvo as calculate


@pytest.fixture
# 功能说明：创建含压强、温度和风等变量的最小原始数据源。
def source(tmp_path):
    path = tmp_path / "wrfout_d03_2023-09-01_000000"
    with Dataset(path, "w") as ds:
        for name, size in {"Time": 2, "bottom_top": 4, "bottom_top_stag": 5,
                           "south_north": 5, "south_north_stag": 6,
                           "west_east": 5, "west_east_stag": 6, "DateStrLen": 19}.items():
            ds.createDimension(name, size)
        ds.DX, ds.DY = 2000.0, 3000.0
        ds.MAP_PROJ, ds.CEN_LAT, ds.CEN_LON = 0, 20.0, 120.0
        mass = ("Time", "bottom_top", "south_north", "west_east")
        horizontal = ("Time", "south_north", "west_east")
        ds.createVariable("U", "f4", mass[:-1] + ("west_east_stag",))[:] = 0
        ds.createVariable("V", "f4", mass[:2] + ("south_north_stag", "west_east"))[:] = 0
        t = ds.createVariable("T", "f4", mass, fill_value=-9999.0)
        t[0] = np.arange(4)[:, None, None] * 20.0
        t[1] = np.arange(4)[:, None, None] * 40.0
        ds.createVariable("P", "f4", mass)[:] = -10000.0 * np.arange(4)[None, :, None, None]
        ds.createVariable("PB", "f4", mass)[:] = 100000.0
        f = ds.createVariable("F", "f4", horizontal)
        f[0], f[1] = 5e-5, 8e-5
        ds.createVariable("XLAT", "f4", horizontal)[:] = 20.0
        ds.createVariable("XLONG", "f4", horizontal)[:] = 120.0
        ds.createVariable("HGT", "f4", horizontal)[:] = 0.0
        z_stag = ("Time", "bottom_top_stag", "south_north", "west_east")
        ds.createVariable("PH", "f4", z_stag)[:] = 9.81 * 500 * np.arange(5)[None, :, None, None]
        ds.createVariable("PHB", "f4", z_stag)[:] = 0
        times = ds.createVariable("Times", "S1", ("Time", "DateStrLen"))
        times[:] = np.array([list("2023-09-01_00:00:00"), list("2023-09-01_01:00:00")], dtype="S1")
    return path


# 功能说明：从测试数据源移除科氏参数 f，用于验证回退规则。
def without_f(source):
    with Dataset(source, "a") as ds:
        ds.renameVariable("F", "unused_F")


# 功能说明：写入成员本地的常数科氏参数文件，作为缺失原始参数时的备用输入。
def fallback_file(path, values):
    with Dataset(path, "w") as ds:
        ds.createDimension("y", 2)
        ds.createDimension("x", 2)
        ds.createVariable("F", "f8", ("y", "x"))[:] = values


# 功能说明：检查变量登记，确认位涡是按高度插值的标量诊断量。
def test_registered_as_height_interpolated_scalar():
    assert "pvo" in VARIABLE_REGISTRY
    item = VARIABLE_REGISTRY["pvo"]
    assert item.dimension == VariableDimension.THREE_D
    assert item.needs_height_interp and item.needs_polar
    assert not item.motion_correctable


@pytest.mark.parametrize("timeidx,slope,f", [(0, -0.002, 5e-5), (1, -0.004, 8e-5)])
# 功能说明：给出解析可算的位涡场和多个时次，确认计算使用完整压强剖面且只取选中时次。
def test_analytic_pv_uses_full_pressure_and_selected_time(source, timeidx, slope, f):
    # Calm flow with theta linear in pressure: PV = -g * f * dtheta/dp.
    with Dataset(source) as ds:
        values, metadata = calculate(ds, source_file=source, timeidx=timeidx)
    assert values.shape == (4, 5, 5)
    np.testing.assert_allclose(values, -9.81 * f * slope * 1e6, rtol=2e-6)
    assert metadata["units"] == "PVU"
    assert metadata["map_factor_mode"] == "unity"
    assert metadata["coriolis_source"] == str(source)


# 功能说明：同时提供原始 f 与不同备用值，确认优先采用当前原始文件的 f。
def test_current_f_wins_over_different_fallback(source):
    fallback_file(source.parent / "wrfinput_d01", 9e-5)
    with Dataset(source) as ds:
        values, _ = calculate(ds, source_file=source)
    np.testing.assert_allclose(values, 0.981, rtol=2e-6)


# 功能说明：原始数据缺 f 时提供成员备用常数，确认计算能继续并使用该值。
def test_missing_f_uses_member_local_constant_fallback(source):
    without_f(source)
    fallback = source.parent / "wrfinput_d01"
    fallback_file(fallback, 6e-5)
    with Dataset(source) as ds:
        values, metadata = calculate(ds, source_file=source)
    np.testing.assert_allclose(values, 1.1772, rtol=2e-6)
    assert metadata["coriolis_source"] == str(fallback)
    assert metadata["coriolis_mode"] == "wrfinput_d01_constant"


# 功能说明：指定自定义备用文件路径，确认它替代默认备用位置。
def test_fallback_path_override(source):
    without_f(source)
    fallback = source.parent / "custom_input"
    fallback_file(fallback, 6e-5)
    with Dataset(source) as ds:
        _, metadata = calculate(ds, source_file=source, wrfinput_d01_path="custom_input")
    assert metadata["coriolis_source"] == str(fallback)


# 功能说明：备用 f 文件含变化值时计算，确认不会悄悄取平均而是拒绝。
def test_nonconstant_fallback_is_not_silently_averaged(source):
    without_f(source)
    fallback_file(source.parent / "wrfinput_d01", [[4e-5, 5e-5], [6e-5, 7e-5]])
    with Dataset(source) as ds, pytest.raises(ValueError, match="constant|f-plane"):
        calculate(ds, source_file=source)


# 功能说明：原始和备用 f 都缺失时，确认报错指出需要补充什么文件。
def test_missing_fallback_has_actionable_error(source):
    without_f(source)
    with Dataset(source) as ds, pytest.raises(FileNotFoundError, match="wrfinput_d01"):
        calculate(ds, source_file=source)


@pytest.mark.parametrize("bad", [np.nan, np.inf, -9999.0])
# 功能说明：删除位涡计算必需的原始变量，确认系统明确拒绝输入。
def test_missing_native_input_is_rejected(source, bad):
    with Dataset(source, "a") as ds:
        ds.variables["T"][0, 1, 1, 1] = bad
    with Dataset(source) as ds, pytest.raises(ValueError, match="T.*missing|T.*finite"):
        calculate(ds, source_file=source)


# 功能说明：把 f 放在静态源文件中，确认位涡流程也能读取它。
def test_static_source_f_is_supported(source):
    without_f(source)
    with Dataset(source, "a") as ds:
        ds.createVariable("F", "f4", ("south_north", "west_east"))[:] = 5e-5
    with Dataset(source) as ds:
        values, _ = calculate(ds, source_file=source, timeidx=1)
    np.testing.assert_allclose(values, 1.962, rtol=2e-6)


# 功能说明：构造固体旋转的交错风场，确认计算正确使用网格间距和交错坐标。
def test_solid_body_rotation_uses_staggered_winds_and_dx_dy(source):
    omega = 1e-4
    with Dataset(source, "a") as ds:
        ds.variables["U"][:] = -omega * ds.DY * np.arange(5)[None, None, :, None]
        ds.variables["V"][:] = omega * ds.DX * np.arange(5)[None, None, None, :]
    with Dataset(source) as ds:
        values, _ = calculate(ds, source_file=source)
    np.testing.assert_allclose(values, 9.81 * (5e-5 + 2 * omega) * 0.002 * 1e6, rtol=2e-6)


# 功能说明：重复处理同一时次，确认第一次计算后第二次复用缓存。
def test_run_computes_once_per_time_and_reuses_cache(source, tmp_path, monkeypatch):
    from wrf_processing import potential_vorticity

    calls = []
    original = potential_vorticity.calculate_pvo

    # 功能说明：记录真实计算器被调用的次数，并把调用转交给原实现。
    def counted(*args, **kwargs):
        calls.append(kwargs["timeidx"])
        return original(*args, **kwargs)

    monkeypatch.setattr(potential_vorticity, "calculate_pvo", counted)
    manager = ProcessingManager(tmp_path / "processed")
    request = ProcessingRequest(
        processed_root=tmp_path / "processed", member_name="test", domain=3,
        wrfout_file=source, variables=["pvo"], times={"values": [0, 1]},
        heights={"values": [500, 1000]}, options={"wrfout_time_mode": "all_times"},
    )
    result = manager.run(request)
    assert result["status"] == "complete"
    assert len(result["processed_files"]) == 4
    assert calls == [0, 1]
    assert manager.run(request)["status"] == "skipped"
    assert calls == [0, 1]


@pytest.mark.parametrize("mode", ["batch", "single"])
# 功能说明：处理含缺测的位涡数据，确认按高度保存、写元数据且不丢失缺测标记。
def test_processing_interpolates_saves_metadata_and_preserves_missing(source, tmp_path, mode):
    root = tmp_path / "processed"
    request = ProcessingRequest(processed_root=root, member_name="test", domain=3,
                                variables=["pvo"], heights={"values": [500, 1000, 3000]})
    manager = ProcessingManager(root)
    kwargs = dict(request=request, source_file=source, source_time_index=1,
                  time_hour=1, dtype="float32")
    if mode == "batch":
        paths = manager._process_wrf_variable_batch(
            **kwargs, variables=[("pvo", VariableDimension.THREE_D, [500, 1000, 3000])])
    else:
        paths = manager._process_3d_variable(**kwargs, variable="pvo", heights=[500, 1000, 3000])
    assert len(paths) == 3
    for path in paths[:2]:
        values = np.load(path)
        assert values.shape == (5, 5) and values.dtype == np.float32
        np.testing.assert_allclose(values, 3.1392, rtol=2e-6)
        metadata = json.loads(metadata_path(Path(path)).read_text())
        assert metadata["units"] == "PVU"
        assert metadata["source_time_index"] == 1
        assert metadata["coriolis_source"] == str(source)
        assert not metadata.get("motion_corrected", False)
    assert np.isnan(np.load(paths[2])).all()


# 功能说明：请求位涡极坐标产品，确认先为所选高度准备位涡缓存。
def test_polar_requests_ensure_pvo_at_requested_heights(tmp_path, monkeypatch):
    from wrf_processing.polar_transformer import PolarTransformer

    manager = ProcessingManager(tmp_path)
    request = ProcessingRequest(processed_root=tmp_path, member_name="test", domain=3,
                                variables=["pvo_polar"], times={"values": [1]},
                                heights={"values": [500, 1000]})
    ensured, transformed = [], []
    monkeypatch.setattr(manager, "_ensure_standard_data", lambda req, variables, heights, progress:
                        ensured.append((variables, heights.values)))
    monkeypatch.setattr(manager, "_ensure_slp_center", lambda *args: None)
    monkeypatch.setattr(PolarTransformer, "transform_3d_per_height", lambda self, **kwargs:
                        transformed.append(kwargs) or [])
    manager._run_operation_variable(request, "pvo_polar", None)
    assert ensured == [(["pvo"], [500, 1000])]
    assert transformed[0]["variable"] == "pvo"
    assert transformed[0]["heights_m"] == [500, 1000]
