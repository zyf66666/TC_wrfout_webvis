"""云分类变量：按旧版阈值把九层反射率与垂直速度归为六类。"""

import numpy as np


def classify_cloud_types(dbz, wa, heights_m):
    """根据旧版云分类阈值返回 ``int8`` 的二维类别图。"""
    # 第一段：导入数组并验证高度层数，避免用错变量或层坐标。
    reflectivity = np.asarray(dbz, dtype=float)
    vertical_velocity = np.asarray(wa, dtype=float)
    heights = np.asarray(heights_m, dtype=float)
    if (
        reflectivity.shape != vertical_velocity.shape
        or reflectivity.ndim != 3
        or reflectivity.shape[0] != heights.size
    ):
        raise ValueError("dbz、wa、heights 形状不匹配")

    def exact_height_index(target_height_m):
        matches = np.where(np.isclose(heights, target_height_m))[0]
        if not matches.size:
            raise ValueError(f"缺少高度 {target_height_m}m")
        return int(matches[0])

    def nearest_height_index(target_height_m):
        return int(np.argmin(np.abs(heights - target_height_m)))

    # 第二段：读取各诊断层。NaN 统一为 -999，保持旧版“缺测不触发阈值”的口径。
    reflectivity_900m = np.nan_to_num(reflectivity[exact_height_index(900)], nan=-999.0)
    reflectivity_2000m = np.nan_to_num(
        reflectivity[exact_height_index(2000)], nan=-999.0
    )
    reflectivity_3000m = np.nan_to_num(
        reflectivity[exact_height_index(3000)], nan=-999.0
    )
    reflectivity_4000m = np.nan_to_num(
        reflectivity[nearest_height_index(4000)], nan=-999.0
    )
    reflectivity_8000m = np.nan_to_num(
        reflectivity[nearest_height_index(8000)], nan=-999.0
    )
    low_level_mask = (heights >= 900.0) & (heights <= 2000.0)
    if not low_level_mask.any():
        raise ValueError("缺少 900 至 2000m 的垂直速度层")
    mean_low_level_velocity = np.nan_to_num(
        np.nanmean(vertical_velocity[low_level_mask], axis=0), nan=-999.0
    )

    # 第三段：先分对流与非对流，再按 8、4、2 km 回波依次区分深、中、浅对流。
    convective = (
        (reflectivity_900m > 46.0)
        | (reflectivity_3000m > 46.0)
        | (mean_low_level_velocity > 0.5)
    )
    stratiform = (~convective) & (reflectivity_3000m > 20.0)
    other = (~convective) & (~stratiform) & (reflectivity_3000m > 0.0)
    deep_convective = convective & (reflectivity_8000m > 20.0)
    moderate_convective = convective & (~deep_convective) & (reflectivity_4000m >= 20.0)
    shallow_convective = (
        convective
        & (~deep_convective)
        & (~moderate_convective)
        & (reflectivity_2000m < 20.0)
    )

    categories = np.zeros(reflectivity_3000m.shape, dtype=np.int8)
    categories[other] = 1
    categories[stratiform] = 2
    categories[shallow_convective] = 3
    categories[moderate_convective] = 4
    categories[deep_convective] = 5
    return categories


def process(ctx):
    """读取注册的九层输入，计算类别，并保存当前任务结果。"""
    # 第一段：从变量规格取得固定九层；这与注册表声明的依赖完全一致。
    heights = ctx.spec.options.get(
        "heights", [900, 1000, 1250, 1500, 1750, 2000, 3000, 4000, 8000]
    )
    # 第二段：通过受限上下文按高度读取，禁止绕过依赖选择。
    reflectivity = np.stack([ctx.read("dbz", height=height) for height in heights])
    vertical_velocity = np.stack([ctx.read("wa", height=height) for height in heights])
    # 第三段：保存当前变量；Storage 会依据 output_dtype 强制为 int8。
    ctx.save(
        ctx.task.variable,
        classify_cloud_types(reflectivity, vertical_velocity, heights),
        metadata={
            "units": "category",
            "dtype": ctx.spec.options.get("output_dtype", "int8"),
            "classification_heights_m": [float(height) for height in heights],
        },
    )
