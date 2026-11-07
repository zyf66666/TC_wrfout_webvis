import numpy as np
from ..common import grid_km
from ._helpers import save
from ._center_options import DEFAULT_LAYER_ALGORITHM, layer_center_algorithm, layer_center_mode


def process(ctx):
    # 导入：独立 PCC/Bezier 定位算法。
    # 读取：当前高度 pressure 与 domain_config、中心算法请求选项。
    data = np.asarray(ctx.read("pressure"), dtype=float)
    dx, dy = grid_km(ctx)
    options = ctx.options
    algorithm, algorithm_options = layer_center_algorithm(options, ctx.task.domain)
    mode = layer_center_mode(options, ctx.task.domain)
    # 默认逐高度独立定位。仅显式 vertical 模式读取地面/前一高度中心，
    # 由规划器补全连续中心链，并在该中心附近约束搜索。
    initial_yx = None
    for dependency in (getattr(ctx.spec, "dependencies", ()) if mode == "vertical" else ()):
        if dependency.name == "slp_center":
            initial_yx = np.asarray(ctx.read("slp_center"), dtype=float).ravel()[:2]
            break
        if dependency.name == "layer_center":
            initial_yx = np.asarray(
                ctx.read("layer_center", height=dependency.height), dtype=float
            ).ravel()[:2]
            break
    y, x, extra = algorithm.locate(
        data,
        dx_km=dx,
        dy_km=dy,
        initial_yx=initial_yx,
        **algorithm_options,
    )
    iy = min(max(int(round(y)), 0), data.shape[0] - 1)
    ix = min(max(int(round(x)), 0), data.shape[1] - 1)
    value = float(data[iy, ix])
    # 计算保存：中心数组稳定保存为整数 [y, x]，气压与该取整格点对应。
    save(
        ctx,
        np.array([iy, ix]),
        units="grid_index",
        pressure_hpa=value,
        # 默认算法保持旧写法（bezier_pcc_v1 / bezier_pcc_vertical_v1），其他登记算法写“名称”或“名称_vertical”。
        algorithm=(
            ("bezier_pcc_vertical_v1" if mode == "vertical" else "bezier_pcc_v1")
            if algorithm.name == DEFAULT_LAYER_ALGORITHM
            else (f"{algorithm.name}_vertical" if mode == "vertical" else algorithm.name)
        ),
        layer_center_mode=mode,
        initial_center_yx=(initial_yx.tolist() if initial_yx is not None else None),
        **algorithm_options,
        **extra,
        derived_from=["pressure"],
    )
