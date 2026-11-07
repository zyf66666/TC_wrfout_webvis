from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


def test_v3_package_imports_and_plans_without_v2_tree(tmp_path):
    source = Path(__file__).resolve().parents[1] / "wrf_processing_v3"
    package_root = tmp_path / "处理数据_v3"
    shutil.copytree(source, package_root / "wrf_processing_v3")
    script = """
import importlib
from pathlib import Path
import numpy as np
from netCDF4 import Dataset
from wrf_processing_v3.processing_manager import ProcessingManager
from wrf_processing_v3.register import build_registry
from wrf_processing_v3.request import Request, Task
from wrf_processing_v3.storage import NetCDFStorage
import wrf_processing_v3.context
import wrf_processing_v3.executor
import wrf_processing_v3.wrf_reader
import wrf_processing_v3.variables.theta_anomaly
import wrf_processing_v3.variables.polar

request = Request(
    experiments=("exp",), domains=(3,), times_minutes=(0,),
    heights_m=(100,), variables=("theta_anomaly",),
    source_root=Path("/missing"), output_root=Path("/tmp/standalone-v3-test"),
    dry_run=True, options={
        "time_origin": "2023-09-01_00:00:00",
        "theta_environment_domain": 2,
        "theta_environment_ring_km": [400.0, 500.0],
        "radius_resolution_km": 1.0,
    },
)
registry = build_registry(request.options, times_minutes=request.times_minutes)
for name in [*registry._scientific_registry.specs, "center", "theta1_polar", "theta_asy1w_polar"]:
    handler = registry.resolve(name).handler
    if isinstance(handler, str):
        module_name = handler.replace(":", ".").rpartition(".")[0]
        module = importlib.import_module(module_name)
        assert "处理数据_v3" in str(Path(module.__file__).resolve())
class Reader:
    pass
summary = ProcessingManager(registry=registry, reader=Reader()).process(request)
assert summary["planned"]
assert all("处理数据_v2" not in str(path) for path in __import__("wrf_processing_v3").__path__)

source = Path.cwd() / "source" / "exp" / "wrfout_d03_sample"
source.parent.mkdir(parents=True)
with Dataset(source, "w") as dataset:
    dataset.createDimension("Time", 1)
    dataset.createDimension("DateStrLen", 19)
    dataset.createDimension("south_north", 2)
    dataset.createDimension("west_east", 3)
    times = dataset.createVariable("Times", "S1", ("Time", "DateStrLen"))
    times[:] = np.asarray([list("2023-09-01_00:00:00")], dtype="S1")
    dataset.START_DATE = "2023-09-01_00:00:00"
    dataset.DX = 1000.0
    dataset.DY = 1000.0
    for name, value in (("U10", 3.0), ("V10", 4.0)):
        field = dataset.createVariable(name, "f4", ("Time", "south_north", "west_east"))
        field[:] = value
actual = Request(
    experiments=("exp",), domains=(3,), times_minutes=(0,),
    heights_m=(100,), variables=("wind_speed_10",),
    source_root=Path.cwd() / "source", output_root=Path.cwd() / "out",
)
result = ProcessingManager().process(actual)
assert result["computed"]
storage = NetCDFStorage(actual.output_root, time_origin="2023-09-01_00:00:00")
np.testing.assert_allclose(storage.load(Task("exp", 3, 0, "wind_speed_10", None)), 5.0)
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(package_root)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
