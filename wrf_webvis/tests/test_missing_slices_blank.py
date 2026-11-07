"""2026-11-05：缺切片“留空”模式（format.common.missing_slices = "blank"）。

默认（error）缺一个切片整张图报错；blank 时变量文件存在、只缺部分时次/高度的切片读成 NaN、图上留白，
中心类变量与整份文件不存在仍报错；留空模式下“插补”自动改为“留空”，不能把整段缺口插值补上。
"""
from __future__ import annotations

import numpy as np
import pytest

from v3_fixtures import save_v3
from wrf_webvis.app.core.schemas import PlotRequest, SelectionSpec, VariableConfig
from wrf_webvis.app.services.plot_inputs import MissingProcessedDataError
from wrf_webvis.app.services.plot_service import PlotService, missing_slices_mode
from wrf_webvis.app.services.processed_data import ProcessedDataReader, blank_missing_slices


def _polar(value: float) -> np.ndarray:
    return np.full((360, 101), value, dtype=np.float32)


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "processed_v3"
    for hour in (0, 2):  # 1 h 故意不写
        save_v3(root, "member", "diabatic_heating_polar", _polar(10.0 + hour), time_hour=hour, height_m=1000)
        save_v3(root, "member", "rmw", np.asarray(40.0 + hour), time_hour=hour)
    return root


def test_reader_blanks_missing_slice_only_inside_blank_mode(root):
    reader = ProcessedDataReader(root)
    assert reader.resolve(member_name="member", domain=3, variable="diabatic_heating_polar", time_hour=1, height_m=1000) is None
    with blank_missing_slices():
        reader = ProcessedDataReader(root)
        ref = reader.resolve(member_name="member", domain=3, variable="diabatic_heating_polar", time_hour=1, height_m=1000)
        assert ref is not None and ref.backend == "blank"
        values = reader.load(ref)
        assert values.shape == (360, 101) and np.isnan(values).all()
        scalar = reader.load(reader.resolve(member_name="member", domain=3, variable="rmw", time_hour=1, height_m=None))
        assert scalar.shape == (1,) and np.isnan(scalar).all()   # 与已完成标量切片同形状
        # 已完成的切片照常读取
        done = reader.resolve(member_name="member", domain=3, variable="rmw", time_hour=2, height_m=None)
        assert done.backend == "netcdf4" and reader.load(done).tolist() == [42.0]
        # 文件不存在、中心类变量：仍然是缺失
        assert reader.resolve(member_name="member", domain=3, variable="theta_polar", time_hour=1, height_m=1000) is None
        assert reader.resolve(member_name="member", domain=3, variable="slp_center", time_hour=1, height_m=None) is None


def _request(root, mode=None, variable="diabatic_heating", fig_type=2):
    fmt = {"common": {"missing_slices": mode}} if mode else {}
    return PlotRequest(
        data_root=root, processed_root=root, member_name="member", domain=3, fig_type=fig_type,
        time=SelectionSpec(values=[0, 1, 2]), height=SelectionSpec(values=[1000]),
        angle=SelectionSpec(values=[0, 360]), radius=SelectionSpec(values=[0, 100]),
        variables=[VariableConfig(name=variable, plot_type="contourf", levels=[0, 5, 10, 15, 20])],
        polar_dir_style="suffix", format=fmt,
    )


def test_default_mode_still_raises(root, tmp_path):
    with pytest.raises(MissingProcessedDataError):
        PlotService(tmp_path / "figs").create_plot(_request(root))


def test_blank_mode_draws_and_warns(root, tmp_path):
    request = _request(root, "blank")
    plan = PlotService(tmp_path / "figs").dry_run(_request(root, "blank"))["plan"]
    assert plan["missing_files"] == [] and len(plan["blank_slices"]) == 1
    result = PlotService(tmp_path / "figs").create_plot(request)
    assert (tmp_path / "figs").joinpath(result["image_path"].split("/")[-1]).is_file()
    assert any("缺切片留空" in message and "1 h" in message for message in result["warnings"])
    # 留空模式不把整段缺口插值补上
    assert all(config.missing_value_policy == "mask" for config in request.variables)


def test_blank_mode_still_raises_when_file_missing(root, tmp_path):
    with pytest.raises(MissingProcessedDataError):
        PlotService(tmp_path / "figs").create_plot(_request(root, "blank", variable="theta"))


def test_missing_slices_mode_validation(root):
    assert missing_slices_mode(_request(root)) == "error"
    with pytest.raises(ValueError):
        missing_slices_mode(_request(root, "skip"))
