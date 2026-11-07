import unittest
from pathlib import Path
from unittest.mock import patch

from wrf_webvis.app.core.schemas import PlotRequest, PlotType, SelectionSpec, VariableConfig
from wrf_processing.center_manager import CenterManager
from wrf_webvis.app.services.plot_service import PlotService
from wrf_webvis.app.plotting.plot_spec import build_plot_spec
from wrf_processing.polar_transformer import PolarTransformer
from wrf_processing.processing_manager import ProcessingManager


class PlotHashTests(unittest.TestCase):
    # 功能说明：对相同绘图请求重复计算标识，确认哈希值稳定不变。
    def test_plot_hash_stable(self):
        request = PlotRequest(
            title="a",
            member_name="m1",
            variables=[VariableConfig(name="slp")],
            time=SelectionSpec(values=[0]),
        )
        service = PlotService()
        self.assertEqual(service.plot_id(request), service.plot_id(request))

    # 功能说明：把成员结束时次放进请求，确认生成绘图规格时仍保留限制。
    def test_plot_request_passes_member_time_limits_to_plot_spec(self):
        request = PlotRequest(
            member_name="land",
            member_names=["land", "sea"],
            fig_type=PlotType.TIME_SERIES,
            variables=[VariableConfig(name="rmw", plot_type="line")],
            time=SelectionSpec(values=[0, 1, 2]),
            selection={"member_time_limits": {"land": 1}},
        )

        spec = build_plot_spec(request, "test.png")

        self.assertEqual(spec.selection["member_time_limits"], {"land": 1})

    def test_plot_config_passes_15000m_height_through(self):
        """所选 15000 m 层直接进入绘图选区（2026-11-01 起不再需要“高度序号表”）。"""
        request = PlotRequest(
            member_name="WRF_315_land",
            variables=[VariableConfig(name="theta_e_anomaly")],
            time=SelectionSpec(values=[2]),
            height=SelectionSpec(values=[15000]),
        )

        spec = build_plot_spec(request, "test.png")

        self.assertEqual(spec.selection["height"], [15000])
        self.assertFalse(hasattr(spec, "height_levels_m"))

    # 功能说明：配置中同变量有启用和禁用两行时，确认禁用行不会覆盖启用设置。
    def test_disabled_duplicate_variable_does_not_override_enabled_row(self):
        request = PlotRequest(
            title="rh",
            member_name="m1",
            variables=[
                VariableConfig(name="rh", enabled=True, cmap="YlGnBu_r", levels=[0, 10, 20]),
                VariableConfig(name="rh", enabled=False, color="k", levels=[60, 70, 80]),
            ],
            time=SelectionSpec(values=[0]),
            height=SelectionSpec(values=[2000]),
        )
        spec = build_plot_spec(request, "test.png")

        self.assertEqual(list(spec.variables), ["rh"])
        self.assertTrue(spec.variables["rh"]["enabled"])
        self.assertEqual(spec.variables["rh"]["cmap"], "YlGnBu_r")

    # 功能说明：给变量选择附加覆盖项，确认生成绘图规格后这些选项仍存在。
    def test_variable_selection_overrides_are_preserved_in_plot_spec(self):
        request = PlotRequest(
            member_name="m1",
            variables=[
                VariableConfig(
                    name="dbz",
                    time=SelectionSpec(values=[6]),
                    height=SelectionSpec(values=[6000]),
                    angle=SelectionSpec(values=[90]),
                    radius=SelectionSpec(values=[50]),
                )
            ],
            time=SelectionSpec(values=[0]),
            height=SelectionSpec(values=[1000]),
            angle=SelectionSpec(values=[0, 360]),
            radius=SelectionSpec(values=[0, 200]),
        )

        spec = build_plot_spec(request, "test.png")

        self.assertEqual(spec.variables["dbz"]["times"], [6])
        self.assertEqual(spec.variables["dbz"]["height"], [6000])
        self.assertEqual(spec.variables["dbz"]["angle"], [90])
        self.assertEqual(spec.variables["dbz"]["radius"], [50])


if __name__ == "__main__":
    unittest.main()
