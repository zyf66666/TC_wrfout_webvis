"""Environmental anomalies on an equally spaced azimuth/radius grid."""
from __future__ import annotations

import numpy as np


def environmental_anomaly(field, radii_km, ring_km=(350.0, 380.0)):
    """Return anomaly, radius-weighted environment and valid ring fraction.

    Each call represents one time and height. Invalid samples retain NaN and
    are excluded from both the weighted numerator and denominator.
    """
    values = np.asarray(field, dtype=np.float64)
    radii = np.asarray(radii_km, dtype=np.float64)
    if values.ndim != 2 or radii.ndim != 1 or values.shape[1] != radii.size:
        raise ValueError("Expected (azimuth, radius) field and matching radii")
    if radii.size < 2 or not np.all(np.diff(radii) > 0):
        raise ValueError("Radii must be strictly increasing")
    if not np.allclose(np.diff(radii), np.diff(radii)[0]):
        raise ValueError("Radius weighting requires equally spaced radii")
    lo, hi = ring_km
    if lo < 0 or hi <= lo or radii[0] > lo or radii[-1] < hi:
        raise ValueError(f"Data must cover the complete {lo:g}–{hi:g} km ring")
    selected = (radii >= lo) & (radii <= hi)
    ring = values[:, selected]
    valid = np.isfinite(ring)
    weights = np.where(valid, radii[selected][None, :], 0.0)
    if weights.sum() <= 0:
        raise ValueError("Environment ring has no valid samples")
    environment = float(np.sum(np.where(valid, ring, 0.0) * weights) / weights.sum())
    anomaly = np.where(np.isfinite(values), values - environment, np.nan)
    return anomaly, environment, float(valid.mean())
