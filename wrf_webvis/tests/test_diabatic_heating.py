import tempfile
import unittest
from pathlib import Path

import numpy as np

from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.registry import DEFAULT_PROCESSING_HEIGHTS_M, VARIABLE_REGISTRY
from wrf_webvis.app.core.schemas import ProcessingRequest, VariableDimension
from wrf_processing.data_manager import ProcessingPlan
from wrf_processing.processing_manager import ProcessingManager


class DiabaticHeatingTests(unittest.TestCase):
    # 功能说明：检查变量定义，确认非绝热加热率按选定高度插值而不是按层平均。
    def test_diabatic_heating_is_a_height_interpolated_h_diabatic_field(self):
        item = VARIABLE_REGISTRY["diabatic_heating"]

        self.assertEqual(item.dimension, VariableDimension.THREE_D)
        self.assertEqual(item.wrf_name, "H_DIABATIC")
        self.assertTrue(item.needs_height_interp)
        self.assertTrue(item.needs_polar)

    # 功能说明：检查登记表，确认已废弃的层平均和眼墙诊断量没有继续暴露。
    def test_legacy_layer_mean_and_eyewall_diagnostics_are_not_registered(self):
        self.assertNotIn("diabatic_heating_axisymmetry", VARIABLE_REGISTRY)
        self.assertNotIn("diabatic_heating_eyewall_mean", VARIABLE_REGISTRY)

    # 功能说明：选择多个高度处理加热率，确认每层各写出一份缓存。
    def test_diabatic_heating_writes_one_cache_per_selected_height(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "wrfout_d03"
            source.touch()
            request = ProcessingRequest(
                processed_root=root,
                member_name="member",
                domain=3,
                variables=["diabatic_heating"],
            )
            manager = ProcessingManager(root)

            result = manager._process_3d_variable(
                request=request,
                variable="diabatic_heating",
                source_file=source,
                source_time_index=0,
                time_hour=0,
                heights=[500, 1000],
                dtype="float32",
                prepared_array=np.stack((np.full((2, 3), 1.0), np.full((2, 3), 2.0))),
            )

            self.assertEqual(len(result), 2)
            paths = PathManager(root)
            for height_m, expected in ((500, 1.0), (1000, 2.0)):
                output = paths.variable_path(
                    member_name="member",
                    domain=3,
                    variable="diabatic_heating",
                    time_hour=0,
                    dimension=VariableDimension.THREE_D,
                    height_m=height_m,
                )
                self.assertTrue(output.is_file())
                np.testing.assert_allclose(np.load(output), expected)

    # 功能说明：不给高度选择时查看计划，确认自动补入默认高度层。
    def test_blank_height_plans_default_levels_for_diabatic_heating(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = ProcessingRequest(
                processed_root=tmp,
                member_name="member",
                domain=3,
                variables=["diabatic_heating"],
                times={"values": [0]},
                heights={"values": []},
            )

            plan = ProcessingManager(tmp).check(request)

            self.assertEqual(len(plan.missing_files), len(DEFAULT_PROCESSING_HEIGHTS_M))
            self.assertEqual(plan.missing_files[0].height_m, 100)
            self.assertEqual(plan.missing_files[-1].height_m, 10000)

    # 功能说明：不给高度直接运行处理，确认实际生成默认高度层的结果。
    def test_blank_height_uses_default_levels_when_processing_diabatic_heating(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "wrfout_d03"
            source.touch()
            request = ProcessingRequest(
                processed_root=root,
                member_name="member",
                domain=3,
                variables=["diabatic_heating"],
                times={"values": [0]},
                heights={"values": []},
            )
            manager = ProcessingManager(root)
            manager.check = lambda _request: ProcessingPlan(  # type: ignore[method-assign]
                domain_config={}, required_files=[], missing_files=[object()], warnings=[]  # type: ignore[list-item]
            )
            manager._find_wrfout_files = lambda _request: [source]  # type: ignore[method-assign]
            manager._source_for_time = lambda _files, _time, _request: (source, 0)  # type: ignore[method-assign]
            batches = []
            manager._process_wrf_variable_batch = lambda **kwargs: batches.append(kwargs["variables"]) or []  # type: ignore[method-assign]

            manager._run_standard_variables(request, ["diabatic_heating"])

            self.assertEqual(len(batches), 1)
            self.assertEqual(batches[0][0][2], DEFAULT_PROCESSING_HEIGHTS_M)


if __name__ == "__main__":
    unittest.main()
