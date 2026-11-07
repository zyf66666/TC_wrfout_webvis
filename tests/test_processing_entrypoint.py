from __future__ import annotations

import importlib
import sys
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSING_ROOT = PROJECT_ROOT / "处理数据"


# 功能说明：临时把处理脚本目录加入模块搜索路径并重新导入入口模块，随后恢复原来的搜索路径。
def _load_entrypoint():
    sys.path.insert(0, str(PROCESSING_ROOT))
    sys.modules.pop("process_data", None)
    try:
        return importlib.import_module("process_data")
    finally:
        sys.path.remove(str(PROCESSING_ROOT))


# 功能说明：替换用户参数中的实验、时次、高度和变量，确认入口据此组成对应的处理请求。
def test_user_parameter_file_builds_a_processing_request(tmp_path: Path) -> None:
    module = _load_entrypoint()
    module.EXPERIMENTS_ROOT = tmp_path
    module.WRFOUT_TEMPLATE = "{root}/{experiment}/run"
    module.PROCESSED_ROOT = tmp_path / "processed"
    module.TIMES = [0, 3, 6]
    module.TIMES_BY_EXPERIMENT = {}
    module.HEIGHTS = [1000, 2000]
    module.VARIABLES = ["slp", "dbz_polar"]

    request = module.build_request("WRF_0_land", 3)

    assert request.member_name == "WRF_0_land"
    assert request.wrfout_root == tmp_path / "WRF_0_land" / "run"
    assert request.variables == ["slp", "dbz_polar"]
    assert request.times.values == [0, 3, 6]
    assert request.heights.values == [1000, 2000]


# 功能说明：为某个实验单独给出时次表，确认它覆盖通用的时间列表。
def test_user_parameter_file_can_set_times_per_experiment(tmp_path: Path) -> None:
    module = _load_entrypoint()
    module.EXPERIMENTS_ROOT = tmp_path
    module.WRFOUT_TEMPLATE = "{root}/{experiment}/run"
    module.PROCESSED_ROOT = tmp_path / "processed"
    module.TIMES = [0, 1, 2, 3]
    module.TIMES_BY_EXPERIMENT = {"WRF_135_land": [0, 1, 2]}

    request = module.build_request("WRF_135_land", 3)

    assert request.times.values == [0, 1, 2]


# 功能说明：导入处理引擎并检查文件位置，确认计算代码留在处理包而没有混入网页服务目录。
def test_processing_engine_lives_outside_the_web_package() -> None:
    sys.path.insert(0, str(PROCESSING_ROOT))
    try:
        module = importlib.import_module("wrf_processing.processing_manager")
    finally:
        sys.path.remove(str(PROCESSING_ROOT))

    assert module.ProcessingManager.__module__.startswith("wrf_processing")
    web_services = PROJECT_ROOT / "wrf_webvis/app/services"
    assert not (web_services / "processing_manager.py").exists()
    assert not (web_services / "processing_utils.py").exists()


# 功能说明：用小型经纬度网格调用保存函数，确认纬度和经度分别写到预期的网格缓存文件。
def test_processing_writes_grid_coordinates_for_plotting(tmp_path: Path) -> None:
    sys.path.insert(0, str(PROCESSING_ROOT))
    try:
        from wrf_processing.processing_utils import save_grid_coordinates
        from wrf_webvis.app.core.paths import PathManager

        lat = np.arange(6, dtype=float).reshape(2, 3)
        lon = lat + 100
        outputs = save_grid_coordinates(PathManager(tmp_path), "member", 1, lat, lon)
    finally:
        sys.path.remove(str(PROCESSING_ROOT))

    assert outputs == [tmp_path / "member/d01/grid/lat.npy", tmp_path / "member/d01/grid/lon.npy"]
    np.testing.assert_array_equal(np.load(outputs[0]), lat)
    np.testing.assert_array_equal(np.load(outputs[1]), lon)


# 功能说明：创建仅含 XLAT、XLONG 的 WRF 文件，确认入口即使不处理变量也会保存轨迹绘图所需网格。
def test_entrypoint_can_save_track_grid_coordinates_without_writing_variables(tmp_path: Path) -> None:
    from netCDF4 import Dataset

    module = _load_entrypoint()
    source_root = tmp_path / "wrfout"
    source_root.mkdir()
    source = source_root / "wrfout_d03_2023-09-06_00:00:00"
    with Dataset(source, "w") as dataset:
        dataset.createDimension("Time", 1)
        dataset.createDimension("south_north", 2)
        dataset.createDimension("west_east", 3)
        lat = dataset.createVariable("XLAT", "f4", ("Time", "south_north", "west_east"))
        lon = dataset.createVariable("XLONG", "f4", ("Time", "south_north", "west_east"))
        lat[0] = np.arange(6, dtype=np.float32).reshape(2, 3)
        lon[0] = lat[0] + 100

    module.PROCESSED_ROOT = tmp_path / "processed"
    outputs = module.ensure_grid_coordinates("WRF_0_sea", 3, source_root)

    assert outputs == [
        tmp_path / "processed/WRF_0_sea/d03/grid/lat.npy",
        tmp_path / "processed/WRF_0_sea/d03/grid/lon.npy",
    ]
    np.testing.assert_array_equal(np.load(outputs[0]), np.arange(6, dtype=np.float32).reshape(2, 3))


# 功能说明：从单个中心记录和网格反查经纬度，确认按时次写入轨迹坐标且原中心文件保持不变。
def test_entrypoint_writes_per_time_track_coordinates_without_replacing_center_file(tmp_path: Path) -> None:
    from netCDF4 import Dataset

    module = _load_entrypoint()
    source_root = tmp_path / "wrfout"
    source_root.mkdir()
    source = source_root / "wrfout_d03_2023-09-06_00:00:00"
    with Dataset(source, "w") as dataset:
        dataset.createDimension("Time", 1)
        dataset.createDimension("south_north", 2)
        dataset.createDimension("west_east", 3)
        lat = dataset.createVariable("XLAT", "f4", ("Time", "south_north", "west_east"))
        lon = dataset.createVariable("XLONG", "f4", ("Time", "south_north", "west_east"))
        lat[0] = np.arange(6, dtype=np.float32).reshape(2, 3)
        lon[0] = lat[0] + 100

    module.PROCESSED_ROOT = tmp_path / "processed"
    center = tmp_path / "processed/WRF_0_sea/center/d03/slp/center.txt"
    center.parent.mkdir(parents=True)
    center.write_text("0 1 2 nan nan 990\n", encoding="utf-8")
    output = module.ensure_track_center_coordinates("WRF_0_sea", 3, source_root)

    assert output == tmp_path / "processed/WRF_0_sea/d03/track/center_latlon.txt"
    np.testing.assert_allclose(np.atleast_2d(np.loadtxt(output)), [[0, 5, 105]])
    assert "nan nan" in center.read_text(encoding="utf-8")
