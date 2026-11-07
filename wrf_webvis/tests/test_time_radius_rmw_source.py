from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pytest

from wrf_webvis.app.plotting.draw_format import set_format2


def _save_rmw(root, source, values_by_minute, height=1000):
    from 处理数据_v3.wrf_processing_v3.register import build_registry
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    storage = NetCDFStorage(root, time_origin="2023-09-01_00:00:00")
    spec = build_registry().resolve(source)
    for minute, value in values_by_minute:
        storage.save(Task("member", 3, minute, source, height), np.asarray([value]), spec)
        # 另一个高度的值不同，用来确认只读取了指定高度。
        storage.save(Task("member", 3, minute, source, height + 1000), np.asarray([value + 500]), spec)
    return storage


# 功能说明：时间—半径图从逐高度 RMW 读取指定高度的逐时次值，并使用自定义颜色与线宽。
@pytest.mark.parametrize(
    "source", ["rmw_by_height", "rmw_by_layer_center", "rmw_by_height_sr", "rmw_by_layer_center_sr"],
)
def test_time_radius_rmw_reads_processed_height_series(tmp_path, source):
    root = tmp_path / "processed_v3"
    _save_rmw(root, source, ((0, 100.0), (60, 80.0), (120, 50.0)))
    fig, ax = plt.subplots()
    try:
        set_format2(
            ax, "member", 3, [0, 1, 2], None, [0, 360], {"diabatic_heating": {}},
            data_root=root,
            config={"time_radius": {
                "draw_rmw": True, "rmw_source": source, "rmw_height_m": 1000,
                "rmw_color": "k", "rmw_linewidth": 2.2,
            }},
        )
        lines = [line for line in ax.lines if line.get_label().startswith("RMW")]
        assert len(lines) == 1
        line = lines[0]
        np.testing.assert_allclose(line.get_xdata(), [100, 80, 50])
        np.testing.assert_allclose(line.get_ydata(), [0, 1, 2])
        assert line.get_color() == "k"
        assert line.get_linewidth() == 2.2
        center = "SLP center" if source.startswith("rmw_by_height") else "layer center"
        if source.endswith("_sr"):
            center += ", storm-relative"
        assert line.get_label() == f"RMW (1 km, {center})"
    finally:
        plt.close(fig)


# 功能说明：逐高度 RMW 也使用 overlay_smooth_sigma 做时间平滑，且缺失时次被跳过。
def test_time_radius_rmw_processed_source_smooths_and_skips_missing(tmp_path):
    root = tmp_path / "processed_v3"
    _save_rmw(root, "rmw_by_height", ((0, 100.0), (60, 40.0), (180, 100.0)))
    fig, ax = plt.subplots()
    try:
        set_format2(
            ax, "member", 3, [0, 1, 2, 3], None, [0, 360], {"diabatic_heating": {}},
            data_root=root,
            config={"time_radius": {
                "draw_rmw": True, "rmw_source": "rmw_by_height", "overlay_smooth_sigma": 1,
            }},
        )
        line = [line for line in ax.lines if line.get_label().startswith("RMW")][0]
        np.testing.assert_allclose(line.get_ydata(), [0, 1, 3])
        assert 40 < line.get_xdata()[1] < 100
    finally:
        plt.close(fig)


# 功能说明：数据源写错时给出明确错误，而不是静默不画。
def test_time_radius_rmw_rejects_unknown_source(tmp_path):
    fig, ax = plt.subplots()
    try:
        with pytest.raises(ValueError, match="rmw_source"):
            set_format2(
                ax, "member", 3, [0], None, [0, 360], {"diabatic_heating": {}},
                data_root=tmp_path,
                config={"time_radius": {"draw_rmw": True, "rmw_source": "bad"}},
            )
    finally:
        plt.close(fig)


# 功能说明：选择逐高度数据源时，输入检查要求 processed_v3 文件，而不是旧的逐时次 rmw 文件。
def test_time_radius_rmw_processed_source_input_check(tmp_path):
    from wrf_webvis.app.core.schemas import PlotRequest, PlotType, SelectionSpec
    from wrf_webvis.app.services.plot_inputs import check_additional_plot_inputs

    root = tmp_path / "processed_v3"
    storage = _save_rmw(root, "rmw_by_height", ((0, 100.0), (60, 80.0)))
    request = PlotRequest(
        data_root=root, processed_root=root, member_name="member", domain=3,
        fig_type=PlotType.TIME_RADIUS, time=SelectionSpec(values=[0, 1]),
        format={"time_radius": {"draw_rmw": True, "rmw_source": "rmw_by_height", "rmw_height_m": 1000}},
    )
    result = check_additional_plot_inputs(request)
    assert not any(path.parts[-2] == "rmw" for path in result.required_files)
    from 处理数据_v3.wrf_processing_v3.request import Task
    expected = storage.path_for(Task("member", 3, 0, "rmw_by_height", 1000))
    assert expected in result.required_files
    assert result.missing_files == []
