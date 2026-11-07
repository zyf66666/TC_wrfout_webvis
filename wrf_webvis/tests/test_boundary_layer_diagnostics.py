import unittest

import numpy as np

from wrf_webvis.app.core.registry import VARIABLE_REGISTRY
from wrf_webvis.app.core.schemas import ProcessingRequest, SelectionSpec, VariableDimension
from wrf_webvis.app.core.paths import PathManager
from wrf_processing.processing_manager import ProcessingManager


class BoundaryLayerDiagnosticsTests(unittest.TestCase):
    # 功能说明：查看变量登记表，确认边界层诊断量分别保留其二维、三维或一维的原生维度。
    def test_boundary_layer_variables_are_registered_with_their_native_dimensions(self):
        self.assertEqual(VARIABLE_REGISTRY["UST"].dimension.value, "2d")
        self.assertEqual(VARIABLE_REGISTRY["PBLH"].dimension.value, "2d")
        for variable in (
            "convergence",
            "mass_convergence",
            "mfc",
            "qflux_r",
            "inflow_angle",
        ):
            item = VARIABLE_REGISTRY[variable]
            self.assertEqual(item.dimension.value, "3d")
            self.assertTrue(item.derived)
            self.assertTrue(item.needs_polar)
        for variable in ("bl_mass_convergence", "bl_mfc"):
            item = VARIABLE_REGISTRY[variable]
            self.assertEqual(item.dimension.value, "2d")
            self.assertTrue(item.derived)
            self.assertTrue(item.needs_polar)

    # 功能说明：给出向中心汇聚的水平风场，确认散度收敛量的正负号和单位符合定义。
    def test_horizontal_convergence_has_the_expected_sign_and_units(self):
        from wrf_processing import boundary_layer_diagnostics

        y, x = np.indices((5, 5), dtype=float)
        # u=-a*x and v=-a*y gives -div(V)=2a.  dx=dy=1000 m.
        coefficient = 2.0e-3
        u = -coefficient * x * 1000.0
        v = -coefficient * y * 1000.0

        convergence = boundary_layer_diagnostics.horizontal_convergence(u, v, dx_m=1000.0, dy_m=1000.0)

        np.testing.assert_allclose(convergence[1:-1, 1:-1], 2.0 * coefficient)

    # 功能说明：用密度、风和水汽的简单格点计算质量与水汽通量收敛，确认加权结果正确。
    def test_density_weighted_mass_and_moisture_flux_convergence(self):
        from wrf_processing import boundary_layer_diagnostics

        self.assertTrue(hasattr(boundary_layer_diagnostics, "moist_air_density"))
        self.assertTrue(hasattr(boundary_layer_diagnostics, "mass_flux_convergence"))
        self.assertTrue(hasattr(boundary_layer_diagnostics, "moisture_flux_convergence"))

        y, x = np.indices((5, 5), dtype=float)
        coefficient = 1.0e-3
        u = -coefficient * x * 1000.0
        v = -coefficient * y * 1000.0
        qv = np.full((5, 5), 0.01)
        density = boundary_layer_diagnostics.moist_air_density(
            np.full((5, 5), 1000.0),
            np.full((5, 5), 300.0),
            qv,
        )

        mass_convergence = boundary_layer_diagnostics.mass_flux_convergence(
            density, u, v, dx_m=1000.0, dy_m=1000.0
        )
        mfc = boundary_layer_diagnostics.moisture_flux_convergence(
            density, qv, u, v, dx_m=1000.0, dy_m=1000.0
        )

        expected_density = 100000.0 / (287.05 * 300.0 * (1.0 + 0.61 * 0.01))
        np.testing.assert_allclose(density, expected_density)
        np.testing.assert_allclose(mass_convergence[1:-1, 1:-1], 2.0 * coefficient * expected_density)
        np.testing.assert_allclose(mfc[1:-1, 1:-1], 2.0 * coefficient * expected_density * 0.01)

    # 功能说明：预先放入风暴相对风缓存，确认管理器直接据此生成收敛量而不读取原始数据。
    def test_processing_manager_derives_convergence_from_cached_storm_relative_wind(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProcessingManager(tmpdir)
            paths = PathManager(tmpdir)
            y, x = np.indices((5, 5), dtype=float)
            for variable, values in (
                ("ua", -1.0e-3 * x * 1000.0),
                ("va", -1.0e-3 * y * 1000.0),
            ):
                path = paths.variable_path(
                    member_name="member",
                    domain=3,
                    variable=variable,
                    time_hour=0,
                    dimension=VariableDimension.THREE_D,
                    height_m=500,
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                np.save(path, values.astype(np.float32))
                paths.write_metadata(path, {"variable": variable, "time": 0, "motion_corrected": True})

            result = manager.run(
                ProcessingRequest(
                    processed_root=tmpdir,
                    member_name="member",
                    domain=3,
                    variables=["convergence"],
                    times=SelectionSpec(values=[0]),
                    heights=SelectionSpec(values=[500]),
                )
            )

            output = Path(result["processed_files"][0])
            np.testing.assert_allclose(np.load(output)[1:-1, 1:-1], 2.0e-3)

    # 功能说明：准备已缓存的风、密度和水汽场，确认管理器能派生水汽通量收敛和边界层积分量。
    def test_processing_manager_derives_mfc_and_boundary_layer_integral_from_cached_fields(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProcessingManager(tmpdir)
            paths = PathManager(tmpdir)
            y, x = np.indices((5, 5), dtype=float)
            source_values = {
                "ua": -1.0e-3 * x * 1000.0,
                "va": -1.0e-3 * y * 1000.0,
                "pressure": np.full((5, 5), 1000.0),
                "temp": np.full((5, 5), 300.0),
                "QVAPOR": np.full((5, 5), 0.01),
            }
            for height_m in (100, 200):
                for variable, values in source_values.items():
                    path = paths.variable_path(
                        member_name="member",
                        domain=3,
                        variable=variable,
                        time_hour=0,
                        dimension=VariableDimension.THREE_D,
                        height_m=height_m,
                    )
                    path.parent.mkdir(parents=True, exist_ok=True)
                    np.save(path, values.astype(np.float32))
                    paths.write_metadata(path, {"variable": variable, "time": 0, "motion_corrected": variable in {"ua", "va"}})

            manager.run(
                ProcessingRequest(
                    processed_root=tmpdir,
                    member_name="member",
                    domain=3,
                    variables=["mfc"],
                    times=SelectionSpec(values=[0]),
                    heights=SelectionSpec(values=[100, 200]),
                )
            )
            result = manager.run(
                ProcessingRequest(
                    processed_root=tmpdir,
                    member_name="member",
                    domain=3,
                    variables=["bl_mfc"],
                    times=SelectionSpec(values=[0]),
                    options={"boundary_layer_heights_m": [100, 200]},
                )
            )

            output = Path(result["processed_files"][0])
            expected_density = 100000.0 / (287.05 * 300.0 * (1.0 + 0.61 * 0.01))
            expected_mfc = 2.0e-3 * expected_density * 0.01
            np.testing.assert_allclose(np.load(output)[1:-1, 1:-1], expected_mfc * 100.0)

    # 功能说明：准备径向风和水汽相关缓存，确认管理器计算径向水汽通量及入流角。
    def test_processing_manager_derives_radial_moisture_flux_and_inflow_angle(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProcessingManager(tmpdir)
            paths = PathManager(tmpdir)
            y, x = np.indices((5, 5), dtype=float)
            source_values = {
                "ua": -1.0e-3 * x * 1000.0,
                "va": -1.0e-3 * y * 1000.0,
                "pressure": np.full((5, 5), 1000.0),
                "temp": np.full((5, 5), 300.0),
                "QVAPOR": np.full((5, 5), 0.01),
            }
            for variable, values in source_values.items():
                path = paths.variable_path(
                    member_name="member",
                    domain=3,
                    variable=variable,
                    time_hour=0,
                    dimension=VariableDimension.THREE_D,
                    height_m=500,
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                np.save(path, values.astype(np.float32))
                paths.write_metadata(path, {"variable": variable, "time": 0, "motion_corrected": variable in {"ua", "va"}})
            center_path = paths.center_path("member", 3, "slp")
            center_path.parent.mkdir(parents=True, exist_ok=True)
            center_path.write_text("0 2 2 0 0 0\n", encoding="utf-8")

            result = manager.run(
                ProcessingRequest(
                    processed_root=tmpdir,
                    member_name="member",
                    domain=3,
                    variables=["qflux_r", "inflow_angle"],
                    times=SelectionSpec(values=[0]),
                    heights=SelectionSpec(values=[500]),
                )
            )

            outputs = {Path(path).parts[-3]: np.load(path) for path in result["processed_files"]}
            expected_density = 100000.0 / (287.05 * 300.0 * (1.0 + 0.61 * 0.01))
            np.testing.assert_allclose(outputs["qflux_r"][2, 3], expected_density * 0.01 * -3.0)
            self.assertAlmostEqual(outputs["inflow_angle"][2, 3], np.degrees(np.arctan2(3.0, -2.0)), places=5)


if __name__ == "__main__":
    unittest.main()
