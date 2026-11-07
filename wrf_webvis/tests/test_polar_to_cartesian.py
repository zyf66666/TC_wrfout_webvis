import tempfile
import unittest
from pathlib import Path

import numpy as np

from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.schemas import ProcessingRequest, SelectionSpec, VariableDimension
from wrf_processing.polar_to_cartesian import polar_to_cartesian_grid, process_polar_to_cartesian
from wrf_processing.processing_manager import ProcessingManager


class PolarToCartesianTests(unittest.TestCase):
    # 功能说明：以不同半径和网格尺寸转换极坐标场，确认笛卡尔网格随输入动态调整。
    def test_polar_to_cartesian_grid_uses_dynamic_shape_and_radius(self):
        polar = np.ones((360, 3), dtype=float) * 7.0

        cartesian = polar_to_cartesian_grid(
            polar,
            center_y=2,
            center_x=2,
            output_shape=(5, 5),
            dx_km=1.0,
            dy_km=1.0,
            radius_resolution_km=1.0,
        )

        self.assertEqual(cartesian.shape, (5, 5))
        self.assertAlmostEqual(cartesian[2, 2], 7.0)
        self.assertAlmostEqual(cartesian[2, 4], 7.0)
        self.assertAlmostEqual(cartesian[0, 0], 0.0)

    # 功能说明：查看转换变量计划，确认每个高度单独输出笛卡尔场。
    def test_cartesian_from_polar_variable_is_planned_as_per_height_output(self):
        request = ProcessingRequest(
            processed_root="processed",
            wrfout_root="0_land/run",
            member_name="0_land",
            domain=3,
            variables=["ua1_asy"],
            times=SelectionSpec(values=[0]),
            heights=SelectionSpec(values=[1000]),
        )
        plan = ProcessingManager("processed").check(request)

        self.assertEqual(plan.required_files[0].variable, "ua1_asy")
        self.assertEqual(plan.required_files[0].dimension, VariableDimension.THREE_D.value)
        self.assertIn("ua1_asy", str(plan.required_files[0].path))
        self.assertIn("1000m.npy", str(plan.required_files[0].path))

    # 功能说明：处理一份极坐标数据，确认保存覆盖整个区域的笛卡尔网格。
    def test_process_polar_to_cartesian_saves_full_domain_grid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = PathManager(tmpdir)
            source_path = paths.variable_path(
                member_name="member",
                domain=3,
                variable="ua1_asy_polar",
                time_hour=0,
                dimension=VariableDimension.THREE_D,
                height_m=1000,
            )
            source_path.parent.mkdir(parents=True, exist_ok=True)
            np.save(source_path, np.ones((360, 3), dtype=np.float32) * 5.0)
            paths.write_metadata(
                source_path,
                {
                    "source_shape": [5, 5],
                    "center_y": 2,
                    "center_x": 2,
                    "dx_km": 1.0,
                    "dy_km": 1.0,
                    "radius_resolution_km": 1.0,
                },
            )

            request = ProcessingRequest(
                processed_root=Path(tmpdir),
                member_name="member",
                domain=3,
                variables=["ua1_asy"],
                times=SelectionSpec(values=[0]),
                heights=SelectionSpec(values=[1000]),
                dtype="float32",
            )
            out_path = Path(
                process_polar_to_cartesian(
                    request=request,
                    variable="ua1_asy",
                    time_hour=0,
                    heights=[1000],
                    dtype="float32",
                    domain_config={"nx": 5, "ny": 5, "dx_km": 1.0, "dy_km": 1.0},
                )[0]
            )

            saved = np.load(out_path)
            self.assertEqual(saved.shape, (5, 5))
            self.assertAlmostEqual(float(saved[2, 2]), 5.0)


if __name__ == "__main__":
    unittest.main()
