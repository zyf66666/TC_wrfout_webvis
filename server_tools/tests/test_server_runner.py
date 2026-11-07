from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
from netCDF4 import Dataset
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server_tools"))
sys.path.insert(0, str(ROOT / "处理数据_v3"))


def runner():
    spec = importlib.util.find_spec("run_server")
    assert spec is not None, "standalone server runner has not been implemented"
    import run_server
    return run_server


def wrf_file(path, hours=(0,), start="2023-09-01_00:00:00", modified=1000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with Dataset(path, "w") as ds:
        ds.createDimension("Time", len(hours))
        ds.createDimension("DateStrLen", 19)
        ds.createDimension("y", 2)
        ds.createDimension("x", 2)
        ds.createVariable("Times", "S1", ("Time", "DateStrLen"))[:] = np.array(
            [list(f"2023-09-01_{hour:02d}:00:00") for hour in hours], dtype="S1"
        )
        ds.START_DATE = start
        ds.DX = ds.DY = 1000.0
        for name in ("P", "PB", "T", "QVAPOR", "PH", "PHB", "U", "V", "XLAT", "XLONG", "QRAIN"):
            ds.createVariable(name, "f4", ("Time", "y", "x"))[:] = 1
    os.utime(path, (modified, modified))
    return path


def config(tmp_path, **extra):
    import server_config
    settings = {name: getattr(server_config, name) for name in dir(server_config) if name.isupper()}
    settings.update(OUTPUT_ROOT=tmp_path / "out", STABILITY_CHECK_SECONDS=0, MIN_FILE_AGE_SECONDS=0)
    settings.update(extra)
    return SimpleNamespace(**settings)


def test_discovery_excludes_latest_but_retains_all_records_of_closed_files(tmp_path):
    module = runner()
    first = wrf_file(tmp_path / "wrfout_d03_a", (0, 1), modified=1000)
    latest = wrf_file(tmp_path / "wrfout_d03_b", (2,), modified=2000)
    snapshot = module.discover_sources(tmp_path, stability_seconds=0, min_age_seconds=0)
    assert sorted(snapshot.records) == [0, 60]
    assert snapshot.records[60] == (first, 1)
    assert any(str(latest) in message for message in snapshot.deferred)
    finished = module.discover_sources(tmp_path, simulation_finished=True, stability_seconds=0, min_age_seconds=0)
    assert sorted(finished.records) == [0, 60, 120]
    assert finished.origin == "2023-09-01_00:00:00"


def test_discovery_skips_unreadable_files_and_rejects_duplicate_times(tmp_path):
    module = runner()
    wrf_file(tmp_path / "wrfout_d03_a")
    (tmp_path / "wrfout_d03_bad").write_bytes(b"incomplete netcdf")
    snapshot = module.discover_sources(tmp_path, simulation_finished=True, stability_seconds=0, min_age_seconds=0)
    assert sorted(snapshot.records) == [0]
    assert snapshot.deferred
    wrf_file(tmp_path / "wrfout_d03_duplicate")
    with pytest.raises(ValueError, match="重复"):
        module.discover_sources(tmp_path, simulation_finished=True, stability_seconds=0, min_age_seconds=0)


def test_fixed_origin_survives_removal_of_initial_outputs(tmp_path):
    module = runner()
    wrf_file(tmp_path / "wrfout_d03_restart", (5,), start="2023-09-01_05:00:00")
    snapshot = module.discover_sources(tmp_path, time_origin="2023-09-01_00:00:00", simulation_finished=True, stability_seconds=0, min_age_seconds=0)
    assert sorted(snapshot.records) == [300]
    assert snapshot.origin == "2023-09-01_00:00:00"


def test_request_retains_scientific_parameters_and_height_chain(tmp_path):
    module = runner()
    cfg = config(tmp_path)
    import project_config
    assert cfg.HEIGHTS_M == project_config.HEIGHTS_M
    assert cfg.OPTIONS == project_config.OPTIONS
    request = module.build_request(cfg, "exp", tmp_path, (0, 60), "2023-09-01_00:00:00", dry_run=True)
    assert request.domains == (3,)
    assert set(request.variables) == {"slp_center", "center_geo", "layer_center", "axis_mean_max_wind", "center_slp_5km", "dbz"}
    from wrf_processing_v3.dependency_planner import DependencyPlanner
    from wrf_processing_v3.register import build_registry
    from wrf_processing_v3.variables._center_options import slp_center_algorithm, center_algorithm_options
    registry = build_registry(request.options)
    plan = DependencyPlanner(registry).plan(request)
    assert all(item.task.domain == 3 for item in plan)
    assert {item.task.height_m for item in plan if item.task.variable == "layer_center"} == set(cfg.HEIGHTS_M)
    assert {item.task.height_m for item in plan if item.task.variable in {"ua", "va"}} == {"lowest"}
    assert slp_center_algorithm(request.options, 3)[0] == "smooth_fg_float_pcc_v2"
    assert center_algorithm_options(request.options, 3)["max_iterations"] == 10


def test_dry_run_continues_other_experiments_without_writing_outputs(tmp_path, capsys):
    module = runner()
    wrf_file(tmp_path / "valid" / "wrfout_d03_a")
    cfg_path = tmp_path / "settings.py"
    cfg_path.write_text(
        "from server_config import *\n"
        f"EXPERIMENTS = {{'bad': {str(tmp_path / 'missing')!r}, 'good': {str(tmp_path / 'valid')!r}}}\n"
        f"OUTPUT_ROOT = {str(tmp_path / 'out')!r}\n"
        "STABILITY_CHECK_SECONDS = 0\nMIN_FILE_AGE_SECONDS = 0\n"
    )
    status = module.main(["--config", str(cfg_path), "--dry-run", "--simulation-finished"])
    assert status == 1
    output = capsys.readouterr().out
    assert "bad" in output and "good" in output and "待计算" in output
    assert not (tmp_path / "out").exists()


def test_manifest_rejects_parameter_changes_and_source_changes_invalidate_slices(tmp_path):
    module = runner()
    cfg = config(tmp_path)
    source = wrf_file(tmp_path / "source" / "wrfout_d03_a")
    snap = module.discover_sources(source.parent, simulation_finished=True, stability_seconds=0, min_age_seconds=0)
    state = module.prepare_state(cfg, "exp", source.parent, snap, dry_run=False)
    module.begin_time(cfg, "exp", snap, 0, state, dry_run=False)
    from wrf_processing_v3.storage import NetCDFStorage
    from wrf_processing_v3.request import Task
    storage = NetCDFStorage(cfg.OUTPUT_ROOT, time_origin=snap.origin)
    task = Task("exp", 3, 0, "axis_mean_max_wind", None)
    from wrf_processing_v3.register import build_registry
    storage.save(task, np.array([30.0]), build_registry().resolve(task.variable), metadata={"units": "m s-1"})
    assert storage.exists(task)
    os.utime(source, (3000, 3000))
    revised = module.discover_sources(source.parent, simulation_finished=True, stability_seconds=0, min_age_seconds=0)
    module.begin_time(cfg, "exp", revised, 0, state, dry_run=True)
    assert storage.exists(task)
    module.begin_time(cfg, "exp", revised, 0, state, dry_run=False)
    assert not storage.exists(task)
    cfg.OPTIONS = {**cfg.OPTIONS, "radius_km": 20}
    with pytest.raises(ValueError, match="参数"):
        module.prepare_state(cfg, "exp", source.parent, revised, dry_run=True)


def test_science_executes_incrementally_with_lowest_wind_and_continuous_layer_centers(tmp_path):
    module = runner()
    cfg = config(tmp_path, HEIGHTS_M=(100, 200))
    source = wrf_file(tmp_path / "source" / "wrfout_d03_a")
    from wrf_processing_v3.wrf_reader import NetCDFBackend
    from wrf_processing_v3.storage import NetCDFStorage
    from wrf_processing_v3.request import Task
    class Backend(NetCDFBackend):
        calls = 0
        def getvar(self, dataset, name, timeidx, **kwargs):
            self.calls += 1
            yy, xx = np.indices((301, 301), dtype=float)
            r = np.hypot(yy - 150, xx - 150)
            vt = 35 * r / 30 * np.exp(1 - r / 30)
            u = np.divide(-vt * (yy - 150), r, out=np.zeros_like(r), where=r > 0)
            v = np.divide(vt * (xx - 150), r, out=np.zeros_like(r), where=r > 0)
            bowl = 1005 - 30 * np.exp(-r ** 2 / (2 * 50 ** 2))
            return {
                "ua": np.stack([u, u * 3]), "va": np.stack([v, v * 3]),
                "slp": bowl, "pressure": np.stack([bowl - 10, bowl - 20]),
                "z": np.stack([np.full(r.shape, 100), np.full(r.shape, 200)]),
                "dbz": np.stack([np.full(r.shape, 20), np.full(r.shape, 40)]),
                "lat": 20 + yy * .01, "lon": 120 + xx * .01,
                "XLAT": 20 + yy * .01, "XLONG": 120 + xx * .01,
            }[name]
        def interpolate(self, field, z, height):
            return field[0 if height == 100 else 1]
    backend = Backend()
    assert module.process_experiment(cfg, "exp", source.parent, simulation_finished=True, dry_run=False, backend=backend)
    storage = NetCDFStorage(cfg.OUTPUT_ROOT, time_origin="2023-09-01_00:00:00")
    axis = storage.load(Task("exp", 3, 0, "axis_mean_max_wind", None))
    assert float(np.ravel(axis)[0]) == pytest.approx(35, abs=.2)
    yy, xx = np.indices((301, 301), dtype=float)
    radius = np.hypot(yy - 150, xx - 150)
    expected_pressure = np.mean((1005 - 30 * np.exp(-radius ** 2 / (2 * 50 ** 2)))[radius <= 5])
    assert float(np.ravel(storage.load(Task("exp", 3, 0, "center_slp_5km", None)))[0]) == pytest.approx(expected_pressure, abs=1e-4)
    np.testing.assert_allclose(storage.load(Task("exp", 3, 0, "center_geo", None)), [21.5, 121.5])
    for height in (100, 200):
        np.testing.assert_array_equal(storage.load(Task("exp", 3, 0, "layer_center", height)), [150, 150])
    np.testing.assert_allclose(storage.load(Task("exp", 3, 0, "dbz", 200)), 40)
    count = backend.calls
    assert module.process_experiment(cfg, "exp", source.parent, simulation_finished=True, dry_run=False, backend=backend)
    assert backend.calls == count
    # An interrupted slice must be filled without re-reading completed source fields.
    with Dataset(storage.path_for(Task("exp", 3, 0, "axis_mean_max_wind", None)), "r+") as ds:
        ds["completed"][0] = 0
    assert module.process_experiment(cfg, "exp", source.parent, simulation_finished=True, dry_run=False, backend=backend)
    assert storage.exists(Task("exp", 3, 0, "axis_mean_max_wind", None))
    assert backend.calls == count


def test_archive_is_standalone_and_contains_only_delivery_files(tmp_path):
    module = runner()
    import build_bundle
    archive = build_bundle.build_bundle(tmp_path / "wrf_server_tools.tar.gz")
    import tarfile
    with tarfile.open(archive) as tf:
        names = tf.getnames()
        assert "wrf_server_tools/run_server.py" in names
        assert "wrf_server_tools/wrf_processing_v3/variables/slp_center.py" in names
        assert not any("._" in name or "__pycache__" in name or ".ssh" in name for name in names)
        tf.extractall(tmp_path / "unpacked")
    result = subprocess.run([sys.executable, str(tmp_path / "unpacked/wrf_server_tools/run_server.py"), "--help"], cwd=tmp_path, env={**os.environ, "PYTHONPATH": ""}, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "--simulation-finished" in result.stdout
    source = wrf_file(tmp_path / "source" / "wrfout_d03_a")
    cfg_path = tmp_path / "settings.py"
    cfg_path.write_text(
        "from server_config import *\n"
        f"EXPERIMENTS = {{'exp': {str(source.parent)!r}}}\n"
        f"OUTPUT_ROOT = {str(tmp_path / 'out')!r}\n"
        "STABILITY_CHECK_SECONDS = 0\nMIN_FILE_AGE_SECONDS = 0\n"
    )
    standalone = tmp_path / "unpacked/wrf_server_tools"
    env = {**os.environ, "PYTHONPATH": ""}
    result = subprocess.run([sys.executable, str(standalone / "run_server.py"), "--dry-run", "--simulation-finished", "--config", str(cfg_path)], cwd=tmp_path, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "待计算" in result.stdout
    assert not (tmp_path / "out").exists()
    # Execute a genuine NetCDF-backed computation entirely from the unpacked
    # module, without repo modules on PYTHONPATH or synthetic reader injection.
    with Dataset(source, "r+") as ds:
        ds.createVariable("U10", "f4", ("Time", "y", "x"))[:] = 3
        ds.createVariable("V10", "f4", ("Time", "y", "x"))[:] = 4
    script = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import run_server
assert str(Path(run_server.__file__).parent) == sys.argv[1]
cfg = run_server.load_config(sys.argv[2])
cfg.VARIABLES = ("wind_speed_10",)
assert run_server.process_experiment(cfg, "exp", cfg.EXPERIMENTS["exp"], simulation_finished=True, dry_run=False)
import wrf_processing_v3
assert str(Path(wrf_processing_v3.__file__).parent.parent) == sys.argv[1]
from wrf_processing_v3.storage import NetCDFStorage
from wrf_processing_v3.request import Task
import numpy as np
store = NetCDFStorage(cfg.OUTPUT_ROOT, time_origin="2023-09-01_00:00:00")
np.testing.assert_allclose(store.load(Task("exp", 3, 0, "wind_speed_10", None)), 5)
from wrf_processing_v3.register import build_registry
registry = build_registry()
for name, value in (("axis_mean_max_wind", 30), ("center_slp_5km", 990)):
    store.save(Task("exp", 3, 0, name, None), np.array([value]), registry.resolve(name))
'''
    result = subprocess.run([sys.executable, "-c", script, str(standalone), str(cfg_path)], cwd=tmp_path, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    plot_script = '''
import sys
sys.path.insert(0, sys.argv[1])
import run_server
assert run_server.main(["--config", sys.argv[2], "--plot-only"]) == 0
assert "wrf" not in sys.modules
'''
    result = subprocess.run([sys.executable, "-c", plot_script, str(standalone), str(cfg_path)], cwd=tmp_path, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "out/exp/figures/intensity.png").is_file()


def test_files_changed_during_scan_or_too_recent_are_deferred(tmp_path, monkeypatch):
    module = runner()
    source = wrf_file(tmp_path / "wrfout_d03_a")
    def change_during_observation(seconds):
        os.utime(source, (4000, 4000))
    monkeypatch.setattr(module.time, "sleep", change_during_observation)
    snapshot = module.discover_sources(tmp_path, simulation_finished=True, stability_seconds=1, min_age_seconds=0)
    assert not snapshot.records
    assert any("变化" in text for text in snapshot.deferred)
    os.utime(source, None)
    snapshot = module.discover_sources(tmp_path, simulation_finished=True, stability_seconds=0, min_age_seconds=120)
    assert not snapshot.records
    assert any("近期" in text for text in snapshot.deferred)


def test_modified_source_during_execution_invalidates_written_results(tmp_path):
    module = runner()
    cfg = config(tmp_path, VARIABLES=("wind_speed_10",), HEIGHTS_M=(100,))
    source = wrf_file(tmp_path / "source" / "wrfout_d03_a")
    from wrf_processing_v3.wrf_reader import NetCDFBackend
    class ChangingBackend(NetCDFBackend):
        def getvar(self, dataset, name, timeidx, **kwargs):
            os.utime(source, (4000, 4000))
            return np.full((2, 2), 3 if name == "U10" else 4)
    assert not module.process_experiment(cfg, "exp", source.parent, simulation_finished=True, dry_run=False, backend=ChangingBackend())
    from wrf_processing_v3.request import Task
    from wrf_processing_v3.storage import NetCDFStorage
    storage = NetCDFStorage(cfg.OUTPUT_ROOT, time_origin="2023-09-01_00:00:00")
    assert not storage.exists(Task("exp", 3, 0, "wind_speed_10", None))


def test_no_new_complete_files_does_not_overwrite_existing_intensity(tmp_path):
    module = runner()
    cfg = config(tmp_path)
    source = wrf_file(tmp_path / "source" / "wrfout_d03_a")
    assert module.process_experiment(cfg, "exp", source.parent, dry_run=True)
    assert not cfg.OUTPUT_ROOT.exists()


def test_old_python_reports_version_before_loading_config_or_v3(tmp_path, monkeypatch, capsys):
    module = runner()
    monkeypatch.setattr(module.sys, "version_info", (3, 9, 18))
    status = module.main(["--config", str(tmp_path / "not-created.py"), "--dry-run"])
    assert status == 1
    output = capsys.readouterr().out
    assert "Python 3.10" in output
    assert "3.9.18" in output
    assert module.sys.executable in output
    assert "conda run -n wrf-server-tools" in output
    assert "not-created" not in output


def test_missing_pkg_resources_reports_setuptools_compatibility_fix(monkeypatch):
    module = runner()
    original_import = module.importlib.import_module
    def import_without_legacy_resources(name, *args, **kwargs):
        if name == "wrf":
            raise ModuleNotFoundError("No module named 'pkg_resources'", name="pkg_resources")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(module.importlib, "import_module", import_without_legacy_resources)
    with pytest.raises(RuntimeError, match="setuptools<82"):
        module.check_environment()


@pytest.mark.parametrize("wrf_version,netcdf_version,should_fail", [
    ("1.3.4.1", "1.7.3", True),
    ("1.3.4.1", "1.7.4", True),
    ("1.3.4.1", "1.7.2", True),
    ("1.3.4.1", "1.6.5", False),
    ("1.4.0", "1.7.4", True),
    ("1.4.1", "1.7.4", False),
    ("1.4.2", "1.7.4", False),
])
def test_wrf_netcdf_incompatibility_is_detected_before_source_processing(monkeypatch, wrf_version, netcdf_version, should_fail):
    module = runner()
    original_import = module.importlib.import_module
    def import_versions(name, *args, **kwargs):
        if name in ("wrf", "netCDF4"):
            if name == "wrf":
                return SimpleNamespace(__version__=wrf_version)
            dataset_type = type("Dataset", (), {"__iter__": lambda self: iter(())} if netcdf_version != "1.6.5" else {})
            return SimpleNamespace(__version__=netcdf_version, Dataset=dataset_type)
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(module.importlib, "import_module", import_versions)
    if should_fail:
        with pytest.raises(RuntimeError, match="netcdf4=1.6.5"):
            module.check_environment()
    else:
        module.check_environment()
    # Plotting never imports wrf and remains available for completed products.
    module.check_environment(plot_only=True)
