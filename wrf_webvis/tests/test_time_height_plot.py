from __future__ import annotations

import numpy as np
import pytest
import matplotlib.pyplot as plt

from wrf_webvis.app.core.registry import FIGURE_TYPE_REGISTRY
from wrf_webvis.app.core.schemas import PlotRequest, PlotType, SelectionSpec, VariableConfig
from wrf_webvis.app.plotting import draw_variables
from wrf_webvis.app.plotting import draw_format
from wrf_webvis.app.plotting.draw_main import selection_for_member_time_limit
from wrf_webvis.app.plotting.draw_variables import apply_postprocess, reduce_variable
from wrf_webvis.app.services.data_manager import DataManager
from wrf_webvis.app.services.plot_service import PlotService
# 2026-11-01：绘图只读 processed_v3；按旧 v2 路径写的假数据实际写成 v3 切片。
from v3_fixtures import save_at_legacy_path


# 功能说明：确认时间—高度图使用追加编号，并自动补齐完整高度、方位角和半径选择。
def test_time_height_type_and_defaults_are_registered():
    request = PlotRequest(fig_type=PlotType.TIME_HEIGHT)

    PlotService()._apply_fig_defaults(request)

    assert int(PlotType.TIME_HEIGHT) == 8
    assert FIGURE_TYPE_REGISTRY[8] == "time_height"
    assert request.height is not None
    assert len(request.height.values) > 1
    assert request.angle.values == [0, 360]
    assert request.radius.values == [0, 200]


# 功能说明：保留高度和时间两个维度，对所选方位角与半径等权平均，并按高度×时间返回。
def test_time_height_reduction_returns_height_by_time_matrix():
    stack = [
        np.asarray([[[1, 3], [5, 7]], [[10, 14], [18, 22]]], dtype=float),
        np.asarray([[[2, 4], [6, 8]], [[20, 24], [28, 32]]], dtype=float),
    ]

    result = reduce_variable(
        stack=stack,
        variable="rh",
        variable_type="3d",
        fig_type=8,
        times=[0, 6],
        heights_m=[1000, 2000],
        angle=[0, 1],
        radius_indices=[0, 1],
        radius_mode="auto",
        radius_resolution_km=1,
        first_path="rh_polar/0h",
    )

    np.testing.assert_allclose(result["values"], [[4, 5], [16, 26]])
    np.testing.assert_allclose(result["x"], [0, 6])
    np.testing.assert_allclose(result["y"], [1000, 2000])


# 功能说明：二维水平场没有高度维，时间—高度图应给出明确错误。
def test_time_height_rejects_non_three_dimensional_variables():
    with pytest.raises(ValueError, match="三维变量"):
        reduce_variable(
            stack=[np.ones((2, 2))],
            variable="slp",
            variable_type="2d",
            fig_type=8,
            times=[0],
            heights_m=[1000],
            angle=[0, 1],
            radius_indices=[0, 1],
            radius_mode="auto",
            radius_resolution_km=1,
            first_path="slp_polar/0h.npy",
        )


# 功能说明：逐高度文件应流式归约为高度×时间矩阵，无需保留完整四维数据。
def test_time_height_streaming_reducer_reads_per_height_files(tmp_path):
    for time, offset in ((0, 0), (6, 10)):
        time_root = tmp_path / "member" / "d03" / "rh_polar" / f"{time}h"
        time_root.mkdir(parents=True)
        save_at_legacy_path(time_root / "1000m.npy", np.asarray([[1, 3], [5, 7]], dtype=float) + offset)
        save_at_legacy_path(time_root / "2000m.npy", np.asarray([[10, 14], [18, 22]], dtype=float) + offset)

    assert hasattr(draw_variables, "reduce_time_height_files")
    result = draw_variables.reduce_time_height_files(
        data_root=tmp_path,
        member_name="member",
        domain=3,
        variable="rh",
        times=[0, 6],
        heights_m=[1000, 2000],
        angle=[0, 1],
        radius_indices=[0, 1],
        radius_mode="auto",
        radius_resolution_km=1,
        polar_dir_style="auto",
    )

    assert result is not None
    values, x, y, file_infos = result
    np.testing.assert_allclose(values, [[4, 14], [16, 26]])
    np.testing.assert_allclose(x, [0, 6])
    np.testing.assert_allclose(y, [1000, 2000])
    assert [info["time"] for info in file_infos] == [0, 6]


# 功能说明：完整数据准备流程必须走流式归约入口，避免先堆叠所有时次和高度数据。
def test_prepare_time_height_data_uses_streaming_reducer(tmp_path, monkeypatch):
    for time, offset in ((0, 0), (6, 10)):
        time_root = tmp_path / "member" / "d03" / "rh_polar" / f"{time}h"
        time_root.mkdir(parents=True)
        save_at_legacy_path(time_root / "1000m.npy", np.asarray([[1, 3], [5, 7]], dtype=float) + offset)
        save_at_legacy_path(time_root / "2000m.npy", np.asarray([[10, 14], [18, 22]], dtype=float) + offset)

    def fail_full_stack(**_kwargs):
        pytest.fail("time-height should not build a full in-memory stack")

    monkeypatch.setattr(draw_variables, "load_variable_stack", fail_full_stack)
    payloads, summaries = draw_variables.prepare_variable_data(
        member_name="member",
        domain=3,
        variables={"rh": {"plot_type": "contourf"}},
        fig_type=8,
        data_root=tmp_path,
        selection={
            "times": [0, 6],
            "height": [1000, 2000],
            "angle": [0, 1],
            "radius": [0, 1],
        },
        polar_dir_style="auto",
        variable_type_registry={"rh": "3d"},
    )

    np.testing.assert_allclose(payloads["rh"]["values"], [[4, 14], [16, 26]])
    assert summaries[0]["result_shape"] == (2, 2)


# 功能说明：time-height 的时间平滑只能沿横轴进行，不能把相邻高度层混合。
def test_time_height_time_smoothing_uses_x_axis():
    values = np.asarray([[0, 10, 0], [100, 100, 100]], dtype=float)

    smoothed = apply_postprocess(
        "rh",
        values,
        {"time_smooth_sigma": 1},
        fig_type=8,
    )

    assert not np.allclose(smoothed[0], values[0])
    np.testing.assert_allclose(smoothed[1], [100, 100, 100])


# 功能说明：时间—高度图的坐标范围、刻度和单位应遵循专属格式配置。
def test_time_height_format_uses_time_x_and_height_km_y():
    assert hasattr(draw_format, "set_format8")
    fig, ax = plt.subplots()
    try:
        draw_format.set_format8(
            ax,
            member_name="member",
            domain=3,
            times=[0, 6],
            heights_m=[1000, 2000],
            angle=[0, 360],
            radius_km=[0, 100],
            draw_variables={"rh": {}},
            config={
                "time_height": {
                    "xlim": [0, 6],
                    "ylim": [1000, 2000],
                    "time_interval": 3,
                    "height_tick_interval_m": 500,
                }
            },
        )

        assert ax.get_xlabel() == "Time (h)"
        assert ax.get_ylabel() == "Height (km)"
        assert ax.get_xlim() == (0, 6)
        assert ax.get_ylim() == (1000, 2000)
        assert [label.get_text() for label in ax.get_yticklabels()] == ["1", "1.5", "2"]
    finally:
        plt.close(fig)


# 功能说明：标准绘图入口应能把流式准备的数据绘制为 time-height 等值填色图。
def test_draw_fig_dispatches_time_height_plot(tmp_path):
    for time, offset in ((0, 0), (6, 10)):
        time_root = tmp_path / "member" / "d03" / "rh_polar" / f"{time}h"
        time_root.mkdir(parents=True)
        save_at_legacy_path(time_root / "1000m.npy", np.asarray([[1, 3], [5, 7]], dtype=float) + offset)
        save_at_legacy_path(time_root / "2000m.npy", np.asarray([[10, 14], [18, 22]], dtype=float) + offset)

    fig, ax = plt.subplots()
    try:
        _, contourf, contour, payloads, _ = draw_variables.draw_fig(
            ax=ax,
            member_name="member",
            domain=3,
            variables={"rh": {"plot_type": "contourf", "levels": [0, 10, 20, 30]}},
            fig_type=8,
            data_root=tmp_path,
            selection={
                "times": [0, 6],
                "height": [1000, 2000],
                "angle": [0, 1],
                "radius": [0, 1],
            },
            polar_dir_style="auto",
            variable_type_registry={"rh": "3d"},
            return_data=True,
        )

        assert contourf is not None
        assert contour is None
        assert payloads["rh"]["values"].shape == (2, 2)
    finally:
        plt.close(fig)


# 功能说明：绘图输入规划应把 time-height 三维变量解析为逐时次、逐高度的极坐标文件。
def test_time_height_data_plan_uses_polar_per_height_files(tmp_path):
    request = PlotRequest(
        processed_root=tmp_path,
        member_name="member",
        domain=3,
        fig_type=PlotType.TIME_HEIGHT,
        variables=[VariableConfig(name="rh")],
        time=SelectionSpec(values=[0, 6]),
        height=SelectionSpec(values=[1000, 2000]),
        angle=SelectionSpec(values=[0, 360]),
        radius=SelectionSpec(values=[0, 100]),
    )

    plan = DataManager.from_plot_request(request).plan_for_plot(request)

    assert len(plan.required_files) == 4
    assert all(item.variable == "rh_polar" for item in plan.required_files)
    assert {item.path.name for item in plan.required_files} == {"rh_polar.nc"}
    assert {(item.time_hour, item.height_m) for item in plan.required_files} == {(0, 1000), (0, 2000), (6, 1000), (6, 2000)}


# 功能说明：time-height 应从自己的格式节读取不同成员的最后可用时次。
def test_time_height_member_end_hours_are_applied():
    selection = {"times": [0, 6, 12]}

    limited = selection_for_member_time_limit(
        selection,
        "short",
        {"time_height": {"member_end_hours": {"short": 6}}},
        fig_type=8,
    )

    assert limited["times"] == [0, 6]


# 功能说明：第一版 time-height 只支持标量等值图，风矢量请求必须明确拒绝。
def test_time_height_rejects_quiver_variables():
    request = PlotRequest(
        fig_type=PlotType.TIME_HEIGHT,
        variables=[VariableConfig(name="rh")],
        quiver=[VariableConfig(name="vr"), VariableConfig(name="wa")],
    )

    with pytest.raises(ValueError, match="不支持.*quiver"):
        PlotService()._apply_fig_defaults(request)


# 功能说明：time-height 的角度与半径平均依赖极坐标缓存，不能切换到笛卡尔文件。
def test_time_height_requires_polar_data():
    request = PlotRequest(
        fig_type=PlotType.TIME_HEIGHT,
        variables=[VariableConfig(name="rh", use_polar=False)],
    )

    with pytest.raises(ValueError, match="极坐标"):
        PlotService()._apply_fig_defaults(request)


# 功能说明：从标准请求、文件检查、绘图格式到保存图片的完整链路应可生成 time-height PNG。
def test_plot_service_creates_time_height_image(tmp_path):
    processed_root = tmp_path / "processed_v3"
    for time, offset in ((0, 0), (6, 10)):
        time_root = processed_root / "member" / "d03" / "rh_polar" / f"{time}h"
        time_root.mkdir(parents=True)
        save_at_legacy_path(time_root / "1000m.npy", np.asarray([[1, 3], [5, 7]], dtype=float) + offset)
        save_at_legacy_path(time_root / "2000m.npy", np.asarray([[10, 14], [18, 22]], dtype=float) + offset)

    request = PlotRequest(
        processed_root=processed_root,
        member_name="member",
        domain=3,
        fig_type=PlotType.TIME_HEIGHT,
        variables=[
            VariableConfig(name="rh", plot_type="contourf", levels=[0, 10, 20, 30])
        ],
        time=SelectionSpec(values=[0, 6]),
        height=SelectionSpec(values=[1000, 2000]),
        angle=SelectionSpec(values=[0, 1]),
        radius=SelectionSpec(values=[0, 1]),
        dpi=40,
        format={
            "time_height": {
                "xlim": [0, 6],
                "ylim": [1000, 2000],
                "time_interval": 3,
                "height_tick_interval_m": 500,
            },
            "colorbar": {"enabled": False},
        },
    )

    result = PlotService(figure_root=tmp_path / "figures").create_plot(request)

    image_path = tmp_path / "figures" / f"{result['plot_id']}.png"
    assert image_path.exists()
    assert image_path.stat().st_size > 0


# 功能说明：2026-11-01 修正：高度–方位图的纵坐标用所选高度本身。以前按“高度序号表”换算，
# 所选高度不是从 100 m 起连续时（如 2000、5000 m）会被画在 100、200 m。
def test_height_azimuth_y_uses_selected_heights():
    stack = [np.ones((2, 360, 150))]
    result = reduce_variable(
        stack=stack, variable="rh", variable_type="3d", fig_type=4, times=[36],
        heights_m=[2000, 5000], angle=[0, 360], radius_indices=[50, 100],
        radius_mode="auto", radius_resolution_km=1, first_path="rh_polar",
    )
    np.testing.assert_allclose(result["y"], [2000, 5000])
