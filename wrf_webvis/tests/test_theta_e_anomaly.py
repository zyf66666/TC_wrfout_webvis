import numpy as np
import pytest


def test_environment_uses_radius_weights_and_valid_samples():
    from wrf_webvis.app.plotting.polar_anomaly import environmental_anomaly
    radii = np.arange(381.)
    field = np.broadcast_to(radii, (2, 381)).copy()
    field[0, 350] = np.nan
    anomaly, env, coverage = environmental_anomaly(field, radii)
    weights = np.broadcast_to(radii[350:], (2, 31)).copy()
    weights[0, 0] = 0
    expected = (weights * radii[None, 350:]).sum() / weights.sum()
    assert env == pytest.approx(expected)
    assert coverage == pytest.approx(61 / 62)
    assert np.isnan(anomaly[0, 350])
    assert np.nansum(anomaly[:, 350:] * weights) / weights.sum() == pytest.approx(0, abs=1e-10)
    assert anomaly[1, 0] == pytest.approx(-expected)


def test_environment_rejects_missing_ring_or_insufficient_radius():
    from wrf_webvis.app.plotting.polar_anomaly import environmental_anomaly
    with pytest.raises(ValueError, match='380'):
        environmental_anomaly(np.ones((2, 301)), np.arange(301.))
    with pytest.raises(ValueError, match='valid'):
        environmental_anomaly(np.full((2, 381), np.nan), np.arange(381.))


def test_anomaly_registered_for_polar_plotting():
    from wrf_webvis.app.core.registry import get_variable_item, resolve_variable_name
    from wrf_webvis.app.plotting.draw_utils import VARIABLE_TYPE_REGISTRY
    assert get_variable_item('theta_e_anomaly').dimension.value == '3d'
    assert resolve_variable_name('theta_e_anomaly').polar_name == 'theta_e_anomaly_polar'
    assert VARIABLE_TYPE_REGISTRY['theta_e_anomaly'] == '3d'


def test_anomaly_can_be_loaded_by_plot_service(tmp_path):
    from wrf_webvis.app.core.schemas import PlotRequest
    from wrf_webvis.app.services.plot_service import PlotService
    from wrf_webvis.app.plotting.draw_variables import prepare_variable_data
    from v3_fixtures import save_v3
    for height in (100, 200):
        save_v3(tmp_path, 'member', 'theta_e_anomaly_polar', np.broadcast_to(np.arange(381.) - 365, (360, 381)),
                time_hour=0, height_m=height)  # 2026-11-01：写成 v3 切片
    request = PlotRequest(member_name='member', data_root=str(tmp_path), processed_root=str(tmp_path),
                          fig_type=1, height={'values': [100, 200]},
                          variables=[{'name': 'theta_e_anomaly'}],
                          format={'radius_height': {'xlim': [0, 380]}})
    result = PlotService().dry_run(request)
    assert result['plan']['missing_files'] == []
    assert len(result['plan']['required_files']) == 2
    payloads, _ = prepare_variable_data(
        member_name='member', domain=3, variables={'theta_e_anomaly': {'plot_type': 'contourf'}},
        fig_type=1, data_root=tmp_path, polar_dir_style='auto',
        selection={'times': [0], 'height': [100, 200], 'angle': [0, 360], 'radius': [0, 380]})
    values = payloads['theta_e_anomaly']['values']
    assert values.shape == (2, 381)
    np.testing.assert_allclose(values[:, [0, -1]], [[-365, 15], [-365, 15]])
