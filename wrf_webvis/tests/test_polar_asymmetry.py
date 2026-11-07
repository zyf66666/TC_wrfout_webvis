import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from wrf_webvis.app.core.paths import PathManager, metadata_path
from wrf_webvis.app.core.schemas import ProcessingRequest, SelectionSpec, VariableDimension
from wrf_processing.diagnostics import polar_asymmetry
from wrf_processing.processing_manager import ProcessingManager


class PolarAsymmetryTests(unittest.TestCase):
    # 功能说明：计算固定地理象限的非对称量，确认统计使用完整圆盘而非仅局部扇区。
    def test_fixed_geographic_quadrant_asymmetry_uses_full_disk(self):
        uniform = np.ones((360, 201), dtype=float)
        self.assertAlmostEqual(
            polar_asymmetry.calculate_fixed_quadrant_asymmetry(uniform, radius_resolution_km=1.0, radius_km=200.0),
            0.007010059828477918,
            places=12,
        )

        down_shear_only = np.zeros((360, 201), dtype=float)
        down_shear_only[135:225, :] = 1.0
        self.assertAlmostEqual(
            polar_asymmetry.calculate_fixed_quadrant_asymmetry(
                down_shear_only,
                radius_resolution_km=1.0,
                radius_km=200.0,
            ),
            0.988909695994167,
            places=12,
        )

    # 功能说明：处理降水和雷达反射率，确认前者使用海平面气压中心、后者使用三公里中心。
    def test_processing_uses_slp_centered_precipitation_and_3km_centered_dbz(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path_manager = PathManager(root)
            member = "experiment"
            time_hour = 0
            precipitation_source = _write_polar_source(
                path_manager,
                member=member,
                variable="RAINNCV_polar",
                time_hour=time_hour,
                dimension=VariableDimension.TWO_D,
                values=np.ones((360, 301), dtype=np.float32),
                center_mode="slp",
            )
            dbz_source = _write_polar_source(
                path_manager,
                member=member,
                variable="dbz1_polar",
                time_hour=time_hour,
                dimension=VariableDimension.THREE_D,
                height_m=3000,
                values=np.ones((360, 301), dtype=np.float32),
                center_mode="layer",
            )
            request = ProcessingRequest(
                processed_root=root,
                member_name=member,
                domain=3,
                variables=[polar_asymmetry.PRECIPITATION_VARIABLE, polar_asymmetry.DBZ_VARIABLE],
                times=SelectionSpec(values=[time_hour]),
            )

            outputs = ProcessingManager(root).run(request)

            self.assertEqual(len(outputs["processed_files"]), 2)
            precipitation = path_manager.variable_path(
                member_name=member,
                domain=3,
                variable=polar_asymmetry.PRECIPITATION_VARIABLE,
                time_hour=time_hour,
                dimension=VariableDimension.ONE_D,
            )
            dbz = path_manager.variable_path(
                member_name=member,
                domain=3,
                variable=polar_asymmetry.DBZ_VARIABLE,
                time_hour=time_hour,
                dimension=VariableDimension.ONE_D,
            )
            np.testing.assert_allclose(np.load(precipitation), [0.007010059828477918])
            np.testing.assert_allclose(np.load(dbz), [0.007010059828477918])

            precipitation_metadata = json.loads(metadata_path(precipitation).read_text(encoding="utf-8"))
            dbz_metadata = json.loads(metadata_path(dbz).read_text(encoding="utf-8"))
            self.assertEqual(precipitation_metadata["derived_from"], [str(precipitation_source)])
            self.assertEqual(precipitation_metadata["center_mode"], "slp")
            self.assertEqual(precipitation_metadata["radius_km"], 200.0)
            self.assertEqual(dbz_metadata["derived_from"], [str(dbz_source)])
            self.assertEqual(dbz_metadata["center_mode"], "layer")
            self.assertEqual(dbz_metadata["center_height_m"], 3000.0)


# 功能说明：把给定极坐标数组写入变量缓存，为非对称处理准备输入。
def _write_polar_source(
    path_manager: PathManager,
    *,
    member: str,
    variable: str,
    time_hour: int,
    dimension: VariableDimension,
    values: np.ndarray,
    center_mode: str,
    height_m: float | None = None,
) -> Path:
    path = path_manager.variable_path(
        member_name=member,
        domain=3,
        variable=variable,
        time_hour=time_hour,
        dimension=dimension,
        height_m=height_m,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, values)
    metadata_path(path).write_text(
        json.dumps(
            {
                "radius_km": 300.0,
                "radius_resolution_km": 1.0,
                "center_mode": center_mode,
                "height_m": height_m,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_mdbz_asymmetry_recomputes_when_radius_changes(tmp_path):
    paths = PathManager(tmp_path)
    values = np.ones((360, 301), dtype=np.float32)
    values[135:225, 101:] = 20
    _write_polar_source(paths, member='experiment', variable='mdbz_polar', time_hour=0,
                        dimension=VariableDimension.TWO_D, values=values, center_mode='slp')
    request = ProcessingRequest(processed_root=tmp_path, member_name='experiment', domain=3,
                                variables=['mdbz_asymmetry'], times=SelectionSpec(values=[0]),
                                options={'asymmetry_radius_km': 100.0})
    manager = ProcessingManager(tmp_path)
    output = paths.variable_path(member_name='experiment', domain=3, variable='mdbz_asymmetry',
                                 time_hour=0, dimension=VariableDimension.ONE_D)
    manager.run(request)
    first = np.load(output).copy()
    request = request.model_copy(update={'options': {'asymmetry_radius_km': 200.0}})
    manager.run(request)
    second = np.load(output)
    assert second.shape == (1,)
    assert second[0] > first[0] + 0.1
    meta = json.loads(metadata_path(output).read_text())
    assert meta['radius_km'] == 200.0
    assert meta['source_polar_variable'] == 'mdbz_polar'
    assert meta['center_mode'] == 'slp'
    assert 'center_height_m' not in meta


def test_mdbz_missing_polar_source_prepares_mdbz_not_rain(tmp_path):
    from unittest.mock import Mock, patch

    paths = PathManager(tmp_path)
    request = ProcessingRequest(processed_root=tmp_path, member_name='experiment', domain=3,
                                variables=['mdbz_asymmetry'], times=SelectionSpec(values=[0]),
                                options={'asymmetry_radius_km': 100.0})
    prepare = Mock()
    center = Mock()
    layers = Mock(side_effect=AssertionError('mdbz does not require layer centers'))

    def transform(**kwargs):
        assert kwargs['variable'] == 'mdbz'
        assert kwargs['radius_km'] == 100.0
        return _write_polar_source(paths, member='experiment', variable='mdbz_polar', time_hour=0,
                                  dimension=VariableDimension.TWO_D,
                                  values=np.ones((360, 301)), center_mode='slp')

    with patch.object(polar_asymmetry.PolarTransformer, 'transform_2d', side_effect=transform):
        output = polar_asymmetry.process(request=request, path_manager=paths,
                    variable='mdbz_asymmetry', time_hour=0, dtype='float32',
                    ensure_slp_center=center, ensure_standard_data=prepare, ensure_layer_centers=layers)
    assert prepare.call_args.args[1] == ['mdbz']
    assert center.call_count == 1
    assert np.load(output).shape == (1,)
