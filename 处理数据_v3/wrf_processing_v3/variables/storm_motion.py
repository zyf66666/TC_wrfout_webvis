"""Storm motion using v3 integer-minute coordinates."""

from __future__ import annotations

import numpy as np


def process(ctx):
    times = sorted(ctx.spec.options["times"])
    index = times.index(int(ctx.task.time_minutes))
    lower = times[max(0, index - 1)]
    upper = times[min(len(times) - 1, index + 1)]
    lat0, lon0 = ctx.read("center_geo", time=lower)
    lat1, lon1 = ctx.read("center_geo", time=upper)
    if lower == upper:
        u_motion = v_motion = 0.0
    else:
        dt_seconds = (upper - lower) * 60.0
        mean_latitude = np.deg2rad((lat0 + lat1) * 0.5)
        u_motion = (
            np.deg2rad(lon1 - lon0)
            * 6371000.0
            * np.cos(mean_latitude)
            / dt_seconds
        )
        v_motion = np.deg2rad(lat1 - lat0) * 6371000.0 / dt_seconds
    speed = np.hypot(u_motion, v_motion)
    direction = (np.degrees(np.arctan2(u_motion, v_motion)) + 360.0) % 360.0
    return ctx.save(
        ctx.task.variable,
        np.array([u_motion, v_motion, speed, direction]),
        metadata={
            "columns": ["u_m_s", "v_m_s", "speed_m_s", "direction_degree"],
            "difference_times_minutes": [lower, upper],
        },
    )
