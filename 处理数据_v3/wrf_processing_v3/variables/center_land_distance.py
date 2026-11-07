"""中心到海岸的有符号距离：负值位于海岸平均经度以西。"""

# 1. 导入数值库。
import numpy as np


def process(ctx):
    # 2. 读取跨域海岸边界和当前中心经纬度。
    latitude, longitude = ctx.read("center_geo")
    boundary = np.asarray(ctx.read("land_boundary"))
    # 3. 沿用旧版符号约定；空海岸线输出全海洋约定值。
    if boundary.size == 0:
        distance = float(ctx.options.get("all_ocean_land_distance_km", 9999.0))
    else:
        lat0, lat1 = np.deg2rad(latitude), np.deg2rad(boundary[:, 0])
        delta_lat = lat1 - lat0
        delta_lon = np.deg2rad(boundary[:, 1] - longitude)
        haversine = (
            np.sin(delta_lat / 2) ** 2
            + np.cos(lat0) * np.cos(lat1) * np.sin(delta_lon / 2) ** 2
        )
        distances = 6371.0 * 2 * np.arcsin(np.sqrt(np.clip(haversine, 0, 1)))
        sign = -1.0 if longitude < np.nanmean(boundary[:, 1]) else 1.0
        distance = sign * float(np.nanmin(distances))
    return ctx.save(
        ctx.task.variable, np.array([distance]), metadata={"sign": "west_negative"}
    )
