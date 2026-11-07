import tempfile
import unittest
from pathlib import Path

import numpy as np

from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.schemas import ProcessingRequest, SelectionSpec, VariableDimension
from wrf_processing.cloud_classification import (
    CLOUD_CLASSIFICATION_HEIGHTS_M,
    classify_cloud_types,
    process_cloud_classification,
)
from wrf_processing.processing_manager import ProcessingManager


class CloudClassificationTests(unittest.TestCase):
    # 功能说明：输入覆盖晴空、低云和高云阈值的样本，确认云型编号与研究笔记本的分类一致。
    def test_classify_cloud_types_matches_notebook_thresholds(self):
        heights = CLOUD_CLASSIFICATION_HEIGHTS_M
        dbz = np.zeros((len(heights), 1, 6), dtype=float)
        wa = np.zeros_like(dbz)

        idx_900 = heights.index(900)
        idx_2000 = heights.index(2000)
        idx_3000 = heights.index(3000)
        idx_4000 = heights.index(4000)
        idx_8000 = heights.index(8000)

        dbz[idx_3000, 0, 0] = 0.0
        dbz[idx_3000, 0, 1] = 10.0
        dbz[idx_3000, 0, 2] = 25.0

        dbz[idx_900, 0, 3] = 47.0
        dbz[idx_2000, 0, 3] = 10.0

        dbz[idx_900, 0, 4] = 47.0
        dbz[idx_4000, 0, 4] = 20.0

        dbz[idx_900, 0, 5] = 47.0
        dbz[idx_8000, 0, 5] = 21.0

        class_map = classify_cloud_types(dbz, wa, heights)

        np.testing.assert_array_equal(class_map[0], np.asarray([0, 1, 2, 3, 4, 5], dtype=np.int8))

    # 功能说明：请求云型分类并查看处理计划，确认它作为二维诊断量处理。
    def test_classification_is_planned_as_two_dimensional_operation(self):
        request = ProcessingRequest(
            processed_root="processed",
            wrfout_root="0_land/run",
            member_name="0_land",
            domain=3,
            variables=["classification"],
            times=SelectionSpec(values=[0]),
        )
        plan = ProcessingManager("processed").check(request)

        self.assertEqual(plan.required_files[0].variable, "classification")
        self.assertEqual(plan.required_files[0].dimension, VariableDimension.TWO_D.value)
        self.assertIn("classification", str(plan.required_files[0].path))
        self.assertIn("0h.npy", str(plan.required_files[0].path))

    # 功能说明：处理一份云相关输入，确认输出的云型栅格以整数类别保存。
    def test_process_cloud_classification_saves_integer_classes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = PathManager(tmpdir)
            for variable in ["dbz", "wa"]:
                for height_m in CLOUD_CLASSIFICATION_HEIGHTS_M:
                    out_path = paths.variable_path(
                        member_name="member",
                        domain=3,
                        variable=variable,
                        time_hour=0,
                        dimension=VariableDimension.THREE_D,
                        height_m=height_m,
                    )
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    np.save(out_path, np.zeros((2, 2), dtype=np.float32))

            request = ProcessingRequest(
                processed_root=Path(tmpdir),
                member_name="member",
                domain=3,
                variables=["classification"],
                times=SelectionSpec(values=[0]),
                dtype="float32",
            )
            out_path = Path(process_cloud_classification(request=request, time_hour=0, dtype=request.dtype))
            saved = np.load(out_path)

            self.assertEqual(saved.dtype, np.dtype("int8"))
            self.assertEqual(saved.shape, (2, 2))


if __name__ == "__main__":
    unittest.main()
