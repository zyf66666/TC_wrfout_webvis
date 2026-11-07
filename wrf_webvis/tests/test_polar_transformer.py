import json
from pathlib import Path

import numpy as np
import pytest

from wrf_webvis.app.core.paths import PathManager, metadata_path
from wrf_processing.polar_transformer import PolarTransformer, polar_geometry


# 功能说明：写入中心记录和规则栅格数据，为极坐标转换建立最小输入。
def _write_inputs(root: Path) -> PathManager:
    paths = PathManager(root)
    center = paths.center_path("member", 3, "slp")
    center.parent.mkdir(parents=True)
    np.savetxt(center, [[0, 5, 5], [1, 6, 5]])
    yy, xx = np.mgrid[:11, :11]
    for time in (0, 1):
        source = paths.variable_path(
            member_name="member", domain=3, variable="RAINNCV",
            time_hour=time, dimension="2d",
        )
        source.parent.mkdir(parents=True, exist_ok=True)
        np.save(source, yy + xx + time)
    return paths


# 功能说明：读取三号域默认几何参数，确认径向分辨率为一公里。
def test_d03_default_geometry_uses_one_kilometre_resolution():
    geometry = polar_geometry(
        (805, 805),
        center_y=403,
        center_x=401,
        domain=3,
        radius_km=380,
        radius_resolution_km=None,
        domain_config=None,
    )

    assert geometry["dx_km"] == 1.0
    assert geometry["dy_km"] == 1.0
    assert geometry["safe_radius_km"] == 401.0
    assert geometry["radius_km"] == 380.0
    assert geometry["radius_resolution_km"] == 1.0
    assert geometry["radial_count"] == 381


# 功能说明：移动中心但固定半径，确认两次转换的输出形状一致。
def test_fixed_polar_radius_gives_same_shape_for_moving_centers(tmp_path: Path):
    paths = _write_inputs(tmp_path)
    transformer = PolarTransformer(tmp_path)
    outputs = [
        transformer.transform_2d(
            member_name="member", domain=3, variable="RAINNCV", time_hour=time,
            radius_km=4, radius_resolution_km=1,
            domain_config={3: {"dx_km": 1, "dy_km": 1}},
        )
        for time in (0, 1)
    ]

    assert [np.load(path).shape for path in outputs] == [(360, 5), (360, 5)]
    metadata = json.loads(metadata_path(outputs[0]).read_text(encoding="utf-8"))
    assert metadata["requested_radius_km"] == 4.0
    assert metadata["radius_km"] == 4.0
    assert metadata["radial_count"] == 5
    assert outputs[0] == paths.variable_path(
        member_name="member", domain=3, variable="RAINNCV_polar", time_hour=0, dimension="2d"
    )


# 功能说明：先按四公里缓存再改为三公里，确认旧缓存失效并得到更小格点数。
def test_changed_radius_invalidates_existing_polar_cache(tmp_path: Path):
    _write_inputs(tmp_path)
    transformer = PolarTransformer(tmp_path)
    path = transformer.transform_2d(
        member_name="member", domain=3, variable="RAINNCV", time_hour=0,
        radius_km=4, radius_resolution_km=1,
        domain_config={3: {"dx_km": 1, "dy_km": 1}},
    )
    assert np.load(path).shape == (360, 5)

    same_path = transformer.transform_2d(
        member_name="member", domain=3, variable="RAINNCV", time_hour=0,
        radius_km=3, radius_resolution_km=1,
        domain_config={3: {"dx_km": 1, "dy_km": 1}},
    )
    assert same_path == path
    assert np.load(path).shape == (360, 4)
    metadata = json.loads(metadata_path(path).read_text(encoding="utf-8"))
    assert metadata["requested_radius_km"] == 3.0


# 功能说明：已有当前缓存时先做检查，确认无需执行转换即可认定结果可用。
def test_current_output_can_be_checked_before_requesting_transform(tmp_path: Path):
    paths = _write_inputs(tmp_path)
    transformer = PolarTransformer(tmp_path)
    transformer.transform_2d(
        member_name="member", domain=3, variable="RAINNCV", time_hour=0,
        radius_km=4, radius_resolution_km=1,
        domain_config={3: {"dx_km": 1, "dy_km": 1}},
    )

    assert transformer.is_2d_output_current(
        member_name="member", domain=3, variable="RAINNCV", time_hour=0,
        radius_km=4, radius_resolution_km=1,
        domain_config={3: {"dx_km": 1, "dy_km": 1}},
    )
    assert not transformer.is_2d_output_current(
        member_name="member", domain=3, variable="RAINNCV", time_hour=0,
        radius_km=3, radius_resolution_km=1,
        domain_config={3: {"dx_km": 1, "dy_km": 1}},
    )
    source = paths.variable_path(member_name="member", domain=3, variable="RAINNCV", time_hour=0, dimension="2d")
    np.save(source, np.full((11, 11), 99.0))
    assert not transformer.is_2d_output_current(
        member_name="member", domain=3, variable="RAINNCV", time_hour=0,
        radius_km=4, radius_resolution_km=1,
        domain_config={3: {"dx_km": 1, "dy_km": 1}},
    )


# 功能说明：把中心靠近边缘并要求过大半径，确认系统拒绝无法在各方向容纳的请求。
def test_polar_radius_must_fit_inside_every_direction(tmp_path: Path):
    _write_inputs(tmp_path)
    with pytest.raises(ValueError, match="POLAR_MAX_RADIUS_KM"):
        PolarTransformer(tmp_path).transform_2d(
            member_name="member", domain=3, variable="RAINNCV", time_hour=0,
            radius_km=6, radius_resolution_km=1,
            domain_config={3: {"dx_km": 1, "dy_km": 1}},
        )
