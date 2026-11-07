import importlib
import sys
import shutil
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "处理数据"))
from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.schemas import ProcessingRequest, SelectionSpec


# 功能说明：为两个高度、三个时次写入层中心和湿度格点；高层在中间时次靠近边界，并返回对应请求。
def inputs(tmp_path):
    paths = PathManager(tmp_path)
    for height, xs in [(100, [4, 4, 4]), (1000, [3, 2, 3])]:
        center = paths.layer_center_path("member", 3, height)
        center.parent.mkdir(parents=True, exist_ok=True)
        np.savetxt(center, [[t, 4, x] for t, x in enumerate(xs)])
        for t in range(3):
            source = paths.variable_path(member_name="member", domain=3, variable="rh",
                                         time_hour=t, height_m=height, dimension="3d")
            source.parent.mkdir(parents=True, exist_ok=True)
            np.save(source, np.ones((9, 9), dtype=np.float32))
    return ProcessingRequest(processed_root=tmp_path, member_name="member", domain=3,
                             variables=["rh1_polar"], times=SelectionSpec(values=[0, 1, 2]),
                             heights=SelectionSpec(values=[100, 1000]),
                             options={"polar_max_radius_km": 3})


# 功能说明：调用 rh1 极坐标安全审计，使用一公里网格间距评估每个时次和高度可用的半径。
def audit(request):
    module = importlib.import_module("wrf_processing.rh1_polar_safety")
    return module.audit_rh1_polar_times(request, domain_config={3: {"dx_km": 1, "dy_km": 1}})


# 功能说明：让高层中心仅在第 1 小时偏到边缘，确认该时次不安全、随后第 2 小时恢复仍会保留。
def test_high_layer_limits_whole_time_and_later_recovery_is_retained(tmp_path):
    result = audit(inputs(tmp_path))
    assert result["safe_times"] == [0, 2]
    assert result["unsafe_times"] == [1]
    assert result["continuous_end"] == 0
    assert result["last_safe_time"] == 2
    assert result["per_time"][1]["limiting_height_m"] == 1000
    assert result["per_time"][1]["safe_radius_km"] == 2


# 功能说明：分别把中心放在边界及删去中间时次中心，确认边界会判为不安全，而缺失记录会明确报错。
def test_boundary_center_is_unsafe_and_missing_center_is_not_silently_skipped(tmp_path):
    request = inputs(tmp_path)
    path = PathManager(tmp_path).layer_center_path("member", 3, 1000)
    np.savetxt(path, [[0, 4, 0], [1, 4, 2], [2, 4, 3]])
    assert audit(request)["safe_times"] == [2]
    np.savetxt(path, [[0, 4, 3], [2, 4, 3]])
    with pytest.raises(ValueError, match="1000.*1h"):
        audit(request)


# 功能说明：把某一高度的湿度数组改成较窄形状，确认审计按实际数组边界而非假定尺寸判断安全性。
def test_checks_actual_shape_of_each_height(tmp_path):
    request = inputs(tmp_path)
    source = PathManager(tmp_path).variable_path(member_name="member", domain=3, variable="rh",
                                                time_hour=2, height_m=1000, dimension="3d")
    np.save(source, np.ones((6, 9)))
    assert audit(request)["safe_times"] == [0]


# 功能说明：让 rh1_polar 与普通变量一起请求，确认入口只筛掉不安全时次，并可按设置在首个不安全时次停止。
def test_entrypoint_filters_only_rh1_and_can_stop_at_first_unsafe_time(tmp_path, monkeypatch):
    entry = importlib.import_module("process_data")
    request = inputs(tmp_path).model_copy(update={"variables": ["rh1_polar", "pvo"]})
    monkeypatch.setattr(entry, "LAYER_POLAR_BOUNDARY_POLICY", "continuous", raising=False)
    monkeypatch.setattr(entry, "LAYER_POLAR_AUDIT_ONLY", False, raising=False)
    monkeypatch.setattr(entry, "prepare_layer_polar_inputs", lambda request: None, raising=False)
    monkeypatch.setattr(entry, "domain_config_dict", lambda request: {3: {"dx_km": 1}}, raising=False)
    calls = []
    monkeypatch.setattr(entry, "_run_processing_request", lambda r: calls.append(r) or {"status": "complete"}, raising=False)
    entry.run_processing_request(request)
    assert [(r.variables, r.times.values) for r in calls] == [(["pvo"], [0, 1, 2]), (["rh1_polar"], [0])]
    assert (tmp_path / "member/d03/rh1_polar_safety.json").exists()


# 功能说明：把实际计算函数替换为会失败的占位函数，确认纯审计模式只报告结果而不会开始计算。
def test_audit_only_does_not_start_computation(tmp_path, monkeypatch):
    entry = importlib.import_module("process_data")
    monkeypatch.setattr(entry, "LAYER_POLAR_BOUNDARY_POLICY", "all_safe", raising=False)
    monkeypatch.setattr(entry, "LAYER_POLAR_AUDIT_ONLY", True, raising=False)
    monkeypatch.setattr(entry, "domain_config_dict", lambda request: {3: {"dx_km": 1}}, raising=False)
    # 功能说明：作为防护占位函数，一旦审计模式错误地开始计算就让测试立即失败。
    def forbidden(*args, **kwargs):
        pytest.fail("audit-only must not compute inputs or polar fields")
    monkeypatch.setattr(entry, "prepare_layer_polar_inputs", forbidden, raising=False)
    monkeypatch.setattr(entry, "_run_processing_request", forbidden, raising=False)
    result = entry.run_processing_request(inputs(tmp_path))
    assert result["status"] == "audit_only"


# 功能说明：使用真实极坐标转换流程处理安全时次，确认只生成审计允许的输出文件。
def test_entrypoint_computes_safe_times_with_real_transformer(tmp_path, monkeypatch):
    entry = importlib.import_module("process_data")
    monkeypatch.setattr(entry, "LAYER_POLAR_BOUNDARY_POLICY", "all_safe")
    monkeypatch.setattr(entry, "LAYER_POLAR_AUDIT_ONLY", False)
    monkeypatch.setattr(entry, "DRY_RUN", False)
    monkeypatch.setattr(entry, "prepare_layer_polar_inputs", lambda request: None)
    monkeypatch.setattr(entry, "domain_config_dict", lambda request: {3: {"dx_km": 1}})
    entry.run_processing_request(inputs(tmp_path))
    root = tmp_path / "member/d03/rh1_polar"
    assert not (root / "1h").exists()
    for time in [0, 2]:
        for height in [100, 1000]:
            result = np.load(root / f"{time}h/{height}m.npy")
            assert result.shape == (360, 4)
            np.testing.assert_array_equal(result, 1)


@pytest.mark.parametrize("variable", ["rh1_polar", "pvo1_polar"])
# 功能说明：在主审计模式下禁止读取原始挂载和辅助量，确认已缓存输入足以完成安全检查。
def test_main_audit_only_needs_no_raw_mount_or_auxiliary_computations(tmp_path, monkeypatch, variable):
    entry = importlib.import_module("process_data")
    inputs(tmp_path)
    if variable == "pvo1_polar":
        (tmp_path / "member/d03/rh").rename(tmp_path / "member/d03/pvo")
    for name, value in dict(EXPERIMENTS=["member"], DOMAINS=[3], VARIABLES=[variable],
                            PROCESSED_ROOT=tmp_path, TIMES_BY_EXPERIMENT={"member": [0, 1, 2]},
                            HEIGHTS=[100, 1000], OPTIONS={"polar_max_radius_km": 3},
                            LAYER_POLAR_AUDIT_ONLY=True, LAYER_POLAR_BOUNDARY_POLICY="all_safe",
                            GRID_COORDINATE_MEMBERS=["member"], TRACK_CENTER_MEMBERS=["member"]).items():
        monkeypatch.setattr(entry, name, value)
    monkeypatch.setattr(entry, "resolve_wrfout_root", lambda *args: tmp_path / "unmounted")
    monkeypatch.setattr(entry, "domain_config_dict", lambda request: {3: {"dx_km": 1}})
    # 功能说明：作为防护占位函数，一旦主审计错误读取原始数据或辅助计算就让测试立即失败。
    def forbidden(*args, **kwargs):
        pytest.fail("audit-only must skip auxiliary calculations")
    monkeypatch.setattr(entry, "ensure_grid_coordinates", forbidden)
    monkeypatch.setattr(entry, "ensure_track_center_coordinates", forbidden)
    assert entry.main() == 0


# 功能说明：只保留完整的湿度和中心缓存，确认审计不需要气压或原始 WRF 文件。
def test_complete_cached_inputs_do_not_require_pressure_or_raw_files(tmp_path):
    entry = importlib.import_module("process_data")
    entry.prepare_layer_polar_inputs(inputs(tmp_path))


# 功能说明：要求强制重算极坐标湿度，确认仍可复用刚完成审计的输入而不重复准备数据。
def test_force_recomputes_polar_using_the_already_audited_inputs(tmp_path, monkeypatch):
    entry = importlib.import_module("process_data")
    request = inputs(tmp_path).model_copy(update={"force": True})
    monkeypatch.setattr(entry, "LAYER_POLAR_BOUNDARY_POLICY", "all_safe")
    monkeypatch.setattr(entry, "LAYER_POLAR_AUDIT_ONLY", False)
    monkeypatch.setattr(entry, "DRY_RUN", False)
    monkeypatch.setattr(entry, "prepare_layer_polar_inputs", lambda request: None)
    monkeypatch.setattr(entry, "domain_config_dict", lambda request: {3: {"dx_km": 1}})
    output = tmp_path / "member/d03/rh1_polar/0h/100m.npy"
    output.parent.mkdir(parents=True)
    np.save(output, np.zeros((360, 4)))
    entry.run_processing_request(request)
    np.testing.assert_array_equal(np.load(output), 1)


# 功能说明：并行处理时禁止重新打开气压文件，确认工作进程使用预先准备的层中心。
def test_parallel_workers_use_prepared_centers_without_reopening_pressure(tmp_path, monkeypatch):
    entry = importlib.import_module("process_data")
    request = inputs(tmp_path).model_copy(update={"options": {
        "polar_max_radius_km": 3, "parallel_workers": 2, "prepared_layer_polar_variables": ["rh1_polar"]}})
    monkeypatch.setattr(entry, "PROCESSING_MODE", "parallel")
    # 功能说明：作为防护占位函数，一旦并行流程重新读取气压就让测试立即失败。
    def forbidden(*args, **kwargs):
        pytest.fail("audited centers must not be prepared again")
    monkeypatch.setattr(entry.ProcessingManager, "prepare_shared_centers", forbidden)
    requests = []
    monkeypatch.setattr(entry, "_run_time_slices", lambda rs, **kwargs: requests.extend(rs) or [])
    entry._run_processing_request(request)
    assert len(requests) == 2
    assert all(r.options["prepared_layer_polar_variables"] for r in requests)


# 功能说明：同时请求其他计算和 rh1 审计，确认普通计算先完成，审计不会妨碍其结果。
def test_other_requested_operations_finish_before_the_rh_audit(tmp_path, monkeypatch):
    entry = importlib.import_module("process_data")
    request = inputs(tmp_path).model_copy(update={"variables": ["pvo", "rh1_polar"]})
    monkeypatch.setattr(entry, "LAYER_POLAR_BOUNDARY_POLICY", "all_safe")
    monkeypatch.setattr(entry, "LAYER_POLAR_AUDIT_ONLY", False)
    monkeypatch.setattr(entry, "DRY_RUN", False)
    monkeypatch.setattr(entry, "domain_config_dict", lambda request: {3: {"dx_km": 1}})
    events = []
    monkeypatch.setattr(entry, "prepare_layer_polar_inputs", lambda request: events.append("prepare"))
    monkeypatch.setattr(entry, "_run_processing_request", lambda r: events.append(r.variables[0]) or {})
    entry.run_processing_request(request)
    assert events == ["pvo", "prepare", "rh1_polar"]


@pytest.mark.parametrize("source,polar", [
    ("pvo", "pvo1_polar"), ("theta_e", "theta_e1_polar"),
    ("dbz", "dbz1_polar"), ("vr1", "vr1_polar"), ("vt1", "vt1_polar"),
])
# 功能说明：把湿度缓存改名为其他层变量来源，确认没有 rh 缓存时这些变量仍只处理安全时次。
def test_other_layer_variables_compute_safely_without_rh_cache(tmp_path, monkeypatch, source, polar):
    entry = importlib.import_module("process_data")
    request = inputs(tmp_path).model_copy(update={"variables": [polar], "force": True})
    (tmp_path / "member/d03/rh").rename(tmp_path / "member/d03" / source)
    monkeypatch.setattr(entry, "domain_config_dict", lambda request: {3: {"dx_km": 1}})
    entry.run_processing_request(request)
    output_root = tmp_path / "member/d03" / polar
    assert not (output_root / "1h").exists()
    for time in [0, 2]:
        np.testing.assert_array_equal(np.load(output_root / f"{time}h/1000m.npy"), 1)
    assert (tmp_path / f"member/d03/{polar}_safety.json").exists()
    assert not (tmp_path / "member/d03/rh").exists()


# 功能说明：混合 rh、pvo 和海平面气压变量，确认每类变量使用自己的安全时次或完整时次。
def test_mixed_variables_keep_separate_safe_times_and_slp_times(tmp_path, monkeypatch):
    entry = importlib.import_module("process_data")
    request = inputs(tmp_path).model_copy(update={"variables": ["rh1_polar", "pvo1_polar", "pvo_polar"]})
    shutil.copytree(tmp_path / "member/d03/rh", tmp_path / "member/d03/pvo")
    np.save(tmp_path / "member/d03/pvo/2h/1000m.npy", np.ones((6, 9)))
    monkeypatch.setattr(entry, "domain_config_dict", lambda request: {3: {"dx_km": 1}})
    calls = []
    monkeypatch.setattr(entry, "_run_processing_request", lambda r: calls.append(r) or {})
    entry.run_processing_request(request)
    assert {r.variables[0]: r.times.values for r in calls} == {
        "pvo_polar": [0, 1, 2], "rh1_polar": [0, 2], "pvo1_polar": [0]}


# 功能说明：开启 pvo 的仅审计设置并禁止实际计算，确认入口返回 audit_only 状态。
def test_pvo_audit_only_never_computes(tmp_path, monkeypatch):
    entry = importlib.import_module("process_data")
    request = inputs(tmp_path).model_copy(update={"variables": ["pvo1_polar"]})
    (tmp_path / "member/d03/rh").rename(tmp_path / "member/d03/pvo")
    monkeypatch.setattr(entry, "LAYER_POLAR_AUDIT_ONLY", True, raising=False)
    monkeypatch.setattr(entry, "domain_config_dict", lambda request: {3: {"dx_km": 1}})
    # 功能说明：作为防护占位函数，一旦 pvo 仅审计流程调用计算器就让测试立即失败。
    def forbidden(*args, **kwargs):
        pytest.fail("audit-only must not compute")
    monkeypatch.setattr(entry, "_run_processing_request", forbidden)
    assert entry.run_processing_request(request)["status"] == "audit_only"
