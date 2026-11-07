from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from wrf_webvis.app.core.schemas import PlotLayerType, PlotRequest, SelectionSpec, VariableConfig
from wrf_webvis.app.plotting.research_analysis import _ls_series, required_paths
from wrf_webvis.app.services.plot_service import PlotService


class ResearchAnalysisPlotTests(unittest.TestCase):
    # 功能说明：用八个方向和两种下垫面的模拟极坐标数据，确认两种研究诊断都能生成无警告图片。
    def test_saved_research_analysis_requests_create_reproducible_figures(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            members = [f"WRF_{direction}_{surface}" for surface in ("sea", "land") for direction in (0, 45, 90, 135, 180, 225, 270, 315)]
            _write_polar_inputs(root, members)
            service = PlotService(root / "figures")

            for analysis_type in ("ls_direction_means", "timing_alignment_180_vs_270"):
                with self.subTest(analysis_type=analysis_type):
                    result = service.create_plot(
                        PlotRequest(
                            title=analysis_type,
                            data_root=root,
                            processed_root=root,
                            member_name=members[0],
                            member_names=members,
                            domain=3,
                            fig_type=5,
                            time=SelectionSpec(values=[0, 3, 6, 9, 12]),
                            extra={
                                "analysis_type": analysis_type,
                                "ls_sector_deg": [225, 315],
                                "radius_km": [20, 80],
                            },
                        )
                    )

                    self.assertTrue(Path(result["image_path"]).exists())
                    self.assertEqual(result["metadata"]["analysis_type"], analysis_type)
                    self.assertEqual(result["warnings"], [])

    # 功能说明：指定 225–315 度、20–30 公里的扇区，确认陆面通量序列使用该扇区的半径加权平均。
    def test_saved_research_analysis_request_uses_its_radius_and_sector_options(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            member = "WRF_180_sea"
            members = [f"WRF_{direction}_{surface}" for surface in ("sea", "land") for direction in (0, 45, 90, 135, 180, 225, 270, 315)]
            _write_polar_inputs(root, members)
            request = PlotRequest(
                data_root=root,
                processed_root=root,
                member_name=member,
                member_names=members,
                domain=3,
                time=SelectionSpec(values=[0]),
                extra={"ls_sector_deg": [225, 315], "radius_km": [20, 30]},
            )

            actual = _ls_series(request, member)["mfc"][0]
            from wrf_webvis.app.services.processed_data import ProcessedDataReader

            reader = ProcessedDataReader(root)
            values = reader.load(reader.resolve(member_name=member, domain=3, variable="bl_mfc_polar", time_hour=0, height_m=None))
            angles = np.arange(360)
            radii = np.arange(81)
            selected = values[np.ix_((angles >= 225) & (angles <= 315), (radii >= 20) & (radii <= 30))]
            weights = np.broadcast_to(radii[(radii >= 20) & (radii <= 30)], selected.shape)
            expected = float(np.sum(selected * weights) / np.sum(weights) * 3600.0)

            self.assertAlmostEqual(actual, expected)

    # 功能说明：创建定时对齐研究图后，确认请求被补成实际需要的变量、高度和面积加权定义。
    def test_research_analysis_saved_request_records_the_actual_diagnostic_variables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            members = [f"WRF_{direction}_{surface}" for surface in ("sea", "land") for direction in (0, 45, 90, 135, 180, 225, 270, 315)]
            _write_polar_inputs(root, members)
            request = PlotRequest(
                title="saved analysis variables",
                data_root=root,
                processed_root=root,
                member_name=members[0],
                member_names=members,
                domain=3,
                fig_type=5,
                time=SelectionSpec(values=[0, 3, 6, 9, 12]),
                variables=[],
                extra={"analysis_type": "timing_alignment_180_vs_270", "ls_sector_deg": [225, 315], "radius_km": [20, 80]},
            )

            PlotService(root / "figures").create_plot(request)

            self.assertEqual([item.name for item in request.variables], ["bl_mfc", "convergence", "qflux_r", "wa", "RAINNCV"])
            self.assertEqual(request.variables[1].height.values, [500])
            self.assertEqual(request.variables[3].height.values, [3000])
            self.assertEqual(request.extra["analysis_definition"]["statistic"], "area-weighted polar mean")

    # 功能说明：关闭 bl_mfc 后计算陆面通量序列，确认结果和必需文件中都不再包含它。
    def test_disabled_ls_variable_is_not_loaded_or_drawn(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            member = "WRF_180_sea"
            members = [f"WRF_{direction}_{surface}" for surface in ("sea", "land") for direction in (0, 45, 90, 135, 180, 225, 270, 315)]
            _write_polar_inputs(root, members)
            request = PlotRequest(
                data_root=root,
                processed_root=root,
                member_name=member,
                member_names=members,
                domain=3,
                time=SelectionSpec(values=[0]),
                variables=[
                    VariableConfig(name="bl_mfc", plot_type=PlotLayerType.LINE, enabled=False, use_polar=True),
                    VariableConfig(name="convergence", plot_type=PlotLayerType.LINE, use_polar=True, height=SelectionSpec(values=[500])),
                    VariableConfig(name="qflux_r", plot_type=PlotLayerType.LINE, use_polar=True, height=SelectionSpec(values=[500])),
                    VariableConfig(name="RAINNCV", plot_type=PlotLayerType.LINE, use_polar=True),
                ],
                extra={"analysis_type": "ls_direction_means"},
            )

            series = _ls_series(request, member, "ls_direction_means")
            paths = required_paths(request, "ls_direction_means")

            self.assertNotIn("mfc", series)
            self.assertEqual(set(series), {"conv", "qin", "rain"})
            self.assertFalse(any("bl_mfc_polar" in str(path) for path in paths))


# 功能说明：为每个成员和时次写入带方向、半径、成员和时次差异的诊断极坐标数组。
def _write_polar_inputs(root: Path, members: list[str]) -> None:
    angles = np.deg2rad(np.arange(360, dtype=float))[:, None]
    radii = np.arange(81, dtype=float)[None, :]
    pattern = 0.5 + 0.1 * np.cos(angles) + 0.001 * radii
    for member_index, member in enumerate(members):
        for time_hour in (0, 3, 6, 9, 12):
            multiplier = 1.0 + member_index * 0.01 + time_hour * 0.02
            _save(root / member / "d03" / "bl_mfc_polar" / f"{time_hour}h.npy", pattern * multiplier)
            _save(root / member / "d03" / "RAINNCV_polar" / f"{time_hour}h.npy", pattern * multiplier)
            for variable in ("convergence_polar", "qflux_r_polar", "wa_polar"):
                _save(root / member / "d03" / variable / f"{time_hour}h" / "500m.npy", pattern * multiplier)
            _save(root / member / "d03" / "wa_polar" / f"{time_hour}h" / "3000m.npy", pattern * multiplier)


# 功能说明：按旧路径写法写 processed_v3 切片（2026-11-01 起专题图只读 v3）。
def _save(path: Path, values: np.ndarray) -> None:
    from v3_fixtures import save_at_legacy_path

    save_at_legacy_path(path, values.astype(np.float32))
