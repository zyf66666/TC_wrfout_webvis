"""沿用旧版科学算法的纯函数；这里不读文件、不写结果。"""

from __future__ import annotations
import numpy as np


def calculate_fixed_quadrant_asymmetry(
    polar_values: np.ndarray,
    *,
    radius_resolution_km: float,
    radius_km: float,
) -> float:
    """Return the unsmoothed four-fixed-geographic-quadrant asymmetry.

    The directions and integrations are intentionally the same as the supplied
    ``my_package.py`` implementation: polar angle 0 degrees points east, the
    four quadrants are fixed in that geographic frame, and the radial integral
    is weighted by radius over the full disk.
    """
    values = np.asarray(polar_values, dtype=float)
    if values.ndim != 2 or values.shape[0] < 360:
        raise ValueError(
            f"Polar asymmetry requires at least 360 angular samples, got shape={values.shape}"
        )
    if not np.isfinite(radius_resolution_km) or radius_resolution_km <= 0:
        raise ValueError(
            f"Polar radial resolution must be positive, got {radius_resolution_km!r}"
        )
    if not np.isfinite(radius_km) or radius_km <= 0:
        raise ValueError(f"Asymmetry radius must be positive, got {radius_km!r}")

    radii = np.arange(values.shape[1], dtype=float) * float(radius_resolution_km)
    selected = radii <= float(radius_km) + 1.0e-8
    if not selected.any() or radii[selected][-1] < float(radius_km) - 1.0e-8:
        available = float(radii[-1]) if radii.size else 0.0
        raise ValueError(
            f"Polar source only reaches {available:g} km, below requested asymmetry radius {float(radius_km):g} km"
        )

    # The reference routine clips negative RAINNCV/DBZ before integration.  It
    # also implicitly drops NaNs via its interpolation; replacing them with zero
    # makes the same physical choice explicit for cached polar fields.
    field = np.nan_to_num(values[:360, selected], nan=0.0, posinf=0.0, neginf=0.0)
    field[field < 0.0] = 0.0
    radial = radii[selected]
    azimuth_integral = _trapezoid(field * radial[np.newaxis, :], radial, axis=1)
    theta = np.deg2rad(np.arange(360, dtype=float))
    total = float(_trapezoid(azimuth_integral, theta))
    if not np.isfinite(total) or total <= 0.0:
        return float("nan")

    down_shear = _trapezoid(azimuth_integral[135:225], theta[135:225])
    left_shear = _trapezoid(azimuth_integral[225:315], theta[225:315])
    up_shear = _trapezoid(azimuth_integral[315:360], theta[315:360]) + _trapezoid(
        azimuth_integral[:45], theta[:45]
    )
    right_shear = _trapezoid(azimuth_integral[45:135], theta[45:135])
    fractions = (
        np.asarray([down_shear, left_shear, up_shear, right_shear], dtype=float) / total
    )
    return float(np.sqrt((4.0 / 3.0) * np.sum((fractions - 0.25) ** 2)))


def _trapezoid(
    values: np.ndarray, coordinates: np.ndarray, axis: int | None = None
) -> np.ndarray:
    function = getattr(np, "trapezoid", np.trapz)
    if axis is None:
        return function(values, x=coordinates)
    return function(values, x=coordinates, axis=axis)


def validate_shear_radii(radius_in: int, radius_out: int, radius_max: int) -> None:
    if radius_in < 0:
        raise ValueError(f"shear radius_in must be non-negative: {radius_in}")
    if radius_out <= radius_in:
        raise ValueError(
            f"shear radius_out must be greater than radius_in: {radius_in}, {radius_out}"
        )
    if radius_max < radius_out:
        raise ValueError(
            f"shear radius_max must be at least radius_out: {radius_max} < {radius_out}"
        )


def polar_annulus_mean(
    data: np.ndarray,
    *,
    center_y: int,
    center_x: int,
    radius_in: int,
    radius_out: int,
    radius_max: int,
) -> float:
    """Bezier-interpolate to 360-degree polar coordinates and average an annulus.

    This follows the supplied inner-core shear script exactly: the outer radius
    is excluded by the ``radius_in:radius_out`` slice.
    """
    validate_shear_radii(radius_in, radius_out, radius_max)
    polar = bezier_interp(
        data, center_y=center_y, center_x=center_x, radius_max=radius_max
    )
    annulus = polar[:, radius_in:radius_out]
    if not np.isfinite(annulus).any():
        raise ValueError(
            f"Bezier polar annulus contains no finite values: radius={radius_in}:{radius_out}"
        )
    return float(np.nanmean(annulus))


def bezier_interp(
    data: np.ndarray,
    *,
    center_y: int,
    center_x: int,
    radius_max: int,
) -> np.ndarray:
    """Vectorized equivalent of the supplied 360-degree ``BezierInterp``."""
    values = np.asarray(data, dtype=float)
    if values.ndim != 2:
        raise ValueError(f"BezierInterp input must be 2D, got shape={values.shape}")
    if radius_max < 0:
        raise ValueError(f"BezierInterp radius_max must be non-negative: {radius_max}")
    cy = int(center_y)
    cx = int(center_x)
    if not (0 <= cy < values.shape[0] and 0 <= cx < values.shape[1]):
        raise ValueError(
            f"BezierInterp center is outside data: center=({cy}, {cx}), shape={values.shape}"
        )

    stock = np.zeros((360, radius_max + 1), dtype=float)
    stock[:, 0] = values[cy, cx]
    if radius_max == 0:
        return stock

    angle = np.deg2rad(np.arange(360, dtype=float))[:, np.newaxis]
    radius = np.arange(1, radius_max + 1, dtype=float)[np.newaxis, :]
    xproj = radius * np.cos(angle)
    yproj = radius * np.sin(angle)
    xfloor = np.floor(xproj)
    yfloor = np.floor(yproj)
    tx = xproj - xfloor
    ty = yproj - yfloor

    x0 = cx + xfloor.astype(int)
    x1 = cx + np.ceil(xproj).astype(int)
    x2 = cx + np.ceil(xproj).astype(int) + 1
    x_minus_1 = cx + xfloor.astype(int) - 1
    y0 = cy + yfloor.astype(int)
    y1 = cy + np.ceil(yproj).astype(int)
    y2 = cy + np.ceil(yproj).astype(int) + 1
    y_minus_1 = cy + yfloor.astype(int) - 1

    if (
        min(int(x_minus_1.min()), int(y_minus_1.min())) < 0
        or int(x2.max()) >= values.shape[1]
        or int(y2.max()) >= values.shape[0]
    ):
        raise ValueError(
            "BezierInterp radius_max reaches outside the WRF grid; "
            f"center=({cy}, {cx}), radius_max={radius_max}, shape={values.shape}"
        )

    # 功能说明：接收一组 y 方向格点索引，沿 x 方向用相邻四个格点计算 Bezier 插值值，返回全部方位和半径的中间结果。
    def interpolate_x(y_index: np.ndarray) -> np.ndarray:
        base = values[y_index, x0]
        first = tx * (values[y_index, x1] - base)
        curve = (
            0.25
            * tx
            * (tx - 1.0)
            * (
                values[y_index, x2]
                - values[y_index, x1]
                - base
                + values[y_index, x_minus_1]
            )
        )
        return base + first + curve

    b_value = interpolate_x(y0)
    c_value = interpolate_x(y1)
    d_value = interpolate_x(y2)
    a_value = interpolate_x(y_minus_1)
    stock[:, 1:] = (
        b_value
        + ty * (c_value - b_value)
        + 0.25 * ty * (ty - 1.0) * (a_value + d_value - b_value - c_value)
    )
    return stock


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
