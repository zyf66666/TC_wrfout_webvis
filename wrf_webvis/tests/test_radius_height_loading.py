from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np

from v3_fixtures import save_v3
from wrf_webvis.app.plotting.draw_variables import load_variable_stack, prepare_variable_data, reduce_radius_height_files


# 功能说明：为两个高度写入常数极坐标 RH 切片（processed_v3），数值可随时次偏移以便识别读取结果。
def _write_polar_height_fields(root: Path, *, time_hour: int, offset: float) -> None:
    for height_m, value in ((100, 10.0 + offset), (200, 20.0 + offset)):
        field = np.full((4, 5), value, dtype=np.float32)
        save_v3(root, "member", "rh_polar", field, time_hour=time_hour, height_m=height_m)


# 功能说明：用两时次两高度极坐标文件绘制半径—高度图，确认先分别归约高度而不构建完整三维栈。
def test_radius_height_reduces_each_height_before_creating_a_stack(tmp_path: Path):
    _write_polar_height_fields(tmp_path, time_hour=0, offset=0.0)
    _write_polar_height_fields(tmp_path, time_hour=1, offset=2.0)

    with patch(
        "wrf_webvis.app.plotting.draw_variables.load_per_height_stack",
        side_effect=AssertionError("radius-height should not build a full 3D stack"),
    ):
        payloads, _summaries = prepare_variable_data(
            member_name="member",
            domain=3,
            variables={"rh": {"enabled": True, "plot_type": "contourf"}},
            fig_type=1,
            data_root=tmp_path,
            domain_config={3: {"dx_km": 1.0, "dy_km": 1.0}},
            selection={"times": [0, 1], "height": [100, 200], "angle": [0, 3], "radius": [0, 4]},
            polar_dir_style="auto",
            variable_type_registry={"rh": "3d"},
        )

    np.testing.assert_allclose(payloads["rh"]["values"], [[11.0] * 5, [21.0] * 5])
    np.testing.assert_allclose(payloads["rh"]["x"], [0, 1, 2, 3, 4])
    np.testing.assert_allclose(payloads["rh"]["y"], [100, 200])


# 功能说明：拦截切片读取，确认半径—高度归约逐高度各读一次切片。
def test_radius_height_reads_each_source_layer_sequentially(tmp_path: Path):
    from wrf_webvis.app.services.processed_data import ProcessedDataReader

    _write_polar_height_fields(tmp_path, time_hour=0, offset=0.0)
    native_load = ProcessedDataReader.load
    loaded: list[object] = []

    # 功能说明：记录每次切片读取后继续执行真实读取。
    def track_load(self, reference):
        loaded.append((reference.task.variable, reference.task.height_m))
        return native_load(self, reference)

    with patch.object(ProcessedDataReader, "load", track_load):
        result = reduce_radius_height_files(
            data_root=tmp_path,
            member_name="member",
            domain=3,
            variable="rh",
            times=[0],
            heights_m=[100, 200],
            angle=[0, 3],
            radius_indices=[0, 1, 2, 3, 4],
            radius_mode="list",
            radius_resolution_km=1.0,
            polar_dir_style="auto",
        )

    assert result is not None
    assert loaded == [("rh_polar", 100), ("rh_polar", 200)]


# 功能说明：读取两个时次的高度文件时，确认进度消息先报告各时次及其高度文件数量。
def test_radius_height_reports_each_time_batch_before_reading_files(tmp_path: Path):
    _write_polar_height_fields(tmp_path, time_hour=0, offset=0.0)
    _write_polar_height_fields(tmp_path, time_hour=1, offset=2.0)
    messages: list[str] = []

    reduce_radius_height_files(
        data_root=tmp_path,
        member_name="member",
        domain=3,
        variable="rh",
        times=[0, 1],
        heights_m=[100, 200],
        angle=[0, 3],
        radius_indices=[0, 1, 2, 3, 4],
        radius_mode="list",
        radius_resolution_km=1.0,
        polar_dir_style="auto",
        progress=messages.append,
    )

    assert messages == [
        "读取 rh 0h：2 个高度文件（1/2）",
        "读取 rh 1h：2 个高度文件（2/2）",
    ]


# 功能说明：加载普通二维变量的两个时次，确认进度消息逐个报告数据文件批次。
def test_other_figure_types_report_each_data_file_batch(tmp_path: Path):
    for hour in (0, 1):
        save_v3(tmp_path, "member", "field", np.ones((3, 4), dtype=np.float32), time_hour=hour)
    messages: list[str] = []

    stack, _files, variable_type = load_variable_stack(
        variable="field",
        member_name="member",
        domain=3,
        times=[0, 1],
        heights_m=None,
        fig_type=0,
        data_root=tmp_path,
        polar=False,
        polar_dir_style="same",
        registry={"field": "2d"},
        dry_run=False,
        allow_missing=False,
        progress=messages.append,
    )

    assert stack is not None
    assert variable_type == "2d"
    assert messages == [
        "读取 field 0h：1 个数据文件（1/2）",
        "读取 field 1h：1 个数据文件（2/2）",
    ]
