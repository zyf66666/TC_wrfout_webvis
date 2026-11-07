from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from metpy.calc import density as metpy_density
from metpy.units import units

PROCESSING_ROOT = Path(__file__).resolve().parents[1] / "处理数据"
sys.path.insert(0, str(PROCESSING_ROOT))
from wrf_processing.diagnostics import ventilation
from wrf_webvis.app.core.paths import PathManager, metadata_path
from wrf_webvis.app.core.schemas import SelectionSpec, VariableDimension


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSING_ROOT = PROJECT_ROOT / "处理数据"


# 功能说明：临时加入处理脚本目录并重新导入入口模块，返回模块后恢复 Python 的搜索路径。
def _load_entrypoint():
    sys.path.insert(0, str(PROCESSING_ROOT))
    sys.modules.pop("process_data", None)
    try:
        return importlib.import_module("process_data")
    finally:
        sys.path.remove(str(PROCESSING_ROOT))


# 功能说明：以压强、温度和水汽构造小数组，确认密度计算采用与笔记本相同的 MetPy 单位换算。
def test_density_uses_the_notebook_metpy_quantity_conversions() -> None:
    pressure_hpa = np.array([[1000.0, 900.0]], dtype=np.float32)
    temperature_k = np.array([[300.0, 290.0]], dtype=np.float32)
    qv = np.array([[0.010, 0.005]], dtype=np.float32)

    actual = ventilation.compute_density_metpy(pressure_hpa, temperature_k, qv)
    expected = metpy_density(
        pressure_hpa * units.hPa,
        temperature_k * units.K,
        qv * 1000.0 * units("g/kg"),
    ).to("kg/m^3").magnitude

    np.testing.assert_allclose(actual, expected, rtol=1e-6)
    assert actual.dtype == np.float32


# 功能说明：读取默认配置，确认通风诊断的高度覆盖 100 米到 10 千米的分段层级。
def test_default_ventilation_heights_cover_100m_to_10km() -> None:
    module = _load_entrypoint()

    assert module.HEIGHTS == list(range(100, 1000, 100)) + list(range(1000, 10001, 250))


@pytest.mark.parametrize("variables", [["dbz_polar"], ["pvo", "pvo_polar"]])
# 功能说明：分别指定两组活动变量，确认用户参数文件会把当前选择原样放进处理请求。
def test_user_parameter_file_uses_the_active_variable_selection(monkeypatch, variables) -> None:
    module = _load_entrypoint()
    monkeypatch.setattr(module, "VARIABLES", variables)

    request = module.build_request("WRF_315_land", 3)

    assert request.variables == variables


# 功能说明：检查处理配置，确认串行模式、三个并行名额、按变量元数据和复用风分量的开关都被保留。
def test_processing_configuration_keeps_parallel_capacity_and_variable_metadata() -> None:
    module = _load_entrypoint()

    assert module.PROCESSING_MODE == "serial"
    assert module.OPTIONS["parallel_workers"] == 3
    assert module.OPTIONS["metadata_layout"] == "variable"
    assert module.OPTIONS["reuse_processed_wind_components"] is True


# 功能说明：分别选择串行和并行模式，确认工作进程数返回一和配置的三个。
def test_worker_count_can_be_selected_between_serial_and_parallel() -> None:
    module = _load_entrypoint()

    assert module.resolve_worker_count("serial", configured_workers=3) == 1
    assert module.resolve_worker_count("parallel", configured_workers=3) == 3


# 功能说明：读取每个实验的结束时间表，确认各实验的小时范围与给定模拟时长一致。
def test_experiment_end_times_follow_the_provided_simulation_table() -> None:
    module = _load_entrypoint()

    assert module.TIMES_BY_EXPERIMENT == {
        "WRF_0_land": list(range(121)),
        "WRF_0_sea": list(range(121)),
        "WRF_45_land": list(range(121)),
        "WRF_45_sea": list(range(121)),
        "WRF_90_land": list(range(85)),
        "WRF_90_sea": list(range(121)),
        "WRF_135_land": list(range(49)),
        "WRF_135_sea": list(range(70)),
        "WRF_180_land": list(range(40)),
        "WRF_180_sea": list(range(60)),
        "WRF_225_land": list(range(43)),
        "WRF_225_sea": list(range(70)),
        "WRF_270_land": list(range(67)),
        "WRF_270_sea": list(range(85)),
        "WRF_315_land": list(range(103)),
        "WRF_315_sea": list(range(103)),
    }


# 功能说明：在强制重算径向风时记录内部请求，确认已有风分量会以可复用方式被读取。
def test_radial_wind_reuses_existing_processed_components_when_forced(tmp_path: Path, monkeypatch) -> None:
    from wrf_processing.processing_manager import ProcessingManager

    request = ventilation.ProcessingRequest(
        processed_root=tmp_path,
        member_name="member",
        domain=3,
        variables=["vr_polar"],
        times=SelectionSpec(values=[0]),
        heights=SelectionSpec(values=[100]),
        force=True,
        trust_existing=False,
        options={"reuse_processed_wind_components": True},
    )
    observed = []
    manager = ProcessingManager(tmp_path)
    monkeypatch.setattr(
        manager,
        "_ensure_standard_data",
        lambda component_request, variables, heights, progress: observed.append(component_request),
    )

    manager._ensure_radial_wind_components(request, [100], progress=None)

    assert len(observed) == 1
    assert observed[0].force is False
    assert observed[0].trust_existing is True


# 功能说明：在强制准备共享中心时记录中心请求，确认已有层中心会以复用方式使用。
def test_shared_center_preparation_reuses_existing_layer_centers_when_forced(tmp_path: Path, monkeypatch) -> None:
    from wrf_processing.processing_manager import ProcessingManager

    request = ventilation.ProcessingRequest(
        processed_root=tmp_path,
        member_name="member",
        domain=3,
        variables=["theta_e1_polar"],
        times=SelectionSpec(values=[0]),
        heights=SelectionSpec(values=[100]),
        force=True,
        trust_existing=False,
    )
    observed = []
    manager = ProcessingManager(tmp_path)
    monkeypatch.setattr(manager, "_ensure_standard_data", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        manager,
        "_ensure_layer_centers",
        lambda center_request, times, heights, progress: observed.append(center_request) or [],
    )

    manager.prepare_shared_centers(request, progress=None)

    assert len(observed) == 1
    assert observed[0].force is False
    assert observed[0].trust_existing is True


# 功能说明：用假的队列和处理器发送一条进度消息，确认子进程的消息会转交给父进程队列。
def test_parallel_worker_forwards_processing_messages_to_parent(tmp_path: Path, monkeypatch) -> None:
    module = _load_entrypoint()
    request = module.ProcessingRequest(
        processed_root=tmp_path,
        member_name="member",
        domain=3,
        variables=[],
    )

    class FakeQueue:
        # 功能说明：创建收集消息的假队列，使测试可以查看工作进程转发了什么文本。
        def __init__(self) -> None:
            self.messages = []

        # 功能说明：把收到的一条进度文本追加到假队列的消息列表。
        def put(self, message: str) -> None:
            self.messages.append(message)

    class FakeManager:
        # 功能说明：建立不保存状态的假处理管理器，用来替代真实计算器。
        def __init__(self, _root: Path) -> None:
            pass

        # 功能说明：向传入的进度回调发送固定消息，并返回一份完成状态作为假计算结果。
        def run(self, _request, progress):
            progress("计算 member vr_polar 0h")
            return {"status": "complete", "processed_files": []}

    monkeypatch.setattr(module, "ProcessingManager", FakeManager)
    queue = FakeQueue()

    result = module._run_time_slice(request, progress_queue=queue)

    assert result["status"] == "complete"
    assert queue.messages == ["计算 member vr_polar 0h"]


# 功能说明：把十个时次分给三个工作者，确认每个时次恰好出现一次且分组顺序稳定。
def test_time_partitions_cover_each_requested_time_once() -> None:
    module = _load_entrypoint()

    partitions = module.partition_times(list(range(10)), worker_count=3)

    assert partitions == [[0, 3, 6, 9], [1, 4, 7], [2, 5, 8]]
    assert sorted(time_hour for partition in partitions for time_hour in partition) == list(range(10))


# 功能说明：在两个高度文件旁写元数据，确认按变量布局只生成一份共用来源 JSON。
def test_variable_metadata_layout_writes_one_json_for_all_height_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WRF_WEBVIS_METADATA_LAYOUT", "variable")
    manager = PathManager(tmp_path)
    first = manager.variable_path(
        member_name="member", domain=3, variable="temp", time_hour=0,
        dimension=VariableDimension.THREE_D, height_m=100,
    )
    second = manager.variable_path(
        member_name="member", domain=3, variable="temp", time_hour=1,
        dimension=VariableDimension.THREE_D, height_m=200,
    )

    manager.write_metadata(first, {"variable": "temp", "time": 0, "height": 100})
    manager.write_metadata(second, {"variable": "temp", "time": 1, "height": 200})

    shared = tmp_path / "member/d03/temp/source_metadata.json"
    assert metadata_path(first) == shared
    assert metadata_path(second) == shared
    assert shared.exists()
    assert list(shared.parent.rglob("*.npy.json")) == []
    assert json.loads(shared.read_text(encoding="utf-8"))["time_height_layout"] == "variable-level provenance"


# 功能说明：同时放入旧式元数据、共用元数据和无关网格文件，确认清理只删除前两类。
def test_variable_metadata_preparation_removes_only_legacy_variable_json(tmp_path: Path) -> None:
    module = _load_entrypoint()
    request = module.ProcessingRequest(
        processed_root=tmp_path,
        member_name="member",
        domain=3,
        variables=[],
        options={"metadata_layout": "variable"},
    )
    variable_root = tmp_path / "member/d03/temp"
    legacy = variable_root / "0h/100m.npy.json"
    shared = variable_root / "source_metadata.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}", encoding="utf-8")
    shared.write_text("{}", encoding="utf-8")
    keep = tmp_path / "member/d03/grid/lat.npy"
    keep.parent.mkdir(parents=True)
    keep.write_bytes(b"grid")

    removed = module.prepare_variable_metadata_layout(request)

    assert removed == [legacy, shared]
    assert not legacy.exists()
    assert not shared.exists()
    assert keep.exists()


# 功能说明：写入 density 的来源说明，确认每个变量只建一份元数据并记下源变量。
def test_new_variable_provenance_is_written_once_per_variable(tmp_path: Path) -> None:
    request = ventilation.ProcessingRequest(
        processed_root=tmp_path,
        member_name="member",
        domain=3,
        variables=[],
    )

    path = ventilation.write_variable_metadata(
        request,
        "density",
        formula="metpy.calc.density(...) ",
        units="kg m-3",
        source_variables=["pressure", "temp", "QVAPOR"],
    )

    assert path == tmp_path / "member/d03/density/source_metadata.json"
    assert json.loads(path.read_text(encoding="utf-8"))["source_variables"] == ["pressure", "temp", "QVAPOR"]
    assert list(path.parent.rglob("*.npy.json")) == []


# 功能说明：检查处理入口的公开属性，确认通风诊断已下放给管理器。
def test_entrypoint_delegates_ventilation_diagnostics_to_the_manager() -> None:
    module = _load_entrypoint()

    assert not hasattr(module, "calculate_density_and_ventilation")
    assert not hasattr(module, "compute_density_metpy")
    assert not hasattr(module, "compute_ventilation_fields")


# 功能说明：把标准 WRF 变量和通风诊断混在一起，确认分流后两组变量各归其位。
def test_ventilation_outputs_are_separated_from_standard_wrf_variables() -> None:
    variables = [
        "pressure",
        "temp",
        "QVAPOR",
        "density",
        "theta_e_polar",
        "vr_ventilation_polar",
        "w_ventilation_polar",
        "vr1_ventilation_polar",
        "w1_ventilation_polar",
    ]
    from wrf_processing.processing_manager import ProcessingManager

    standard, operations = ProcessingManager("processed")._split_variables(variables)
    assert "vr_ventilation_polar" in operations
    assert "density" in operations
    assert "theta_e_polar" in operations
    assert standard == ["pressure", "temp", "QVAPOR"]
    assert "vr_ventilation_polar" in ventilation.VARIABLES


# 功能说明：给出正负径向和垂直风，确认只保留入流与下沉贡献且原始非对称场不被改写。
def test_ventilation_keeps_only_inflow_and_subsidence_without_mutating_asymmetries() -> None:
    density = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    theta_e_asymmetry = np.array([[2.0, -1.0], [3.0, 4.0]], dtype=np.float32)
    radial_wind = np.array([[-1.0, 1.0], [0.0, -2.0]], dtype=np.float32)
    radial_asymmetry = np.array([[5.0, 6.0], [7.0, 8.0]], dtype=np.float32)
    vertical_wind = np.array([[1.0, -1.0], [0.0, -2.0]], dtype=np.float32)
    vertical_asymmetry = np.array([[9.0, 10.0], [11.0, 12.0]], dtype=np.float32)

    radial, subsidence = ventilation.compute_ventilation_fields(
        density,
        theta_e_asymmetry,
        radial_wind,
        radial_asymmetry,
        vertical_wind,
        vertical_asymmetry,
    )

    np.testing.assert_array_equal(radial, [[10.0, 0.0], [63.0, 128.0]])
    np.testing.assert_array_equal(subsidence, [[0.0, -20.0], [99.0, 192.0]])
    np.testing.assert_array_equal(radial_asymmetry, [[5.0, 6.0], [7.0, 8.0]])
    np.testing.assert_array_equal(vertical_asymmetry, [[9.0, 10.0], [11.0, 12.0]])
