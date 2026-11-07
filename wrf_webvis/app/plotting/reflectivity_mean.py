"""dbz 轴平均：所有有限dBZ转线性Z求均值，再转回dBZ，缺测排除。"""
from __future__ import annotations

import numpy as np

from ..core.registry import resolve_variable_name

METHOD = "linear_z_mean_v1"


def uses_echo_mean(variable: str, fig_type: int | None) -> bool:
    return fig_type in (1, 2, 5, 8) and resolve_variable_name(variable).base_name == "dbz"


def echo_totals(values: np.ndarray, axis=None) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(values, dtype=float)
    valid = np.isfinite(values)
    safe = np.where(valid, values, 0.0)
    linear_z = np.where(valid, 10 ** (safe / 10), 0.0)
    return np.sum(linear_z, axis=axis), np.sum(valid, axis=axis)


def finish_echo_mean(totals: np.ndarray, counts: np.ndarray) -> np.ndarray:
    mean_z = np.divide(totals, counts, out=np.full(np.shape(totals), np.nan), where=counts > 0)
    return 10 * np.log10(mean_z)


def echo_mean(values: np.ndarray, axis=None) -> np.ndarray:
    return finish_echo_mean(*echo_totals(values, axis=axis))
