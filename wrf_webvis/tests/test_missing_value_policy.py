import numpy as np
import pytest

from wrf_webvis.app.plotting.draw_variables import apply_postprocess


# 功能说明：对含缺测值数组应用掩码策略，确认缺测保留且原数组不被修改。
def test_mask_keeps_missing_values_and_does_not_change_input():
    source = np.array([1., np.nan, 3.])
    result = apply_postprocess('temp', source, {'missing_value_policy': 'mask'})
    np.testing.assert_allclose(result, source, equal_nan=True)
    assert np.isnan(source[1])


@pytest.mark.parametrize('missing', [np.nan, np.inf, -np.inf])
# 功能说明：向错误策略传入 NaN 和无穷值，确认立即拒绝输入。
def test_error_policy_rejects_nonfinite_values(missing):
    with pytest.raises(ValueError, match='temp'):
        apply_postprocess('temp', np.array([1., missing, 3.]), {'missing_value_policy': 'error'})


# 功能说明：插补时间序列中的空缺，确认使用邻近有效值而不会凭空填零。
def test_interpolation_fills_time_series_without_inventing_zero():
    result = apply_postprocess('temp', np.array([1., np.nan, 3.]), {'missing_value_policy': 'interpolate'})
    np.testing.assert_allclose(result, [1., 2., 3.])


# 功能说明：整块数据均缺失时尝试插补，确认无法估计而报错。
def test_interpolation_rejects_a_field_without_valid_samples():
    with pytest.raises(ValueError, match='finite|有效'):
        apply_postprocess('temp', np.full((2, 2), np.nan), {'missing_value_policy': 'interpolate'})


# 功能说明：把全缺失数据交给掩码策略，确认输出仍全部缺失。
def test_all_missing_mask_remains_missing():
    assert np.isnan(apply_postprocess('temp', np.full((2, 2), np.nan), {'missing_value_policy': 'mask'})).all()


# 功能说明：平滑含空洞的数据，确认空洞不被填满也不扩散到有效格点。
def test_mask_smoothing_keeps_holes_without_spreading_them():
    source = np.full((5, 5), 4.)
    source[2, 2] = np.nan
    result = apply_postprocess('temp', source, {'missing_value_policy': 'mask', 'sigma': 1, 'scale_factor': 2})
    assert np.isnan(result[2, 2])
    np.testing.assert_allclose(result[np.isfinite(source)], 8.)


# 功能说明：传入带掩码数组，确认被遮住的样本不会作为有效数值泄露。
def test_masked_array_missing_samples_are_not_exposed():
    source = np.ma.array([1., 999., 3.], mask=[False, True, False])
    result = apply_postprocess('temp', source, {'missing_value_policy': 'mask'})
    assert np.isnan(result[1])


# 功能说明：用稀疏二维有效点插补，确认结果只依据现有样本。
def test_sparse_two_dimensional_interpolation_uses_available_samples():
    source = np.full((3, 3), np.nan)
    source[1, 1] = 7.
    np.testing.assert_allclose(apply_postprocess('temp', source, {}), 7.)
