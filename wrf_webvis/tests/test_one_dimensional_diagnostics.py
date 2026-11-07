import tempfile
import unittest
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from wrf_webvis.app.core.registry import plotting_variable_type_registry
from wrf_webvis.app.core.schemas import FormatConfig, PlotRequest, PlotType, ProcessingRequest, SelectionSpec, VariableConfig
from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.plotting.draw_format import maybe_draw_land_distance, maybe_draw_rmw, set_format2, set_format3, set_format5
from wrf_webvis.app.plotting.draw_main import draw_overlay_member_time_series, resolve_figsize_per_panel, render, resolve_members_from_config, selection_for_member_time_limit
from wrf_webvis.app.plotting.draw_variables import prepare_variable_data, time_series_plot
from wrf_webvis.app.plotting.plot_spec import PlotSpec, build_plot_spec
from wrf_processing.data_manager import DataManager
from wrf_webvis.app.services.plot_service import PlotService, _member_limited_times
from wrf_processing.diagnostics import center_land_distance as center_land_distance_diagnostic
# 2026-11-01：绘图只读 processed_v3；下面按旧 v2 路径写的假数据实际写成 v3 切片。
from v3_fixtures import save_at_legacy_path


class OneDimensionalDiagnosticTests(unittest.TestCase):
    # 功能说明：在选定扇区和半径内分别放入2和4，检查区域平均得到两个时刻的2和4，时间轴仍为0和1。
    def test_time_series_reduces_polar_2d_over_selected_angle_and_radius(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            variable_root = root / "member" / "d03" / "RAINNCV_polar"
            variable_root.mkdir(parents=True)
            for time_hour, radial_size, selected_value in ((0, 100, 2.0), (1, 100, 4.0)):
                values = np.zeros((360, radial_size), dtype=np.float32)
                values[225:316, 20:81] = selected_value
                save_at_legacy_path(variable_root / f"{time_hour}h.npy", values)

            payloads, _summaries = prepare_variable_data(
                member_name="member",
                domain=3,
                variables={"RAINNCV": {"enabled": True, "plot_type": "line"}},
                fig_type=5,
                data_root=root,
                domain_config={3: {"dx_km": 1.0, "dy_km": 1.0}},
                selection={
                    "times": [0, 1],
                    "angle": list(range(225, 316)),
                    "radius": list(range(20, 81)),
                },
                polar_dir_style="auto",
                variable_type_registry={"RAINNCV": "2d"},
            )

            np.testing.assert_allclose(payloads["RAINNCV"]["values"], [2.0, 4.0])
            np.testing.assert_allclose(payloads["RAINNCV"]["x"], [0.0, 1.0])

    # 功能说明：提交带方向扇区和半径范围的时间曲线预检查，确认选区未丢失，而且检查的是已有极坐标降雨文件。
    def test_polar_time_series_dry_run_preserves_sector_and_requires_polar_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            variable_root = root / "member" / "d03" / "RAINNCV_polar"
            variable_root.mkdir(parents=True)
            save_at_legacy_path(variable_root / "0h.npy", np.ones((360, 100), dtype=np.float32))

            request = PlotRequest(
                data_root=root,
                processed_root=root,
                member_name="member",
                domain=3,
                fig_type=PlotType.TIME_SERIES,
                variables=[VariableConfig(name="RAINNCV", plot_type="line", use_polar=True)],
                time=SelectionSpec(values=[0]),
                angle=SelectionSpec(start=225, end=315, step=1, average=True),
                radius=SelectionSpec(start=20, end=80, step=1, average=True),
            )

            result = PlotService(root / "figures").dry_run(request)

            self.assertIsNotNone(request.angle)
            self.assertIsNotNone(request.radius)
            self.assertEqual(len(result["plan"]["missing_files"]), 0)
            self.assertIn("RAINNCV_polar", result["plan"]["required_files"][0]["path"])

    # 功能说明：把试验标为全海洋并指定约定距离8888公里，检查输出确实保存该值，而不要求真实海岸距离。
    def test_all_ocean_center_land_distance_uses_configured_large_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "wrfout_d03_test"
            source.touch()
            request = ProcessingRequest(
                processed_root=root,
                member_name="WRF_0_sea",
                domain=3,
                variables=["center_land_distance"],
                options={
                    "all_ocean_member_patterns": ["_sea"],
                    "all_ocean_land_distance_km": 8888.0,
                },
            )
            out = center_land_distance_diagnostic.process(
                request=request,
                path_manager=PathManager(root),
                source_file=source,
                source_time_index=0,
                time_hour=0,
                dtype="float32",
                ensure_slp_center=lambda *_args, **_kwargs: root / "center.txt",
            )

            np.testing.assert_allclose(np.load(out), np.asarray([8888.0], dtype=np.float32))

    # 功能说明：请求计算最大风速，检查计划把它归为单值诊断并放在以小时命名的文件中。
    def test_processing_plan_uses_per_hour_scalar_path(self):
        request = ProcessingRequest(
            processed_root="processed",
            wrfout_root="0_land/run",
            member_name="0_land",
            domain=3,
            variables=["local_max_wind"],
            times=SelectionSpec(values=[0]),
        )
        plan = DataManager("processed").plan_for_processing(request)

        self.assertEqual(plan.required_files[0].dimension, "1d")
        self.assertIn("local_max_wind", str(plan.required_files[0].path))
        self.assertTrue(str(plan.required_files[0].path).endswith("0h.npy"))

    # 功能说明：请求最大风速半径的时间曲线，检查它读取每小时的单值文件，不去寻找不存在的半径极坐标场。
    def test_time_series_plot_plan_does_not_use_polar_path(self):
        request = PlotRequest(
            data_root="processed",
            processed_root="processed",
            member_name="0_land",
            domain=3,
            fig_type=PlotType.TIME_SERIES,
            variables=[VariableConfig(name="rmw", plot_type="line")],
            time=SelectionSpec(values=[0, 1]),
        )
        plan = DataManager.from_plot_request(request).plan_for_plot(request)

        paths = [str(item.path) for item in plan.required_files]
        self.assertTrue(all("rmw_polar" not in path for path in paths))
        self.assertTrue(paths[0].endswith("0h.npy"))
        self.assertTrue(paths[1].endswith("1h.npy"))

    # 功能说明：让一个试验只到1小时、另一个到2小时，检查时间—半径图的文件计划分别截止于各自结束时刻。
    def test_time_radius_plan_respects_each_member_end_hour(self):
        request = PlotRequest(
            data_root="processed",
            processed_root="processed",
            member_name="long",
            member_names=["long", "short"],
            selection={"members": ["long", "short"], "member_time_limits": {"short": 1}},
            domain=3,
            fig_type=PlotType.TIME_RADIUS,
            variables=[VariableConfig(name="RAINNCV")],
            time=SelectionSpec(values=[0, 1, 2]),
            height=SelectionSpec(values=[1000]),
            angle=SelectionSpec(values=[0, 360]),
            radius=SelectionSpec(values=[0, 200]),
        )

        plan = DataManager.from_plot_request(request).plan_for_plot(request)
        requested = {(Path(item.path).parts[-4], item.time_hour) for item in plan.required_files}

        self.assertEqual(requested, {("long", 0), ("long", 1), ("long", 2), ("short", 0), ("short", 1)})

    # 功能说明：设置图上半径显示到300公里而原选择只到200公里，检查预检查会按显示范围将读取半径扩到300公里。
    def test_time_radius_reading_radius_uses_xlim(self):
        request = PlotRequest(
            data_root="processed",
            processed_root="processed",
            member_name="member",
            domain=3,
            fig_type=PlotType.TIME_RADIUS,
            variables=[VariableConfig(name="vt")],
            time=SelectionSpec(values=[0]),
            radius=SelectionSpec(values=[0, 200]),
            format=FormatConfig(time_radius={"xlim": [0, 300]}),
        )

        PlotService().dry_run(request)

        self.assertEqual(request.radius.values, [0, 300])

    # 功能说明：给短试验设置1小时截止，检查时间筛选只裁掉它的后续时次，长试验仍保留完整列表。
    def test_polar_preparation_limits_each_member_to_its_end_hour(self):
        request = PlotRequest(
            member_name="long",
            member_names=["long", "short"],
            selection={"member_time_limits": {"short": 1}},
            domain=3,
            fig_type=PlotType.TIME_RADIUS,
        )

        self.assertEqual(_member_limited_times(request, "long", [0, 1, 2]), [0, 1, 2])
        self.assertEqual(_member_limited_times(request, "short", [0, 1, 2]), [0, 1])

    # 功能说明：创建结束时刻不同的两个试验的小文件，实际生成并排时间—半径图，检查不会为短试验缺少后续时刻而失败。
    def test_time_radius_multi_panel_truncates_each_member_at_its_end_hour(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for member, times in {"long": [0, 1, 2], "short": [0, 1]}.items():
                variable_root = root / member / "d03" / "RAINNCV_polar"
                variable_root.mkdir(parents=True)
                for time_hour in times:
                    save_at_legacy_path(variable_root / f"{time_hour}h.npy", np.full((360, 3), time_hour, dtype=float))

            output = root / "time_radius.png"
            spec = PlotSpec(
                data_root=root,
                output_path=output,
                members=["long", "short"],
                domain=3,
                domain_config={3: {"dx_km": 1.0, "dy_km": 1.0}},
                figure={"fig_type": 2, "row": 1, "col": 2, "panel_by": "member"},
                variables={"RAINNCV": {"enabled": True}},
                selection={
                    "members": ["long", "short"],
                    "times": [0, 1, 2],
                    "height": [1000],
                    "angle": [0, 360],
                    "radius": [0, 2],
                    "member_time_limits": {"short": 1},
                },
                format={"time_radius": {"ylim": [0, 2]}},
                polar_dir_style="suffix",
                polar_radius_resolution_km=1.0,
                variable_type_registry=plotting_variable_type_registry(),
            )

            render(spec)

            self.assertTrue(output.exists())

    # 功能说明：让选择区的试验名单与旧默认名单不同，检查文件计划和绘图规格都采用选择区的新名单。
    def test_plot_request_members_can_come_from_selection(self):
        request = PlotRequest(
            data_root="processed",
            processed_root="processed",
            member_name="fallback",
            member_names=["old"],
            selection={"members": ["m1", "m2"]},
            domain=3,
            fig_type=PlotType.TIME_SERIES,
            variables=[VariableConfig(name="rmw", plot_type="line")],
            time=SelectionSpec(values=[0]),
        )
        plan = DataManager.from_plot_request(request).plan_for_plot(request)
        spec = build_plot_spec(request, "plot.png")

        self.assertEqual(spec.members, ["m1", "m2"])
        self.assertEqual(spec.selection["members"], ["m1", "m2"])
        self.assertTrue(all(("m1" in str(item.path) or "m2" in str(item.path)) for item in plan.required_files))

    # 功能说明：分别传入列表、逗号分隔的试验名和空选择区，检查优先使用选择区，未提供时才用总配置名单。
    def test_config_members_can_come_from_selection(self):
        self.assertEqual(resolve_members_from_config([], {"members": ["m1", "m2"]}), ["m1", "m2"])
        self.assertEqual(resolve_members_from_config(["top"], {"members": "m1,m2"}), ["m1", "m2"])
        self.assertEqual(resolve_members_from_config(["top"], {}), ["top"])

    # 功能说明：保存两个时刻的半径25和30，检查读取后时间轴为0、1，曲线值仍为25、30。
    def test_prepare_time_series_reads_one_value_per_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            var_root = root / "member" / "d03" / "rmw"
            var_root.mkdir(parents=True)
            save_at_legacy_path(var_root / "0h.npy", np.asarray([25.0], dtype=np.float32))
            save_at_legacy_path(var_root / "1h.npy", np.asarray([30.0], dtype=np.float32))

            payloads, _summaries = prepare_variable_data(
                member_name="member",
                domain=3,
                variables={"rmw": {"enabled": True, "plot_type": "line"}},
                fig_type=5,
                data_root=root,
                selection={"times": [0, 1]},
                variable_type_registry=plotting_variable_type_registry(),
            )

        np.testing.assert_allclose(payloads["rmw"]["x"], np.asarray([0.0, 1.0]))
        np.testing.assert_allclose(payloads["rmw"]["values"], np.asarray([25.0, 30.0]))

    # 功能说明：给三个时刻设置平均最大风速12、8、11并开启10的阈值，检查只将中间弱风时刻的半径隐藏为缺测。
    def test_time_series_can_hide_rmw_when_axis_mean_wind_is_too_weak(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for variable, values in {
                "rmw": [45.0, 63.0, 70.0],
                "axis_mean_max_wind": [12.0, 8.0, 11.0],
            }.items():
                variable_root = root / "member" / "d03" / variable
                variable_root.mkdir(parents=True)
                for time_hour, value in enumerate(values):
                    save_at_legacy_path(variable_root / f"{time_hour}h.npy", np.asarray([value], dtype=np.float32))

            payloads, _summaries = prepare_variable_data(
                member_name="member",
                domain=3,
                variables={"rmw": {"enabled": True, "plot_type": "line"}},
                fig_type=5,
                data_root=root,
                selection={"times": [0, 1, 2]},
                variable_type_registry=plotting_variable_type_registry(),
                format_config={
                    "time_series": {
                        "hide_weak_rmw": True,
                        "rmw_min_axis_mean_wind": 10.0,
                    }
                },
            )

        np.testing.assert_allclose(
            payloads["rmw"]["values"],
            np.asarray([45.0, np.nan, 70.0]),
            equal_nan=True,
        )

    # 功能说明：叠加海洋和陆地曲线，仅限制陆地试验结束时刻，检查陆地曲线变短而海洋曲线保持完整。
    def test_overlay_time_series_truncates_only_the_limited_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for member, values in {
                "land": [30.0, 25.0, 20.0],
                "sea": [30.0, 28.0, 26.0],
            }.items():
                variable_root = root / member / "d03" / "rmw"
                variable_root.mkdir(parents=True)
                for time_hour, value in enumerate(values):
                    save_at_legacy_path(variable_root / f"{time_hour}h.npy", np.asarray([value], dtype=np.float32))

            fig, ax = plt.subplots()
            try:
                draw_overlay_member_time_series(
                    ax=ax,
                    member_names=["land", "sea"],
                    domain=3,
                    variables={"rmw": {"enabled": True, "plot_type": "line"}},
                    data_root=root,
                    domain_config={3: {"dx_km": 1.0, "dy_km": 1.0}},
                    selection={
                        "times": [0, 1, 2],
                        "member_time_limits": {"land": 1},
                    },
                    polar_dir_style="same",
                    polar_radius_resolution_km=None,
                    variable_registry=plotting_variable_type_registry(),
                    format_config={"time_series": {"overlay_members": True}},
                )

                np.testing.assert_allclose(ax.lines[0].get_xdata(), np.asarray([0.0, 1.0]))
                np.testing.assert_allclose(ax.lines[1].get_xdata(), np.asarray([0.0, 1.0, 2.0]))
            finally:
                plt.close(fig)

    # 功能说明：把结束时刻放在时间曲线格式设置中，检查读取陆地试验时也会采用该限制，海洋试验仍保留全部时刻。
    def test_overlay_time_series_uses_member_end_hours_from_time_series_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for member in ("sea", "land"):
                variable_root = root / member / "d03" / "local_max_wind"
                variable_root.mkdir(parents=True)
                for time_hour in (0, 1, 2):
                    save_at_legacy_path(variable_root / f"{time_hour}h.npy", np.asarray([time_hour], dtype=float))

            fig, ax = plt.subplots()
            try:
                _ax, _cf, _cr, payloads, _summaries = draw_overlay_member_time_series(
                    ax=ax,
                    member_names=["sea", "land"],
                    domain=3,
                    variables={"local_max_wind": {"enabled": True, "plot_type": "line"}},
                    data_root=root,
                    domain_config={3: {"dx_km": 1.0, "dy_km": 1.0}},
                    selection={"times": [0, 1, 2]},
                    polar_dir_style="auto",
                    polar_radius_resolution_km=None,
                    variable_registry={"local_max_wind": "1d"},
                    format_config={
                        "time_series": {
                            "overlay_members": True,
                            "member_end_hours": {"land": 1},
                        }
                    },
                )

                self.assertEqual(payloads["sea:local_max_wind"]["times"], [0, 1, 2])
                self.assertEqual(payloads["land:local_max_wind"]["times"], [0, 1])
            finally:
                plt.close(fig)

    # 功能说明：保存一份逐时最大风速半径文件，检查水平图能读到它并画出半径35的参考圆。
    def test_horizontal_rmw_overlay_reads_per_hour_scalar_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            var_root = root / "member" / "d03" / "rmw"
            var_root.mkdir(parents=True)
            save_at_legacy_path(var_root / "4h.npy", np.asarray([35.0], dtype=np.float32))

            fig, ax = plt.subplots()
            try:
                maybe_draw_rmw(
                    ax,
                    root,
                    "member",
                    3,
                    [4],
                    center_x=0.0,
                    center_y=0.0,
                    dx_km=1.0,
                    fmt={"draw_rmw": True, "coordinate_system": "center_km"},
                )

                self.assertEqual(len(ax.patches), 1)
                self.assertEqual(ax.patches[0].radius, 35.0)
            finally:
                plt.close(fig)

    # 功能说明：保存中心距陆地的单值诊断，检查水平图按距离在正确的横坐标画出虚线边界。
    def test_horizontal_land_boundary_reads_center_land_distance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            var_root = root / "member" / "d03" / "center_land_distance"
            var_root.mkdir(parents=True)
            save_at_legacy_path(var_root / "4h.npy", np.asarray([-30.0], dtype=np.float32))

            fig, ax = plt.subplots()
            try:
                maybe_draw_land_distance(
                    ax,
                    root,
                    "member",
                    3,
                    [4],
                    center_x=0.0,
                    dx_km=1.0,
                    fmt={"draw_land_distance": True, "coordinate_system": "center_km"},
                )

                self.assertEqual(len(ax.lines), 1)
                np.testing.assert_allclose(ax.lines[0].get_xdata(), np.asarray([30.0, 30.0]))
                self.assertEqual(ax.lines[0].get_linestyle(), "--")
            finally:
                plt.close(fig)

    # 功能说明：给时间—半径图设置0至120公里、每40公里一格，检查横轴范围和刻度以及时间刻度均正确。
    def test_time_radius_format_accepts_xlim_and_radius_interval(self):
        fig, ax = plt.subplots()
        try:
            ax.set_xlim(0, 200)
            ax.set_ylim(0, 10)
            set_format2(
                ax,
                "member",
                3,
                [0, 10],
                [1000],
                [0, 360],
                {"dbz": {"enabled": True}},
                config={"common": {}, "time_radius": {"xlim": [0, 120], "radius_interval": 40, "time_interval": 5}},
            )

            self.assertEqual(tuple(ax.get_xlim()), (0.0, 120.0))
            np.testing.assert_allclose(ax.get_xticks(), np.asarray([0.0, 40.0, 80.0, 120.0]))
            np.testing.assert_allclose(ax.get_yticks(), np.asarray([0.0, 5.0, 10.0]))
        finally:
            plt.close(fig)

    # 功能说明：写入三个时刻的距陆地距离120、80、40，检查叠加曲线的距离和时间逐项对应。
    def test_time_radius_land_distance_uses_per_time_center_distance_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            distance_root = root / "WRF_90_land" / "d03" / "center_land_distance"
            distance_root.mkdir(parents=True)
            for time_hour, distance_km in ((0, 120.0), (5, 80.0), (10, 40.0)):
                save_at_legacy_path(distance_root / f"{time_hour}h.npy", np.asarray([distance_km]))

            fig, ax = plt.subplots()
            try:
                set_format2(
                    ax,
                    "WRF_90_land",
                    3,
                    [0, 5, 10],
                    [1000],
                    [0, 360],
                    {"dbz": {"enabled": True}},
                    data_root=root,
                    config={"common": {}, "time_radius": {"draw_land_distance": True}},
                )

                self.assertEqual(len(ax.lines), 1)
                np.testing.assert_allclose(ax.lines[0].get_xdata(), np.asarray([120.0, 80.0, 40.0]))
                np.testing.assert_allclose(ax.lines[0].get_ydata(), np.asarray([0.0, 5.0, 10.0]))
            finally:
                plt.close(fig)

    # 功能说明：将当前试验排除在距陆地曲线的显示名单之外，检查即使有数据也不画这条辅助线。
    def test_time_radius_land_distance_can_be_limited_to_configured_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            distance_root = root / "WRF_90_land" / "d03" / "center_land_distance"
            distance_root.mkdir(parents=True)
            for time_hour, distance_km in ((0, 120.0), (5, 80.0)):
                save_at_legacy_path(distance_root / f"{time_hour}h.npy", np.asarray([distance_km]))

            fig, ax = plt.subplots()
            try:
                set_format2(
                    ax,
                    "WRF_90_land",
                    3,
                    [0, 5],
                    [1000],
                    [0, 360],
                    {"dbz": {"enabled": True}},
                    data_root=root,
                    config={"common": {}, "time_radius": {"draw_land_distance": True, "land_distance_members": ["WRF_45_land"]}},
                )
                self.assertEqual(len(ax.lines), 0)
            finally:
                plt.close(fig)

    # 功能说明：准备最大风速半径与34节风半径数据，检查两条参考曲线的数值、时次、标签和指定颜色线型都正确。
    def test_time_radius_draws_rmw_and_r34_from_per_time_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for variable, values in (("rmw", (25.0, 30.0)), ("r_34", (110.0, 125.0))):
                variable_root = root / "WRF_90_land" / "d03" / variable
                variable_root.mkdir(parents=True)
                for time_hour, value in zip((0, 5), values):
                    save_at_legacy_path(variable_root / f"{time_hour}h.npy", np.asarray([value]))

            fig, ax = plt.subplots()
            try:
                set_format2(
                    ax,
                    "WRF_90_land",
                    3,
                    [0, 5],
                    [1000],
                    [0, 360],
                    {"dbz": {"enabled": True}},
                    data_root=root,
                    config={
                        "common": {},
                        "time_radius": {
                            "draw_rmw": True,
                            "draw_r34": True,
                            "rmw_color": "magenta",
                            "rmw_linestyle": ":",
                            "rmw_linewidth": 2.5,
                            "r_34_color": "navy",
                            "r_34_linestyle": "-.",
                            "r_34_linewidth": 3.5,
                        },
                    },
                )

                self.assertEqual([line.get_label() for line in ax.lines], ["RMW", "R34"])
                self.assertEqual(ax.lines[0].get_color(), "magenta")
                self.assertEqual(ax.lines[0].get_linestyle(), ":")
                self.assertEqual(ax.lines[0].get_linewidth(), 2.5)
                self.assertEqual(ax.lines[1].get_color(), "navy")
                self.assertEqual(ax.lines[1].get_linestyle(), "-.")
                self.assertEqual(ax.lines[1].get_linewidth(), 3.5)
                np.testing.assert_allclose(ax.lines[0].get_xdata(), np.asarray([25.0, 30.0]))
                np.testing.assert_allclose(ax.lines[1].get_xdata(), np.asarray([110.0, 125.0]))
                np.testing.assert_allclose(ax.lines[0].get_ydata(), np.asarray([0.0, 5.0]))
                np.testing.assert_allclose(ax.lines[1].get_ydata(), np.asarray([0.0, 5.0]))
            finally:
                plt.close(fig)

    # 功能说明：给两种半径曲线启用时间平滑，检查绘制值与指定强度的高斯平滑结果一致。
    def test_time_radius_smooths_rmw_and_r34_over_time(self):
        from scipy.ndimage import gaussian_filter1d

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_values = {"rmw": np.asarray([20.0, 60.0, 20.0]), "r_34": np.asarray([100.0, 180.0, 100.0])}
            for variable, values in source_values.items():
                variable_root = root / "WRF_90_land" / "d03" / variable
                variable_root.mkdir(parents=True)
                for time_hour, value in zip((0, 5, 10), values):
                    save_at_legacy_path(variable_root / f"{time_hour}h.npy", np.asarray([value]))

            fig, ax = plt.subplots()
            try:
                set_format2(
                    ax,
                    "WRF_90_land",
                    3,
                    [0, 5, 10],
                    [1000],
                    [0, 360],
                    {"dbz": {"enabled": True}},
                    data_root=root,
                    config={
                        "common": {},
                        "time_radius": {"draw_rmw": True, "draw_r34": True, "overlay_smooth_sigma": 1.0},
                    },
                )

                np.testing.assert_allclose(
                    ax.lines[0].get_xdata(),
                    gaussian_filter1d(source_values["rmw"], sigma=1.0, mode="nearest"),
                )
                np.testing.assert_allclose(
                    ax.lines[1].get_xdata(),
                    gaussian_filter1d(source_values["r_34"], sigma=1.0, mode="nearest"),
                )
            finally:
                plt.close(fig)

    # 功能说明：分别创建时间—半径图和时间—方向图，指定7小时登陆，检查两种图均在7小时画一条水平线。
    def test_time_radius_and_time_azimuth_draw_landfall_time_line(self):
        for formatter, section in ((set_format2, "time_radius"), (set_format3, "time_azimuth")):
            fig, ax = plt.subplots()
            try:
                formatter(
                    ax,
                    "member",
                    3,
                    [0, 10],
                    [1000],
                    [0, 360],
                    {"dbz": {"enabled": True}},
                    config={"common": {}, section: {"landfall_time_h": 7}},
                )

                landfall_lines = [
                    line for line in ax.lines
                    if np.allclose(line.get_ydata(), np.asarray([7.0, 7.0]))
                ]
                self.assertEqual(len(landfall_lines), 1)
            finally:
                plt.close(fig)

    # 功能说明：通过试验名称指定7小时登陆，检查两种以时间为纵轴的图都能从试验对照表读取该时刻。
    def test_time_radius_and_time_azimuth_use_member_landfall_time_mapping(self):
        for formatter, section in ((set_format2, "time_radius"), (set_format3, "time_azimuth")):
            fig, ax = plt.subplots()
            try:
                formatter(
                    ax,
                    "WRF_90_land",
                    3,
                    [0, 10],
                    [1000],
                    [0, 360],
                    {"dbz": {"enabled": True}},
                    config={"common": {}, section: {"landfall_times_h": {"WRF_90_land": 7}}},
                )
                landfall_lines = [line for line in ax.lines if np.allclose(line.get_ydata(), np.asarray([7.0, 7.0]))]
                self.assertEqual(len(landfall_lines), 1)
            finally:
                plt.close(fig)

    # 功能说明：在时间—方向格式中设置48小时截止，检查所选0、24、48、72小时会去掉72小时。
    def test_time_azimuth_member_end_hours_can_come_from_format(self):
        limited = selection_for_member_time_limit(
            {"times": [0, 24, 48, 72]},
            "WRF_135_land",
            {"time_azimuth": {"member_end_hours": {"WRF_135_land": 48}}},
            fig_type=3,
        )
        self.assertEqual(limited["times"], [0, 24, 48])

    # 功能说明：检查水平图和时间—半径图能使用各自格式里的宽高，同时直接指定的小图大小优先于格式默认值。
    def test_figsize_can_come_from_any_plot_format(self):
        self.assertEqual(resolve_figsize_per_panel({}, {"horizontal": {"figsize": [10, 6]}}, 0), (10.0, 6.0))
        self.assertEqual(resolve_figsize_per_panel({}, {"time_radius": {"figsize": [11, 5]}}, 2), (11.0, 5.0))
        self.assertEqual(
            resolve_figsize_per_panel({"figsize_per_panel": [7, 4]}, {"time_radius": {"figsize": [11, 5]}}, 2),
            (7.0, 4.0),
        )

    # 功能说明：绘制单独的风速曲线，检查纵轴标为米每秒，标题和刻度使用黑色。
    def test_time_series_wind_uses_speed_units(self):
        fig, ax = plt.subplots()
        try:
            payloads = {
                "local_max_wind": {
                    "config": {"label": "wind"},
                    "x": np.asarray([0.0, 1.0]),
                    "values": np.asarray([10.0, 12.0]),
                }
            }
            time_series_plot(ax, payloads)
            set_format5(ax, "member", 3, [0, 1], {"local_max_wind": {}}, config={"common": {}, "time_series": {}})

            self.assertEqual(ax.get_ylabel(), "m/s")
            self.assertEqual(ax.yaxis.label.get_color(), "black")
            self.assertTrue(all(label.get_color() == "black" for label in ax.get_yticklabels()))
        finally:
            plt.close(fig)

    # 功能说明：同时画气压和风速并开启散点，检查它们分用百帕与米每秒的左右纵轴，轴颜色随各自曲线变化，散点也分别出现。
    def test_time_series_pressure_and_wind_use_dual_colored_axes(self):
        fig, ax = plt.subplots()
        try:
            payloads = {
                "center_slp_5km": {
                    "config": {"label": "pressure"},
                    "x": np.asarray([0.0, 1.0, 2.0]),
                    "values": np.asarray([980.0, 970.0, 960.0]),
                },
                "local_max_wind": {
                    "config": {"label": "wind"},
                    "x": np.asarray([0.0, 1.0, 2.0]),
                    "values": np.asarray([20.0, 30.0, 40.0]),
                },
            }
            time_series_plot(ax, payloads, format_config={"time_series": {"scatter_points": True}})
            set_format5(
                ax,
                "member",
                3,
                [0, 1, 2],
                {"center_slp_5km": {}, "local_max_wind": {}},
                config={"common": {}, "time_series": {"xlim": [0, 2], "time_interval": 1}},
            )

            self.assertEqual(len(fig.axes), 2)
            right_ax = fig.axes[1]
            self.assertEqual(ax.get_ylabel(), "hPa")
            self.assertEqual(right_ax.get_ylabel(), "m/s")
            self.assertEqual(ax.yaxis.label.get_color(), "C0")
            self.assertEqual(right_ax.yaxis.label.get_color(), "C1")
            self.assertEqual(len(ax.collections), 1)
            self.assertEqual(len(right_ax.collections), 1)
        finally:
            plt.close(fig)

    # 功能说明：输入负90、0、90度风切变方向，检查绘图换成270、0、90度，并用US、RS、DS、LS方向标签显示刻度。
    def test_shear_direction_time_series_uses_direction_ticks(self):
        fig, ax = plt.subplots()
        try:
            payloads = {
                "shear_200_850_direction": {
                    "config": {},
                    "x": np.asarray([0.0, 1.0, 2.0]),
                    "values": np.asarray([-90.0, 0.0, 90.0]),
                }
            }
            time_series_plot(ax, payloads)
            set_format5(
                ax,
                "member",
                3,
                [0, 1, 2],
                {"shear_200_850_direction": {}},
                config={"common": {}, "time_series": {}},
            )

            np.testing.assert_allclose(ax.lines[0].get_ydata(), np.asarray([270.0, 0.0, 90.0]))
            np.testing.assert_allclose(ax.get_yticks(), np.asarray([0.0, 90.0, 180.0, 270.0, 360.0]))
            self.assertEqual([item.get_text() for item in ax.get_yticklabels()], ["US", "RS", "DS", "LS", "US"])
        finally:
            plt.close(fig)

    # 功能说明：对跨越0度的方向序列启用圆周平滑和负360至0度显示，检查曲线符合先平滑圆周分量再转回角度的计算结果。
    def test_shear_direction_reference_clockwise_wrap_and_circular_smoothing(self):
        from scipy.ndimage import gaussian_filter1d

        fig, ax = plt.subplots()
        try:
            raw = np.asarray([10.0, 5.0, 0.0, -5.0, -10.0])
            config = {
                "angle_wrap": "-360_0",
                "angle_smooth_sigma": 1.0,
                "units": "degree",
            }
            payloads = {
                "shear_200_850_direction": {
                    "config": config,
                    "x": np.arange(raw.size, dtype=float),
                    "values": raw,
                }
            }
            time_series_plot(ax, payloads)
            direction_ticks = [-360, -270, -180, -90, 0]
            set_format5(
                ax,
                "member",
                3,
                list(range(raw.size)),
                {"shear_200_850_direction": config},
                config={
                    "common": {},
                    "time_series": {
                        "direction_tick_values": direction_ticks,
                        "direction_tick_labels": ["US", "RS", "DS", "LS", "US"],
                    },
                },
            )

            theta_math = np.deg2rad(-raw)
            expected = -np.degrees(
                np.arctan2(
                    gaussian_filter1d(np.sin(theta_math), sigma=1.0),
                    gaussian_filter1d(np.cos(theta_math), sigma=1.0),
                )
            )
            expected = np.mod(expected, 360.0)
            expected[expected > 0.0] -= 360.0
            np.testing.assert_allclose(ax.lines[0].get_ydata(), expected)
            np.testing.assert_allclose(ax.get_yticks(), np.asarray(direction_ticks, dtype=float))
            self.assertEqual([item.get_text() for item in ax.get_yticklabels()], ["US", "RS", "DS", "LS", "US"])
        finally:
            plt.close(fig)

    # 功能说明：给两个试验准备风切变分量，检查箭头分别占一行、纵轴显示试验名，而且使用变量指定的箭头缩放值123。
    def test_time_series_can_draw_shear_arrows_by_member_row(self):
        fig, ax = plt.subplots()
        try:
            payloads = {
                "m1:shear_200_850_u": {
                    "variable": "shear_200_850_u",
                    "member_name": "m1",
                    "config": {"extra": {"scale": 123}},
                    "x": np.asarray([0.0, 1.0]),
                    "values": np.asarray([10.0, 12.0]),
                },
                "m1:shear_200_850_v": {
                    "variable": "shear_200_850_v",
                    "member_name": "m1",
                    "config": {},
                    "x": np.asarray([0.0, 1.0]),
                    "values": np.asarray([1.0, 2.0]),
                },
                "m2:shear_200_850_u": {
                    "variable": "shear_200_850_u",
                    "member_name": "m2",
                    "config": {"extra": {"scale": 123}},
                    "x": np.asarray([0.0, 1.0]),
                    "values": np.asarray([5.0, 6.0]),
                },
                "m2:shear_200_850_v": {
                    "variable": "shear_200_850_v",
                    "member_name": "m2",
                    "config": {},
                    "x": np.asarray([0.0, 1.0]),
                    "values": np.asarray([3.0, 4.0]),
                },
            }
            _, _, quiver = time_series_plot(
                ax,
                payloads,
                format_config={"time_series": {"shear_arrows": {"enabled": True, "member_rows": True}}},
            )
            set_format5(
                ax,
                "members",
                3,
                [0, 1],
                {"shear_200_850_u": {}, "shear_200_850_v": {}},
                config={"common": {}, "time_series": {"shear_arrows": {"enabled": True, "member_rows": True}}},
            )

            self.assertEqual(getattr(ax, "_time_series_member_rows"), ["m1", "m2"])
            self.assertEqual([item.get_text() for item in ax.get_yticklabels()], ["m1", "m2"])
            self.assertGreaterEqual(len(ax.collections), 2)
            self.assertEqual(quiver.scale, 123.0)
        finally:
            plt.close(fig)

    # 功能说明：只提供风切变箭头分量而不提供普通曲线变量，运行时间图流程，检查这种配置可以顺利完成。
    def test_time_series_quiver_variables_are_used_as_shear_arrows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for variable, values in {
                "shear_200_850_u": [10.0, 12.0],
                "shear_200_850_v": [1.0, 2.0],
            }.items():
                var_root = root / "member" / "d03" / variable
                var_root.mkdir(parents=True)
                save_at_legacy_path(var_root / "0h.npy", np.asarray([values[0]], dtype=np.float32))
                save_at_legacy_path(var_root / "1h.npy", np.asarray([values[1]], dtype=np.float32))

            spec = PlotSpec(
                data_root=root,
                output_path=root / "figures" / "plot.png",
                members=["member"],
                domain=3,
                figure={"fig_type": 5, "save": False},
                selection={"times": [0, 1]},
                quiver={
                    "enabled": True,
                    "variables": {
                        "shear_200_850_u": {"enabled": True, "color": "k"},
                        "shear_200_850_v": {"enabled": True, "color": "k"},
                    },
                },
                format={"time_series": {"shear_arrows": {"member_rows": True}}},
                variable_type_registry=plotting_variable_type_registry(),
            )

            render(spec)

    # 功能说明：给两个试验准备风切变强度，检查叠加后同一坐标轴上有两条曲线，图例分别标出试验名。
    def test_overlay_member_time_series_draws_members_on_one_axis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for member, values in {"m1": [10.0, 12.0], "m2": [20.0, 22.0]}.items():
                var_root = root / member / "d03" / "shear_200_850_magnitude"
                var_root.mkdir(parents=True)
                save_at_legacy_path(var_root / "0h.npy", np.asarray([values[0]], dtype=np.float32))
                save_at_legacy_path(var_root / "1h.npy", np.asarray([values[1]], dtype=np.float32))

            fig, ax = plt.subplots()
            try:
                draw_overlay_member_time_series(
                    ax=ax,
                    member_names=["m1", "m2"],
                    domain=3,
                    variables={"shear_200_850_magnitude": {"enabled": True, "plot_type": "line"}},
                    data_root=root,
                    domain_config={3: {"dx_km": 1.0, "dy_km": 1.0}},
                    selection={"times": [0, 1]},
                    polar_dir_style="same",
                    polar_radius_resolution_km=None,
                    variable_registry=plotting_variable_type_registry(),
                    format_config={"time_series": {"overlay_members": True}},
                )

                self.assertEqual(len(ax.lines), 2)
                self.assertEqual([line.get_label() for line in ax.lines], ["m1", "m2"])
            finally:
                plt.close(fig)

    # 功能说明：让两个试验实际拥有的文件时次不同，检查叠加曲线分别使用各自可读时次，短试验不会被补出多余点。
    def test_overlay_member_time_series_uses_each_members_available_times(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for member, values in {"long_run": [10.0, 12.0, 14.0], "short_run": [20.0, 22.0]}.items():
                var_root = root / member / "d03" / "shear_200_850_magnitude"
                var_root.mkdir(parents=True)
                for time_hour, value in enumerate(values):
                    save_at_legacy_path(var_root / f"{time_hour}h.npy", np.asarray([value], dtype=np.float32))

            fig, ax = plt.subplots()
            try:
                draw_overlay_member_time_series(
                    ax=ax,
                    member_names=["long_run", "short_run"],
                    domain=3,
                    variables={"shear_200_850_magnitude": {"enabled": True, "plot_type": "line"}},
                    data_root=root,
                    domain_config={3: {"dx_km": 1.0, "dy_km": 1.0}},
                    selection={"times": [0, 1, 2]},
                    polar_dir_style="same",
                    polar_radius_resolution_km=None,
                    variable_registry=plotting_variable_type_registry(),
                    format_config={"time_series": {"overlay_members": True}},
                )

                self.assertEqual(len(ax.lines), 2)
                np.testing.assert_allclose(ax.lines[0].get_xdata(), [0.0, 1.0, 2.0])
                np.testing.assert_allclose(ax.lines[1].get_xdata(), [0.0, 1.0])
            finally:
                plt.close(fig)

    # 功能说明：给海洋和陆地试验画时间曲线，检查按配置分别使用实线和虚线。
    def test_time_series_uses_sea_solid_and_land_dashed(self):
        fig, ax = plt.subplots()
        try:
            payloads = {
                "WRF_0_sea:local_max_wind": {
                    "variable": "local_max_wind",
                    "member_name": "WRF_0_sea",
                    "config": {},
                    "x": np.asarray([0.0, 1.0]),
                    "values": np.asarray([40.0, 45.0]),
                },
                "WRF_0_land:local_max_wind": {
                    "variable": "local_max_wind",
                    "member_name": "WRF_0_land",
                    "config": {},
                    "x": np.asarray([0.0, 1.0]),
                    "values": np.asarray([38.0, 42.0]),
                },
            }
            time_series_plot(ax, payloads, format_config={"time_series": {"member_linestyles": {"sea": "-", "land": "--"}}})
            self.assertEqual(ax.lines[0].get_linestyle(), "-")
            self.assertEqual(ax.lines[1].get_linestyle(), "--")
        finally:
            plt.close(fig)

    # 功能说明：多个试验画同一变量且都带变量标签，检查图例仍显示试验名称，便于分清各条曲线。
    def test_overlay_time_series_legend_uses_member_name_even_with_variable_label(self):
        fig, ax = plt.subplots()
        try:
            payloads = {
                "WRF_0_sea:dbz_asymmetry": {
                    "variable": "dbz_asymmetry",
                    "member_name": "WRF_0_sea",
                    "config": {"label": "dbz_asymmetry"},
                    "x": np.asarray([0.0, 1.0]),
                    "values": np.asarray([0.3, 0.4]),
                },
                "WRF_45_sea:dbz_asymmetry": {
                    "variable": "dbz_asymmetry",
                    "member_name": "WRF_45_sea",
                    "config": {"label": "dbz_asymmetry"},
                    "x": np.asarray([0.0, 1.0]),
                    "values": np.asarray([0.4, 0.5]),
                },
            }

            time_series_plot(ax, payloads, format_config={"time_series": {"overlay_members": True}})

            self.assertEqual([line.get_label() for line in ax.lines], ["WRF_0_sea", "WRF_45_sea"])
        finally:
            plt.close(fig)

    # 功能说明：同时提供试验配色表和显式橙色变量设置，检查两条曲线都优先采用指定的橙色。
    def test_time_series_explicit_variable_color_overrides_experiment_colors(self):
        fig, ax = plt.subplots()
        try:
            payloads = {
                "WRF_0_land:axis_mean_max_wind": {
                    "variable": "axis_mean_max_wind",
                    "member_name": "WRF_0_land",
                    "config": {"color": "orange"},
                    "x": np.asarray([0.0, 1.0]),
                    "values": np.asarray([40.0, 45.0]),
                },
                "WRF_225_land:axis_mean_max_wind": {
                    "variable": "axis_mean_max_wind",
                    "member_name": "WRF_225_land",
                    "config": {"color": "orange"},
                    "x": np.asarray([0.0, 1.0]),
                    "values": np.asarray([38.0, 42.0]),
                },
            }
            time_series_plot(
                ax,
                payloads,
                format_config={
                    "time_series": {"overlay_members": True},
                    "track": {
                        "experiment_order": ["0", "225"],
                        "experiment_colors": {"0": "#d34a38", "225": "#b8e34e"},
                    },
                },
            )

            self.assertEqual(ax.lines[0].get_color(), "orange")
            self.assertEqual(ax.lines[1].get_color(), "orange")
        finally:
            plt.close(fig)

    # 功能说明：指定纵轴30到70、每10一格，检查时间曲线按要求设置范围与五个刻度。
    def test_time_series_y_ticks_can_be_controlled(self):
        fig, ax = plt.subplots()
        try:
            payloads = {
                "local_max_wind": {
                    "config": {},
                    "x": np.asarray([0.0, 1.0]),
                    "values": np.asarray([33.0, 68.0]),
                }
            }
            time_series_plot(ax, payloads)
            set_format5(
                ax,
                "member",
                3,
                [0, 1],
                {"local_max_wind": {}},
                config={
                    "common": {},
                    "time_series": {"ytick_start": 30, "ytick_end": 70, "ytick_interval": 10},
                },
            )
            np.testing.assert_allclose(ax.get_yticks(), np.asarray([30, 40, 50, 60, 70]))
            np.testing.assert_allclose(ax.get_ylim(), np.asarray([30, 70]))
        finally:
            plt.close(fig)


if __name__ == "__main__":
    unittest.main()
