#!/usr/bin/env python3
"""正式计算前的环境自检（只读 wrfout，只在输出目录写一个临时小文件后删除）。

    python tc_hourly_sc/check_env.py WRF_0_sea_P1 [--root .] [--run-subdir run] [--out tc_hourly_out]
"""

from __future__ import annotations

import os

os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")

import argparse
import fcntl
from pathlib import Path
import sys

OK, BAD, WARN = "[ 通过 ]", "[ 失败 ]", "[ 注意 ]"
REQUIRED_WRF_VARS = ("Times", "P", "PB", "T", "QVAPOR", "PH", "PHB", "U", "V", "XLAT", "XLONG")  # slp 诊断要 P/PB/T/QVAPOR/PH/PHB


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("experiments", nargs="*")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--run-subdir", default="run")
    parser.add_argument("--out", type=Path, default=Path("tc_hourly_out"))
    args, _ = parser.parse_known_args(argv)
    problems = 0

    print(f"Python {sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info < (3, 10):
        print(f"{BAD} 需要 Python ≥ 3.10（管线用了 3.10 语法）"); problems += 1

    try:
        import numpy as np
        print(f"{OK} numpy {np.__version__}")
    except ImportError:
        print(f"{BAD} 缺 numpy"); return 1

    try:
        import netCDF4
        print(f"{OK} netCDF4 {netCDF4.__version__}（HDF5 {netCDF4.__hdf5libversion__}, netcdf-c {netCDF4.__netcdf4libversion__}）")
    except ImportError:
        print(f"{BAD} 缺 netCDF4 → conda install -c conda-forge netcdf4  （或 pip install netCDF4）"); problems += 1
        netCDF4 = None

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "pipeline"))
        from wrf_processing_v3.wrf_reader import prepare_wrf_import

        prepare_wrf_import()  # 与管线相同：补 numpy 旧别名、OMP_NUM_THREADS=1
        import wrf
        from wrf import getvar, interplevel, destagger  # noqa: F401
        print(f"{OK} wrf-python {getattr(wrf, '__version__', '?')}")
    except Exception as exc:  # ImportError 或旧版 wrf-python 与新 numpy 的兼容错误
        print(f"{BAD} wrf-python 不可用：{exc}\n"
              "        → conda install -c conda-forge wrf-python  （超算不能联网时，见 README“离线安装”）")
        problems += 1

    root = args.root.resolve()
    for experiment in args.experiments:
        run_dir = root / experiment / args.run_subdir if args.run_subdir else root / experiment
        files = sorted(run_dir.glob("**/wrfout_d03*")) if run_dir.is_dir() else []
        if not files:
            print(f"{BAD} {run_dir} 下没有 wrfout_d03* 文件"); problems += 1
            continue
        size_gb = sum(f.stat().st_size for f in files) / 1024**3
        print(f"{OK} {experiment}：{len(files)} 个 wrfout_d03 文件，共 {size_gb:.1f} GB")
        print(f"        第一个 {files[0].name}，最后一个 {files[-1].name}")
        if netCDF4 is None:
            continue
        try:
            with netCDF4.Dataset(str(files[0])) as ds:
                missing = [name for name in REQUIRED_WRF_VARS if name not in ds.variables]
                ny, nx = len(ds.dimensions["south_north"]), len(ds.dimensions["west_east"])
                nz = len(ds.dimensions["bottom_top"])
                nt = len(ds.dimensions["Time"])
                dx = float(getattr(ds, "DX", float("nan"))) / 1000.0
                print(f"        网格 {nx}×{ny}×{nz}，DX = {dx:g} km，每个文件 {nt} 个时次，"
                      f"START_DATE = {getattr(ds, 'START_DATE', '?')}")
                if missing:
                    print(f"{BAD} 缺变量：{', '.join(missing)}"); problems += 1
                if abs(dx - 1.0) > 1e-6:
                    print(f"{WARN} DX 不是 1 km：管线对 d03 按 1 km 计算半径（common.DEFAULT_DOMAIN_CONFIG），"
                          "请先告诉 Claude 再算")
                    problems += 1
        except Exception as exc:
            print(f"{BAD} 打不开 {files[0]}：{exc}"); problems += 1

    # 输出目录可写、NetCDF4 能写、flock 是否支持
    out = args.out if args.out.is_absolute() else Path.cwd() / args.out
    try:
        out.mkdir(parents=True, exist_ok=True)
        probe = out / ".check_env_probe.nc"
        if netCDF4 is not None:
            with netCDF4.Dataset(str(probe), "w") as ds:
                ds.createDimension("record", None)
                ds.createVariable("data", "f4", ("record",), zlib=True, fletcher32=True)[0] = 1.0
        else:
            probe.write_bytes(b"x")
        with (out / ".check_env_probe.lock").open("a+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                print(f"{OK} 输出目录可写、支持文件锁：{out}")
            except OSError as exc:
                print(f"{WARN} 输出目录可写，但不支持 flock（{exc}）；run_hourly.py 会自动跳过文件锁，"
                      "同一试验不要同时开两个进程")
        for name in (".check_env_probe.nc", ".check_env_probe.lock"):
            try:
                (out / name).unlink()
            except OSError:
                pass
    except Exception as exc:
        print(f"{BAD} 输出目录 {out} 不可写或 NetCDF4 写入失败：{exc}"); problems += 1

    print("\n自检结论：" + ("全部通过，可以开始计算。" if problems == 0 else f"有 {problems} 个问题，先解决再算。"))
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
