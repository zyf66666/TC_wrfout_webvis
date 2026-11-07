"""普通轴平均图的 dbz 有效回波默认口径回归。"""
import numpy as np
import pytest

from wrf_webvis.app.core.schemas import PlotRequest, VariableConfig
from wrf_webvis.app.plotting.draw_variables import (
    apply_postprocess, prepare_variable_data, reduce_variable,
)
from wrf_webvis.app.services.plot_service import PlotService


def reduce_stack(stack, fig_type, variable="dbz"):
    return reduce_variable(
        stack=stack, variable=variable, variable_type="3d", fig_type=fig_type,
        times=list(range(len(stack))), heights_m=[3000],
        angle=[0, 360], radius_indices=[0, 2], radius_mode="range",
        radius_resolution_km=1, first_path="test_dbz_polar",
    )["values"]


@pytest.mark.parametrize("variable", ["dbz", "dbz1"])
def test_time_radius_averages_linear_z_including_negative_dbz(variable):
    stack = [np.array([[[-30, 0, -30], [40, 10, -1]]], dtype=float)]
    values = reduce_stack(stack, 2, variable)
    np.testing.assert_allclose(values, [[10*np.log10(10000.001/2), 10*np.log10(5.5), 10*np.log10((0.001+10**(-0.1))/2)]], equal_nan=True)


@pytest.mark.parametrize("fig_type", [1, 5, 8])
def test_other_axis_average_reducers_use_valid_arithmetic_samples(fig_type):
    stack = [np.array([[[-30, 0, -30], [40, 10, -1]]], dtype=float)]
    result = reduce_stack(stack, fig_type)
    if fig_type == 1:
        np.testing.assert_allclose(result, [[10*np.log10(10000.001/2), 10*np.log10(5.5), 10*np.log10((0.001+10**(-0.1))/2)]], equal_nan=True)
    else:
        np.testing.assert_allclose(np.ravel(result), [10*np.log10((10011.002+10**(-0.1))/6)])


def test_nonfinite_values_are_not_effective_echoes():
    result = reduce_stack([np.array([[[np.nan, 0, -30], [40, np.inf, -1]]])], 2)
    np.testing.assert_allclose(result, [[40, 0, 10*np.log10((0.001+10**(-0.1))/2)]], equal_nan=True)


def test_non_dbz_and_horizontal_dbz_keep_existing_arithmetic_mean():
    stack = [np.array([[[-30, 0, -30], [40, 10, -1]]], dtype=float)]
    np.testing.assert_allclose(reduce_stack(stack, 2, "rh"), [[5, 5, -15.5]])
    np.testing.assert_allclose(reduce_stack(stack, 0), stack[0][0])


@pytest.mark.parametrize("fig_type", [1, 2, 5, 8])
def test_no_echo_gaps_survive_default_interpolation_and_smoothing(fig_type):
    result = apply_postprocess("dbz", np.array([20, np.nan, 40.]),
                               {"missing_value_policy": "interpolate", "sigma": 1}, fig_type=fig_type)
    assert np.isnan(result[1])
    assert np.isfinite(result[[0, 2]]).all()


# 2026-11-01：绘图只读 processed_v3，测试数据写成 v3 极坐标切片。
def write_layer(root, variable, hour, field):
    from v3_fixtures import save_v3

    save_v3(root, "member", f"{variable}_polar", field, time_hour=hour, height_m=3000)


@pytest.mark.parametrize("fig_type", [1, 2, 8])
def test_prepare_data_handles_streaming_paths_and_sample_count_weighting(tmp_path, fig_type):
    # 时间0的半径0有一个有限样本，时间1有两个；负dBZ转成小Z并计数，NaN排除。
    write_layer(tmp_path, "dbz", 0, np.array([[40, -30, -30], [np.nan, -30, -30]], dtype=float))
    write_layer(tmp_path, "dbz", 1, np.array([[10, 10, -30], [10, 10, -30]], dtype=float))
    payloads, _ = prepare_variable_data(
        member_name="member", domain=3, variables={"dbz": {"plot_type": "contourf"}},
        fig_type=fig_type, data_root=tmp_path,
        selection={"times": [0, 1], "height": [3000], "angle": [0, 360], "radius": [0, 2]},
        polar_dir_style="auto", variable_type_registry={"dbz": "3d"},
    )
    if fig_type == 1:
        np.testing.assert_allclose(payloads["dbz"]["values"],
                                   [[10*np.log10(10020/3), 10*np.log10(20.002/4), -30]], equal_nan=True)
    elif fig_type == 2:
        np.testing.assert_allclose(payloads["dbz"]["values"], [[40, -30, -30], [10, 10, -30]], equal_nan=True)
    else:
        np.testing.assert_allclose(payloads["dbz"]["values"], [[10*np.log10(10000.004/5), 10*np.log10(40.002/6)]])


def test_normalized_request_records_new_method_and_changes_hash():
    service = PlotService()
    request = PlotRequest(fig_type=2, variables=[VariableConfig(name="dbz"), VariableConfig(name="rh")])
    before = service.plot_id(request)
    service._apply_fig_defaults(request)
    config = request.variables[0]
    assert config.missing_value_policy == "mask"
    assert config.extra["reflectivity_mean"] == "linear_z_mean_v1"
    assert "dbz_clip_min" not in config.extra
    assert config.extra["reflectivity_denominator"] == "finite_samples"
    assert "echo_threshold_operator" not in config.extra
    assert request.variables[1].missing_value_policy == "interpolate"
    assert service.plot_id(request) != before
    restored = PlotRequest(**request.model_dump())
    service._apply_fig_defaults(restored)
    assert restored.model_dump() == request.model_dump()


def test_horizontal_request_does_not_get_axis_mean_defaults():
    request = PlotRequest(fig_type=0, variables=[VariableConfig(name="dbz")])
    PlotService()._apply_fig_defaults(request)
    assert request.variables[0].extra == {}
    assert request.variables[0].missing_value_policy == "interpolate"


def test_dbz_default_colormap_is_precip3():
    from wrf_webvis.app.core.registry import VARIABLE_REGISTRY
    assert VARIABLE_REGISTRY["dbz"].default_cmap == "cmaps.precip3_16lev"
