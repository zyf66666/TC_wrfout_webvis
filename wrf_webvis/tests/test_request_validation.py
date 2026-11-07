import pytest
from fastapi.testclient import TestClient
from wrf_webvis.app.main import app
from wrf_webvis.app.core.schemas import PlotRequest, SelectionSpec
from wrf_webvis.app.core.selection import expand_selection


@pytest.mark.parametrize('selection', [
    {'start': 0, 'end': 2, 'step': 0},
    {'start': 0, 'end': 2, 'step': -1},
    {'start': 2, 'end': 0, 'step': 1},
    {'start': 0},
    {'values': [float('nan')]},
    {'start': 0, 'end': 2, 'step': float('inf')},
    {'values': [0], 'start': 0, 'end': 2},
])
# 功能说明：逐个提供零步长、方向错误、缺端点、NaN/无穷和混用范围列表的选择，确认都会拒绝。
def test_invalid_selection_is_rejected(selection):
    with pytest.raises(ValueError):
        expand_selection(SelectionSpec(**selection))


# 功能说明：给出超过一万个值的范围，确认模型在展开前就报出数量上限。
def test_selection_size_is_limited_before_expansion():
    with pytest.raises(ValueError, match='10000'):
        SelectionSpec(start=0, end=10001, step=1)


# 功能说明：确认递减整数范围和 0.1 步长的小数范围能正确展开。
def test_valid_descending_and_fractional_selections():
    assert expand_selection(SelectionSpec(start=2, end=0, step=-1)) == [2, 1, 0]
    assert expand_selection(SelectionSpec(start=0, end=0.3, step=0.1)) == pytest.approx([0, .1, .2, .3])


@pytest.mark.parametrize('payload', [
    {'row': 0}, {'col': -1}, {'dpi': 0}, {'dpi': -1},
    {'figsize': [-1, 2]}, {'figsize': [2]}, {'figsize': [float('inf'), 2]},
    {'domain': 0}, {'output_format': 'exe'},
    {'variables': [{'name': 'slp', 'missing_value_policy': 'typo'}]},
])
# 功能说明：逐个提供非法行列、dpi、画布、域、格式和缺测策略，确认 PlotRequest 拒绝它们。
def test_invalid_plot_parameters_are_rejected(payload):
    with pytest.raises(ValueError):
        PlotRequest(**payload)


# 功能说明：向异步绘图接口提交零步长选择，确认请求校验返回 422 而不创建任务。
def test_invalid_http_request_fails_before_job_creation():
    response = TestClient(app, client=("127.0.0.1", 50000)).post('/api/plots/jobs', json={'time': {'start': 0, 'end': 2, 'step': 0}})
    assert response.status_code == 422
