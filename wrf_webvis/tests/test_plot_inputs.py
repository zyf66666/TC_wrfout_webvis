import tempfile
import unittest
from pathlib import Path

from wrf_webvis.app.core.schemas import PlotRequest, PlotType, SelectionSpec
from wrf_webvis.app.services.plot_inputs import check_additional_plot_inputs


# 2026-11-01：预检按 processed_v3 切片列出所需输入（变量、成员、时次），不再列 .npy 文件名。
def _requested(result, variables):
    return {
        (item.variable, Path(item.path).parts[-3], item.time_hour)
        for item in result.items
        if item.variable in variables
    }


class AdditionalPlotInputTests(unittest.TestCase):
    # 功能说明：为不同成员设置不同结束小时，确认时间半径图只读取各自可用的登陆距离。
    def test_time_radius_land_distance_respects_each_member_end_hour(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "processed_v3"
            request = PlotRequest(
                data_root=root,
                member_name="long",
                member_names=["long", "short"],
                selection={"members": ["long", "short"], "member_time_limits": {"short": 1}},
                domain=3,
                fig_type=PlotType.TIME_RADIUS,
                time=SelectionSpec(values=[0, 1, 2]),
                format={"time_radius": {"draw_land_distance": True}},
            )

            result = check_additional_plot_inputs(request)

            self.assertEqual(
                _requested(result, {"center_land_distance"}),
                {("center_land_distance", member, float(hour))
                 for member, hour in (("long", 0), ("long", 1), ("long", 2), ("short", 0), ("short", 1))},
            )
            self.assertTrue(all(Path(item.path).name == "center_land_distance.nc" for item in result.items))
            self.assertEqual(len(result.missing_items), 5)
            self.assertEqual(len(result.missing_files), 2)

    # 功能说明：为不同成员限制结束小时，确认 RMW 和 R34 曲线不会读到限制之后。
    def test_time_radius_rmw_and_r34_respect_each_member_end_hour(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "processed_v3"
            request = PlotRequest(
                data_root=root,
                member_name="long",
                member_names=["long", "short"],
                selection={"members": ["long", "short"], "member_time_limits": {"short": 1}},
                domain=3,
                fig_type=PlotType.TIME_RADIUS,
                time=SelectionSpec(values=[0, 1, 2]),
                format={"time_radius": {"draw_rmw": True, "draw_r34": True}},
            )

            result = check_additional_plot_inputs(request)

            self.assertEqual(
                _requested(result, {"rmw", "r_34"}),
                {
                    (variable, member, float(time_hour))
                    for variable in ("rmw", "r_34")
                    for member, time_hour in (("long", 0), ("long", 1), ("long", 2), ("short", 0), ("short", 1))
                },
            )


if __name__ == "__main__":
    unittest.main()


# 功能说明：3D 中心连线预检接受 v3 各高度中心；缺层时仍报告缺失。
def test_three_d_center_inputs_use_completed_v3_slices(tmp_path):
    import numpy as np
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.register import build_registry

    root = tmp_path / "processed_v3"
    store = NetCDFStorage(root, time_origin="2023-09-01_00:00:00")
    spec = build_registry().resolve("layer_center")
    for h in [1000, 3000, 5000, 7000, 9000]:
        store.save(Task("m1", 3, 1800, "layer_center", h), np.array([3, 4]), spec)
    req = PlotRequest(processed_root=root, data_root=root, member_name="m1", domain=3,
                      fig_type=7, time={"values": [30]}, height={"values": [1000, 3000, 5000, 7000, 9000]},
                      format={"three_d": {"draw_layer_centers": True, "center_heights_km": [1, 3, 5, 7, 9]}})
    result = check_additional_plot_inputs(req)
    assert result.missing_files == []
    assert result.required_files == [root / "m1/d03/layer_center.nc"]
    req.format.three_d["center_heights_km"] = [2]
    assert check_additional_plot_inputs(req).missing_files
