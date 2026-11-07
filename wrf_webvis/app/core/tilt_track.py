"""倾斜轨迹范围与已处理 x/y 分量的统一对应关系。"""

from typing import Any, Callable


TILT_TRACK_COMPONENTS = {
    "2_4": ("tilt_x_2_4", "tilt_y_2_4"),
    "2_6": ("tilt_x_2_6", "tilt_y_2_6"),
    # 2026-11-04：2–8 km 改用与其他层次同样的命名 tilt_x_2_8 / tilt_y_2_8。
    "2_8": ("tilt_x_2_8", "tilt_y_2_8"),
    "2_10": ("tilt_x_2_10", "tilt_y_2_10"),
}

# 旧名：2026-11-04 以前 2–8 km 的分量叫 tilt_x / tilt_y（同一算法、同一高度，数值相同）。
# 新名文件不存在而旧名文件存在时（例如 processed_v3 旧数据）读旧名；这是同一个量换了名字，不是改读别的范围。
LEGACY_TILT_TRACK_COMPONENTS = {
    "2_8": ("tilt_x", "tilt_y"),
}


def tilt_track_components(
    horizontal: dict[str, Any], exists: Callable[[str], bool] | None = None,
) -> tuple[str, str]:
    """读取选定范围；旧请求未指定范围时沿用 2–8 km。exists(变量名) 给出时，新名缺文件则退回同一量的旧名。"""
    layer = horizontal.get("tilt_track_layer", "2_8")
    if not isinstance(layer, str) or layer not in TILT_TRACK_COMPONENTS:
        raise ValueError("tilt_track_layer 必须为 2_4、2_6、2_8 或 2_10")
    names = TILT_TRACK_COMPONENTS[layer]
    legacy = LEGACY_TILT_TRACK_COMPONENTS.get(layer)
    if exists is not None and legacy is not None and not all(exists(name) for name in names) \
            and all(exists(name) for name in legacy):
        return legacy
    return names
