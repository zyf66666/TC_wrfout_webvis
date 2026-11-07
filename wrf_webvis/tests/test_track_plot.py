import tempfile
import unittest
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from wrf_webvis.app.plotting.draw_main import render
from wrf_webvis.app.plotting.plot_spec import PlotSpec, build_plot_spec
from wrf_webvis.app.plotting.track_plot import (
    draw_track_plot,
    load_center_track,
    lonlat_to_km,
)
from wrf_webvis.app.core.schemas import PlotRequest, PlotType
from wrf_webvis.app.services.plot_service import PlotService
from v3_fixtures import save_v3


# 2026-11-01：路径图只读 processed_v3。旧测试用 center.txt 的行格式 [时次, y, x, lat, lon, slp]，
# 这里把它写成 v3 的 slp_center（[y, x]）和 center_geo（[lat, lon]，经纬度为 NaN 时不写）。
def _write_center(target, rows, domain=3):
    root, member = target
    for row in np.atleast_2d(np.asarray(rows, dtype=float)):
        hour, y, x = row[0], row[1], row[2]
        save_v3(root, member, "slp_center", [y, x], time_hour=hour, domain=domain)
        if row.size >= 5 and np.isfinite(row[3]) and np.isfinite(row[4]):
            save_v3(root, member, "center_geo", [row[3], row[4]], time_hour=hour, domain=domain)


# 海岸线点 [纬度, 经度] 写成 v3 d01 的 land_boundary（参考时次 0 h）。
def _write_land_boundary(root, member, points):
    save_v3(root, member, "land_boundary", np.asarray(points, dtype=float), time_hour=0, domain=1)


class TrackPlotTests(unittest.TestCase):
    # 功能说明：用 d03 中心轨迹、d01 120 小时累计雨量和经纬网格，确认雨量背景按轨迹坐标绘制且轨迹位于其上方。
    def test_track_plot_overlays_d01_120h_rainnc_in_track_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            center_root = (root, "member")
            _write_center(
                center_root,
                [[0, 10, 10, 20.0, 120.0, 990], [120, 11, 12, 21.0, 121.0, 980]],
            )
            # d01 120 h 的 RAINNC 与同时次 XLAT/XLONG（processed_v3）。
            save_v3(root, "member", "RAINNC", np.asarray([[0, 2, 4], [1, 3, 5], [0, 2, 4]], dtype=float), time_hour=120, domain=1)
            save_v3(root, "member", "XLAT", np.asarray([[19.0] * 3, [20.0] * 3, [21.0] * 3]), time_hour=120, domain=1)
            save_v3(root, "member", "XLONG", np.asarray([[119.0, 120.0, 121.0]] * 3), time_hour=120, domain=1)

            fig, ax = plt.subplots()
            try:
                _ax, contour, _q, payloads, _summaries = draw_track_plot(
                    ax=ax,
                    member_names=["member"],
                    domain=3,
                    data_root=root,
                    selection={"times": [0, 120]},
                    format_config={
                        "track": {
                            "draw_land_boundary": False,
                            "draw_rainnc_background": True,
                            "rainnc_domain": 1,
                            "rainnc_time_hour": 120,
                            "rainnc_levels": [1, 3, 5],
                            "rainnc_colorbar": True,
                            "xlim_km": None,
                            "ylim_km": None,
                        }
                    },
                )

                self.assertIsNotNone(contour)
                self.assertIn("member", payloads)
                self.assertTrue(hasattr(ax, "_track_background_extent"))
                self.assertLess(ax.get_xlim()[0], -100)
                self.assertGreater(ax.get_xlim()[1], 100)
                track_line = next(line for line in ax.lines if line.get_label() == "member")
                self.assertGreater(track_line.get_zorder(), contour.get_zorder())
            finally:
                plt.close(fig)

    # 功能说明：为两个成员写入不同起始经度，确认各自的轨迹坐标都以本成员起点为原点。
    def test_center_track_starts_at_origin_for_each_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for member, lon0 in {"m1": 120.0, "m2": 121.0}.items():
                center_root = (root, member)
                rows = np.asarray(
                    [
                        [0, 10, 10, 20.0, lon0, 990],
                        [1, 11, 12, 20.0, lon0 + 1.0, 988],
                    ],
                    dtype=float,
                )
                _write_center(center_root, rows)

            track1 = load_center_track(data_root=root, member_name="m1", domain=3, times=[0, 1])
            track2 = load_center_track(data_root=root, member_name="m2", domain=3, times=[0, 1])

            self.assertEqual((track1["x"][0], track1["y"][0]), (0.0, 0.0))
            self.assertEqual((track2["x"][0], track2["y"][0]), (0.0, 0.0))
            self.assertGreater(track1["x"][1], 100.0)
            self.assertGreater(track2["x"][1], 100.0)

    # 功能说明：后一时次没有 center_geo 时，确认读取器从同时次 XLAT/XLONG 按 y、x 补回坐标。
    def test_center_track_recovers_missing_lat_lon_from_processed_grid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            center_root = (root, "member")
            _write_center(
                center_root,
                np.asarray([[0, 0, 0, 20.0, 120.0, 990], [1, 1, 2, np.nan, np.nan, 988]], dtype=float),
            )
            # 1 h 没有 center_geo 时，用同一时次（移动嵌套）的 XLAT/XLONG 在中心格点处取经纬度。
            save_v3(root, "member", "XLAT", np.asarray([[20.0, 20.0, 20.0], [21.0, 21.0, 21.0]]), time_hour=1)
            save_v3(root, "member", "XLONG", np.asarray([[120.0, 121.0, 122.0], [120.0, 121.0, 122.0]]), time_hour=1)

            track = load_center_track(data_root=root, member_name="member", domain=3, times=[0, 1])

            self.assertEqual(track["coordinate_source"], "lat_lon")
            self.assertTrue(np.isfinite(track["lat"]).all())
            self.assertTrue(np.isfinite(track["lon"]).all())
            self.assertGreater(track["x"][1], 100.0)

    # 功能说明：为两个成员和海岸点绘制轨迹图，确认两条带圆点轨迹和黑色虚线海岸线都出现。
    def test_track_plot_draws_members_and_dashed_land_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for member, lon0 in {"m1": 120.0, "m2": 121.0}.items():
                center_root = (root, member)
                rows = np.asarray(
                    [
                        [0, 10, 10, 20.0, lon0, 990],
                        [1, 11, 12, 20.5, lon0 + 0.5, 988],
                    ],
                    dtype=float,
                )
                _write_center(center_root, rows)
                _write_land_boundary(root, member, [[19.5, lon0 + 0.2], [20.0, lon0 + 0.2], [20.5, lon0 + 0.2]])

            fig, ax = plt.subplots()
            try:
                draw_track_plot(
                    ax=ax,
                    member_names=["m1", "m2"],
                    domain=3,
                    data_root=root,
                    selection={"times": [0, 1]},
                    domain_config={3: {"dx_km": 1.0, "dy_km": 1.0}},
                    format_config={"track": {"draw_land_boundary": True}},
                )

                track_lines = [line for line in ax.lines if line.get_marker() == "o"]
                land_lines = [line for line in ax.lines if line.get_linestyle() == "--" and line.get_color() == "black"]
                self.assertEqual(len(track_lines), 2)
                self.assertGreaterEqual(len(land_lines), 1)
                self.assertEqual(np.ptp(np.asarray(land_lines[0].get_xdata(), dtype=float)), 0.0)
                self.assertEqual(ax.get_xlabel(), "West-East distance (km)")
                self.assertEqual(ax.get_ylabel(), "South-North distance (km)")
                self.assertEqual(ax.get_title(), "Typhoon track")
                for line in track_lines:
                    self.assertEqual(line.get_xdata()[0], 0.0)
                    self.assertEqual(line.get_ydata()[0], 0.0)
            finally:
                plt.close(fig)

    # 功能说明：启用直线海岸选项后，确认锯齿状边界被其横坐标中位数处的竖线替代。
    def test_track_plot_replaces_zigzag_boundary_with_vertical_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            center_root = (root, "member")
            _write_center(
                center_root,
                np.asarray([[0, 10, 10, 20.0, 121.0, 990]], dtype=float),
            )
            boundary = np.asarray(
                [
                    [19.0, 119.9], [19.0, 120.1],
                    [20.0, 119.9], [20.0, 120.1],
                    [21.0, 119.9], [21.0, 120.1],
                ],
                dtype=float,
            )
            _write_land_boundary(root, "member", boundary)

            fig, ax = plt.subplots()
            try:
                draw_track_plot(
                    ax=ax,
                    member_names=["member"],
                    domain=3,
                    data_root=root,
                    selection={"times": [0]},
                    format_config={"track": {"straight_land_boundary": True}},
                )
                land_lines = [line for line in ax.lines if line.get_gid() == "land_boundary"]
                self.assertEqual(len(land_lines), 1)
                self.assertEqual(np.ptp(np.asarray(land_lines[0].get_xdata(), dtype=float)), 0.0)
                expected_x, _ = lonlat_to_km(121.0, 20.0, boundary[:, 1], boundary[:, 0])
                self.assertAlmostEqual(
                    float(land_lines[0].get_xdata()[0]),
                    float(np.median(expected_x)),
                    places=5,
                )
            finally:
                plt.close(fig)

    # 功能说明：以 12 小时间隔提供轨迹，确认每 24 小时标注 0、24、48。
    def test_track_plot_marks_and_annotates_each_24_hours(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            center_root = (root, "member")
            _write_center(
                center_root,
                np.asarray(
                    [[time_hour, 10, 10, 20.0, 120.0 + time_hour / 24.0, 990] for time_hour in [0, 12, 24, 36, 48]],
                    dtype=float,
                ),
            )

            fig, ax = plt.subplots()
            try:
                draw_track_plot(
                    ax=ax,
                    member_names=["member"],
                    domain=3,
                    data_root=root,
                    selection={"times": [0, 12, 24, 36, 48]},
                    format_config={
                        "track": {
                            "draw_land_boundary": False,
                            "annotate_time": True,
                            "annotation_interval_hours": 24,
                        }
                    },
                )

                self.assertEqual([text.get_text() for text in ax.texts], ["0", "24", "48"])
            finally:
                plt.close(fig)

    # 功能说明：启用最终时次标注后，确认非整间隔的 50 小时也会显示。
    def test_track_plot_also_annotates_the_final_time_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            center_root = (root, "member")
            times = [0, 12, 24, 36, 48, 50]
            _write_center(
                center_root,
                np.asarray(
                    [[time_hour, 10, 10, 20.0, 120.0 + time_hour / 24.0, 990] for time_hour in times],
                    dtype=float,
                ),
            )

            fig, ax = plt.subplots()
            try:
                draw_track_plot(
                    ax=ax,
                    member_names=["member"],
                    domain=3,
                    data_root=root,
                    selection={"times": times},
                    format_config={
                        "track": {
                            "draw_land_boundary": False,
                            "annotate_time": True,
                            "annotation_interval_hours": 24,
                            "annotate_final_time": True,
                        }
                    },
                )

                self.assertEqual([text.get_text() for text in ax.texts], ["0", "24", "48", "50"])
            finally:
                plt.close(fig)

    # 功能说明：关闭常规时次标注但开启最终标注时，确认只显示最后的 50。
    def test_track_plot_can_annotate_only_the_final_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            center_root = (root, "member")
            times = [0, 12, 24, 36, 48, 50]
            _write_center(
                center_root,
                np.asarray(
                    [[time_hour, 10, 10, 20.0, 120.0 + time_hour / 24.0, 990] for time_hour in times],
                    dtype=float,
                ),
            )

            fig, ax = plt.subplots()
            try:
                draw_track_plot(
                    ax=ax,
                    member_names=["member"],
                    domain=3,
                    data_root=root,
                    selection={"times": times},
                    format_config={
                        "track": {
                            "draw_land_boundary": False,
                            "annotate_time": False,
                            "annotate_final_time": True,
                        }
                    },
                )

                self.assertEqual([text.get_text() for text in ax.texts], ["50"])
            finally:
                plt.close(fig)

    # 功能说明：设置 12 小时标记间隔与最终标注，确认仅三个间隔点被标记且 25 小时被标注。
    def test_track_plot_marks_only_the_requested_interval_and_labels_the_final_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            center_root = (root, "member")
            _write_center(
                center_root,
                np.asarray(
                    [
                        [time_hour, 10, 10, 20.0, 120.0 + time_hour / 24.0, 990]
                        for time_hour in [0, 6, 12, 18, 24, 25]
                    ],
                    dtype=float,
                ),
            )

            fig, ax = plt.subplots()
            try:
                draw_track_plot(
                    ax=ax,
                    member_names=["member"],
                    domain=3,
                    data_root=root,
                    selection={"times": [0, 6, 12, 18, 24, 25]},
                    domain_config={3: {"dx_km": 1.0, "dy_km": 1.0}},
                    format_config={
                        "track": {
                            "track_markersize": 0,
                            "marker_interval_hours": 12,
                            "annotate_time": False,
                            "annotate_final_time": True,
                            "member_end_hours": {"member": 25},
                        }
                    },
                )

                self.assertEqual(len(ax.collections), 2)
                self.assertEqual(ax.collections[0].get_offsets().shape, (3, 2))
                self.assertEqual([text.get_text() for text in ax.texts], ["25"])
            finally:
                plt.close(fig)

    # 功能说明：给两个成员不同结束时次，确认各自轨迹分别截断在 24 和 48 小时。
    def test_track_plot_truncates_each_member_at_its_configured_end_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            times = [0, 12, 24, 36, 48, 50]
            for index, member in enumerate(["m1", "m2"]):
                center_root = (root, member)
                _write_center(
                    center_root,
                    np.asarray(
                        [[time_hour, 10, 10, 20.0 + index, 120.0 + time_hour / 24.0, 990] for time_hour in times],
                        dtype=float,
                    ),
                )

            fig, ax = plt.subplots()
            try:
                draw_track_plot(
                    ax=ax,
                    member_names=["m1", "m2"],
                    domain=3,
                    data_root=root,
                    selection={"times": times},
                    format_config={
                        "track": {
                            "draw_land_boundary": False,
                            "member_end_hours": {"m1": 24, "m2": 48},
                        }
                    },
                )

                lines = {line.get_label(): line for line in ax.lines if line.get_marker() == "o"}
                self.assertEqual(len(lines["m1"].get_xdata()), 3)
                self.assertEqual(len(lines["m2"].get_xdata()), 5)
            finally:
                plt.close(fig)

    # 功能说明：确认海陆成员使用预设实验颜色，并按海/陆类别使用不同线型。
    def test_track_plot_uses_experiment_colors_and_sea_land_linestyles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            members = ["WRF_0_sea", "WRF_45_land", "WRF_180_sea"]
            for index, member in enumerate(members):
                center_root = (root, member)
                _write_center(
                    center_root,
                    np.asarray(
                        [
                            [0, 10, 10, 20.0, 120.0 + index, 990],
                            [1, 11, 12, 20.5, 120.5 + index, 988],
                        ],
                        dtype=float,
                    ),
                )

            fig, ax = plt.subplots()
            try:
                draw_track_plot(
                    ax=ax,
                    member_names=members,
                    domain=3,
                    data_root=root,
                    selection={"times": [0, 1]},
                    format_config={
                        "track": {
                            "draw_land_boundary": False,
                            "experiment_order": ["0", "45", "180"],
                            "experiment_colors": {
                                "0": "#d34a38",
                                "45": "#7000df",
                                "180": "#67c7c5",
                            },
                            "member_linestyles": {"sea": "-", "land": "--"},
                        }
                    },
                )

                lines = {line.get_label(): line for line in ax.lines if line.get_marker() == "o"}
                self.assertEqual(lines["WRF_0_sea"].get_color(), "#d34a38")
                self.assertEqual(lines["WRF_0_sea"].get_linestyle(), "-")
                self.assertEqual(lines["WRF_45_land"].get_color(), "#7000df")
                self.assertEqual(lines["WRF_45_land"].get_linestyle(), "--")
                self.assertEqual(lines["WRF_180_sea"].get_color(), "#67c7c5")
            finally:
                plt.close(fig)

    # 功能说明：选择时次为空时，确认轨迹读取中心文件中的全部时次。
    def test_track_plot_uses_all_center_times_when_selection_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            center_root = (root, "m1")
            _write_center(
                center_root,
                np.asarray(
                    [
                        [0, 10, 10, 20.0, 120.0, 990],
                        [1, 11, 12, 20.0, 121.0, 988],
                        [2, 12, 14, 20.0, 122.0, 987],
                    ],
                    dtype=float,
                ),
            )

            fig, ax = plt.subplots()
            try:
                draw_track_plot(
                    ax=ax,
                    member_names=["m1"],
                    domain=3,
                    data_root=root,
                    selection={},
                    domain_config={3: {"dx_km": 1.0, "dy_km": 1.0}},
                    format_config={"common": {"title": "{member} {time}"}, "track": {"draw_land_boundary": False}},
                )

                track_lines = [line for line in ax.lines if line.get_marker() == "o"]
                self.assertEqual(len(track_lines), 1)
                self.assertEqual(len(track_lines[0].get_xdata()), 3)
                self.assertEqual(ax.get_title(), "Typhoon track")
            finally:
                plt.close(fig)

    # 功能说明：创建轨迹图请求时，确认 PlotService 不会把空时次改成 0 小时。
    def test_plot_service_does_not_default_track_time_to_zero(self):
        request = PlotRequest(
            data_root="processed",
            processed_root="processed",
            member_name="m1",
            selection={"members": ["m1"]},
            fig_type=PlotType.TRACK,
        )
        service = PlotService()
        service._apply_fig_defaults(request)
        spec = build_plot_spec(request, "plot.png")

        self.assertNotIn("times", spec.selection)

    # 功能说明：构造没有变量图层的轨迹绘图规格，确认绘图入口仍能完成调用。
    def test_render_accepts_track_without_variables(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            center_root = (root, "m1")
            _write_center(center_root, np.asarray([[0, 10, 10, 20.0, 120.0, 990]], dtype=float))

            spec = PlotSpec(
                data_root=root,
                output_path=root / "figures" / "plot.png",
                members=["m1"],
                domain=3,
                figure={"fig_type": 6, "save": False},
                selection={"times": [0]},
                format={"track": {"draw_land_boundary": False}},
            )

            render(spec)


if __name__ == "__main__":
    unittest.main()
