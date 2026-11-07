"""Keep web job tests away from the local application's persistent history."""

import os
from pathlib import Path
import tempfile


_job_directory = tempfile.TemporaryDirectory(prefix="wrf-webvis-test-jobs-")
os.environ["WRF_WEBVIS_JOB_DB"] = str(Path(_job_directory.name) / "jobs.sqlite")
# 2026-11-03：数据集 post 也指到临时目录，网页接口测试不会读到真实 /Volumes/land_1/post。
os.environ["WRF_WEBVIS_POST_ROOT"] = str(Path(_job_directory.name) / "post")
# 2026-11-04：网页上选的“同时运行任务数”存放位置也指到临时目录，测试不改真实设置。
os.environ["WRF_WEBVIS_PROCESSING_SETTINGS"] = str(Path(_job_directory.name) / "processing_settings.json")
# 2026-11-04：绘图自动识别网格间距时不去读外接盘上真实试验的 wrfout（见 wrf_webvis/app/services/grid_detection.py）。
os.environ["WRF_WEBVIS_PLOT_GRID_SOURCE_LOOKUP"] = "0"
