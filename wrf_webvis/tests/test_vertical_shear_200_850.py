import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.schemas import ProcessingRequest, SelectionSpec
from wrf_processing.data_manager import DataManager
from wrf_processing.diagnostics import vertical_shear_200_850
from wrf_processing.processing_manager import ProcessingManager


class VerticalShear200850Tests(unittest.TestCase):
    # 功能说明：用模拟 200/850 hPa 风场，确认风切变分量、强度和方向遵循笔记本中的方向定义。
    def test_calculate_shear_values_uses_notebook_direction_definition(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            center_path = Path(tmpdir) / "center.txt"
            np.savetxt(center_path, np.asarray([[0, 4, 4]], dtype=float))
            u_levels = np.stack([np.full((9, 9), 20.0), np.full((9, 9), 5.0)])
            v_levels = np.stack([np.full((9, 9), 4.0), np.full((9, 9), 1.0)])

            with patch.object(vertical_shear_200_850, "read_pressure_level_winds", return_value=(u_levels, v_levels)):
                values = vertical_shear_200_850.calculate_shear_values(
                    source_file=Path("wrfout"),
                    source_time_index=0,
                    center_path=center_path,
                    time_hour=0,
                    domain_config={"dx_km": 1.0, "dy_km": 1.0},
                    request_options={
                        "shear_radius_in": 0,
                        "shear_radius_out": 3,
                        "shear_radius_max": 3,
                    },
                )

            self.assertAlmostEqual(values["shear_200_850_u"], 15.0)
            self.assertAlmostEqual(values["shear_200_850_v"], 3.0)
            self.assertAlmostEqual(values["shear_200_850_magnitude"], np.hypot(15.0, 3.0))
            self.assertAlmostEqual(values["shear_200_850_direction"], -np.degrees(np.arctan2(3.0, 15.0)))

    # 功能说明：请求一个风切变分量并预置旧输出，确认处理会重写四个分量且数值更新。
    def test_processing_one_shear_variable_writes_all_components(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = PathManager(tmpdir)
            center_path = paths.center_path("member", 3, "slp")
            center_path.parent.mkdir(parents=True, exist_ok=True)
            np.savetxt(center_path, np.asarray([[0, 4, 4]], dtype=float))
            u_levels = np.stack([np.full((9, 9), 20.0), np.full((9, 9), 5.0)])
            v_levels = np.stack([np.full((9, 9), 4.0), np.full((9, 9), 1.0)])
            request = ProcessingRequest(
                processed_root=Path(tmpdir),
                member_name="member",
                domain=3,
                variables=["shear_200_850_magnitude"],
                times=SelectionSpec(values=[0]),
                dtype="float32",
                options={"shear_radius_in": 0, "shear_radius_out": 3, "shear_radius_max": 3},
            )
            stale_path = paths.variable_path(
                member_name="member",
                domain=3,
                variable="shear_200_850_magnitude",
                time_hour=0,
                dimension="1d",
            )
            stale_path.parent.mkdir(parents=True, exist_ok=True)
            np.save(stale_path, np.asarray([-999.0], dtype=np.float32))

            with patch.object(vertical_shear_200_850, "read_pressure_level_winds", return_value=(u_levels, v_levels)):
                vertical_shear_200_850.process(
                    request=request,
                    path_manager=paths,
                    variable="shear_200_850_magnitude",
                    source_file=Path("wrfout"),
                    source_time_index=0,
                    time_hour=0,
                    dtype="float32",
                    domain_config={"dx_km": 1.0, "dy_km": 1.0},
                    ensure_slp_center=lambda *args, **kwargs: center_path,
                )

            for variable in vertical_shear_200_850.VARIABLES:
                out_path = paths.variable_path(
                    member_name="member",
                    domain=3,
                    variable=variable,
                    time_hour=0,
                    dimension="1d",
                )
                self.assertTrue(out_path.exists(), variable)
            self.assertAlmostEqual(float(np.load(stale_path)[0]), np.hypot(15.0, 3.0), places=5)

    # 功能说明：规划一个风切变变量，确认它的输出是按时次保存的一维 NPY 文件。
    def test_processing_plan_treats_shear_as_one_dimensional(self):
        request = ProcessingRequest(
            processed_root="processed",
            wrfout_root="0_land/run",
            member_name="0_land",
            domain=3,
            variables=["shear_200_850_magnitude"],
            times=SelectionSpec(values=[0]),
        )
        plan = DataManager("processed").plan_for_processing(request)

        self.assertEqual(plan.required_files[0].dimension, "1d")
        self.assertIn("shear_200_850_magnitude", str(plan.required_files[0].path))
        self.assertTrue(str(plan.required_files[0].path).endswith("0h.npy"))

    # 功能说明：放置无元数据的旧风切变文件，确认处理管理器仍调用一维诊断重算。
    def test_processing_manager_does_not_skip_stale_shear_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProcessingManager(tmpdir)
            stale_path = manager.path_manager.variable_path(
                member_name="member",
                domain=3,
                variable="shear_200_850_u",
                time_hour=0,
                dimension="1d",
            )
            stale_path.parent.mkdir(parents=True, exist_ok=True)
            np.save(stale_path, np.asarray([-999.0], dtype=np.float32))
            request = ProcessingRequest(
                processed_root=tmpdir,
                wrfout_root="run",
                member_name="member",
                domain=3,
                variables=["shear_200_850_u"],
                times=SelectionSpec(values=[0]),
            )

            with (
                patch.object(manager, "_find_wrfout_files", return_value=[Path("wrfout")]),
                patch.object(manager, "_source_for_time", return_value=(Path("wrfout"), 0)),
                patch.object(manager, "_process_1d_diagnostic", return_value="reprocessed") as process_mock,
            ):
                result = manager._run_standard_variables(request, ["shear_200_850_u"])

            self.assertEqual(result, ["reprocessed"])
            process_mock.assert_called_once()

    # 功能说明：对线性二维场做 360 方位 Bezier 插值，确认结果等于该线性公式的解析值。
    def test_bezier_interp_matches_linear_field_in_polar_coordinates(self):
        yy, xx = np.indices((9, 9), dtype=float)
        field = 2.0 * xx + 3.0 * yy + 7.0

        polar = vertical_shear_200_850.bezier_interp(
            field,
            center_y=4,
            center_x=4,
            radius_max=3,
        )

        angles = np.deg2rad(np.arange(360, dtype=float))[:, np.newaxis]
        radii = np.arange(0, 4, dtype=float)[np.newaxis, :]
        expected = 2.0 * (4.0 + radii * np.cos(angles)) + 3.0 * (
            4.0 + radii * np.sin(angles)
        ) + 7.0
        np.testing.assert_allclose(polar, expected, atol=1.0e-10)

    # 功能说明：用固定随机场比较向量化插值与逐半径、逐方位的参考循环公式。
    def test_vectorized_bezier_interp_matches_supplied_loop_formula(self):
        rng = np.random.RandomState(42)
        field = rng.normal(size=(11, 11))
        actual = vertical_shear_200_850.bezier_interp(
            field,
            center_y=5,
            center_x=5,
            radius_max=3,
        )
        expected = np.zeros((360, 4), dtype=float)
        expected[:, 0] = field[5, 5]
        for radius in range(1, 4):
            for angle in range(360):
                xproj = radius * np.cos(np.deg2rad(angle))
                yproj = radius * np.sin(np.deg2rad(angle))
                tx = xproj - np.floor(xproj)
                ty = yproj - np.floor(yproj)
                x0 = 5 + int(np.floor(xproj))
                x1 = 5 + int(np.ceil(xproj))
                x2 = 5 + int(np.ceil(xproj) + 1)
                xm = 5 + int(np.floor(xproj) - 1)
                y0 = 5 + int(np.floor(yproj))
                y1 = 5 + int(np.ceil(yproj))
                y2 = 5 + int(np.ceil(yproj) + 1)
                ym = 5 + int(np.floor(yproj) - 1)

                # 功能说明：在参考循环中按四个相邻 x 格点计算一条 y 行的 Bezier 插值值。
                def interp_x(y_index):
                    return (
                        field[y_index, x0]
                        + tx * (field[y_index, x1] - field[y_index, x0])
                        + 0.25
                        * tx
                        * (tx - 1.0)
                        * (
                            field[y_index, x2]
                            - field[y_index, x1]
                            - field[y_index, x0]
                            + field[y_index, xm]
                        )
                    )

                b_value = interp_x(y0)
                c_value = interp_x(y1)
                d_value = interp_x(y2)
                a_value = interp_x(ym)
                expected[angle, radius] = (
                    b_value
                    + ty * (c_value - b_value)
                    + 0.25
                    * ty
                    * (ty - 1.0)
                    * (a_value + d_value - b_value - c_value)
                )

        np.testing.assert_allclose(actual, expected, atol=1.0e-12)


if __name__ == "__main__":
    unittest.main()
