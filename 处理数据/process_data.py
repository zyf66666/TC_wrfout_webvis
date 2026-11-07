#!/usr/bin/env python3
"""修改本文件的“用户参数区”，然后运行：python 处理数据/process_data.py。"""

from __future__ import annotations

import os
import json
import sys
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from multiprocessing import Manager as MultiprocessingManager
from pathlib import Path
from queue import Empty
from typing import Any

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from wrf_processing.processing_manager import ProcessingManager, center_file_has_times  # noqa: E402
from wrf_processing.processing_utils import domain_config_dict, resolve_wrfout_root, save_grid_coordinates  # noqa: E402
from wrf_processing.layer_polar_safety import audit_layer_polar_times, layer_polar_source  # noqa: E402
from wrf_webvis.app.core.paths import PathManager  # noqa: E402
from wrf_webvis.app.core.schemas import ProcessingRequest, SelectionSpec  # noqa: E402


# =============================================================================
# 用户参数区
# =============================================================================

EXPERIMENTS = ["WRF_315_land","WRF_315_sea"]
# EXPERIMENTS = ["WRF_315_land", "WRF_315_sea","WRF_0_land", "WRF_0_sea",
#                "WRF_45_land", "WRF_45_sea","WRF_90_land", "WRF_90_sea",
#                "WRF_135_land", "WRF_135_sea","WRF_180_land", "WRF_180_sea",
#                "WRF_225_land", "WRF_225_sea","WRF_270_land", "WRF_270_sea"
#                ]
EXPERIMENTS_ROOT = Path("/Volumes/WRF")
# None uses the standard mounted sources under /Volumes/land_*/ and /Volumes/sea_*.
WRFOUT_TEMPLATE: str | None = None

PROCESSED_ROOT = Path("/Volumes/WRF/processed")
DOMAINS = [3]

# 可直接使用 Python 列表或 range，例如 list(range(0, 121, 3))。
# 下表保留模拟的完整输出时段；各层中心极坐标变量的安全时次会在运行时重新筛选。
TIMES: list[int] = list(range(0, 121))
# 本轮时间，单位是小时：36:00、36:05、……、48:00
# TIMES: list[float] = [
#     minute / 60.0
#     for minute in range(36 * 60, 48 * 60 + 1, 5)
# ]
# 每组模拟的实际结束时次（包含结束时次），按用户提供的试验表。
TIMES_BY_EXPERIMENT: dict[str, list[int]] = {
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
# 本轮覆盖：保留上面的完整试验时间表，仅让 315 使用逐 5 分钟时次。
# 恢复原时间表时，注释掉下面这条 update 即可。
# TIMES_BY_EXPERIMENT.update({"WRF_315_land": TIMES, "WRF_315_sea": TIMES})

HEIGHTS = list(range(100, 1000, 100)) + list(range(1000, 10001, 250))  # 2026-10-16：普通变量算到 10000 m

# 路径图的降水背景需要将 d03 涡旋中心转换为经纬度。下面的成员只会新增
# d03/grid/lat.npy、lon.npy；不会覆盖任何既有变量或中心文件。
GRID_COORDINATE_MEMBERS = []
GRID_COORDINATE_DOMAINS = [3]

# 仅存储“时次、中心纬度、中心经度”辅助表，供移动嵌套 d03 的路径图使用。
# 原始 center.txt 保持不变。
TRACK_CENTER_MEMBERS = []
TRACK_CENTER_DOMAINS = [3]

# 变量配置示例；本轮启用变量以列表下方的 VARIABLES 赋值为准。
VARIABLES = [
    # "pressure",
    # "temp",
    # "QVAPOR",
    # "rh1_polar",
    # "pvo",
    # "pvo1_polar",
    # "pvo_polar",
    # "vr1_polar",
    # "wa1_polar",
    # "theta_e_polar",
    # "classification",
    # "vr_polar",
    # "wa_polar",
    # "vt",
    # "vt_polar",
    # "r_34",
    # "cape",
    # "dbz_polar",
    # "diabatic_heating",
    # "diabatic_heating_polar",
    # "theta_e1_polar",
    # "vr1_polar",
    # "wa1_polar",
    "density",
    "density_polar",
    "density1_polar",
    "theta_e_asy_polar",
    "vr_asy_polar",
    "wa_asy_polar",
    "theta_e1_asy_polar",
    "vr1_asy_polar",
    "wa1_asy_polar",
    "vr_ventilation_polar",
    "w_ventilation_polar",
    "vr1_ventilation_polar",
    "w1_ventilation_polar",
]

# 跨域相当位温异常：d02 各层中心的 400–500 km 环境均值，减自 d03 theta_e。
# 单独填写 theta_e_anomaly1_polar 也会先准备 theta_e_anomaly。
VARIABLES = [
    "slp_center",    # 每时次海平面气压中心
    "layer_center",  # 每个 HEIGHTS 高度的 pressure 最小值中心
    "theta_e",
    "theta_e_polar",
    # "theta_e1_polar",
    "theta_e_anomaly",
    "theta_e_anomaly1_polar",
    ]

# 非对称度统计范围：以海平面气压中心为圆心，积分 0 至该半径的完整圆盘（km）。
# 修改此值可重新计算同名输出；该参数也适用于降水和 dbz 的非对称度。
ASYMMETRY_RADIUS_KM = 100.0

DTYPE = "float32"
MOTION_CORRECT = False

FORCE = False
DRY_RUN = False
# 所有按层中心的 *_polar 变量：all_safe 保留所有安全时次（含越界后恢复的时次）；
# continuous 只计算首个请求时次起的连续安全段；off 使用原流程，不筛选。
LAYER_POLAR_BOUNDARY_POLICY = "all_safe"
# True 只读取对应变量的现有源场/中心并输出边界报告，不计算数据；缺少输入会明确报错。
LAYER_POLAR_AUDIT_ONLY = False
# 仅显式需要迁移旧 metadata 布局时开启；普通续算保留现有 metadata。
CLEAN_OLD_METADATA = False
# "serial" uses one process; "parallel" uses PARALLEL_WORKERS time-slice workers.
PROCESSING_MODE = "serial"
PARALLEL_WORKERS = 3
METADATA_LAYOUT = "variable"

OPTIONS: dict[str, Any] = {
    "theta_e_environment_domain": 2,
    "theta_e_environment_ring_km": [400.0, 500.0],
    "asymmetry_radius_km": ASYMMETRY_RADIUS_KM,
    "wrfout_time_mode": "per_file",
    "wrfout_match_times": True,
    "wrfout_time_index": 0,
    "wrfout_file_interval_hours_by_domain": {1: 9, 2: 1, 3: 1},
    "wrfout_interval_minutes": 5,
    "wrfout_interval_minutes_by_domain": {1: 180, 2: 5, 3: 5},
    "center_search_radius_km": 60.0,
    "center_cross_radius_grid": 121,
    "center_pcc_radius_grid": 44,
    "center_boundary_width": 20,
    "center_max_iterations": 10,
    "polar_max_radius_km": 300.0,
    "polar_radius_resolution_km": None,
    "parallel_workers": PARALLEL_WORKERS,
    "metadata_layout": METADATA_LAYOUT,
    "reuse_processed_wind_components": True,
    "shear_radius_in": 150,
    "shear_radius_out": 350,
    "shear_radius_max": 350,
    "tilt_lower_height_m": 2000,
    "tilt_upper_height_m": 8000,
}


# 功能说明：像轮流发牌一样把时次分给多个进程，返回各进程负责的完整时次列表，确保它们不写同一时次。
def partition_times(times: list[int], *, worker_count: int) -> list[list[int]]:
    """Split time steps into disjoint, reproducible round-robin worker slices."""
    if worker_count < 1:
        raise ValueError("worker_count must be at least 1")
    return [list(times[index::worker_count]) for index in range(worker_count) if times[index::worker_count]]


# 功能说明：串行模式返回1，并行模式采用配置的进程数并要求至少为1；不支持的模式直接报错。
def resolve_worker_count(mode: str, *, configured_workers: int) -> int:
    normalized = str(mode).strip().lower()
    if normalized == "serial":
        return 1
    if normalized == "parallel":
        if configured_workers < 1:
            raise ValueError("PARALLEL_WORKERS must be at least 1")
        return configured_workers
    raise ValueError('PROCESSING_MODE must be "serial" or "parallel"')


# 功能说明：将试验名、模拟区域和顶部设置的路径、变量、时间、高度等装进一份处理选择单，供处理管理器执行。
def build_request(experiment: str, domain: int) -> ProcessingRequest:
    root = Path(EXPERIMENTS_ROOT)
    wrfout_root = (
        Path(WRFOUT_TEMPLATE.format(root=root, experiment=experiment)).expanduser()
        if WRFOUT_TEMPLATE
        else None
    )
    times = list(TIMES_BY_EXPERIMENT.get(experiment, TIMES))
    return ProcessingRequest(
        processed_root=Path(PROCESSED_ROOT).expanduser(),
        wrfout_root=wrfout_root,
        member_name=experiment,
        domain=int(domain),
        variables=list(VARIABLES),
        times=SelectionSpec(values=times),
        heights=SelectionSpec(values=list(HEIGHTS)) if HEIGHTS else None,
        dtype=DTYPE,
        motion_correct=MOTION_CORRECT,
        force=FORCE,
        trust_existing=not FORCE,
        options=dict(OPTIONS),
    )


# 功能说明：把进度文字加上固定前缀立即打印到终端，让运行中的处理进度及时可见。
def progress(message: str) -> None:
    print(f"[WRF processing] {message}", flush=True)


# 功能说明：按请求设置或清除环境开关，决定数据说明采用每变量共用一份还是逐文件保存的布局。
def _configure_metadata_layout(request: ProcessingRequest) -> None:
    layout = str(request.options.get("metadata_layout", "per_file")).strip().lower()
    if layout == "variable":
        os.environ["WRF_WEBVIS_METADATA_LAYOUT"] = "variable"
    else:
        os.environ.pop("WRF_WEBVIS_METADATA_LAYOUT", None)


# 功能说明：切换到每变量共用说明的布局时，删除当前试验、当前区域下旧的逐文件说明及已有汇总说明，返回已删除路径；调用它会实际删除JSON说明文件。
def prepare_variable_metadata_layout(request: ProcessingRequest) -> list[Path]:
    """Remove old per-file provenance only for the member/domain being rebuilt."""
    if str(request.options.get("metadata_layout", "per_file")).strip().lower() != "variable":
        return []
    domain_root = PathManager(request.processed_root).domain_root(request.member_name, request.domain)
    if not domain_root.is_dir():
        return []
    targets = sorted(
        [*domain_root.rglob("*.npy.json"), *domain_root.glob("*/source_metadata.json")],
        key=str,
    )
    for path in targets:
        path.unlink()
    return targets


# 功能说明：处理一个时间片的请求，调用处理器并把进度或错误结果返回给并行调度器。
def _run_time_slice(request: ProcessingRequest, progress_queue: Any | None = None) -> dict[str, Any]:
    """Worker entry point: one process writes one disjoint set of forecast hours."""
    _configure_metadata_layout(request)
    worker_progress = progress if progress_queue is None else progress_queue.put
    return ProcessingManager(request.processed_root).run(request, progress=worker_progress)


# 功能说明：遍历请求的全部时间片，按配置并发执行并收集每个时间片的结果。
def _run_time_slices(requests: list[ProcessingRequest], *, stage: str) -> list[dict[str, Any]]:
    if not requests:
        return []
    if len(requests) == 1:
        return [_run_time_slice(requests[0])]

    progress(f"{stage}：使用 {len(requests)} 个进程按时次并行")
    results: list[dict[str, Any]] = []
    with MultiprocessingManager() as manager:
        progress_queue = manager.Queue()
        with ProcessPoolExecutor(max_workers=len(requests)) as executor:
            pending = {executor.submit(_run_time_slice, request, progress_queue) for request in requests}
            while pending:
                try:
                    progress(f"[并行] {progress_queue.get(timeout=0.25)}")
                except Empty:
                    pass
                completed, pending = wait(pending, timeout=0, return_when=FIRST_COMPLETED)
                for future in completed:
                    results.append(future.result())
            while True:
                try:
                    progress(f"[并行] {progress_queue.get_nowait()}")
                except Empty:
                    break
    return results


# 功能说明：复制请求对象并替换时间选择，生成只针对给定时段的子请求。
def _request_for_times(request: ProcessingRequest, variables: list[str], times: list[int], *, reuse_centers: bool) -> ProcessingRequest:
    options = dict(request.options)
    if reuse_centers:
        options["reuse_prepared_centers"] = True
    return request.model_copy(
        update={
            "variables": variables,
            "times": SelectionSpec(values=times),
            "options": options,
        }
    )


# 功能说明：逐个审计按层中心的极坐标变量，筛出半径不越界的时次并写安全报告，再按策略处理选中时次；只审计模式不计算变量，其他变量走原流程。
def run_processing_request(request: ProcessingRequest) -> dict[str, Any]:
    """Audit and filter each direct layer-centred polar variable independently."""
    policy = LAYER_POLAR_BOUNDARY_POLICY
    if policy not in {"all_safe", "continuous", "off"}:
        raise ValueError("LAYER_POLAR_BOUNDARY_POLICY 必须为 all_safe、continuous 或 off")
    layer_variables = list(dict.fromkeys(variable for variable in request.variables if layer_polar_source(variable)))
    if not layer_variables or policy == "off":
        return _run_processing_request(request)
    audit_only = LAYER_POLAR_AUDIT_ONLY or DRY_RUN
    results = []
    if not audit_only:
        others = [variable for variable in request.variables if variable not in layer_variables]
        if others:
            results.append(_run_processing_request(request.model_copy(update={"variables": others})))
    reports = {}
    for variable in layer_variables:
        variable_request = request.model_copy(update={"variables": [variable]})
        if not audit_only:
            prepare_layer_polar_inputs(variable_request)
        report = audit_layer_polar_times(variable_request, domain_config=domain_config_dict(request), progress=progress)
        selected = report["safe_times"]
        if policy == "continuous":
            end = report["continuous_end"]
            selected = [] if end is None else [time for time in selected if time <= end]
        report.update(policy=policy, selected_times=selected)
        report_path = PathManager(request.processed_root).domain_root(request.member_name, request.domain) / f"{variable}_safety.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        reports[variable] = str(report_path)
        progress(f"{request.member_name} {variable}: 连续安全上限={report['continuous_end']}h；"
                 f"最后安全时次={report['last_safe_time']}h；越界时次={report['unsafe_times']}；"
                 f"本次选择 {len(selected)} 个时次；报告：{report_path}")
        if selected and not audit_only:
            results.append(_run_processing_request(variable_request.model_copy(update={
                "times": SelectionSpec(values=selected),
                "options": {**request.options, "prepared_layer_polar_variables": [variable]}})))
    return {"status": "audit_only" if audit_only else ("complete" if results else "no_safe_times"),
            "safety_report": next(iter(reports.values())) if len(reports) == 1 else None,
            "safety_reports": reports, "results": results}


# 功能说明：检查按层中心插值所需的各时次、各高度基础变量和中心文件，不齐全时先补算，让后续安全审计有真实输入可检查。
def prepare_layer_polar_inputs(request: ProcessingRequest) -> None:
    """Prepare the actual source field and missing layer centres for one variable."""
    _configure_metadata_layout(request)
    manager = ProcessingManager(request.processed_root)
    source = layer_polar_source(request.variables[0])
    if source is None:
        raise ValueError(f"不是按层中心的极坐标变量：{request.variables[0]}")
    inputs = request.model_copy(update={"variables": [source], "force": False, "trust_existing": True})
    paths = PathManager(request.processed_root)
    times = list(request.times.values or [])
    heights = list(request.heights.values or []) if request.heights else []
    if source == "theta_e_anomaly" or not times or not heights or not all(paths.variable_path(
        member_name=request.member_name, domain=request.domain, variable=source,
        time_hour=time, height_m=height, dimension="3d"
    ).exists() for time in times for height in heights):
        manager.run(inputs, progress=progress)
    if not heights or not all(center_file_has_times(
        paths.layer_center_path(request.member_name, request.domain, height), times, request.options
    ) for height in heights):
        manager.prepare_shared_centers(inputs, progress=progress)


# 功能说明：按时次划分工作，先并行处理基础变量，再集中准备共享中心，最后并行处理极坐标和诊断；只有一组时直接运行。
def _run_processing_request(request: ProcessingRequest) -> dict[str, Any]:
    """Run raw fields, shared centres, and dependent diagnostics in safe stages."""
    _configure_metadata_layout(request)
    manager = ProcessingManager(request.processed_root)
    worker_count = resolve_worker_count(
        PROCESSING_MODE,
        configured_workers=int(request.options.get("parallel_workers", 1) or 1),
    )
    standard_variables, operation_variables = manager._split_variables(request.variables)
    times = [int(time_hour) for time_hour in (request.times.values or [])]
    partitions = partition_times(times, worker_count=max(1, worker_count))
    if len(partitions) <= 1:
        return manager.run(request, progress=progress)

    results: list[dict[str, Any]] = []
    if standard_variables:
        raw_requests = [
            _request_for_times(request, standard_variables, partition, reuse_centers=False)
            for partition in partitions
        ]
        results.extend(_run_time_slices(raw_requests, stage="原始三维场"))

    if operation_variables:
        if not set(operation_variables).issubset(request.options.get("prepared_layer_polar_variables", [])):
            progress("并行派生场前，串行准备共享涡旋中心")
            results.append({"shared_centers": manager.prepare_shared_centers(request, progress=progress)})
        operation_requests = [
            _request_for_times(request, operation_variables, partition, reuse_centers=True)
            for partition in partitions
        ]
        results.extend(_run_time_slices(operation_requests, stage="极坐标、诊断量和通风"))

    processed_files = [
        path
        for result in results
        for path in result.get("processed_files", [])
    ]
    return {"status": "complete", "parallel_workers": len(partitions), "processed_files": processed_files}


# 功能说明：检查并生成每个网格点的经纬度坐标文件，供绘图定位使用。
def ensure_grid_coordinates(experiment: str, domain: int, source_root: Path) -> list[Path]:
    """Save static WRF grid coordinates once, without changing existing data."""
    manager = PathManager(PROCESSED_ROOT)
    grid_root = manager.domain_root(experiment, domain) / "grid"
    outputs = [grid_root / "lat.npy", grid_root / "lon.npy"]
    if all(path.exists() for path in outputs):
        progress(f"{experiment} d0{domain}: 经纬度网格已存在，跳过")
        return outputs
    if any(path.exists() for path in outputs):
        raise FileExistsError(f"经纬度网格不完整，拒绝覆盖：{grid_root}")

    files = sorted(source_root.glob(f"wrfout_d0{int(domain)}_*"))
    if not files:
        raise FileNotFoundError(f"未找到 d0{domain} 的 wrfout 文件：{source_root}")
    from netCDF4 import Dataset

    with Dataset(files[0]) as dataset:
        variables = dataset.variables
        lat_var = next((variables[name] for name in ("XLAT", "XLAT_M", "lat", "LAT") if name in variables), None)
        lon_var = next((variables[name] for name in ("XLONG", "XLONG_M", "lon", "LON") if name in variables), None)
        if lat_var is None or lon_var is None:
            raise KeyError(f"wrfout 缺少 XLAT/XLONG：{files[0]}")
        latitude = lat_var[0] if lat_var.ndim >= 3 else lat_var[:]
        longitude = lon_var[0] if lon_var.ndim >= 3 else lon_var[:]
    outputs = save_grid_coordinates(manager, experiment, domain, latitude, longitude)
    progress(f"{experiment} d0{domain}: 已新增经纬度网格")
    return outputs


# 功能说明：检查并生成风暴中心随时间移动的坐标轨迹文件。
def ensure_track_center_coordinates(experiment: str, domain: int, source_root: Path) -> Path:
    """Write moving-nest center lat/lon as a new auxiliary file."""
    manager = PathManager(PROCESSED_ROOT)
    output = manager.domain_root(experiment, domain) / "track" / "center_latlon.txt"
    if output.exists():
        progress(f"{experiment} d0{domain}: 路径中心经纬度已存在，跳过")
        return output
    center_path = manager.center_path(experiment, domain, "slp")
    if not center_path.exists():
        raise FileNotFoundError(f"路径图缺少中心文件：{center_path}")
    rows = np.atleast_2d(np.loadtxt(center_path)).astype(float)
    if rows.shape[1] < 3:
        raise ValueError(f"中心文件格式不支持：{center_path}")
    files = sorted(source_root.glob(f"wrfout_d0{int(domain)}_*"))
    if not files:
        raise FileNotFoundError(f"未找到 d0{domain} 的 wrfout 文件：{source_root}")

    from netCDF4 import Dataset

    coordinates: list[list[float]] = []
    for row in rows:
        time_hour = int(round(float(row[0])))
        if not 0 <= time_hour < len(files):
            raise IndexError(f"{experiment} d0{domain} 缺少 {time_hour}h 的 wrfout 文件")
        y, x = int(round(float(row[1]))), int(round(float(row[2])))
        with Dataset(files[time_hour]) as dataset:
            variables = dataset.variables
            lat_var = next((variables[name] for name in ("XLAT", "XLAT_M", "lat", "LAT") if name in variables), None)
            lon_var = next((variables[name] for name in ("XLONG", "XLONG_M", "lon", "LON") if name in variables), None)
            if lat_var is None or lon_var is None:
                raise KeyError(f"wrfout 缺少 XLAT/XLONG：{files[time_hour]}")
            latitude = lat_var[0] if lat_var.ndim >= 3 else lat_var[:]
            longitude = lon_var[0] if lon_var.ndim >= 3 else lon_var[:]
            if not (0 <= y < latitude.shape[0] and 0 <= x < latitude.shape[1]):
                raise IndexError(f"中心格点超出 wrfout 网格：{experiment} {time_hour}h ({y}, {x})")
            coordinates.append([float(row[0]), float(latitude[y, x]), float(longitude[y, x])])

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    np.savetxt(temporary, np.asarray(coordinates, dtype=float), fmt="%.8g")
    temporary.replace(output)
    progress(f"{experiment} d0{domain}: 已新增逐时路径中心经纬度")
    return output


# 功能说明：读取顶部配置，按开关补齐坐标、清理旧说明并逐试验区域处理或检查；把汇总写入 last_processing_summary.json，成功时返回退出码0。
def main() -> int:
    if not EXPERIMENTS:
        raise ValueError("EXPERIMENTS 不能为空")
    if not DOMAINS:
        raise ValueError("DOMAINS 不能为空")
    if not VARIABLES:
        raise ValueError("VARIABLES 不能为空")

    results: list[dict[str, Any]] = []
    audit_only = (LAYER_POLAR_AUDIT_ONLY or DRY_RUN) and any(layer_polar_source(variable) for variable in VARIABLES) and LAYER_POLAR_BOUNDARY_POLICY != "off"
    for experiment in ([] if audit_only else GRID_COORDINATE_MEMBERS):
        source_root = resolve_wrfout_root(experiment, None)
        if not source_root.is_dir():
            raise FileNotFoundError(f"WRF 输出目录不存在：{source_root}")
        for domain in GRID_COORDINATE_DOMAINS:
            outputs = ensure_grid_coordinates(experiment, int(domain), source_root)
            results.append({"experiment": experiment, "domain": domain, "grid_coordinates": [str(path) for path in outputs]})

    for experiment in ([] if audit_only else TRACK_CENTER_MEMBERS):
        source_root = resolve_wrfout_root(experiment, None)
        if not source_root.is_dir():
            raise FileNotFoundError(f"WRF 输出目录不存在：{source_root}")
        for domain in TRACK_CENTER_DOMAINS:
            output = ensure_track_center_coordinates(experiment, int(domain), source_root)
            results.append({"experiment": experiment, "domain": domain, "track_center_coordinates": str(output)})

    for experiment in EXPERIMENTS:
        for domain in DOMAINS:
            request = build_request(experiment, domain)
            source_root = resolve_wrfout_root(experiment, request.wrfout_root)
            if not audit_only and not source_root.is_dir():
                raise FileNotFoundError(f"WRF 输出目录不存在：{source_root}")
            progress(f"{experiment} d0{domain}: {source_root}")
            removed_metadata = [] if DRY_RUN or LAYER_POLAR_AUDIT_ONLY or not CLEAN_OLD_METADATA else prepare_variable_metadata_layout(request)
            if removed_metadata:
                progress(f"{experiment} d0{domain}: 已移除 {len(removed_metadata)} 个旧逐文件元数据")
            if DRY_RUN and (not any(layer_polar_source(variable) for variable in request.variables) or LAYER_POLAR_BOUNDARY_POLICY == "off"):
                result = ProcessingManager(request.processed_root).check(request).as_dict()
            else:
                result = run_processing_request(request)
            results.append({"experiment": experiment, "domain": domain, "result": result})

    summary_path = Path(PROCESSED_ROOT) / "last_processing_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    progress(f"完成；摘要：{summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
