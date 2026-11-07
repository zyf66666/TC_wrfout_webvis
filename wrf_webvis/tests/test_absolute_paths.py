from pathlib import Path

from wrf_webvis.app.core.config import DEFAULT_PLOT_PROCESSED_ROOT, DEFAULT_PROCESSED_ROOT
from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.schemas import PlotRequest, ProcessingRequest

REPO_ROOT = Path(__file__).resolve().parents[2]


# 功能说明：切换到临时工作目录后创建默认请求，确认数据根目录仍是项目的绝对预处理目录。
def test_default_data_paths_do_not_depend_on_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert DEFAULT_PROCESSED_ROOT == Path('/Volumes/land_1/processed_v2')
    assert DEFAULT_PLOT_PROCESSED_ROOT == Path('/Volumes/land_1/processed_v3')
    assert PlotRequest().data_root == DEFAULT_PLOT_PROCESSED_ROOT
    request = ProcessingRequest(member_name='sample', domain=3, variables=[])
    assert request.processed_root == DEFAULT_PROCESSED_ROOT
    assert request.data_root.is_absolute()


# 功能说明：传入相对的数据、原始文件和中心路径，确认它们都按项目根目录解释。
def test_explicit_relative_request_paths_are_anchored_to_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    request = PlotRequest(data_root='data', wrfout_file='input/wrfout',
                          format={'common': {'center_path': 'centers/center.txt'}})
    assert request.data_root == REPO_ROOT / 'data'
    assert request.wrfout_file == REPO_ROOT / 'input/wrfout'
    assert Path(request.format.common['center_path']) == REPO_ROOT / 'centers/center.txt'
    processing = ProcessingRequest(member_name='sample', domain=3, variables=[], processed_root='cache')
    assert processing.processed_root == REPO_ROOT / 'cache'


# 功能说明：以相对根目录建立路径管理器，确认生成的缓存路径落在项目根目录下。
def test_path_manager_anchors_relative_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert PathManager('cache').processed_root == REPO_ROOT / 'cache'
