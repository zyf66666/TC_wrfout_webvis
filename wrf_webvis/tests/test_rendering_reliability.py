from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import matplotlib.pyplot as plt
import numpy as np
import pytest
from matplotlib.figure import Figure

from wrf_webvis.app.plotting import draw_main
from wrf_webvis.app.plotting.draw_format import set_cb_q
from wrf_webvis.app.plotting.plot_spec import PlotSpec
from wrf_webvis.app.services.progress import JobCancelled
# 2026-11-01：绘图只读 processed_v3；按旧路径写的假数据实际写成 v3 切片。
from v3_fixtures import save_at_legacy_path


# 功能说明：构造最小二维绘图规格，供渲染失败和并发测试调用。
def config(tmp_path):
    return PlotSpec(members=['demo'], domain=3,
        figure={'row': 1, 'col': 1, 'fig_type': 0, 'save': True},
        output_path=tmp_path / 'result.png', selection={'times': [0], 'angle': [0, 360], 'radius': [0, 10]},
        variables={'slp': {'enabled': True}})


# 功能说明：在两个图窗中只给第一个图添加箭头图例，确认图例没有错误地挂到第二个图。
def test_quiver_key_belongs_to_its_source_figure():
    first, ax = plt.subplots()
    other, other_ax = plt.subplots()
    try:
        q = ax.quiver([0], [0], [1], [1])
        set_cb_q(None, q, first, colorbar_config={'enabled': False})
        assert any(type(child).__name__ == 'QuiverKey' for child in ax.get_children())
        assert not any(type(child).__name__ == 'QuiverKey' for child in other_ax.get_children())
    finally:
        plt.close(first)
        plt.close(other)


@pytest.mark.parametrize('error', [ValueError('invalid plot'), JobCancelled('canceled')])
# 功能说明：让绘图抛出普通错误或取消错误，确认 render 退出后没有遗留图窗。
def test_failed_or_canceled_drawing_closes_its_figure(tmp_path, monkeypatch, error):
    before = set(plt.get_fignums())
    # 功能说明：替代绘图函数并抛出参数化的错误，用于验证异常后图窗清理。
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(draw_main, 'draw_fig', fail)
    try:
        with pytest.raises(type(error)):
            draw_main.render(config(tmp_path))
        assert set(plt.get_fignums()) == before
    finally:
        for number in set(plt.get_fignums()) - before:
            plt.close(number)


# 功能说明：同时提交两次绘图，确认第二次在第一次释放前未进入 draw_fig。
def test_concurrent_renders_do_not_overlap(tmp_path, monkeypatch):
    first_started, second_started, release = Event(), Event(), Event()
    # 功能说明：让第一个并发绘图调用阻塞、第二个调用记录开始，用于探测两个渲染是否重叠。
    def draw(**kwargs):
        if not first_started.is_set():
            first_started.set()
            assert release.wait(3)
        else:
            second_started.set()
        raise JobCancelled('done')
    monkeypatch.setattr(draw_main, 'draw_fig', draw)
    # 功能说明：执行一次绘图并确认人为抛出的取消异常会向调用者传播。
    def run():
        with pytest.raises(JobCancelled):
            draw_main.render(config(tmp_path))
    try:
        with ThreadPoolExecutor(2) as executor:
            first = executor.submit(run)
            assert first_started.wait(2)
            second = executor.submit(run)
            try:
                assert not second_started.wait(.2)
            finally:
                release.set()
            first.result()
            second.result()
    finally:
        plt.close('all')


# 功能说明：模拟保存中断，确认原图片字节不变、临时文件被清理且图窗关闭。
def test_failed_save_preserves_previous_image_and_closes_figure(tmp_path, monkeypatch):
    data = tmp_path / 'demo/d03/slp/0h.npy'
    data.parent.mkdir(parents=True)
    save_at_legacy_path(data, np.arange(9).reshape(3, 3))
    request = config(tmp_path)
    request.data_root = tmp_path
    previous = tmp_path / 'result.png'
    previous.write_bytes(b'previous image')
    # 功能说明：先写入不完整文件再模拟保存失败，用于检查旧图片是否被保留。
    def fail_save(self, output, **kwargs):
        Path(output).write_bytes(b'incomplete')
        raise OSError('disk write failed')
    monkeypatch.setattr(Figure, 'savefig', fail_save)
    before = set(plt.get_fignums())
    try:
        with pytest.raises(OSError, match='disk write'):
            draw_main.render(request)
        assert previous.read_bytes() == b'previous image'
        assert set(plt.get_fignums()) == before
        assert sorted(p.name for p in tmp_path.iterdir()) == ['demo', 'result.png']
    finally:
        for number in set(plt.get_fignums()) - before:
            plt.close(number)


def test_progress_identifies_layout_and_save_and_can_cancel_before_save(tmp_path, monkeypatch):
    data = tmp_path / 'demo/d03/slp/0h.npy'
    data.parent.mkdir(parents=True)
    save_at_legacy_path(data, np.arange(9).reshape(3, 3))
    request = config(tmp_path)
    request.data_root = tmp_path
    messages = []

    def progress(message):
        messages.append(message)
        if message.startswith('正在保存图片'):
            raise JobCancelled('cancel before save')

    with pytest.raises(JobCancelled, match='cancel before save'):
        draw_main.render(request, progress=progress)
    assert '数据绘制完成，正在调整布局和图例' in messages
    assert not (tmp_path / 'result.png').exists()
