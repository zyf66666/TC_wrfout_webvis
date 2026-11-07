import tempfile
import unittest
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import pytest

from wrf_webvis.app.core.schemas import FormatConfig, PlotRequest, PlotType, SelectionSpec, VariableConfig
from wrf_webvis.app.plotting.plotly_3d import create_3d_plot, normalize_3d_heights
from wrf_webvis.app.services.plot_service import PlotService
from v3_fixtures import save_v3


class Plotly3DTests(unittest.TestCase):
    # 功能说明：分别用千米和米传入高度，确认两种写法解析成同一高度。
    def test_height_values_accept_km_or_m(self):
        self.assertEqual(normalize_3d_heights([2, 5000, 8]), [2000.0, 5000.0, 8000.0])

    # 功能说明：创建三维绘图，确认同时生成静态 PNG 和交互 HTML 文件。
    def test_create_3d_plot_outputs_png_and_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 2026-11-01：三维图只读 processed_v3：2 km、5 km 两层 dbz 切片和 0 h 的 slp_center。
            low = np.zeros((24, 24), dtype=np.float32)
            low[8:16, 8:16] = 35.0
            high = np.zeros((24, 24), dtype=np.float32)
            high[9:17, 9:17] = 45.0
            save_v3(root, "m1", "dbz", low, time_hour=0, height_m=2000)
            save_v3(root, "m1", "dbz", high, time_hour=0, height_m=5000)
            save_v3(root, "m1", "slp_center", [12, 12], time_hour=0)

            service = PlotService(root / "figures")
            request = PlotRequest(
                data_root=root,
                processed_root=root,
                member_name="m1",
                selection={"members": ["m1"]},
                domain=3,
                fig_type=PlotType.THREE_D,
                variables=[VariableConfig(name="dbz")],
                time=SelectionSpec(values=[0]),
                height=SelectionSpec(values=[2, 5]),
                format=FormatConfig(three_d={"draw_layer_centers": False, "draw_arrows": False, "radius_km": 10}),
            )

            result = service.create_plot(request)

            self.assertTrue(Path(result["image_path"]).exists())
            self.assertTrue(Path(result["html_path"]).exists())
            self.assertTrue(result["static_path"].endswith(".png"))
            self.assertTrue(result["html_static_path"].endswith(".html"))


@pytest.mark.parametrize(
    "options, threshold, stride",
    [
        ({}, 0.0, 1),
        ({"transparent_below": 0, "cmin": 15, "cmax": 55}, 0.0, 1),
        ({"transparent_below": 15, "surface_stride": 2}, 15.0, 2),
    ],
)
# 功能说明：检查各高度表面的实际几何，确保低值和缺测值成为空洞，阈值边界及更高值保留；支持自定义色标和抽稀。
def test_3d_dbz_hides_surface_geometry_below_threshold(tmp_path, monkeypatch, options, threshold, stride):
    field = np.tile(
        [[-30.0, -0.01, 0.0], [5.0, 15.0, 55.0], [np.nan, np.inf, -np.inf]],
        (2, 2),
    )
    fields = [field, np.flip(field)]
    for height_m, values in zip([1000, 2000], fields):
        save_v3(tmp_path, "m1", "dbz", values, time_hour=0, height_m=height_m)

    figures = []
    # Keep real HTML serialization; intercept only the external PNG exporter.
    monkeypatch.setattr(go.Figure, "write_image", lambda self, *args, **kwargs: figures.append(self))
    result = create_3d_plot(
        data_root=tmp_path,
        output_png=tmp_path / "plot.png",
        output_html=tmp_path / "plot.html",
        member_name="m1",
        domain=3,
        variable="dbz",
        time_hour=0,
        heights_m=[1000, 2000],
        domain_config={3: {"dx_km": 1.0}},
        format_config={"three_d": {"draw_layer_centers": False, "radius_km": 10, **options}},
    )

    assert Path(result["html_path"]).exists()
    assert len(figures) == 1
    for height_km, values, trace in zip([1.0, 2.0], fields, figures[0].data):
        sampled = values[::stride, ::stride]
        visible = np.isfinite(sampled) & (sampled >= threshold)
        np.testing.assert_allclose(trace.z, np.where(visible, height_km, np.nan), equal_nan=True)
        np.testing.assert_allclose(trace.surfacecolor, np.where(visible, sampled, np.nan), equal_nan=True)
        assert trace.connectgaps is False
        assert trace.cmin == options.get("cmin", threshold)
        assert trace.cmax == 55
    # 输入切片没有被就地修改。
    from wrf_webvis.app.services.processed_data import ProcessedDataReader

    reader = ProcessedDataReader(tmp_path)
    stored = reader.load(reader.resolve(member_name="m1", domain=3, variable="dbz", time_hour=0, height_m=1000))
    np.testing.assert_allclose(stored, field.astype(np.float32), equal_nan=True)


if __name__ == "__main__":
    unittest.main()


# 功能说明：三维图读取 v3 指定时次和高度，缺层时明确报错。
def test_load_3d_variable_reads_v3_selected_slices(tmp_path):
    from 处理数据_v3.wrf_processing_v3.register import build_registry
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage
    from wrf_webvis.app.plotting.plotly_3d import load_3d_variable

    root = tmp_path / "processed_v3"
    storage = NetCDFStorage(root, time_origin="2023-09-01_00:00:00")
    spec = build_registry().resolve("dbz")
    for minute in [1740, 1800]:
        for height in [1000, 3000, 5000, 7000, 9000]:
            storage.save(Task("m1", 3, minute, "dbz", height),
                         np.full((4, 5), minute / 60 + height / 1000), spec)
    kwargs = dict(data_root=root, member_name="m1", domain=3, variable="dbz", time_hour=30)
    values, heights, paths = load_3d_variable(**kwargs, heights_m=[9000, 1000, 5000])
    np.testing.assert_allclose(heights, [9000, 1000, 5000])
    np.testing.assert_allclose(values[:, 0, 0], [39, 31, 35])
    assert values.shape == (3, 4, 5)
    assert set(paths) == {root / "m1/d03/dbz.nc"}
    with pytest.raises(FileNotFoundError):
        load_3d_variable(**kwargs, heights_m=[2000])
