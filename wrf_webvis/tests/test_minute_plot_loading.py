from pathlib import Path

import numpy as np
import pytest

from v3_fixtures import save_v3
from wrf_webvis.app.plotting.draw_variables import _time_label, load_variable_stack


# 2026-11-01：绘图只读 processed_v3；5 分钟时次（36 h 5 min = 2165 分钟）从 v3 切片读取。
@pytest.mark.parametrize('polar', [False, True])
def test_load_five_minute_dbz_from_v3_slice(tmp_path: Path, polar: bool):
    variable = 'dbz_polar' if polar else 'dbz'
    field = np.full((4, 5), 35, dtype=np.float32)
    save_v3(tmp_path, 'WRF_315_sea', variable, field, time_hour=36 + 5 / 60, height_m=3000)
    arrays, infos, kind = load_variable_stack(
        variable='dbz', member_name='WRF_315_sea', domain=3,
        times=[36.0833333333333], heights_m=[3000], fig_type=0,
        data_root=tmp_path, polar=polar, polar_dir_style='suffix',
        registry=None, dry_run=False, allow_missing=False,
    )
    assert kind == '3d'
    np.testing.assert_array_equal(arrays[0], field[None, ...])


@pytest.mark.parametrize('time, label', [(36.0833333333333, '36h5min'), (36, '36h'), (0.5, '0h30min')])
def test_minute_time_labels(time, label):
    assert _time_label(time) == label
