from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from wrf_webvis.app.core.centers import read_new_center_rows
from wrf_webvis.app.core.paths import PathManager, WrfoutDomainReader
from wrf_webvis.app.core.registry import (
    DEFAULT_DOMAIN_CONFIG,
    DEFAULT_HEIGHT_LEVELS_M,
    default_processing_heights,
    VARIABLE_REGISTRY,
    parse_cartesian_from_polar_variable_name,
    parse_fourier_variable_name,
    resolve_variable_name,
)
from wrf_webvis.app.core.schemas import ProcessingRequest, SelectionSpec, VariableDimension
from wrf_webvis.app.core.selection import expand_selection
from .center_manager import CenterManager, center_output_is_current
from .cloud_classification import (
    CLOUD_CLASSIFICATION_HEIGHTS_M,
    CLOUD_CLASSIFICATION_VARIABLES,
    process_cloud_classification,
)
from .boundary_layer_diagnostics import (
    BOUNDARY_LAYER_INTEGRAL_VARIABLES,
    DERIVED_THREE_D_VARIABLES,
    boundary_layer_heights,
    dependencies_for as boundary_layer_dependencies,
    process_boundary_layer_integral,
    process_derived_3d_variable,
    requires_slp_center as boundary_layer_requires_slp_center,
)
from .data_manager import DataManager, ProcessingPlan, RequiredFile
from .diagnostics import (
    ONE_D_DIAGNOSTIC_VARIABLES,
    POLAR_ASYMMETRY_VARIABLES,
    SELF_VALIDATING_DIAGNOSTIC_VARIABLES,
    process_1d_diagnostic,
)
from .diagnostics import polar_asymmetry
from .diagnostics import ventilation
from .fourier_decomposition import process_fourier_decomposition
from .polar_transformer import PolarTransformer
from .theta_e_anomaly import process_theta_e_anomaly
from .polar_to_cartesian import process_polar_to_cartesian
from .processing_utils import (
    atomic_save,
    build_processing_metadata,
    domain_config_dict,
    fill_nan,
    prepare_wrf_import,
    processed_file_is_current,
    resolve_wrfout_root,
    save_grid_coordinates,
    STORM_RELATIVE_WIND_VARIABLES,
    wrfout_read_error,
)
from .progress import ProgressCallback, emit
from .radial_tangential_wind import RADIAL_TANGENTIAL_VARIABLES, process_radial_tangential_variable
from .storm_motion import StormMotionService, load_motion_for_time, motion_file_has_times


SLP_CENTER_VARIABLES = {"center", "slp_center"}
LAYER_CENTER_VARIABLES = {"layer_center", "height_center", "pressure_center"}
STORM_MOTION_VARIABLES = {"storm_motion", "motion"}
DERIVED_TWO_D_VARIABLES = {"wind_speed_10"}


# 功能说明：仅对 CAPE 选择 WRF 返回的第一项结果；其他变量原样返回，保持注册变量的原始形状。
def prepare_wrf_output(variable: str, data: Any) -> Any:
    """Select the single field represented by a registered WRF diagnostic."""
    if variable != "cape":
        return data
    if np.ndim(data) != 3 or int(np.shape(data)[0]) < 1:
        raise ValueError(f"cape_2d must have a leading product dimension, got shape={np.shape(data)}")
    return data[0, ...]


# 功能说明：从当前 WRF 文件读取注册变量在指定时次的场；位涡会走专用算法，并同时返回数据来源说明。
def _read_wrf_field(
    dataset: Any,
    variable: str,
    *,
    source_file: Path,
    timeidx: int,
    options: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    if variable == "pvo":
        from .potential_vorticity import calculate_pvo

        return calculate_pvo(
            dataset, source_file=source_file, timeidx=timeidx,
            wrfinput_d01_path=options.get("pvo_wrfinput_d01"),
        )
    from wrf import getvar

    item = VARIABLE_REGISTRY.get(variable)
    data = getvar(dataset, item.wrf_name if item is not None else variable, timeidx=timeidx)
    return prepare_wrf_output(variable, data), {}


class ProcessingManager:
    # 功能说明：保存处理根目录，创建数据规划器，并初始化 WRF 文件时间索引缓存。
    def __init__(self, processed_root: str | Path) -> None:
        self.path_manager = PathManager(processed_root)
        self.data_manager = DataManager(processed_root)
        self._wrfout_time_count_cache: dict[Path, int] = {}
        self._wrfout_index_cache: dict[tuple[Path, ...], list[tuple[int, int, Path]]] = {}
        self._wrfout_timestamp_index_cache: dict[tuple[Path, ...], list[tuple[float, Path, int]]] = {}

    # 功能说明：按请求拆分标准变量和派生操作，规划所需文件并列出缺失项。
    def check(self, request: ProcessingRequest) -> ProcessingPlan:
        standard_variables, operation_variables = self._split_variables(request.variables)
        if not operation_variables:
            return self.data_manager.plan_for_processing(request)

        if standard_variables:
            base_plan = self.data_manager.plan_for_processing(_request_with(request, variables=standard_variables))
            required = list(base_plan.required_files)
            warnings = list(base_plan.warnings)
            domain_config = base_plan.domain_config
        else:
            domain_config = WrfoutDomainReader._fallback(request.domain, DEFAULT_DOMAIN_CONFIG)
            required = []
            warnings = []

        times = [_clean_time(value) for value in expand_selection(request.times, default=[0])]
        heights = expand_selection(request.heights, default=default_processing_heights(request.variables))
        for variable in operation_variables:
            required.extend(self._required_for_operation(request, variable, times, heights, warnings))
        if request.trust_existing:
            for item in required:
                item.exists = item.path.exists()
        missing = [item for item in required if not item.exists]
        return ProcessingPlan(domain_config=domain_config, required_files=required, missing_files=missing, warnings=warnings)

    # 功能说明：针对派生操作展开前置变量、中心和源文件依赖，形成所需文件记录。
    def _required_for_operation(
        self,
        request: ProcessingRequest,
        variable: str,
        times: list[int],
        heights: list[float],
        warnings: list[str],
    ) -> list[RequiredFile]:
        if variable in ventilation.VARIABLES:
            return [
                self._file_status(
                    request.member_name,
                    request.domain,
                    variable,
                    VariableDimension.THREE_D,
                    time_hour,
                    height_m,
                )
                for time_hour in times
                for height_m in heights
            ]
        if variable in POLAR_ASYMMETRY_VARIABLES:
            result: list[RequiredFile] = []
            for time_hour in times:
                path = self.path_manager.variable_path(
                    member_name=request.member_name,
                    domain=request.domain,
                    variable=variable,
                    time_hour=time_hour,
                    dimension=VariableDimension.ONE_D,
                )
                result.append(
                    RequiredFile(
                        variable,
                        VariableDimension.ONE_D.value,
                        time_hour,
                        None,
                        path,
                        polar_asymmetry.is_output_current(
                            request=request,
                            path_manager=self.path_manager,
                            variable=variable,
                            time_hour=time_hour,
                        ),
                        "standard",
                    )
                )
            return result
        if variable in STORM_MOTION_VARIABLES:
            path = self.path_manager.storm_motion_root(request.member_name, request.domain) / "storm_motion.npy"
            return [
                RequiredFile(variable, "motion", time_hour, None, path, motion_file_has_times(path, [time_hour]), "motion")
                for time_hour in times
            ]
        if variable in DERIVED_TWO_D_VARIABLES:
            return [
                self._file_status(
                    request.member_name,
                    request.domain,
                    variable,
                    VariableDimension.TWO_D,
                    time_hour,
                    None,
                )
                for time_hour in times
            ]
        if variable in DERIVED_THREE_D_VARIABLES or variable == "theta_e_anomaly":
            return [
                self._file_status(
                    request.member_name,
                    request.domain,
                    variable,
                    VariableDimension.THREE_D,
                    time_hour,
                    height_m,
                )
                for time_hour in times
                for height_m in heights
            ]
        if variable in BOUNDARY_LAYER_INTEGRAL_VARIABLES:
            return [
                self._file_status(
                    request.member_name,
                    request.domain,
                    variable,
                    VariableDimension.TWO_D,
                    time_hour,
                    None,
                )
                for time_hour in times
            ]
        if variable in SLP_CENTER_VARIABLES:
            path = self.path_manager.center_path(request.member_name, request.domain, "slp")
            return [RequiredFile(variable, "center", times[0] if times else 0, None, path, path.exists(), "center")]
        if variable in LAYER_CENTER_VARIABLES:
            result = []
            for height_m in heights:
                path = self.path_manager.layer_center_path(request.member_name, request.domain, height_m)
                result.append(RequiredFile(variable, "center", times[0] if times else 0, height_m, path, path.exists(), "center"))
            return result
        if variable in CLOUD_CLASSIFICATION_VARIABLES:
            result = []
            for time_hour in times:
                existing = self.path_manager.existing_variable(
                    member_name=request.member_name,
                    domain=request.domain,
                    variable="classification",
                    time_hour=time_hour,
                    dimension=VariableDimension.TWO_D,
                )
                path = existing.path if existing is not None else self.path_manager.variable_path(
                    member_name=request.member_name,
                    domain=request.domain,
                    variable="classification",
                    time_hour=time_hour,
                    dimension=VariableDimension.TWO_D,
                )
                result.append(
                    RequiredFile(
                        "classification",
                        VariableDimension.TWO_D.value,
                        time_hour,
                        None,
                        path,
                        existing is not None,
                        existing.layout if existing is not None else None,
                    )
                )
            return result
        fourier = parse_fourier_variable_name(variable)
        if fourier is not None:
            item = VARIABLE_REGISTRY.get(fourier.base_name)
            if item is None:
                warnings.append(f"Base variable is not registered for Fourier decomposition: {fourier.base_name}")
                return []
            result = []
            for time_hour in times:
                if item.dimension == VariableDimension.THREE_D:
                    for height_m in heights:
                        result.append(self._file_status(request.member_name, request.domain, variable, item.dimension, time_hour, height_m))
                else:
                    result.append(self._file_status(request.member_name, request.domain, variable, item.dimension, time_hour, None))
            return result
        cartesian = parse_cartesian_from_polar_variable_name(variable)
        if cartesian is not None:
            item = VARIABLE_REGISTRY.get(cartesian.base_name)
            if item is None:
                warnings.append(f"Base variable is not registered for polar-to-cartesian: {cartesian.base_name}")
                return []
            result = []
            for time_hour in times:
                if item.dimension == VariableDimension.THREE_D:
                    for height_m in heights:
                        result.append(self._file_status(request.member_name, request.domain, variable, item.dimension, time_hour, height_m))
                else:
                    result.append(self._file_status(request.member_name, request.domain, variable, item.dimension, time_hour, None))
            return result
        polar = _parse_polar_variable(variable)
        if polar is None:
            warnings.append(f"Unsupported processing operation: {variable}")
            return []
        requested_name, base_name, _center_mode = polar
        item = VARIABLE_REGISTRY.get(base_name)
        if item is None:
            warnings.append(f"Variable is not registered for polar processing: {requested_name}")
            return []
        result = []
        for time_hour in times:
            if item.dimension == VariableDimension.THREE_D:
                for height_m in heights:
                    result.append(self._file_status(request.member_name, request.domain, variable, item.dimension, time_hour, height_m))
            else:
                result.append(self._file_status(request.member_name, request.domain, variable, item.dimension, time_hour, None))
        return result

    # 功能说明：计算某成员、域、变量、时间和高度对应的输出路径，并标记文件是否存在。
    def _file_status(
        self,
        member_name: str,
        domain: int,
        variable: str,
        dimension: VariableDimension,
        time_hour: float,
        height_m: float | None,
    ) -> RequiredFile:
        existing = self.path_manager.existing_variable(
            member_name=member_name,
            domain=domain,
            variable=variable,
            time_hour=time_hour,
            dimension=dimension,
            height_m=height_m,
        )
        if existing is not None:
            return RequiredFile(variable, dimension.value, time_hour, height_m, existing.path, True, existing.layout)
        path = self.path_manager.variable_path(
            member_name=member_name,
            domain=domain,
            variable=variable,
            time_hour=time_hour,
            dimension=dimension,
            height_m=height_m,
        )
        return RequiredFile(variable, dimension.value, time_hour, height_m, path, False, None)

    # 功能说明：预先生成请求涉及的海平面气压中心、层中心和风暴移动数据。
    def prepare_shared_centers(
        self,
        request: ProcessingRequest,
        progress: ProgressCallback | None = None,
    ) -> list[str]:
        """Create centres once before disjoint time-slice workers consume them."""
        times = [_clean_time(value) for value in expand_selection(request.times, default=[0])]
        heights = expand_selection(request.heights, default=default_processing_heights(request.variables))
        # Polar radius does not affect centers.  Reuse complete center files
        # even when downstream polar fields are being forced from 200 to 300 km.
        cached_request = _request_with(request, force=False, trust_existing=True)
        self._ensure_standard_data(cached_request, ["pressure"], _selection(heights), progress)
        return [str(path) for path in self._ensure_layer_centers(cached_request, times, heights, progress)]

    # 功能说明：遍历请求中的变量和时间，先补齐依赖，再调用各变量处理器写出结果。
    def run(self, request: ProcessingRequest, progress: ProgressCallback | None = None) -> dict[str, Any]:
        emit(progress, f"force={request.force}")
        emit(progress, f"检查 {request.member_name} d0{request.domain} 数据是否已存在")
        standard_variables, operation_variables = self._split_variables(request.variables)
        plan = self.check(_request_with(request, variables=standard_variables)) if standard_variables else self.check(request)
        processed_files: list[str] = []

        if standard_variables:
            if not plan.missing_files and not request.force:
                emit(progress, "目标普通变量数据已存在，跳过 wrfout 读取")
            else:
                processed_files.extend(self._run_standard_variables(request, standard_variables, progress))

        for variable in operation_variables:
            if request.trust_existing and not request.force and variable not in {"theta_e_anomaly", "theta_e_anomaly_polar", "theta_e_anomaly1_polar"}:
                operation_plan = self.check(_request_with(request, variables=[variable]))
                if not operation_plan.missing_files:
                    emit(progress, f"{request.member_name} {variable} 目标缓存已存在，按请求直接复用")
                    continue
            processed_files.extend(self._run_operation_variable(request, variable, progress))

        if any(variable in ventilation.VARIABLES for variable in request.variables):
            processed_files.extend(str(path) for path in ventilation.calculate(request, progress=progress))

        if processed_files:
            emit(progress, "数据处理写入完成")
            return {"status": "complete", "plan": plan.as_dict(), "processed_files": processed_files}
        return {"status": "skipped", "plan": plan.as_dict(), "processed_files": []}

    # 功能说明：批量处理普通二维或三维变量，并报告每个变量的进度。
    def _run_standard_variables(
        self,
        request: ProcessingRequest,
        variables: list[str],
        progress: ProgressCallback | None = None,
    ) -> list[str]:
        standard_request = _request_with(request, variables=variables)
        plan = self.check(standard_request)
        self_validating = any(variable in SELF_VALIDATING_DIAGNOSTIC_VARIABLES for variable in variables)
        validates_source = str(request.options.get("wrfout_time_mode", "all_times")).strip().lower() in {
            "per_file", "one_per_file", "hourly_file"
        }
        if not plan.missing_files and not request.force and not self_validating and not validates_source:
            return []

        if all(variable in RADIAL_TANGENTIAL_VARIABLES for variable in variables):
            times = [_clean_time(value) for value in expand_selection(request.times, default=[0])]
            heights = expand_selection(request.heights, default=default_processing_heights(request.variables))
            if not heights:
                raise ValueError(f"3d variable requires heights: {','.join(variables)}")
            self._ensure_radial_wind_components(request, heights, progress)
            if any(variable.endswith("1") for variable in variables):
                self._ensure_layer_centers(request, times, heights, progress)
            else:
                self._ensure_slp_center(request, times, progress)
            processed_files: list[str] = []
            for variable in variables:
                for time_hour in times:
                    emit(progress, f"计算 {request.member_name} time={time_hour}h 变量 {variable}")
                    processed_files.extend(
                        self._process_radial_tangential_variable(
                            request=request,
                            variable=variable,
                            time_hour=time_hour,
                            heights=heights,
                            dtype=request.dtype,
                            center_mode="layer" if variable.endswith("1") else "slp",
                        )
                    )
            return processed_files

        wrfout_files = self._find_wrfout_files(request)
        if not wrfout_files:
            raise FileNotFoundError("No wrfout files found. Provide wrfout_file or wrfout_root.")
        emit(progress, f"找到 wrfout 文件 {len(wrfout_files)} 个")

        processed_files: list[str] = []
        times = [_clean_time(value) for value in expand_selection(request.times, default=[0])]
        heights = expand_selection(request.heights, default=default_processing_heights(request.variables))
        sources_by_time = {
            time_hour: self._source_for_time(wrfout_files, time_hour, request)
            for time_hour in times
        }

        active_variables: list[tuple[str, VariableDimension, bool]] = []
        for variable in variables:
            registry_item = VARIABLE_REGISTRY.get(variable)
            dimension = registry_item.dimension if registry_item is not None else VariableDimension.TWO_D
            variable_validates_cache = variable in SELF_VALIDATING_DIAGNOSTIC_VARIABLES
            if not request.force and not variable_validates_cache and all(
                self._standard_outputs_exist(
                    request,
                    variable,
                    dimension,
                    time_hour,
                    heights,
                    *sources_by_time[time_hour],
                )
                for time_hour in times
            ):
                emit(progress, f"{request.member_name} {variable} 目标数据已存在，跳过")
                continue
            if variable in RADIAL_TANGENTIAL_VARIABLES:
                if not heights:
                    raise ValueError(f"3d variable requires heights: {variable}")
                self._ensure_radial_wind_components(request, heights, progress)
                self._ensure_slp_center(request, times, progress)
            active_variables.append((variable, dimension, variable_validates_cache))

        for time_hour in times:
            source_file, source_time_index = sources_by_time[time_hour]
            wrf_batch: list[tuple[str, VariableDimension, list[float]]] = []
            for variable, dimension, variable_validates_cache in active_variables:
                if (
                    not request.force
                    and not variable_validates_cache
                    and self._standard_outputs_exist(
                        request,
                        variable,
                        dimension,
                        time_hour,
                        heights,
                        source_file,
                        source_time_index,
                    )
                ):
                    emit(progress, f"{request.member_name} {variable} {time_hour}h 已存在，跳过")
                    continue
                if variable in ONE_D_DIAGNOSTIC_VARIABLES:
                    if wrf_batch:
                        processed_files.extend(
                            self._process_wrf_variable_batch(
                                request=request,
                                source_file=source_file,
                                source_time_index=source_time_index,
                                time_hour=time_hour,
                                variables=wrf_batch,
                                dtype=request.dtype,
                                progress=progress,
                            )
                        )
                        wrf_batch = []
                    processed_files.append(
                        self._process_1d_diagnostic(
                            request=request,
                            variable=variable,
                            source_file=source_file,
                            source_time_index=source_time_index,
                            time_hour=time_hour,
                            dtype=request.dtype,
                            progress=progress,
                        )
                    )
                    continue
                emit(
                    progress,
                    f"读取 {source_file.name} source_index={source_time_index} "
                    f"time={time_hour}h 变量 {variable}",
                )
                if dimension == VariableDimension.THREE_D:
                    if not heights:
                        raise ValueError(f"3d variable requires heights: {variable}")
                    pending_heights = self._pending_standard_heights(
                        request,
                        variable,
                        time_hour,
                        heights,
                        source_file,
                        source_time_index,
                    )
                    if not pending_heights and not request.force:
                        emit(progress, f"{request.member_name} {variable} {time_hour}h 所有高度已存在，跳过")
                        continue
                    if variable in RADIAL_TANGENTIAL_VARIABLES:
                        if wrf_batch:
                            processed_files.extend(
                                self._process_wrf_variable_batch(
                                    request=request,
                                    source_file=source_file,
                                    source_time_index=source_time_index,
                                    time_hour=time_hour,
                                    variables=wrf_batch,
                                    dtype=request.dtype,
                                    progress=progress,
                                )
                            )
                            wrf_batch = []
                        processed_files.extend(
                            self._process_radial_tangential_variable(
                                request=request,
                                variable=variable,
                                time_hour=time_hour,
                                heights=heights,
                                dtype=request.dtype,
                                center_mode="layer" if variable.endswith("1") else "slp",
                            )
                        )
                        continue
                    wrf_batch.append(
                        (variable, dimension, pending_heights if not request.force else heights)
                    )
                else:
                    wrf_batch.append((variable, dimension, []))

            if wrf_batch:
                processed_files.extend(
                    self._process_wrf_variable_batch(
                        request=request,
                        source_file=source_file,
                        source_time_index=source_time_index,
                        time_hour=time_hour,
                        variables=wrf_batch,
                        dtype=request.dtype,
                        progress=progress,
                    )
                )

        return processed_files

    # 功能说明：检查指定变量在所有请求时间和高度的标准输出是否已经存在且有效。
    def _standard_outputs_exist(
        self,
        request: ProcessingRequest,
        variable: str,
        dimension: VariableDimension,
        time_hour: int,
        heights: list[float],
        source_file: Path | None = None,
        source_time_index: int | None = None,
    ) -> bool:
        if dimension == VariableDimension.THREE_D:
            if not heights:
                return False
            for height_m in heights:
                existing = self.path_manager.existing_variable(
                    member_name=request.member_name,
                    domain=request.domain,
                    variable=variable,
                    time_hour=time_hour,
                    dimension=dimension,
                    height_m=height_m,
                )
                if existing is None or not processed_file_is_current(
                    variable,
                    existing.metadata,
                    source_file=source_file,
                    source_time_index=source_time_index,
                ):
                    return False
            return True
        existing = self.path_manager.existing_variable(
            member_name=request.member_name,
            domain=request.domain,
            variable=variable,
            time_hour=time_hour,
            dimension=dimension,
        )
        return existing is not None and processed_file_is_current(
            variable,
            existing.metadata,
            source_file=source_file,
            source_time_index=source_time_index,
        )

    # 功能说明：找出仍缺失标准输出的高度层，避免重复计算已有层。
    def _pending_standard_heights(
        self,
        request: ProcessingRequest,
        variable: str,
        time_hour: int | float,
        heights: list[float],
        source_file: Path | None = None,
        source_time_index: int | None = None,
    ) -> list[float]:
        pending: list[float] = []
        for height_m in heights:
            existing = self.path_manager.existing_variable(
                member_name=request.member_name,
                domain=request.domain,
                variable=variable,
                time_hour=time_hour,
                dimension=VariableDimension.THREE_D,
                height_m=height_m,
            )
            if existing is None or not processed_file_is_current(
                variable,
                existing.metadata,
                source_file=source_file,
                source_time_index=source_time_index,
            ):
                pending.append(height_m)
        return pending

    # 功能说明：按派生变量名称准备风、气压、中心或极坐标等前置数据，再调用相应诊断并返回生成文件。
    def _run_operation_variable(
        self,
        request: ProcessingRequest,
        variable: str,
        progress: ProgressCallback | None = None,
    ) -> list[str]:
        token = variable.strip()
        times = [_clean_time(value) for value in expand_selection(request.times, default=[0])]
        heights = expand_selection(request.heights, default=default_processing_heights(request.variables))

        if token == "theta_e_anomaly":
            outputs = []
            for time_hour in times:
                emit(progress, f"计算 {request.member_name} {time_hour}h 相当位温异常（d02 环境参考）")
                outputs.extend(process_theta_e_anomaly(request, time_hour, heights))
            return outputs

        if token in ventilation.VARIABLES:
            # All ventilation products are written together after every listed
            # dependency has been prepared by this manager run.
            return []

        if token in STORM_MOTION_VARIABLES:
            return [str(self._ensure_storm_motion(request, times, progress))]
        if token == "wind_speed_10":
            return self._process_wind_speed_10(request, times, progress)
        if token in DERIVED_THREE_D_VARIABLES:
            if not heights:
                raise ValueError(f"3d diagnostic requires heights: {token}")
            self._ensure_boundary_layer_dependencies(request, token, times, heights, progress)
            if boundary_layer_requires_slp_center(token):
                self._ensure_slp_center(request, times, progress)
            outputs: list[str] = []
            domain_config = _domain_config_dict(request).get(request.domain, {})
            for time_hour in times:
                emit(progress, f"计算 {request.member_name} {time_hour}h 变量 {token}")
                outputs.extend(
                    process_derived_3d_variable(
                        request=request,
                        path_manager=self.path_manager,
                        variable=token,
                        time_hour=time_hour,
                        heights=heights,
                        dtype=request.dtype,
                        domain_config=domain_config,
                    )
                )
            return outputs
        if token in BOUNDARY_LAYER_INTEGRAL_VARIABLES:
            integral_heights = boundary_layer_heights(request.options)
            source_variable = boundary_layer_dependencies(token)[0]
            self._run_operation_variable(
                _request_with(request, variables=[source_variable], heights=_selection(integral_heights)),
                source_variable,
                progress,
            )
            outputs: list[str] = []
            for time_hour in times:
                emit(progress, f"计算 {request.member_name} {time_hour}h 变量 {token}")
                outputs.append(
                    process_boundary_layer_integral(
                        request=request,
                        path_manager=self.path_manager,
                        variable=token,
                        time_hour=time_hour,
                        heights=integral_heights,
                        dtype=request.dtype,
                    )
                )
            return outputs
        if token in SLP_CENTER_VARIABLES:
            return [str(self._ensure_slp_center(request, times, progress))]
        if token in LAYER_CENTER_VARIABLES:
            return [str(path) for path in self._ensure_layer_centers(request, times, heights, progress)]
        if token in POLAR_ASYMMETRY_VARIABLES:
            outputs: list[str] = []
            for time_hour in times:
                outputs.append(
                    self._process_1d_diagnostic(
                        request=request,
                        variable=token,
                        source_file=None,
                        source_time_index=None,
                        time_hour=time_hour,
                        dtype=request.dtype,
                        progress=progress,
                    )
                )
            return outputs
        if token in CLOUD_CLASSIFICATION_VARIABLES:
            self._ensure_standard_data(request, ["dbz", "wa"], _selection(CLOUD_CLASSIFICATION_HEIGHTS_M), progress)
            wrfout_files = self._find_wrfout_files(request)
            outputs: list[str] = []
            for time_hour in times:
                source_file = None
                source_time_index = None
                if wrfout_files:
                    source_file, source_time_index = self._source_for_time(wrfout_files, time_hour, request)
                emit(progress, f"计算 {request.member_name} {time_hour}h 云分类")
                outputs.append(
                    process_cloud_classification(
                        request=request,
                        time_hour=time_hour,
                        dtype=request.dtype,
                        source_file=source_file,
                        source_time_index=source_time_index,
                    )
                )
            return outputs
        cartesian = parse_cartesian_from_polar_variable_name(token)
        if cartesian is not None:
            self._ensure_fourier_source(request, cartesian.source_name, times, heights, progress)
            outputs: list[str] = []
            domain_config = _domain_config_dict(request).get(request.domain, {})
            for time_hour in times:
                emit(progress, f"计算 {request.member_name} {token} {time_hour}h 极坐标转笛卡尔")
                outputs.extend(
                    process_polar_to_cartesian(
                        request=request,
                        variable=token,
                        time_hour=time_hour,
                        heights=heights,
                        dtype=request.dtype,
                        domain_config=domain_config,
                    )
                )
            return outputs
        fourier = parse_fourier_variable_name(token)
        if fourier is not None:
            self._ensure_fourier_source(request, fourier.source_name, times, heights, progress)
            outputs: list[str] = []
            for time_hour in times:
                emit(progress, f"计算 {request.member_name} {token} {time_hour}h 傅里叶分解")
                outputs.extend(
                    process_fourier_decomposition(
                        request=request,
                        variable=token,
                        time_hour=time_hour,
                        heights=heights,
                        dtype=request.dtype,
                    )
                )
            return outputs

        polar = _parse_polar_variable(token)
        if polar is None:
            raise ValueError(f"Unsupported processing variable: {variable}")

        requested_name, base_name, center_mode = polar
        item = VARIABLE_REGISTRY.get(base_name)
        if item is None:
            raise ValueError(f"Variable is not registered for polar processing: {requested_name}")

        raw_heights = (request.heights or _selection(heights)) if item.dimension == VariableDimension.THREE_D else None
        # The trailing 1 selects a center, not another WRF field. Only vr/vt
        # have separate Cartesian fields computed relative to each layer center.
        source_variable = requested_name if requested_name in RADIAL_TANGENTIAL_VARIABLES else base_name
        # The entrypoint has prepared and audited these exact source grids/centres.
        if token not in request.options.get("prepared_layer_polar_variables", []):
            if base_name == "theta_e_anomaly" or base_name in DERIVED_TWO_D_VARIABLES or base_name in DERIVED_THREE_D_VARIABLES or base_name in BOUNDARY_LAYER_INTEGRAL_VARIABLES:
                self._run_operation_variable(
                    _request_with(request, variables=[source_variable]),
                    source_variable,
                    progress,
                )
            else:
                self._ensure_standard_data(request, [source_variable], raw_heights, progress)
            if center_mode == "layer":
                self._ensure_layer_centers(request, times, heights, progress)
            else:
                self._ensure_slp_center(request, times, progress)

        transformer = PolarTransformer(request.processed_root)
        domain_config = _domain_config_dict(request)
        radius_km = request.options.get("polar_max_radius_km")
        radius_resolution_km = request.options.get("polar_radius_resolution_km")
        outputs: list[str] = []
        for time_hour in times:
            emit(progress, f"插值到极坐标 {request.member_name} {requested_name} {time_hour}h")
            if item.dimension == VariableDimension.THREE_D:
                outputs.extend(
                    str(path)
                    for path in transformer.transform_3d_per_height(
                        member_name=request.member_name,
                        domain=request.domain,
                        variable=base_name,
                        time_hour=time_hour,
                        heights_m=heights,
                        center_mode=center_mode,
                        radius_km=radius_km,
                        radius_resolution_km=radius_resolution_km,
                        domain_config=domain_config,
                        force=request.force,
                        source_variable=source_variable,
                    )
                )
            else:
                outputs.append(
                    str(
                        transformer.transform_2d(
                            member_name=request.member_name,
                            domain=request.domain,
                            variable=base_name,
                            time_hour=time_hour,
                            center_mode=center_mode,
                            radius_km=radius_km,
                            radius_resolution_km=radius_resolution_km,
                            domain_config=domain_config,
                            force=request.force,
                        )
                    )
                )
        return outputs

    # 功能说明：为傅里叶非对称量先创建它依赖的极坐标源变量，沿用当前时次和高度选择。
    def _ensure_fourier_source(
        self,
        request: ProcessingRequest,
        source_variable: str,
        times: list[int],
        heights: list[float],
        progress: ProgressCallback | None,
    ) -> None:
        source_request = _request_with(
            request,
            variables=[source_variable],
            times=_selection(times),
            heights=_selection(heights) if heights else request.heights,
        )
        self._run_operation_variable(source_request, source_variable, progress)

    # 功能说明：检查指定原始变量的处理文件；缺失、过期或强制重算时从 wrfout 读取并重新保存。
    def _ensure_standard_data(
        self,
        request: ProcessingRequest,
        variables: list[str],
        heights: Any,
        progress: ProgressCallback | None,
    ) -> None:
        ensure_request = _request_with(request, variables=variables, heights=heights)
        plan = self.check(ensure_request)
        if not plan.missing_files and not request.force:
            emit(progress, f"{','.join(variables)} 原始处理数据已存在，跳过 wrfout 读取")
            return
        self._run_standard_variables(ensure_request, variables, progress)

    # 功能说明：为径向或切向风确保 ua、va 两个风分量已处理；配置允许时优先复用已有分量。
    def _ensure_radial_wind_components(
        self,
        request: ProcessingRequest,
        heights: list[float],
        progress: ProgressCallback | None,
    ) -> None:
        """Prefer already processed ua/va when deriving vr or vt."""
        component_request = request
        if bool(request.options.get("reuse_processed_wind_components")):
            component_request = _request_with(request, force=False, trust_existing=True)
        self._ensure_standard_data(component_request, ["ua", "va"], _selection(heights), progress)

    # 功能说明：查询边界层诊断需要哪些基本变量，并按请求时次和高度确保这些变量已处理。
    def _ensure_boundary_layer_dependencies(
        self,
        request: ProcessingRequest,
        variable: str,
        times: list[int | float],
        heights: list[float],
        progress: ProgressCallback | None,
    ) -> None:
        dependencies = boundary_layer_dependencies(variable)
        self._ensure_standard_data(
            _request_with(request, times=_selection(times)),
            dependencies,
            _selection(heights),
            progress,
        )

    # 功能说明：检查海平面气压中心文件是否覆盖请求时次；否则先处理 slp 再计算并返回中心文件。
    def _ensure_slp_center(
        self,
        request: ProcessingRequest,
        times: list[int],
        progress: ProgressCallback | None,
    ) -> Path:
        center_path = self.path_manager.center_path(request.member_name, request.domain, "slp")
        reuse_prepared = bool(request.options.get("reuse_prepared_centers"))
        if (reuse_prepared or not request.force) and center_file_has_times(center_path, times, request.options):
            emit(progress, f"{request.member_name} d0{request.domain} slp center 已存在，跳过中心计算")
            return center_path
        self._ensure_standard_data(request, ["slp"], None, progress)
        emit(progress, f"计算 {request.member_name} d0{request.domain} slp center")
        manager = CenterManager(request.processed_root)
        return manager.compute_slp_centers(
            member_name=request.member_name,
            domain=request.domain,
            times=times,
            domain_config=_domain_config_dict(request),
            force=request.force,
            algorithm_options=request.options,
        )

    # 功能说明：检查风暴移动数组是否覆盖请求时次；否则确保中心经纬度可用后计算并返回移动文件。
    def _ensure_storm_motion(
        self,
        request: ProcessingRequest,
        times: list[int],
        progress: ProgressCallback | None,
    ) -> Path:
        motion_path = self.path_manager.storm_motion_root(request.member_name, request.domain) / "storm_motion.npy"
        if not request.force and motion_file_has_times(motion_path, times):
            emit(progress, f"{request.member_name} d0{request.domain} storm motion 已存在，跳过下游依赖检查")
            return motion_path

        center_path = self._ensure_slp_center(request, times, progress)
        service = StormMotionService(request.processed_root)
        try:
            result = service.compute(
                member_name=request.member_name,
                domain=request.domain,
                center_path=center_path,
                force=True,
            )
        except ValueError as exc:
            if "lat/lon" not in str(exc):
                raise
            center_path = CenterManager(request.processed_root).compute_slp_centers(
                member_name=request.member_name,
                domain=request.domain,
                times=times,
                domain_config=_domain_config_dict(request),
                force=True,
                algorithm_options=request.options,
            )
            result = service.compute(
                member_name=request.member_name,
                domain=request.domain,
                center_path=center_path,
                force=True,
            )
        motion_path = Path(result["npy"])
        if not motion_file_has_times(motion_path, times):
            result = service.compute(
                member_name=request.member_name,
                domain=request.domain,
                center_path=center_path,
                force=True,
            )
            motion_path = Path(result["npy"])
        emit(progress, f"{request.member_name} d0{request.domain} storm motion 已就绪")
        return motion_path

    # 功能说明：检查各高度层的气压最小值中心文件；缺失时先准备气压和海平面中心，再计算并返回这些文件。
    def _ensure_layer_centers(
        self,
        request: ProcessingRequest,
        times: list[int],
        heights: list[float],
        progress: ProgressCallback | None,
    ) -> list[Path]:
        center_paths = [self.path_manager.layer_center_path(request.member_name, request.domain, height_m) for height_m in heights]
        reuse_prepared = bool(request.options.get("reuse_prepared_centers"))
        if (reuse_prepared or not request.force) and center_paths and all(
            center_file_has_times(path, times, request.options) for path in center_paths
        ):
            emit(progress, f"{request.member_name} d0{request.domain} layer centers 已存在，跳过中心计算")
            return center_paths
        self._ensure_slp_center(request, times, progress)
        # Layer-center callers explicitly choose the required heights.  Do not
        # accidentally reuse unrelated HEIGHTS from the outer request.
        self._ensure_standard_data(request, ["pressure"], _selection(heights), progress)
        manager = CenterManager(request.processed_root)
        outputs: list[Path] = []
        for time_hour in times:
            emit(progress, f"计算 {request.member_name} d0{request.domain} {time_hour}h layer centers")
            outputs.extend(
                manager.compute_layer_centers_from_minimum(
                    member_name=request.member_name,
                    domain=request.domain,
                    variable="pressure",
                    time_hour=time_hour,
                    heights_m=heights,
                    domain_config=_domain_config_dict(request),
                    force=request.force,
                    algorithm_options=request.options,
                )
            )
        return outputs

    # 功能说明：把请求变量分成可直接读取的标准变量和需要额外计算的操作变量。
    def _split_variables(self, variables: list[str]) -> tuple[list[str], list[str]]:
        standard: list[str] = []
        operations: list[str] = []
        seen: set[str] = set()
        for variable in variables:
            token = variable.strip()
            if not token or token in seen:
                continue
            seen.add(token)
            if (
                token in ventilation.VARIABLES
                or
                token in SLP_CENTER_VARIABLES
                or token in LAYER_CENTER_VARIABLES
                or token in STORM_MOTION_VARIABLES
                or token in DERIVED_TWO_D_VARIABLES
                or token in DERIVED_THREE_D_VARIABLES
                or token == "theta_e_anomaly"
                or token in BOUNDARY_LAYER_INTEGRAL_VARIABLES
                or token in POLAR_ASYMMETRY_VARIABLES
                or token in CLOUD_CLASSIFICATION_VARIABLES
                or parse_cartesian_from_polar_variable_name(token) is not None
                or parse_fourier_variable_name(token) is not None
                or _parse_polar_variable(token) is not None
            ):
                operations.append(token)
            else:
                standard.append(token)
        return standard, operations

    # 功能说明：确保已处理的 U10、V10 文件存在，逐时次计算其平方和开方得到 10 米风速，并写入二维输出和来源元数据。
    def _process_wind_speed_10(
        self,
        request: ProcessingRequest,
        times: list[int],
        progress: ProgressCallback | None,
    ) -> list[str]:
        self._ensure_standard_data(request, ["U10", "V10"], None, progress)
        outputs: list[str] = []
        for time_hour in times:
            existing_output = self.path_manager.existing_variable(
                member_name=request.member_name,
                domain=request.domain,
                variable="wind_speed_10",
                time_hour=time_hour,
                dimension=VariableDimension.TWO_D,
            )
            if existing_output is not None and not request.force:
                outputs.append(str(existing_output.path))
                continue

            components = []
            source_paths = []
            for variable in ("U10", "V10"):
                source = self.path_manager.existing_variable(
                    member_name=request.member_name,
                    domain=request.domain,
                    variable=variable,
                    time_hour=time_hour,
                    dimension=VariableDimension.TWO_D,
                )
                if source is None:
                    raise FileNotFoundError(
                        f"Missing cached {variable} for {request.member_name} d0{request.domain} "
                        f"at {time_hour}h"
                    )
                components.append(np.load(source.path))
                source_paths.append(str(source.path))

            values = np.hypot(components[0], components[1]).astype(request.dtype, copy=False)
            out_path = self.path_manager.variable_path(
                member_name=request.member_name,
                domain=request.domain,
                variable="wind_speed_10",
                time_hour=time_hour,
                dimension=VariableDimension.TWO_D,
            )
            self._atomic_save(out_path, values)
            self.path_manager.write_metadata(
                out_path,
                {
                    "variable": "wind_speed_10",
                    "domain": request.domain,
                    "time": time_hour,
                    "height": None,
                    "member_name": request.member_name,
                    "created_at": datetime.now().astimezone().isoformat(),
                    "source_variables": ["U10", "V10"],
                    "source_paths": source_paths,
                    "formula": "hypot(U10, V10)",
                    "processing_options": request.options,
                },
            )
            outputs.append(str(out_path))
        return outputs

    # 功能说明：在成员的 wrfout 根目录中查找并按域编号筛选 WRF 输出文件。
    def _find_wrfout_files(self, request: ProcessingRequest) -> list[Path]:
        if request.wrfout_file is not None:
            return WrfoutDomainReader.find_wrfout_files(request.wrfout_file, request.domain)
        return WrfoutDomainReader.find_wrfout_files(resolve_wrfout_root(request.member_name, request.wrfout_root), request.domain)

    # 功能说明：根据目标时间和文件时间索引选择包含该时刻的 wrfout 文件。
    def _source_for_time(
        self,
        files: list[Path],
        time_hour: int | float,
        request: ProcessingRequest | None = None,
    ) -> tuple[Path, int]:
        options = request.options if request is not None else {}
        mode = str(options.get("wrfout_time_mode", "all_times")).strip().lower()
        if mode in {"per_file", "one_per_file", "hourly_file"}:
            numeric_time = float(time_hour)
            if options.get("wrfout_match_times", True):
                timestamp_index = self._wrfout_timestamp_index(files)
                if timestamp_index:
                    matches = [
                        (path, source_index)
                        for forecast_hour, path, source_index in timestamp_index
                        if abs(forecast_hour - numeric_time) <= 1.0e-7
                    ]
                    if matches:
                        return matches[0]
                    available = [item[0] for item in timestamp_index]
                    raise IndexError(
                        f"time={numeric_time:g}h is not present in wrfout Times; available range is "
                        f"{min(available):g}h to {max(available):g}h"
                    )

            file_interval_hours = 1.0
            interval_by_domain = options.get("wrfout_file_interval_hours_by_domain", {})
            if request is not None and isinstance(interval_by_domain, dict):
                configured_interval = interval_by_domain.get(
                    request.domain,
                    interval_by_domain.get(str(request.domain)),
                )
                if configured_interval is not None:
                    file_interval_hours = float(configured_interval)
            if not np.isfinite(file_interval_hours) or file_interval_hours <= 0:
                raise ValueError(
                    "options.wrfout_file_interval_hours_by_domain values must be positive finite hours"
                )

            file_index = int(np.floor(numeric_time / file_interval_hours + 1e-10))
            if file_index < 0 or file_index >= len(files):
                domain_text = f" for d0{request.domain}" if request is not None else ""
                raise IndexError(
                    f"time={time_hour}h exceeds available per-file wrfout range{domain_text}: "
                    f"{len(files)} files at {file_interval_hours:g}-hour intervals"
                )
            path = Path(files[file_index])
            file_start_hour = file_index * file_interval_hours
            fractional_minutes = (numeric_time - file_start_hour) * 60.0
            if abs(fractional_minutes) > 1e-7:
                interval_minutes = float(options.get("wrfout_interval_minutes", 5))
                interval_minutes_by_domain = options.get("wrfout_interval_minutes_by_domain", {})
                if request is not None and isinstance(interval_minutes_by_domain, dict):
                    configured_interval = interval_minutes_by_domain.get(
                        request.domain,
                        interval_minutes_by_domain.get(str(request.domain)),
                    )
                    if configured_interval is not None:
                        interval_minutes = float(configured_interval)
                if interval_minutes <= 0:
                    raise ValueError("options.wrfout_interval_minutes must be positive")
                source_index_float = fractional_minutes / interval_minutes
                source_index = int(round(source_index_float))
                if abs(source_index_float - source_index) > 1e-7:
                    raise ValueError(
                        f"time={numeric_time:g}h is not aligned with the {interval_minutes:g}-minute "
                        "internal wrfout interval"
                    )
            else:
                source_index = int(options.get("wrfout_time_index", 0))
            # The exact first record of a per-file product needs no Times scan.
            # It is validated naturally when the selected field is read below.
            if source_index == 0:
                return path, 0
            ntimes = self._wrfout_time_count(path)
            if source_index < 0:
                source_index += ntimes
            if source_index < 0 or source_index >= ntimes:
                raise IndexError(
                    f"time={numeric_time:g}h resolves to source_time_index={source_index} in "
                    f"{path.name}, which contains {ntimes} times"
                )
            return path, source_index
        if mode not in {"all_times", "continuous"}:
            raise ValueError(
                "options.wrfout_time_mode must be 'per_file' or 'all_times', "
                f"got {mode!r}"
            )
        for start, end, path in self._wrfout_index(files):
            if start <= time_hour < end:
                return path, time_hour - start
        total = self._wrfout_index(files)[-1][1] if files else 0
        raise IndexError(f"time={time_hour}h exceeds available wrfout times ({total})")

    # 功能说明：读取并缓存各 wrfout 文件的起止时间，建立时间到文件的索引。
    def _wrfout_timestamp_index(self, files: list[Path]) -> list[tuple[float, Path, int]]:
        key = tuple(Path(path) for path in files)
        cached = self._wrfout_timestamp_index_cache.get(key)
        if cached is not None:
            return cached
        if not key or any(not path.is_file() for path in key):
            return []

        from netCDF4 import Dataset, chartostring

        timestamped_records: list[tuple[datetime, Path, int]] = []
        for path in key:
            try:
                with Dataset(path, "r") as dataset:
                    if "Times" not in dataset.variables:
                        self._wrfout_timestamp_index_cache[key] = []
                        return []
                    raw_times = np.asarray(dataset.variables["Times"][:])
                    decoded_times = np.atleast_1d(chartostring(raw_times))
                    for source_index, raw_time in enumerate(decoded_times):
                        text = str(raw_time).strip().strip("\x00")
                        timestamped_records.append(
                            (datetime.strptime(text, "%Y-%m-%d_%H:%M:%S"), path, source_index)
                        )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    wrfout_read_error(path=path, action="read Times timestamps", original=exc)
                ) from exc

        if not timestamped_records:
            self._wrfout_timestamp_index_cache[key] = []
            return []
        initial_time = min(item[0] for item in timestamped_records)
        records = [
            ((timestamp - initial_time).total_seconds() / 3600.0, path, source_index)
            for timestamp, path, source_index in timestamped_records
        ]
        records.sort(key=lambda item: (item[0], str(item[1]), item[2]))
        self._wrfout_timestamp_index_cache[key] = records
        return records

    # 功能说明：建立 wrfout 文件和域编号的索引，减少重复扫描文件系统。
    def _wrfout_index(self, files: list[Path]) -> list[tuple[int, int, Path]]:
        key = tuple(Path(path) for path in files)
        cached = self._wrfout_index_cache.get(key)
        if cached is not None:
            return cached

        entries: list[tuple[int, int, Path]] = []
        start = 0
        for path in key:
            ntimes = self._wrfout_time_count(path)
            entries.append((start, start + ntimes, path))
            start += ntimes
        self._wrfout_index_cache[key] = entries
        return entries

    # 功能说明：读取单个 wrfout 文件的时间维长度。
    def _wrfout_time_count(self, path: Path) -> int:
        path = Path(path)
        cached = self._wrfout_time_count_cache.get(path)
        if cached is not None:
            return cached

        from netCDF4 import Dataset

        try:
            with Dataset(path, "r") as dataset:
                if "Time" in dataset.dimensions:
                    ntimes = max(len(dataset.dimensions["Time"]), 1)
                else:
                    ntimes = 1
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(wrfout_read_error(path=path, action="open/count Time dimension", original=exc)) from exc

        self._wrfout_time_count_cache[path] = ntimes
        return ntimes

    # 功能说明：准备一维诊断依赖并调用诊断模块，生成随时间变化的标量结果。
    def _process_1d_diagnostic(
        self,
        *,
        request: ProcessingRequest,
        variable: str,
        source_file: Path | None,
        source_time_index: int | None,
        time_hour: int,
        dtype: str,
        progress: ProgressCallback | None = None,
    ) -> str:
        return process_1d_diagnostic(
            request=request,
            path_manager=self.path_manager,
            variable=variable,
            source_file=source_file,
            source_time_index=source_time_index,
            time_hour=time_hour,
            dtype=dtype,
            domain_config=_domain_config_dict(request).get(request.domain, {}),
            ensure_slp_center=self._ensure_slp_center,
            ensure_standard_data=self._ensure_standard_data,
            ensure_layer_centers=self._ensure_layer_centers,
            progress=progress,
        )

    # 功能说明：读取水平风和中心位置，计算径向风或切向风并保存极坐标结果。
    def _process_radial_tangential_variable(
        self,
        *,
        request: ProcessingRequest,
        variable: str,
        time_hour: int,
        heights: list[float],
        dtype: str,
        center_mode: str = "slp",
    ) -> list[str]:
        return process_radial_tangential_variable(
            request=request,
            variable=variable,
            time_hour=time_hour,
            heights=heights,
            dtype=dtype,
            center_mode=center_mode,
        )


    # 功能说明：按时间批量读取 WRF 文件，计算一个变量并安全写入各输出文件。
    def _process_wrf_variable_batch(
        self,
        *,
        request: ProcessingRequest,
        source_file: Path,
        source_time_index: int,
        time_hour: int,
        variables: list[tuple[str, VariableDimension, list[float]]],
        dtype: str,
        progress: ProgressCallback | None = None,
    ) -> list[str]:
        if any(variable in STORM_RELATIVE_WIND_VARIABLES for variable, _dimension, _heights in variables):
            self._ensure_storm_motion(request, [time_hour], progress)

        prepare_wrf_import()
        from netCDF4 import Dataset
        from wrf import getvar, interplevel, to_np

        try:
            dataset_context = Dataset(source_file, "r")
        except Exception as exc:  # noqa: BLE001
            action = f"open source_time_index={source_time_index} time={time_hour}h"
            raise RuntimeError(wrfout_read_error(path=source_file, action=action, original=exc)) from exc

        processed: list[str] = []
        with dataset_context as dataset:
            _save_dataset_grid_coordinates(
                dataset,
                self.path_manager,
                request.member_name,
                request.domain,
                source_time_index,
            )
            z = None
            for variable, dimension, selected_heights in variables:
                try:
                    data, field_metadata = _read_wrf_field(
                        dataset, variable, source_file=source_file,
                        timeidx=source_time_index, options=request.options,
                    )
                    if dimension == VariableDimension.THREE_D:
                        if z is None:
                            z = getvar(dataset, "z", timeidx=source_time_index, units="m")
                        interpolated = interplevel(data, z, np.asarray(selected_heights, dtype=float))
                        values = to_np(interpolated)
                        if variable == "pvo":
                            values = np.ma.filled(values, np.nan)
                        array = np.asarray(values, dtype=dtype)
                    else:
                        array = np.asarray(to_np(data), dtype=dtype)
                except Exception as exc:  # noqa: BLE001
                    action = (
                        f"read/interpolate variable={variable} time={time_hour}h "
                        f"source_time_index={source_time_index}"
                    )
                    raise RuntimeError(wrfout_read_error(path=source_file, action=action, original=exc)) from exc

                if dimension == VariableDimension.THREE_D:
                    processed.extend(
                        self._process_3d_variable(
                            request=request,
                            variable=variable,
                            source_file=source_file,
                            source_time_index=source_time_index,
                            time_hour=time_hour,
                            heights=selected_heights,
                            dtype=dtype,
                            progress=progress,
                            prepared_array=array,
                            additional_metadata=field_metadata,
                        )
                    )
                else:
                    processed.append(
                        self._process_2d_variable(
                            request=request,
                            variable=variable,
                            source_file=source_file,
                            source_time_index=source_time_index,
                            time_hour=time_hour,
                            dtype=dtype,
                            prepared_array=array,
                        )
                    )
        return processed

    # 功能说明：逐时间和高度读取三维 WRF 场，保存每个高度层的处理结果。
    def _process_3d_variable(
        self,
        *,
        request: ProcessingRequest,
        variable: str,
        source_file: Path,
        source_time_index: int,
        time_hour: int,
        heights: list[float],
        dtype: str,
        progress: ProgressCallback | None = None,
        prepared_array: np.ndarray | None = None,
        additional_metadata: dict[str, Any] | None = None,
    ) -> list[str]:
        if prepared_array is None:
            prepare_wrf_import()
            from netCDF4 import Dataset
            from wrf import getvar, interplevel, to_np

            try:
                with Dataset(source_file, "r") as dataset:
                    data, additional_metadata = _read_wrf_field(
                        dataset, variable, source_file=source_file,
                        timeidx=source_time_index, options=request.options,
                    )
                    z = getvar(dataset, "z", timeidx=source_time_index, units="m")
                    interpolated = interplevel(data, z, np.asarray(heights, dtype=float))
                    values = to_np(interpolated)
                    if variable == "pvo":
                        values = np.ma.filled(values, np.nan)
                    array = np.asarray(values, dtype=dtype)
            except Exception as exc:  # noqa: BLE001
                action = f"read/interpolate variable={variable} time={time_hour}h source_time_index={source_time_index}"
                raise RuntimeError(wrfout_read_error(path=source_file, action=action, original=exc)) from exc
        else:
            array = np.asarray(prepared_array, dtype=dtype)

        if array.ndim == 2:
            array = array[np.newaxis, :, :]

        storm_motion_metadata: dict[str, Any] | None = None
        if variable in STORM_RELATIVE_WIND_VARIABLES:
            motion_path = self._ensure_storm_motion(request, [time_hour], progress)
            storm_u, storm_v = load_motion_for_time(motion_path, time_hour)
            component_name = STORM_RELATIVE_WIND_VARIABLES[variable]
            motion_value = storm_u if component_name == "u" else storm_v
            array = array - np.asarray(motion_value, dtype=array.dtype)
            storm_motion_metadata = {
                "motion_corrected": True,
                "storm_motion_path": str(motion_path),
                "storm_motion_component": component_name,
                "storm_motion_value": float(motion_value),
            }

        processed: list[str] = []
        for idx, height_m in enumerate(heights):
            existing = self.path_manager.existing_variable(
                member_name=request.member_name,
                domain=request.domain,
                variable=variable,
                time_hour=time_hour,
                dimension=VariableDimension.THREE_D,
                height_m=height_m,
            )
            if existing is not None and not request.force and processed_file_is_current(
                variable,
                existing.metadata,
                source_file=source_file,
                source_time_index=source_time_index,
            ):
                processed.append(str(existing.path))
                continue
            out_path = self.path_manager.variable_path(
                member_name=request.member_name,
                domain=request.domain,
                variable=variable,
                time_hour=time_hour,
                dimension=VariableDimension.THREE_D,
                height_m=height_m,
            )
            values = array[idx] if variable == "pvo" else fill_nan(array[idx])
            self._atomic_save(out_path, values.astype(dtype, copy=False))
            metadata = self._metadata(request, variable, source_file, time_hour, height_m, source_time_index)
            if additional_metadata:
                metadata.update(additional_metadata)
            if storm_motion_metadata is not None:
                metadata.update(storm_motion_metadata)
            self.path_manager.write_metadata(
                out_path,
                metadata,
            )
            processed.append(str(out_path))
        return processed

    # 功能说明：逐时间读取二维 WRF 场，写出带源文件元数据的二维结果。
    def _process_2d_variable(
        self,
        *,
        request: ProcessingRequest,
        variable: str,
        source_file: Path,
        source_time_index: int,
        time_hour: int,
        dtype: str,
        prepared_array: np.ndarray | None = None,
        additional_metadata: dict[str, Any] | None = None,
    ) -> str:
        out_path = self.path_manager.variable_path(
            member_name=request.member_name,
            domain=request.domain,
            variable=variable,
            time_hour=time_hour,
            dimension=VariableDimension.TWO_D,
        )
        existing = self.path_manager.existing_variable(
            member_name=request.member_name,
            domain=request.domain,
            variable=variable,
            time_hour=time_hour,
            dimension=VariableDimension.TWO_D,
        )
        if existing is not None and not request.force and processed_file_is_current(
            variable,
            existing.metadata,
            source_file=source_file,
            source_time_index=source_time_index,
        ):
            return str(existing.path)

        if prepared_array is None:
            prepare_wrf_import()
            from netCDF4 import Dataset
            from wrf import getvar, to_np

            try:
                with Dataset(source_file, "r") as dataset:
                    wrf_name = VARIABLE_REGISTRY.get(variable).wrf_name if variable in VARIABLE_REGISTRY else variable
                    data = getvar(dataset, wrf_name, timeidx=source_time_index)
                    data = prepare_wrf_output(variable, data)
                    array = np.asarray(to_np(data), dtype=dtype)
            except Exception as exc:  # noqa: BLE001
                action = f"read variable={variable} time={time_hour}h source_time_index={source_time_index}"
                raise RuntimeError(wrfout_read_error(path=source_file, action=action, original=exc)) from exc
        else:
            array = np.asarray(prepared_array, dtype=dtype)

        self._atomic_save(out_path, fill_nan(array).astype(dtype, copy=False))
        metadata = self._metadata(request, variable, source_file, time_hour, None, source_time_index)
        if additional_metadata:
            metadata.update(additional_metadata)
        self.path_manager.write_metadata(out_path, metadata)
        return str(out_path)

    # 功能说明：为一个处理输出记录请求参数、源文件、时间索引和高度等追溯信息。
    def _metadata(
        self,
        request: ProcessingRequest,
        variable: str,
        source_file: Path,
        time_hour: int,
        height_m: float | None,
        source_time_index: int | None = None,
    ) -> dict[str, Any]:
        return build_processing_metadata(request, variable, source_file, time_hour, height_m, source_time_index)

    # 功能说明：将处理数组写入临时路径后替换目标文件，保证并行任务不会读到半成品。
    def _atomic_save(self, path: Path, array: np.ndarray) -> None:
        atomic_save(path, array)


# 功能说明：从 WRF 数据集提取经纬度网格，并保存到成员域的共享 grid 目录。
def _save_dataset_grid_coordinates(
    dataset: Any,
    path_manager: PathManager,
    member_name: str,
    domain: int,
    source_time_index: int,
) -> None:
    variables = getattr(dataset, "variables", {})
    lat_var = next((variables[name] for name in ("XLAT", "XLAT_M", "lat", "LAT") if name in variables), None)
    lon_var = next((variables[name] for name in ("XLONG", "XLONG_M", "lon", "LON") if name in variables), None)
    if lat_var is None or lon_var is None:
        return

    # 功能说明：读取一个变量的处理文件，返回数组及其元数据。
    def read(variable: Any) -> np.ndarray:
        if len(variable.shape) >= 3:
            index = min(max(int(source_time_index), 0), variable.shape[0] - 1)
            return np.asarray(variable[index, ...], dtype=np.float32)
        return np.asarray(variable[:, :], dtype=np.float32)

    save_grid_coordinates(path_manager, member_name, domain, read(lat_var), read(lon_var))


# 功能说明：解析极坐标变量名，取得源变量、中心模式和高度等组成部分。
def _parse_polar_variable(variable: str) -> tuple[str, str, str] | None:
    if not variable.endswith("_polar"):
        return None
    requested_name = variable[: -len("_polar")]
    resolved = resolve_variable_name(requested_name)
    return requested_name, resolved.base_name, resolved.center_mode


# 功能说明：复制请求并覆盖指定字段，使派生流程能安全使用临时选项。
def _request_with(request: ProcessingRequest, **updates: Any) -> ProcessingRequest:
    if hasattr(request, "model_copy"):
        return request.model_copy(update=updates)
    if hasattr(request, "copy"):
        return request.copy(update=updates)
    data = request.dict()
    data.update(updates)
    return ProcessingRequest(**data)


# 功能说明：优先采用主选择；主选择为空时使用后备选择，并规范成统一选择对象。
def _selection(values: list[float]) -> SelectionSpec:
    return SelectionSpec(values=list(values))


# 功能说明：把时次转为浮点数；接近整数时返回整数，带小数的时次保留至 10 位小数，避免同一时次因格式不同重复处理。
def _clean_time(value: int | float) -> int | float:
    number = float(value)
    rounded = round(number)
    return int(rounded) if abs(number - rounded) < 1e-10 else round(number, 10)


# 功能说明：从请求取得域的网格尺寸、经纬间距和投影设置，整理成中心定位和极坐标转换模块能直接读取的字典。
def _domain_config_dict(request: ProcessingRequest) -> dict[int, dict[str, Any]]:
    return domain_config_dict(request)


# 功能说明：检查中心文件的时间列是否包含请求的全部时间，并验证算法参数匹配。
def center_file_has_times(
    path: Path,
    times: list[int | float],
    algorithm_options: dict[str, Any] | None = None,
) -> bool:
    if not path.exists():
        return False
    center_keys = {
        "center_search_radius_km",
        "center_cross_radius_grid",
        "center_pcc_radius_grid",
        "center_boundary_width",
        "center_max_iterations",
    }
    if algorithm_options and center_keys.intersection(algorithm_options):
        if not center_output_is_current(path, algorithm_options):
            return False
    rows = read_new_center_rows(path)
    return bool(rows) and all(_clean_time(time_hour) in rows for time_hour in times)

