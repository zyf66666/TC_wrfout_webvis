import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from wrf_webvis.app.core.centers import (
    CENTER_ALGORITHM,
    clear_center_cache,
    find_pressure_center,
    load_center_yx,
    pcc_bezier_interp,
    pcc_centroid,
    pcc_first_guess,
    read_new_center_rows,
)
from wrf_webvis.app.core.paths import PathManager, metadata_path
from wrf_webvis.app.core.schemas import ProcessingRequest, SelectionSpec, VariableDimension
from wrf_processing.center_manager import CenterManager
from wrf_processing.processing_manager import ProcessingManager


class CenterParsingTests(unittest.TestCase):
    # 功能说明：用参考气压格点计算初猜中心，确认向量化结果与原标量算法一致。
    def test_first_guess_matches_uploaded_scalar_algorithm(self):
        yy, xx = np.mgrid[:31, :35]
        field = 1000.0 - 40.0 * np.exp(-(((yy - 14) / 5.0) ** 2 + ((xx - 19) / 7.0) ** 2))
        field[12, 16] -= 2.0

        expected = _reference_first_guess(field, dist=4)
        self.assertEqual(pcc_first_guess(field, dist=4), expected)

    # 功能说明：比较贝塞尔插值和质心计算，确认实现复现上传的标量算法。
    def test_bezier_and_centroid_match_uploaded_scalar_algorithm(self):
        yy, xx = np.mgrid[:25, :25]
        field = -30.0 * np.exp(-(((yy - 11) / 6.0) ** 2 + ((xx - 14) / 5.0) ** 2))
        expected_polar = _reference_bezier(field, 12, 12, 4)
        actual_polar = pcc_bezier_interp(field, 12, 12, 4)
        np.testing.assert_allclose(actual_polar, expected_polar, rtol=1e-12, atol=1e-12)
        np.testing.assert_allclose(pcc_centroid(actual_polar), _reference_centroid(expected_polar), rtol=1e-12, atol=1e-12)

    # 功能说明：制作中心最低压的合成场，确认完整 PCC 流程找到该中心。
    def test_full_pcc_finds_synthetic_pressure_center(self):
        yy, xx = np.mgrid[:151, :151]
        field = 1000.0 - 65.0 * np.exp(-(((yy - 72) / 12.0) ** 2 + ((xx - 85) / 16.0) ** 2))
        y, x, value = find_pressure_center(
            field,
            dx_km=1.0,
            search_radius_km=12,
            cross_radius_grid=30,
            pcc_radius_grid=10,
        )
        self.assertEqual((y, x), (72.0, 85.0))
        self.assertAlmostEqual(value, 935.0)

    # 功能说明：读取带时次列的新中心文件，确认返回记录包含正确时次。
    def test_new_center_format_uses_time_column(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "center.txt"
            np.savetxt(path, np.asarray([[1, 243, 241, np.nan, np.nan, 942], [0, 250, 251, np.nan, np.nan, 940]]))
            self.assertEqual(load_center_yx(path, 0), (250.0, 251.0))
            self.assertEqual(load_center_yx(path, 1), (243.0, 241.0))

    # 功能说明：读取五分钟间隔的中心记录，确认分钟不会被截断。
    def test_center_format_preserves_five_minute_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "center.txt"
            np.savetxt(path, np.asarray([[5 / 60, 250, 251, np.nan, np.nan, 940]]), fmt="%.10g")
            rows = read_new_center_rows(path)
            self.assertIn(round(5 / 60, 10), rows)
            self.assertEqual(load_center_yx(path, 5 / 60), (250.0, 251.0))

    # 功能说明：请求新格式中不存在的时次，确认明确报告缺失。
    def test_missing_new_center_time_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "center.txt"
            np.savetxt(path, np.asarray([[1, 243, 241, np.nan, np.nan, 942]]))
            with self.assertRaises(KeyError):
                load_center_yx(path, 0)

    # 功能说明：重复读取同一中心表，确认第二次使用内存缓存。
    def test_center_table_is_cached_between_reads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "center.txt"
            np.savetxt(path, np.asarray([[0, 250, 251, np.nan, np.nan, 940], [1, 243, 241, np.nan, np.nan, 942]]))

            clear_center_cache()
            with patch("wrf_webvis.app.core.centers.np.loadtxt", wraps=np.loadtxt) as loadtxt:
                self.assertEqual(load_center_yx(path, 0), (250.0, 251.0))
                rows = read_new_center_rows(path)
                self.assertEqual(rows[1][1:3], [243.0, 241.0])
                self.assertEqual(load_center_yx(path, 1), (243.0, 241.0))
                self.assertEqual(loadtxt.call_count, 1)
            clear_center_cache()

    # 功能说明：预置海平面气压中心记录，确认处理时保留它而不重新计算。
    def test_existing_slp_center_row_is_not_recomputed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = PathManager(root)
            center_path = paths.center_path("member", 3, "slp")
            center_path.parent.mkdir(parents=True)
            np.savetxt(center_path, np.asarray([[0, 1, 1, np.nan, np.nan, 999]], dtype=float))

            slp_path = paths.variable_path(
                member_name="member",
                domain=3,
                variable="slp",
                time_hour=0,
                dimension=VariableDimension.TWO_D,
            )
            slp_path.parent.mkdir(parents=True)
            field = np.full((5, 5), 1000.0)
            field[3, 3] = 900.0
            np.save(slp_path, field)

            CenterManager(root).compute_slp_centers(member_name="member", domain=3, times=[0])
            row = np.atleast_2d(np.loadtxt(center_path))[0]
            self.assertEqual((row[1], row[2], row[5]), (1.0, 1.0, 999.0))

    # 功能说明：只有中心缓存而没有原始文件时处理，确认仍能使用已有中心。
    def test_processing_uses_existing_center_without_wrfout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = PathManager(root)
            center_path = paths.center_path("member", 3, "slp")
            center_path.parent.mkdir(parents=True)
            np.savetxt(center_path, np.asarray([[0, 1, 1, np.nan, np.nan, 999]], dtype=float))

            result = ProcessingManager(root).run(
                ProcessingRequest(
                    processed_root=root,
                    member_name="member",
                    domain=3,
                    variables=["slp_center"],
                    times=SelectionSpec(values=[0]),
                )
            )
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["processed_files"], [str(center_path)])

    # 功能说明：改变 PCC 选项后读取旧缓存，确认旧结果失效并重新定位中心。
    def test_explicit_pcc_options_invalidate_legacy_center_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = PathManager(root)
            center_path = paths.center_path("member", 3, "slp")
            center_path.parent.mkdir(parents=True)
            np.savetxt(center_path, [[0, 1, 1, np.nan, np.nan, 999]])
            slp_path = paths.variable_path(
                member_name="member", domain=3, variable="slp", time_hour=0, dimension="2d"
            )
            slp_path.parent.mkdir(parents=True)
            np.save(slp_path, np.full((21, 21), 1000.0))
            options = {
                "center_search_radius_km": 6,
                "center_cross_radius_grid": 8,
                "center_pcc_radius_grid": 3,
                "center_boundary_width": 2,
                "center_max_iterations": 10,
            }

            with patch("wrf_processing.center_manager.find_pressure_center", return_value=(3, 4, 950)) as finder:
                CenterManager(root).compute_slp_centers(
                    member_name="member", domain=3, times=[0], algorithm_options=options
                )
                finder.assert_called_once()
            row = np.atleast_2d(np.loadtxt(center_path))[0]
            self.assertEqual((row[1], row[2]), (3.0, 4.0))
            metadata = __import__("json").loads(metadata_path(center_path).read_text(encoding="utf-8"))
            self.assertEqual(metadata["algorithm_version"], CENTER_ALGORITHM)

            with patch("wrf_processing.center_manager.find_pressure_center") as finder:
                CenterManager(root).compute_slp_centers(
                    member_name="member", domain=3, times=[0], algorithm_options=options
                )
                finder.assert_not_called()

    # 功能说明：为不同高度提供场，确认每层中心各自执行初猜。
    def test_each_layer_center_runs_its_own_uploaded_first_guess(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = PathManager(root)
            for height in (2000, 8000):
                path = paths.variable_path(
                    member_name="member", domain=3, variable="pressure",
                    time_hour=0, dimension="3d", height_m=height,
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                np.save(path, np.full((21, 21), 1000.0 - height / 10000.0))

            with patch(
                "wrf_processing.center_manager.find_pressure_center",
                side_effect=[(5, 6, 950), (7, 9, 700)],
            ) as finder:
                CenterManager(root).compute_layer_centers_from_minimum(
                    member_name="member",
                    domain=3,
                    variable="pressure",
                    time_hour=0,
                    heights_m=[2000, 8000],
                )

            self.assertEqual(finder.call_count, 2)
            for call in finder.call_args_list:
                self.assertNotIn("initial_yx", call.kwargs)
            self.assertEqual(load_center_yx(paths.layer_center_path("member", 3, 2000), 0), (5.0, 6.0))
            self.assertEqual(load_center_yx(paths.layer_center_path("member", 3, 8000), 0), (7.0, 9.0))


# 功能说明：逐点累加给定中心周围圆内且未越界的数值，再除以格点数，得到参考算法使用的局地平均。
def _reference_hgtcount(data, c1, c2, dist):
    total = 0.0
    count = 0
    for i in range(-dist, dist + 1):
        for j in range(-dist, dist + 1):
            if i * i + j * j <= dist * dist:
                y, x = c1 + i, c2 + j
                if 0 <= y < data.shape[0] and 0 <= x < data.shape[1]:
                    total += data[y, x]
                    count += 1
    return total / count


# 功能说明：用逐点循环复现上传算法的第一轮中心估计。
def _reference_first_guess(data, dist):
    i0, j0 = np.unravel_index(np.argmin(data), data.shape)
    best_avg, best = np.inf, (int(i0), int(j0))
    for i in range(-dist, dist + 1):
        for j in range(-dist, dist + 1):
            if i * i + j * j <= dist * dist:
                y, x = i0 + i, j0 + j
                if 0 <= y < data.shape[0] and 0 <= x < data.shape[1]:
                    average = _reference_hgtcount(data, y, x, dist)
                    if average < best_avg:
                        best_avg, best = average, (int(y), int(x))
    return best


# 功能说明：用逐方向、逐半径的循环计算贝塞尔插值数值，作为核对快速极坐标转换实现的参考答案。
def _reference_bezier(data, c1, c2, radius):
    stock = np.zeros((360, radius + 1))
    stock[:, 0] = data[c1, c2]
    for i in range(1, radius + 1):
        for j in range(360):
            xproj = i * np.cos(np.pi / 180.0 * j)
            yproj = i * np.sin(np.pi / 180.0 * j)
            tx, ty = xproj - np.floor(xproj), yproj - np.floor(yproj)
            x0, x1 = c2 + int(np.floor(xproj)), c2 + int(np.ceil(xproj))
            x2, xm1 = c2 + int(np.ceil(xproj) + 1), c2 + int(np.floor(xproj) - 1)
            y0, y1 = c1 + int(np.floor(yproj)), c1 + int(np.ceil(yproj))
            y2, ym1 = c1 + int(np.ceil(yproj) + 1), c1 + int(np.floor(yproj) - 1)

            # 功能说明：为参考计算提供一维插值后的数值。
            def interp(y):
                return (
                    data[y, x0] + tx * (data[y, x1] - data[y, x0])
                    + 0.25 * tx * (tx - 1.0)
                    * (data[y, x2] - data[y, x1] - data[y, x0] + data[y, xm1])
                )

            b, c, d, a = interp(y0), interp(y1), interp(y2), interp(ym1)
            stock[j, i] = b + ty * (c - b) + 0.25 * ty * (ty - 1.0) * (a + d - b - c)
    return stock


# 功能说明：拼接相对方向的径向剖面并按数值加权求重心，平均得到中心应向东西、南北移动多少格的参考偏移。
def _reference_centroid(value):
    radius = value.shape[1] - 1
    x_coord, y_coord = np.zeros(180), np.zeros(180)
    for i in range(180):
        profile = np.concatenate((value[i + 180, ::-1], value[i, 1:]))
        total = np.sum(profile)
        offset = 0.0 if abs(total) < 1e-12 else np.sum(np.arange(1, len(profile) + 1) * profile) / total - (radius + 1)
        x_coord[i] = offset * np.cos(i * np.pi / 180.0)
        y_coord[i] = offset * np.sin(i * np.pi / 180.0)
    return np.sum(x_coord) / 180.0, np.sum(y_coord) / 180.0


if __name__ == "__main__":
    unittest.main()
