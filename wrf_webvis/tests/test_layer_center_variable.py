import unittest
import json
from pathlib import Path

import numpy as np
import pytest

from wrf_webvis.app.core.paths import PathManager, metadata_path
from wrf_webvis.app.core.schemas import PlotRequest, PlotType, ProcessingRequest, SelectionSpec, VariableConfig, VariableDimension
from wrf_processing.data_manager import DataManager
from wrf_processing.processing_manager import ProcessingManager


# 功能说明：创建请求层中心极坐标变量及两个高度的标准处理请求。
def _layer_request(root, variable):
    return ProcessingRequest(
        processed_root=root, wrfout_root=root / "missing-wrfout", member_name="member", domain=3,
        variables=[variable], times={"values": [0]}, heights={"values": [500, 1000]},
        trust_existing=True,
        options={"polar_max_radius_km": 1, "polar_radius_resolution_km": 1},
    )


# 功能说明：为每个高度写入指定时次的层中心坐标文件。
def _write_centers(paths):
    centers = [(paths.center_path("member", 3, "slp"), (5, 5)),
               (paths.layer_center_path("member", 3, 500), (3, 4)),
               (paths.layer_center_path("member", 3, 1000), (6, 7))]
    for path, (y, x) in centers:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savetxt(path, [[0, y, x]])


@pytest.mark.parametrize("variable", ["rh", "theta_e", "wa", "dbz", "pvo", "ua"])
# 功能说明：给同一基础场和不同高度中心，确认各高度从基础场复用数据并围绕各自中心取样。
def test_layer_polar_reuses_base_field_and_samples_each_height_center(tmp_path, variable):
    paths = PathManager(tmp_path)
    _write_centers(paths)
    yy, xx = np.mgrid[:11, :11]
    for height in [500, 1000]:
        path = paths.variable_path(member_name="member", domain=3, variable=variable,
                                   time_hour=0, dimension="3d", height_m=height)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, 10 * yy + xx + height)
    manager = ProcessingManager(tmp_path)
    for suffix, centers in [("1_polar", [(3, 4), (6, 7)]), ("_polar", [(5, 5), (5, 5)])]:
        result = manager.run(_layer_request(tmp_path, variable + suffix))
        assert result["status"] == "complete"
        assert len(result["processed_files"]) == 2
        for height, (cy, cx), output in zip([500, 1000], centers, result["processed_files"]):
            output = Path(output)
            assert output == paths.variable_path(member_name="member", domain=3,
                variable=variable + suffix, time_hour=0, dimension="3d", height_m=height)
            field = np.load(output)
            assert field.shape == (360, 2)
            # The entire zero-radius column must sample this height's own center.
            np.testing.assert_allclose(field[:, 0], 10 * cy + cx + height)
            np.testing.assert_allclose(field[0, 1], 10 * cy + cx + height + 1)
            metadata = json.loads(metadata_path(output).read_text())
            assert metadata["center_mode"] == ("layer" if suffix == "1_polar" else "slp")
            assert Path(metadata["source"]).parent.parent.name == variable


# 功能说明：缺少湿度缓存时请求层极坐标量，确认系统把湿度作为三维原始量补处理。
def test_missing_rh_for_layer_polar_is_processed_as_3d_rh(tmp_path, monkeypatch):
    paths = PathManager(tmp_path)
    _write_centers(paths)
    manager = ProcessingManager(tmp_path)
    source = tmp_path / "wrfout_d03"
    source.touch()
    monkeypatch.setattr(manager, "_find_wrfout_files", lambda request: [source])
    monkeypatch.setattr(manager, "_source_for_time", lambda *args: (source, 0))
    batches = []

    # 功能说明：记录被交给批量处理器的变量和高度，避免真正运行计算。
    def process_batch(**kwargs):
        batches.append(kwargs["variables"])
        # Stand in for NetCDF reads; exercise real planning, cache saving and polar interpolation.
        for variable, dimension, heights in kwargs["variables"]:
            assert variable == "rh"
            assert dimension == VariableDimension.THREE_D
            assert heights == [500, 1000]
        return manager._process_3d_variable(
            request=kwargs["request"], variable="rh", source_file=source, source_time_index=0,
            time_hour=0, heights=[500, 1000], dtype="float32", prepared_array=np.ones((2, 11, 11)) * 60,
        )

    monkeypatch.setattr(manager, "_process_wrf_variable_batch", process_batch)
    result = manager.run(_layer_request(tmp_path, "rh1_polar"))
    assert len(batches) == 1
    assert len(result["processed_files"]) == 2
    for path in result["processed_files"]:
        np.testing.assert_allclose(np.load(path), 60)


@pytest.mark.parametrize("variable", ["vr", "vt"])
# 功能说明：计算层径向风时检查来源，确认每个高度保持自己的中心信息。
def test_layer_radial_winds_keep_center_specific_source(tmp_path, monkeypatch, variable):
    from wrf_processing.polar_transformer import PolarTransformer

    manager = ProcessingManager(tmp_path)
    sources, transforms = [], []
    monkeypatch.setattr(manager, "_ensure_standard_data", lambda request, names, heights, progress: sources.extend(names))
    monkeypatch.setattr(manager, "_ensure_layer_centers", lambda *args: [])
    monkeypatch.setattr(PolarTransformer, "transform_3d_per_height", lambda self, **kwargs: transforms.append(kwargs) or [])
    manager.run(_layer_request(tmp_path, variable + "1_polar"))
    assert sources == [variable + "1"]
    assert transforms[0]["source_variable"] == variable + "1"
    assert transforms[0]["center_mode"] == "layer"


class LayerCenterVariableTests(unittest.TestCase):
    # 功能说明：请求 dbz1 时检查变量名，确认采用层中心极坐标的专用名称。
    def test_dbz1_uses_layer_center_polar_name(self):
        request = PlotRequest(
            data_root="processed",
            processed_root="processed",
            member_name="0_land",
            domain=3,
            fig_type=PlotType.RADIUS_HEIGHT,
            variables=[VariableConfig(name="dbz1")],
            time=SelectionSpec(values=[0]),
            height=SelectionSpec(values=[1000]),
            angle=SelectionSpec(values=[0, 360]),
            radius=SelectionSpec(values=[0, 100]),
        )
        plan = DataManager.from_plot_request(request).plan_for_plot(request)
        self.assertIn("dbz1_polar", str(plan.required_files[0].path))
        self.assertIn("0h", str(plan.required_files[0].path))
        self.assertIn("1000m.npy", str(plan.required_files[0].path))


if __name__ == "__main__":
    unittest.main()
