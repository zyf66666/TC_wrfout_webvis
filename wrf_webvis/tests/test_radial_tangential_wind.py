import unittest
from pathlib import Path
import tempfile

import numpy as np

from wrf_webvis.app.core.schemas import ProcessingRequest, SelectionSpec, VariableDimension
from wrf_processing.processing_manager import ProcessingManager
from wrf_processing.radial_tangential_wind import (
    process_radial_tangential_variable,
    radial_tangential_wind,
)


class RadialTangentialWindTests(unittest.TestCase):
    # 功能说明：用 3×3 的纯东风场验证中心四周的径向风正负方向和切向风方向。
    def test_radial_and_tangential_projection(self):
        ua = np.ones((3, 3), dtype=float)
        va = np.zeros((3, 3), dtype=float)

        vr = radial_tangential_wind(ua, va, center_y=1, center_x=1, dx_km=1, dy_km=1, component="vr")
        vt = radial_tangential_wind(ua, va, center_y=1, center_x=1, dx_km=1, dy_km=1, component="vt")

        self.assertAlmostEqual(vr[1, 2], 1.0)
        self.assertAlmostEqual(vr[1, 0], -1.0)
        self.assertAlmostEqual(vr[0, 1], 0.0)
        self.assertAlmostEqual(vt[0, 1], 1.0)
        self.assertAlmostEqual(vt[2, 1], -1.0)

    # 功能说明：请求 vr 极坐标数据和 1000 米高度，确认处理计划把它列为按高度保存的三维文件。
    def test_vr_polar_is_planned_as_three_dimensional_polar_output(self):
        request = ProcessingRequest(
            processed_root="processed",
            wrfout_root="0_land/run",
            member_name="0_land",
            domain=3,
            variables=["vr_polar"],
            times=SelectionSpec(values=[0]),
            heights=SelectionSpec(values=[1000]),
        )
        plan = ProcessingManager("processed").check(request)

        self.assertEqual(plan.required_files[0].variable, "vr_polar")
        self.assertEqual(plan.required_files[0].dimension, VariableDimension.THREE_D.value)
        self.assertIn("vr_polar", str(plan.required_files[0].path))
        self.assertIn("1000m.npy", str(plan.required_files[0].path))

    # 功能说明：为两个高度写入不同中心和相同东风，确认 vr1 分别使用各高度中心计算。
    def test_layer_center_mode_uses_each_height_center_for_vr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            member = "WRF_0_sea"
            base = root / member / "d03"
            for height in (1000, 2000):
                (base / "ua" / "0h").mkdir(parents=True, exist_ok=True)
                (base / "va" / "0h").mkdir(parents=True, exist_ok=True)
                np.save(base / "ua" / "0h" / f"{height}m.npy", np.ones((3, 3)))
                np.save(base / "va" / "0h" / f"{height}m.npy", np.zeros((3, 3)))

            center_root = root / member / "center" / "d03"
            (center_root / "slp").mkdir(parents=True)
            (center_root / "1000m").mkdir(parents=True)
            (center_root / "2000m").mkdir(parents=True)
            (center_root / "slp" / "center.txt").write_text("0 1 1\n")
            (center_root / "1000m" / "center.txt").write_text("0 1 1\n")
            (center_root / "2000m" / "center.txt").write_text("0 1 0\n")

            request = ProcessingRequest(
                processed_root=str(root),
                member_name=member,
                domain=3,
                variables=["vr1"],
                times=SelectionSpec(values=[0]),
                heights=SelectionSpec(values=[1000, 2000]),
            )
            outputs = process_radial_tangential_variable(
                request=request,
                variable="vr",
                time_hour=0,
                heights=[1000, 2000],
                dtype="float32",
                center_mode="layer",
            )

            values_1000 = np.load(outputs[0])
            values_2000 = np.load(outputs[1])
            self.assertAlmostEqual(values_1000[1, 1], 0.0)
            self.assertAlmostEqual(values_2000[1, 1], 1.0)


if __name__ == "__main__":
    unittest.main()
