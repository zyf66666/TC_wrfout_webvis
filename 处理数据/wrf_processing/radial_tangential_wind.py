from __future__ import annotations

import numpy as np

from wrf_webvis.app.core.centers import load_center_yx
from wrf_webvis.app.core.paths import PathManager
from wrf_webvis.app.core.schemas import ProcessingRequest, VariableDimension
from .processing_utils import atomic_save, build_processing_metadata, domain_config_dict, fill_nan


RADIAL_TANGENTIAL_VARIABLES = {"vr", "vt", "vr1", "vt1"}


# 功能说明：逐高度读取已处理的 ua、va，按海平面或层中心投影为 vr 或 vt；跳过已有输出，并通过 fill_nan 处理计算后的缺测值再保存。
def process_radial_tangential_variable(
    *,
    request: ProcessingRequest,
    variable: str,
    time_hour: int,
    heights: list[float],
    dtype: str,
    center_mode: str | None = None,
) -> list[str]:
    path_manager = PathManager(request.processed_root)
    requested_variable = variable
    if variable.endswith("1"):
        variable = variable[:-1]
        center_mode = center_mode or "layer"
    center_mode = center_mode or "slp"
    center_path = path_manager.center_path(request.member_name, request.domain, "slp")
    if center_mode == "slp":
        center_y, center_x = load_center_yx(center_path, time_hour)
    domain_config = domain_config_dict(request).get(request.domain, {})
    dx_km = float(domain_config.get("dx_km") or 1.0)
    dy_km = float(domain_config.get("dy_km") or dx_km)
    processed: list[str] = []

    for height_m in heights:
        out_path = path_manager.variable_path(
            member_name=request.member_name,
            domain=request.domain,
            variable=requested_variable,
            time_hour=time_hour,
            dimension=VariableDimension.THREE_D,
            height_m=height_m,
        )
        if out_path.exists() and not request.force:
            processed.append(str(out_path))
            continue

        ua_path = path_manager.existing_variable(
            member_name=request.member_name,
            domain=request.domain,
            variable="ua",
            time_hour=time_hour,
            dimension=VariableDimension.THREE_D,
            height_m=height_m,
        )
        va_path = path_manager.existing_variable(
            member_name=request.member_name,
            domain=request.domain,
            variable="va",
            time_hour=time_hour,
            dimension=VariableDimension.THREE_D,
            height_m=height_m,
        )
        if ua_path is None or va_path is None:
            raise FileNotFoundError(f"Missing ua/va dependency for {variable} {time_hour}h {height_m}m")

        ua = np.asarray(np.load(ua_path.path, mmap_mode="r"), dtype=float)
        va = np.asarray(np.load(va_path.path, mmap_mode="r"), dtype=float)
        if center_mode == "layer":
            center_path = path_manager.layer_center_path(request.member_name, request.domain, height_m)
            center_y, center_x = load_center_yx(center_path, time_hour)
        values = radial_tangential_wind(
            ua,
            va,
            center_y=center_y,
            center_x=center_x,
            dx_km=dx_km,
            dy_km=dy_km,
            component=variable,
        )
        atomic_save(out_path, fill_nan(values).astype(dtype, copy=False))
        metadata = build_processing_metadata(request, requested_variable, ua_path.path, time_hour, height_m, None)
        metadata.update(
            {
                "derived_from": [str(ua_path.path), str(va_path.path)],
                "center_path": str(center_path),
                "center_mode": center_mode,
            }
        )
        path_manager.write_metadata(out_path, metadata)
        processed.append(str(out_path))
    return processed


# 功能说明：把东西、南北风分量投影到从中心向外和绕中心旋转的方向。
def radial_tangential_wind(
    ua: np.ndarray,
    va: np.ndarray,
    *,
    center_y: float,
    center_x: float,
    dx_km: float,
    dy_km: float,
    component: str,
) -> np.ndarray:
    """Convert earth-relative u/v wind to storm-centered radial or tangential wind.

    ``ua`` is positive eastward and ``va`` is positive northward. The center is
    stored in raw WRF grid indices ``(y, x)``. For each grid point:

        vr =  u cos(theta) + v sin(theta)
        vt = -u sin(theta) + v cos(theta)
    """
    u = np.asarray(ua, dtype=float)
    v = np.asarray(va, dtype=float)
    if u.shape != v.shape:
        raise ValueError(f"ua/va shapes differ: {u.shape} vs {v.shape}")
    if u.ndim != 2:
        raise ValueError(f"ua/va for vr/vt must be 2D per height, got shape={u.shape}")

    y_index = np.arange(u.shape[0], dtype=float)
    x_index = np.arange(u.shape[1], dtype=float)
    y_km = (y_index[:, None] - float(center_y)) * float(dy_km or dx_km or 1.0)
    x_km = (x_index[None, :] - float(center_x)) * float(dx_km or 1.0)
    radius = np.hypot(x_km, y_km)
    cos_theta = np.divide(x_km, radius, out=np.zeros_like(radius), where=radius > 0)
    sin_theta = np.divide(y_km, radius, out=np.zeros_like(radius), where=radius > 0)

    if component == "vr":
        return u * cos_theta + v * sin_theta
    if component == "vt":
        return -u * sin_theta + v * cos_theta
    raise ValueError(f"component must be 'vr' or 'vt', got {component}")
