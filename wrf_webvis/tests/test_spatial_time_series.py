from pathlib import Path

import numpy as np
import pytest

from wrf_webvis.app.core.registry import plotting_variable_type_registry
from wrf_webvis.app.core.schemas import PlotRequest, SelectionSpec, VariableConfig
from wrf_webvis.app.plotting.draw_variables import prepare_variable_data, reduce_variable
from wrf_webvis.app.services.plot_service import PlotService
from wrf_webvis.app.services.data_manager import DataManager as WebDataManager
from wrf_processing.data_manager import DataManager as ProcessingDataManager
from v3_fixtures import save_at_legacy_path


HEIGHTS = list(range(1000, 8001, 250))


@pytest.fixture
# 功能说明：创建两个时次、多高度的极坐标加热数组，并在选定高度和半径写入可验证的数值。
def polar_data(tmp_path):
    for time in [0, 1]:
        folder = tmp_path / "member" / "d03" / "diabatic_heating_polar" / f"{time}h"
        folder.mkdir(parents=True)
        for height in [500, *HEIGHTS, 9000]:
            values = np.full((360, 91), 100000.0, dtype=np.float32)
            if height in HEIGHTS:
                values[:, 20:81] = time * 10 + (height - 1000) / 250 + np.arange(20, 81)[None, :]
            np.save(folder / f"{height}m.npy", values)  # v1 处理端 DataManager 仍按 .npy 规划
            save_at_legacy_path(folder / f"{height}m.npy", values)  # 2026-11-01：网页绘图只读 processed_v3
    return tmp_path


# 功能说明：构造三维极坐标时间序列请求，可选择把高度放在全局或变量级配置。
def request_for(root, *, global_height=False):
    return PlotRequest(
        data_root=root, processed_root=root, member_name="member", domain=3, fig_type=5,
        time=SelectionSpec(values=[0, 1]),
        height=SelectionSpec(values=HEIGHTS) if global_height else None,
        angle=SelectionSpec(values=[0, 360]), radius=SelectionSpec(values=[20, 80]),
        variables=[VariableConfig(name="diabatic_heating", plot_type="line",
                   height=None if global_height else SelectionSpec(values=HEIGHTS))],
        figure={"dpi": 80}, polar_dir_style="suffix",
    )


# 功能说明：对两个时次的合成三维数组只平均指定高度、角度和半径，确认时间轴仍保留两个值。
def test_3d_time_series_reduces_height_angle_radius_without_reducing_time():
    arrays = [np.zeros((4, 360, 101)), np.zeros((4, 360, 91))]
    for index, array in enumerate(arrays):
        array[1:3, 30:61, 20:81] = 2 + index * 2
        array[2, 30, 20] = np.nan
    # 2026-11-01：三维栈只含所选高度（1000、1250 m 两层），顺序与 heights_m 一致。
    arrays = [array[1:3] for array in arrays]
    result = reduce_variable(
        stack=arrays, variable="diabatic_heating", variable_type="3d", fig_type=5,
        times=[0, 1], heights_m=[1000, 1250], angle=[30, 60], radius_indices=[20, 80], radius_mode="auto",
        radius_resolution_km=1, first_path="synthetic.npy",
    )
    np.testing.assert_allclose(result["values"], [2, 4])
    np.testing.assert_array_equal(result["x"], [0, 1])


# 功能说明：用预制高度场绘制时间序列，确认体平均只采用 1000–8000 米和 20–80 公里环带。
def test_requested_volume_mean_uses_only_selected_heights_and_annulus(polar_data):
    payloads, _ = prepare_variable_data(
        member_name="member", domain=3, variables={"diabatic_heating": {
            "plot_type": "line", "height": HEIGHTS, "angle": [0, 360], "radius": [20, 80]}},
        fig_type=5, data_root=polar_data,
        selection={"times": [0, 1]}, polar_dir_style="suffix",
        variable_type_registry=plotting_variable_type_registry(),
    )
    np.testing.assert_allclose(payloads["diabatic_heating"]["values"], [64, 74])
    np.testing.assert_array_equal(payloads["diabatic_heating"]["x"], [0, 1])


@pytest.mark.parametrize("manager_class", [WebDataManager, ProcessingDataManager])
# 功能说明：分别用网页和处理数据管理器规划请求，确认都需要两个时次的全部高度极坐标文件。
def test_auto_polar_file_plan_matches_time_series_reader(polar_data, manager_class):
    request = request_for(polar_data)
    plan = manager_class(polar_data).plan_for_plot(request)
    assert len(plan.required_files) == 2 * len(HEIGHTS)
    assert not plan.missing_files
    assert all("diabatic_heating_polar" in str(item.path) for item in plan.required_files)


@pytest.mark.parametrize("global_height", [False, True])
# 功能说明：以两种高度配置运行预检查和实际绘图，确认空间选择未被清除且使用全部所需文件。
def test_plot_service_accepts_3d_auto_polar_and_keeps_spatial_selections(polar_data, global_height):
    request = request_for(polar_data, global_height=global_height)
    service = PlotService(polar_data / "figures")
    result = service.dry_run(request)
    assert not result["plan"]["missing_files"]
    assert len(result["plan"]["required_files"]) == 2 * len(HEIGHTS)
    assert request.angle.values == [0, 360]
    assert request.radius.values == [20, 80]
    if global_height:
        assert request.height.values == HEIGHTS
    result = service.create_plot(request)
    assert Path(result["image_path"]).is_file()
    assert len(result["used_files"]) == 2 * len(HEIGHTS)
