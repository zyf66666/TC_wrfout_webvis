import importlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '处理数据'))
from wrf_processing.processing_manager import ProcessingManager
from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.schemas import ProcessingRequest


def inputs(root, variables):
    paths = PathManager(root)
    yy, xx = np.mgrid[:11, :11]
    for height, cy, cx in [(500, 3, 4), (1000, 6, 7)]:
        for domain, variable, data in [
            (2, 'theta_e1_polar', np.broadcast_to(300 + height / 1000 + .01 * np.arange(0, 605, 5), (360, 121))),
            (3, 'theta_e', 320 + yy + 2 * xx + height / 1000),
        ]:
            p = paths.variable_path(member_name='member', domain=domain, variable=variable,
                                    time_hour=0, height_m=height, dimension='3d')
            p.parent.mkdir(parents=True, exist_ok=True)
            np.save(p, data)
        c = paths.layer_center_path('member', 3, height)
        c.parent.mkdir(parents=True, exist_ok=True)
        np.savetxt(c, [[0, cy, cx]])
    meta = root / 'member/d02/theta_e1_polar/source_metadata.json'
    meta.write_text(json.dumps({'center_mode': 'layer', 'radius_resolution_km': 5, 'radius_km': 600}))
    return ProcessingRequest(processed_root=root, member_name='member', domain=3,
                             variables=variables, times={'values': [0]}, heights={'values': [500, 1000]},
                             trust_existing=True, options={'polar_max_radius_km': 1, 'polar_radius_resolution_km': 1})


@pytest.mark.parametrize('entrypoint', [False, True])
def test_raw_and_layer_polar_use_same_d02_environment(tmp_path, entrypoint):
    request = inputs(tmp_path, ['theta_e_anomaly', 'theta_e_anomaly1_polar'])
    sources = {p: p.read_bytes() for p in tmp_path.rglob('*.npy')}
    if entrypoint:
        module = importlib.import_module('process_data')
        module.run_processing_request(request)
    else:
        ProcessingManager(tmp_path).run(request)
    r = np.arange(400, 505, 5)
    env_radial = np.sum(r * (.01 * r)) / r.sum()
    for height, cy, cx in [(500, 3, 4), (1000, 6, 7)]:
        p = tmp_path / f'member/d03/theta_e_anomaly/0h/{height}m.npy'
        raw = np.load(p)
        assert raw.shape == (11, 11)
        assert raw[cy, cx] == pytest.approx(20 + cy + 2 * cx - env_radial, abs=1e-5)
        polar = np.load(tmp_path / f'member/d03/theta_e_anomaly1_polar/0h/{height}m.npy')
        assert polar.shape == (360, 2)
        np.testing.assert_allclose(polar[:, 0], raw[cy, cx], atol=1e-5)
        meta = json.loads(p.with_suffix('.npy.environment.json').read_text())
        assert meta['environment_domain'] == 2
        assert meta['ring_km'] == [400, 500]
        assert meta['valid_fraction'] == 1
    assert all(p.read_bytes() == before for p, before in sources.items())


def test_polar_only_builds_anomaly_dependency(tmp_path):
    request = inputs(tmp_path, ['theta_e_anomaly1_polar'])
    ProcessingManager(tmp_path).run(request)
    assert (tmp_path / 'member/d03/theta_e_anomaly1_polar/0h/500m.npy').exists()


def test_changed_environment_invalidates_cached_anomaly(tmp_path):
    request = inputs(tmp_path, ['theta_e_anomaly'])
    manager = ProcessingManager(tmp_path)
    manager.run(request)
    out = tmp_path / 'member/d03/theta_e_anomaly/0h/500m.npy'
    before = np.load(out)
    ref = tmp_path / 'member/d02/theta_e1_polar/0h/500m.npy'
    np.save(ref, np.load(ref) + 2)
    manager.run(request)
    np.testing.assert_allclose(np.load(out), before - 2, atol=1e-5)


def test_missing_reference_does_not_compute_or_overwrite_source(tmp_path):
    request = inputs(tmp_path, ['theta_e_anomaly'])
    ref = tmp_path / 'member/d02/theta_e1_polar/0h/500m.npy'
    ref.unlink()
    with pytest.raises(FileNotFoundError, match='theta_e1_polar'):
        ProcessingManager(tmp_path).run(request)
    assert not ref.exists()


def test_entrypoint_polar_only_refreshes_changed_environment(tmp_path):
    request = inputs(tmp_path, ['theta_e_anomaly1_polar'])
    module = importlib.import_module('process_data')
    module.run_processing_request(request)
    output = tmp_path / 'member/d03/theta_e_anomaly1_polar/0h/500m.npy'
    before = np.load(output)
    ref = tmp_path / 'member/d02/theta_e1_polar/0h/500m.npy'
    np.save(ref, np.load(ref) + 2)
    module.run_processing_request(request)
    np.testing.assert_allclose(np.load(output), before - 2, atol=1e-5)


def test_entrypoint_configures_cross_domain_anomaly():
    module = importlib.import_module('process_data')
    assert module.DOMAINS == [3]
    # 变量清单是用户每次批处理前会改的，不在这里固定；只检查跨域环境的参数。
    assert module.OPTIONS['theta_e_environment_domain'] == 2
    assert module.OPTIONS['theta_e_environment_ring_km'] == [400, 500]


@pytest.mark.parametrize('bad_input', ['short_radius', 'all_nan', 'wrong_center'])
def test_invalid_reference_cannot_produce_anomaly(tmp_path, bad_input):
    request = inputs(tmp_path, ['theta_e_anomaly'])
    ref = tmp_path / 'member/d02/theta_e1_polar/0h/500m.npy'
    if bad_input == 'short_radius':
        np.save(ref, np.ones((360, 81)))
    elif bad_input == 'all_nan':
        np.save(ref, np.full((360, 121), np.nan))
    else:
        meta = ref.parent.parent / 'source_metadata.json'
        meta.write_text(json.dumps({'center_mode': 'slp', 'radius_resolution_km': 5}))
    with pytest.raises(ValueError):
        ProcessingManager(tmp_path).run(request)
    assert not (tmp_path / 'member/d03/theta_e_anomaly/0h/500m.npy').exists()
