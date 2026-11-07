"""水平图 RMW 圈读 processed_v3（2026-10-26，Claude）。

原来水平图只找旧版 processed_v2 的 rmw/<时次>.npy，v3 数据下圈画不出来。现在按
horizontal.rmw_source（rmw / rmw_by_height / rmw_by_layer_center）与 rmw_height_m 读 v3，
每个子图用自己的成员和时次。2026-11-01 起不再退回旧版 rmw/<时次>.npy。
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pytest


def _save(root, member, variable, values_by_minute, height=None):
    from 处理数据_v3.wrf_processing_v3.register import build_registry
    from 处理数据_v3.wrf_processing_v3.request import Task
    from 处理数据_v3.wrf_processing_v3.storage import NetCDFStorage

    storage = NetCDFStorage(root, time_origin="2023-09-01_00:00:00")
    spec = build_registry().resolve(variable)
    for minute, value in values_by_minute:
        storage.save(Task(member, 3, minute, variable, height), np.asarray([value]), spec)
        if height is not None:
            # 另一高度的值不同，确认只读了指定高度。
            storage.save(Task(member, 3, minute, variable, height + 800), np.asarray([value + 500]), spec)


def _circles(ax):
    return [patch for patch in ax.patches if isinstance(patch, mpatches.Circle)]


def _draw(root, member, time_hour, fmt):
    from wrf_webvis.app.plotting.draw_format import maybe_draw_rmw

    fig, ax = plt.subplots()
    maybe_draw_rmw(ax, root, member, 3, [time_hour], 0.0, 0.0, 1.0,
                   {"coordinate_system": "center_km", "draw_rmw": True, **fmt})
    return fig, ax


# 功能说明：默认数据源 rmw（最低模式层）从 rmw.nc 读，各成员、各时次分别取值。
def test_lowest_level_rmw_reads_v3_per_member_and_time(tmp_path):
    root = tmp_path / "processed_v3"
    _save(root, "land", "rmw", ((34 * 60, 83.0), (37 * 60, 53.0)))
    _save(root, "sea", "rmw", ((37 * 60, 86.0),))
    for member, hour, expected in (("land", 34, 83.0), ("land", 37, 53.0), ("sea", 37, 86.0)):
        fig, ax = _draw(root, member, hour, {})
        try:
            circles = _circles(ax)
            assert len(circles) == 1
            assert circles[0].get_radius() == pytest.approx(expected)
        finally:
            plt.close(fig)


# 功能说明：逐高度数据源读 rmw_height_m 那一层；颜色、线宽、标注按选项。
@pytest.mark.parametrize("source", ["rmw_by_height", "rmw_by_layer_center"])
def test_height_rmw_reads_selected_height(tmp_path, source):
    root = tmp_path / "processed_v3"
    _save(root, "land", source, ((37 * 60, 53.0),), height=200)
    fig, ax = _draw(root, "land", 37, {
        "rmw_source": source, "rmw_height_m": 200, "rmw_color": "k", "rmw_linewidth": 2.2, "rmw_annotate": True,
    })
    try:
        circle = _circles(ax)[0]
        assert circle.get_radius() == pytest.approx(53.0)
        assert circle.get_linewidth() == pytest.approx(2.2)
        texts = [text.get_text() for text in ax.texts]
        assert any("53 km" in text and "0.2 km" in text for text in texts)
    finally:
        plt.close(fig)


# 功能说明：网格坐标（非 center_km）时半径换成格点数。
def test_grid_coordinates_divide_by_dx(tmp_path):
    from wrf_webvis.app.plotting.draw_format import maybe_draw_rmw

    root = tmp_path / "processed_v3"
    _save(root, "land", "rmw", ((60, 30.0),))
    fig, ax = plt.subplots()
    try:
        maybe_draw_rmw(ax, root, "land", 3, [1], 10.0, 10.0, 3.0, {"draw_rmw": True})
        assert _circles(ax)[0].get_radius() == pytest.approx(10.0)
    finally:
        plt.close(fig)


# 功能说明：逐高度数据源缺这一层时只警告、不画，不会悄悄换成别的高度。
def test_missing_height_warns_without_circle(tmp_path):
    root = tmp_path / "processed_v3"
    _save(root, "land", "rmw_by_height", ((37 * 60, 53.0),), height=200)
    with pytest.warns(UserWarning, match="rmw_by_height"):
        fig, ax = _draw(root, "land", 37, {"rmw_source": "rmw_by_height", "rmw_height_m": 1500})
    try:
        assert _circles(ax) == []
    finally:
        plt.close(fig)


# 功能说明：数据源写错时报错。
def test_unknown_source_rejected(tmp_path):
    with pytest.raises(ValueError, match="rmw_source"):
        _draw(tmp_path, "land", 37, {"rmw_source": "bad"})


# 功能说明：2026-11-01 起旧版 rmw/<时次>.npy 不再读取：v3 没有切片时只警告、不画圈。
def test_legacy_npy_is_ignored(tmp_path):
    legacy = tmp_path / "processed_v3"
    target = legacy / "land" / "d03" / "rmw"
    target.mkdir(parents=True)
    np.save(target / "37h.npy", np.array([61.0]))
    with pytest.warns(UserWarning, match="processed_v3"):
        fig, ax = _draw(legacy, "land", 37, {})
    try:
        assert _circles(ax) == []
    finally:
        plt.close(fig)


# 功能说明：出图前检查按 v3 切片判断：已完成的切片可用，缺的时次报缺失（路径仍指向 v3 文件）。
def test_plot_inputs_require_v3_slices(tmp_path):
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.plot_inputs import check_additional_plot_inputs

    root = tmp_path / "processed_v3"
    _save(root, "land", "rmw", ((37 * 60, 53.0),))
    _save(root, "land", "rmw_by_height", ((37 * 60, 53.0),), height=200)

    def check(horizontal, hours):
        request = PlotRequest(
            member_name="land", data_root=root, fig_type=0, time={"values": hours},
            height={"values": [1000]}, variables=[], format={"horizontal": {"draw_rmw": True, **horizontal}},
        )
        return check_additional_plot_inputs(request)

    lowest = check({}, [37])
    assert [path.name for path in lowest.required_files] == ["rmw.nc"]
    assert lowest.missing_files == []
    by_height = check({"rmw_source": "rmw_by_height", "rmw_height_m": 200}, [37])
    assert [path.name for path in by_height.required_files] == ["rmw_by_height.nc"]
    assert by_height.missing_files == []
    missing = check({}, [37, 38])
    assert [(item.variable, item.time_hour) for item in missing.missing_items] == [("rmw", 38.0)]
    assert missing.missing_files == [root / "land" / "d03" / "rmw.nc"]
