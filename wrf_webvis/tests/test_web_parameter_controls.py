from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    import wrf_hpc_parameters
except ModuleNotFoundError:
    wrf_hpc_parameters = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HTML = (PROJECT_ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
JAVASCRIPT = (PROJECT_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
PLOT_SERVICE = (PROJECT_ROOT / "app" / "services" / "plot_service.py").read_text(encoding="utf-8")


class WebParameterControlTests(unittest.TestCase):
    @unittest.skipIf(wrf_hpc_parameters is None, "legacy wrf_hpc_parameters.py is absent from this worktree")
    # 功能说明：比较旧参数表、HTML 和脚本，确认处理选项同时出现在处理与绘图界面并传入请求。
    def test_processing_parameters_are_available_to_both_workflows(self):
        expected = set(wrf_hpc_parameters.build_parameters().processing_options)
        expected.add("wrfout_match_times")
        for key in expected:
            self.assertIn(key, JAVASCRIPT)
        self.assertIn('id="processAdvancedOptions"', HTML)
        self.assertIn('id="plotProcessingOptions"', HTML)
        self.assertIn('options: collectProcessingOptions(data, "process_opt_")', JAVASCRIPT)
        self.assertIn('processing_options: collectProcessingOptions(data, "plot_opt_")', JAVASCRIPT)
        self.assertIn("motion_correct=motion_correct_by_variable[variable]", PLOT_SERVICE)

    @unittest.skipIf(wrf_hpc_parameters is None, "legacy wrf_hpc_parameters.py is absent from this worktree")
    # 功能说明：逐节比较旧格式配置与网页高级控件，确认除手工控件外没有缺失参数。
    def test_hpc_format_parameters_have_web_controls(self):
        advanced_pairs = set(
            re.findall(r'\{ section: "([^"]+)".*?key: "([^"]+)"', JAVASCRIPT)
        )
        manual = {
            "common": {"title"},
            "horizontal": {
                "xlim_km", "tick_interval_km", "radius_circles_km", "draw_rmw",
                "draw_land_distance", "center_enabled", "center_height_m",
            },
            "time_radius": {"xlim", "ylim", "radius_interval", "time_interval"},
            "time_azimuth": {"ylim"},
            "time_series": {"xlim", "time_interval", "overlay_members", "scatter_points", "shear_arrows"},
            "track": {"xlim_km", "ylim_km", "tick_interval_km", "draw_land_boundary"},
            "three_d": {
                "radius_km", "draw_arrows", "draw_layer_centers", "arrow_w_min",
                "arrow_w_max", "arrow_interval", "surface_opacity",
            },
            "colorbar": {"title", "position"},
            "quiver_key": {"position", "U", "label"},
        }
        missing = {}
        for section, config in wrf_hpc_parameters.FORMAT_CONFIG.items():
            covered = {key for candidate, key in advanced_pairs if candidate == section}
            absent = set(config) - covered - manual.get(section, set())
            if absent:
                missing[section] = sorted(absent)
        self.assertEqual({}, missing)

    # 功能说明：检查脚本包含自由变量名输入和变量级样式、极坐标、平滑及坐标轴字段。
    def test_new_diagnostics_and_variable_level_parameters_are_selectable(self):
        # Plot variables are free-text entries; data-preparation variable menus
        # deliberately do not exist in the plot-only browser.
        self.assertIn('data-field="name"', JAVASCRIPT)
        for field in (
            "alpha", "linewidth", "linestyle", "clabel", "use_polar",
            "time_smooth_sigma", "angle_smooth_sigma", "angle_wrap", "ylim", "yticks",
            "direction_tick_values", "direction_tick_labels", "direction_ylim",
        ):
            self.assertIn(f'data-field="{field}"', JAVASCRIPT)

    # 功能说明：检查脚本以可选数值解析轨迹范围，避免空输入被错误转换为零。
    def test_blank_track_limits_are_not_converted_to_zero(self):
        self.assertIn(
            'const trackXlimSingle = parseOptionalNumber(data.get("track_xlim_km"))',
            JAVASCRIPT,
        )
        self.assertIn(
            'const trackYlimSingle = parseOptionalNumber(data.get("track_ylim_km"))',
            JAVASCRIPT,
        )

    # 功能说明：检查高级参数登记了时间—半径图和时间—方位图的登陆时刻控制项。
    def test_time_angle_plots_expose_landfall_time_control(self):
        advanced_pairs = set(
            re.findall(r'\{ section: "([^"]+)".*?key: "([^"]+)"', JAVASCRIPT)
        )
        self.assertIn(("time_radius", "landfall_time_h"), advanced_pairs)
        self.assertIn(("time_azimuth", "landfall_time_h"), advanced_pairs)

    # 功能说明：检查极坐标时间序列显示角度和半径输入，并将两项选择写入提交载荷。
    def test_polar_time_series_exposes_and_submits_global_sector_selection(self):
        self.assertIn(
            'src="/static/app.js?v=20261107-core&access=20261106&custom=20261014"',
            HTML,
        )
        self.assertIn(
            '5: timeSeriesUsesPolarData() ? ["time", "angle", "radius"] : ["time"]',
            JAVASCRIPT,
        )
        self.assertRegex(
            JAVASCRIPT,
            r'else if \(figType === 5 && timeSeriesUsesPolarData\(\)\) \{\s*'
            r'payload\.angle = selectionFromText\(data\.get\("angle"\)\);\s*'
            r'payload\.radius = selectionFromText\(data\.get\("radius"\)\);',
        )
        self.assertIn(
            'row.addEventListener("change", updateFigTypeFields)',
            JAVASCRIPT,
        )

    # 功能说明：确认普通绘图表单没有已移除的研究诊断类型、扇区和半径控件。
    def test_removed_research_controls_are_not_shown_in_the_plot_form(self):
        self.assertNotIn('name="analysis_type"', HTML)
        self.assertNotIn('name="analysis_ls_sector_deg"', HTML)
        self.assertNotIn('name="analysis_radius_km"', HTML)

    # 功能说明：确认脚本仍为已保存研究图片提供重新创建按钮和处理函数。
    def test_saved_research_analysis_images_can_still_be_recreated(self):
        self.assertIn('data-recreate="${image.id}"', JAVASCRIPT)
        self.assertIn('async function recreateResearchAnalysisImage(image)', JAVASCRIPT)

    # 功能说明：确认绘图表单和脚本都不暴露按成员填写 wrfout 根目录的字段。
    def test_member_specific_wrfout_roots_are_not_exposed_in_the_plot_form(self):
        self.assertNotIn('name="member_wrfout_roots"', HTML)
        self.assertNotIn('parseMemberWrfoutRoots(data.get("member_wrfout_roots"))', JAVASCRIPT)


if __name__ == "__main__":
    unittest.main()
