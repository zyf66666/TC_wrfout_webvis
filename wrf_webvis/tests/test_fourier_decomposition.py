import tempfile
import unittest
from pathlib import Path

import numpy as np

from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.schemas import ProcessingRequest, SelectionSpec, VariableDimension
from wrf_processing.fourier_decomposition import (
    asymmetric_component,
    extract_wave,
    process_fourier_decomposition,
)
from wrf_processing.processing_manager import ProcessingManager


class FourierDecompositionTests(unittest.TestCase):
    # 功能说明：构造沿方位角变化的极坐标场，确认非对称量和波数分量沿正确轴提取。
    def test_asymmetry_and_wave_extraction_follow_azimuth_axis(self):
        theta = np.deg2rad(np.arange(360.0))
        mean = 10.0
        wave1 = 2.0 * np.cos(theta)
        wave2 = 0.5 * np.sin(2.0 * theta)
        data = (mean + wave1 + wave2)[:, np.newaxis]

        asymmetric = asymmetric_component(data)
        extracted_1 = extract_wave(asymmetric, 1)
        extracted_2 = extract_wave(asymmetric, 2)

        np.testing.assert_allclose(asymmetric[:, 0], wave1 + wave2, atol=1e-10)
        np.testing.assert_allclose(extracted_1[:, 0], wave1, atol=1e-10)
        np.testing.assert_allclose(extracted_2[:, 0], wave2, atol=1e-10)

    # 功能说明：查看傅里叶变量的处理计划，确认每个高度单独生成输出。
    def test_fourier_variable_is_planned_as_per_height_output(self):
        request = ProcessingRequest(
            processed_root="processed",
            wrfout_root="0_land/run",
            member_name="0_land",
            domain=3,
            variables=["ua1_asy1w_polar"],
            times=SelectionSpec(values=[0]),
            heights=SelectionSpec(values=[1000]),
        )
        plan = ProcessingManager("processed").check(request)

        self.assertEqual(plan.required_files[0].variable, "ua1_asy1w_polar")
        self.assertEqual(plan.required_files[0].dimension, VariableDimension.THREE_D.value)
        self.assertIn("ua1_asy1w_polar", str(plan.required_files[0].path))
        self.assertIn("1000m.npy", str(plan.required_files[0].path))

    # 功能说明：处理一份极坐标样本，确认非对称场和指定波数场均写入缓存。
    def test_process_fourier_decomposition_saves_asymmetry_and_wave(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = PathManager(tmpdir)
            theta = np.deg2rad(np.arange(360.0))
            source_data = (5.0 + 3.0 * np.cos(theta))[:, np.newaxis].astype(np.float32)
            source_path = paths.variable_path(
                member_name="member",
                domain=3,
                variable="ua1_polar",
                time_hour=0,
                dimension=VariableDimension.THREE_D,
                height_m=1000,
            )
            source_path.parent.mkdir(parents=True, exist_ok=True)
            np.save(source_path, source_data)

            request = ProcessingRequest(
                processed_root=Path(tmpdir),
                member_name="member",
                domain=3,
                variables=["ua1_asy_polar"],
                times=SelectionSpec(values=[0]),
                heights=SelectionSpec(values=[1000]),
                dtype="float32",
            )
            asym_path = Path(
                process_fourier_decomposition(
                    request=request,
                    variable="ua1_asy_polar",
                    time_hour=0,
                    heights=[1000],
                    dtype="float32",
                )[0]
            )
            wave_path = Path(
                process_fourier_decomposition(
                    request=request,
                    variable="ua1_asy1w_polar",
                    time_hour=0,
                    heights=[1000],
                    dtype="float32",
                )[0]
            )

            expected = (3.0 * np.cos(theta))[:, np.newaxis]
            np.testing.assert_allclose(np.load(asym_path), expected, atol=1e-6)
            np.testing.assert_allclose(np.load(wave_path), expected, atol=1e-6)


if __name__ == "__main__":
    unittest.main()
