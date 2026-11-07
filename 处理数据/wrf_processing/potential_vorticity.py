"""Native-grid PV for the idealized experiments used by calc_pv.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .processing_utils import prepare_wrf_import


# 功能说明：从 WRF 文件取出指定变量在一个时次的全部空间格点，检查维度、缺测值和无穷值后返回普通数组。
def _read_field(dataset: Any, name: str, timeidx: int, ndim: int) -> np.ndarray:
    if name not in dataset.variables:
        raise ValueError(f"PV requires WRF variable {name}")
    variable = dataset.variables[name]
    index = tuple(timeidx if dim == "Time" else slice(None) for dim in variable.dimensions)
    values = np.ma.asarray(variable[index])
    if values.ndim != ndim:
        raise ValueError(f"PV {name} must have {ndim} spatial dimensions; got {values.shape}")
    if np.ma.getmask(values).any() or not np.isfinite(values.data).all():
        raise ValueError(f"PV {name} contains missing or non-finite values")
    return np.asarray(values.data)


# 功能说明：读取交错网格的风、温度、气压和科氏参数，计算原生垂直层的位涡，并返回位涡数组及来源说明。
def calculate_pvo(
    dataset: Any,
    *,
    source_file: str | Path,
    timeidx: int = 0,
    wrfinput_d01_path: str | Path | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Return native-level PV in PVU and its calculation provenance.

    Preserve the idealized script's unity map factors. Use the source's F
    field when available; otherwise require a constant f-plane field from
    the same experiment's wrfinput_d01. Never use processed, destaggered or
    storm-relative winds here. Height interpolation happens after this call.
    """
    prepare_wrf_import()
    from netCDF4 import Dataset
    from wrf import pvo

    source_file = Path(source_file)
    u = _read_field(dataset, "U", timeidx, 3)
    v = _read_field(dataset, "V", timeidx, 3)
    theta = _read_field(dataset, "T", timeidx, 3) + 300.0
    pressure = _read_field(dataset, "P", timeidx, 3) + _read_field(dataset, "PB", timeidx, 3)
    nz, ny, nx = theta.shape
    if u.shape != (nz, ny, nx + 1) or v.shape != (nz, ny + 1, nx) or pressure.shape != theta.shape:
        raise ValueError(
            f"PV requires matching staggered U/V and mass-grid T/P/PB: "
            f"U={u.shape}, V={v.shape}, theta={theta.shape}, pressure={pressure.shape}"
        )
    dx, dy = float(dataset.DX), float(dataset.DY)
    if not np.isfinite([dx, dy]).all() or dx <= 0 or dy <= 0:
        raise ValueError(f"PV requires positive finite DX/DY in metres; got {dx}, {dy}")

    if "F" in dataset.variables:
        cor = _read_field(dataset, "F", timeidx, 2)
        coriolis_source = source_file
        coriolis_mode = "wrfout"
    else:
        coriolis_source = Path(wrfinput_d01_path or "wrfinput_d01").expanduser()
        if not coriolis_source.is_absolute():
            coriolis_source = source_file.parent / coriolis_source
        if not coriolis_source.is_file():
            raise FileNotFoundError(
                f"PV source {source_file} has no F and fallback wrfinput_d01 was not found: "
                f"{coriolis_source}. Set options['pvo_wrfinput_d01'] to the matching experiment's input."
            )
        with Dataset(coriolis_source, "r") as fallback:
            f_d01 = _read_field(fallback, "F", 0, 2)
        f0 = float(np.mean(f_d01, dtype=np.float64))
        if not np.allclose(f_d01, f0, rtol=1e-6, atol=1e-12):
            raise ValueError(
                f"PV fallback F must be constant for an f-plane experiment: {coriolis_source}; "
                f"range=[{f_d01.min()}, {f_d01.max()}]"
            )
        cor = np.full((ny, nx), f0, dtype=np.float64)
        coriolis_mode = "wrfinput_d01_constant"
    if cor.shape != (ny, nx):
        raise ValueError(f"PV F must match the mass grid {(ny, nx)}; got {cor.shape}")

    values = pvo(
        u, v, theta, pressure,
        np.ones(u.shape[-2:], dtype=np.float64),
        np.ones(v.shape[-2:], dtype=np.float64),
        np.ones((ny, nx), dtype=np.float64),
        cor, dx, dy, meta=False,
    )
    metadata = {
        "diagnostic": "wrf.pvo",
        "units": "PVU",
        "map_factor_mode": "unity",
        "theta_offset_k": 300.0,
        "pressure_units": "Pa",
        "coriolis_mode": coriolis_mode,
        "coriolis_source": str(coriolis_source),
        "coriolis_min_s-1": float(cor.min()),
        "coriolis_max_s-1": float(cor.max()),
        "missing_value_policy": "preserve_after_height_interpolation",
    }
    return np.asarray(values), metadata
