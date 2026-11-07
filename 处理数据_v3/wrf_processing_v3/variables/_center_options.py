"""按 domain 解析 SLP 与层中心共享的中心定位参数；登记可用的定中心算法。

2026-11-02 起算法用登记表 ``CENTER_ALGORITHMS`` 管理。新增一种定中心算法：
1. 在 ``wrf_processing_v3/_center_algorithm.py`` 写定位函数：输入二维场（地面用 SLP，层中心用该高度气压）
   和格距，返回浮点 (y, x) 与要写进元数据的附加信息；
2. 在下面 ``CENTER_ALGORITHMS`` 登记一行：名称、用到的参数及默认值、地面/层中心调用方式；
3. 在 ``处理数据_v3/center_schemes.json`` 新增一个中心方案，选用该算法（地面中心用 ``algorithm``，
   层中心用 ``layer_algorithm``，不写时为 bezier_pcc_v1）。
已登记的算法名称和参数一旦有数据就不要改含义；改算法请另起名称（例如 ``_v2``）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class CenterAlgorithm:
    """一种定中心算法。``parameters``：传给定位函数的参数名 → (本模块参数表里的键, 类型)。"""

    name: str
    label: str
    parameters: dict[str, tuple[str, type]]
    # (field, dx_km, dy_km, initial_yx, **params) -> (y, x, extra_metadata)
    locate: Callable[..., tuple[float, float, dict[str, Any]]]
    supports_layer: bool = False
    supports_initial_guess: bool = False
    defaults: dict[str, Any] = field(default_factory=dict)


def _locate_bezier_pcc_v1(field, *, dx_km, dy_km, initial_yx=None, **params):
    from .._center_algorithm import find_pressure_center

    y, x, _ = find_pressure_center(field, dx_km=dx_km, dy_km=dy_km, initial_yx=initial_yx, **params)
    return y, x, {}


def _locate_smooth_fg_float_pcc_v2(field, *, dx_km, dy_km, initial_yx=None, **params):
    from .._center_algorithm import find_pressure_center_v2

    y, x, _, extra = find_pressure_center_v2(field, dx_km=dx_km, dy_km=dy_km, initial_yx=initial_yx, **params)
    return y, x, extra


_V1_PARAMETERS = {
    "search_radius_km": ("search_radius_km", float),
    "cross_radius_grid": ("cross_radius_grid", int),
    "pcc_radius_grid": ("pcc_radius_grid", int),
    "boundary_width": ("boundary_width", int),
    "max_iterations": ("max_iterations", int),
}

CENTER_ALGORITHMS: dict[str, CenterAlgorithm] = {
    "bezier_pcc_v1": CenterAlgorithm(
        name="bezier_pcc_v1",
        label="首猜 + Bezier PCC（整数格点，v1）",
        parameters=_V1_PARAMETERS,
        locate=_locate_bezier_pcc_v1,
        supports_layer=True,
        supports_initial_guess=True,
        defaults={
            "search_radius_km": 60.0,
            "cross_radius_grid": 121,
            "pcc_radius_grid": 44,
            "boundary_width": 20,
            "max_iterations": 10,
        },
    ),
    "smooth_fg_float_pcc_v2": CenterAlgorithm(
        name="smooth_fg_float_pcc_v2",
        label="平滑首猜 + 浮点 PCC（v2）",
        parameters={
            "search_radius_km": ("search_radius_km", float),
            "first_guess_radius_km": ("first_guess_radius_km", float),
            "pcc_radius_km": ("pcc_radius_km", float),
            "cross_radius_grid": ("cross_radius_grid", int),
            "boundary_width": ("boundary_width", int),
            # v2 单独的迭代上限，避免改变层中心（v1）的 max_iterations。
            "max_iterations": ("float_max_iterations", int),
            "tolerance_grid": ("tolerance_grid", float),
        },
        locate=_locate_smooth_fg_float_pcc_v2,
        supports_initial_guess=True,
        defaults={
            "first_guess_radius_km": 50.0,
            "pcc_radius_km": 80.0,
            "tolerance_grid": 0.05,
            "float_max_iterations": 50,
        },
    ),
}

# 兼容旧引用：可选算法名称。
ALGORITHMS = tuple(CENTER_ALGORITHMS)
LAYER_MODES = ("independent", "vertical")
DEFAULT_LAYER_ALGORITHM = "bezier_pcc_v1"


def _defaults() -> dict[str, Any]:
    values: dict[str, Any] = {
        "algorithm": "bezier_pcc_v1",
        "layer_algorithm": DEFAULT_LAYER_ALGORITHM,
        "layer_mode": "independent",
    }
    for algorithm in CENTER_ALGORITHMS.values():
        for key, value in algorithm.defaults.items():
            values.setdefault(key, value)
    return values


_GLOBAL_KEYS = {
    "center_search_radius_km": "search_radius_km",
    "center_cross_radius_grid": "cross_radius_grid",
    "center_pcc_radius_grid": "pcc_radius_grid",
    "center_boundary_width": "boundary_width",
    "center_max_iterations": "max_iterations",
}


def _resolved(options: dict, domain: int) -> dict:
    defaults = _defaults()
    resolved = dict(defaults)
    for global_key, local_key in _GLOBAL_KEYS.items():
        if global_key in options:
            resolved[local_key] = options[global_key]

    configured = options.get("center_options_by_domain", {})
    if configured:
        if not isinstance(configured, dict):
            raise ValueError("center_options_by_domain 必须是按 domain 编号组织的字典")
        override = configured.get(domain, configured.get(str(domain), {}))
        if not isinstance(override, dict):
            raise ValueError(f"domain={domain} 的中心参数必须是字典")
        unknown = set(override) - set(defaults)
        if unknown:
            raise ValueError(f"未知的中心参数：{', '.join(sorted(unknown))}")
        resolved.update(override)
    if resolved["algorithm"] not in CENTER_ALGORITHMS:
        raise ValueError(f"未知的中心算法：{resolved['algorithm']}（可选 {', '.join(CENTER_ALGORITHMS)}）")
    layer_algorithm = CENTER_ALGORITHMS.get(resolved["layer_algorithm"])
    if layer_algorithm is None or not layer_algorithm.supports_layer:
        layer_choices = ", ".join(name for name, item in CENTER_ALGORITHMS.items() if item.supports_layer)
        raise ValueError(f"未知或不支持层中心的算法：{resolved['layer_algorithm']}（可选 {layer_choices}）")
    if resolved["layer_mode"] not in LAYER_MODES:
        raise ValueError(f"未知的 layer_mode：{resolved['layer_mode']}（可选 {', '.join(LAYER_MODES)}）")
    if resolved["layer_mode"] == "vertical" and not layer_algorithm.supports_initial_guess:
        raise ValueError(f"层中心算法 {layer_algorithm.name} 不支持逐层连续（vertical）模式")
    return resolved


def validate_center_options(options: dict, domain: int) -> dict:
    """校验并返回某个 domain 解析后的完整中心参数（中心方案目录载入时调用）。"""
    return _resolved(options, domain)


def _parameters(algorithm: CenterAlgorithm, resolved: dict) -> dict:
    return {name: kind(resolved[key]) for name, (key, kind) in algorithm.parameters.items()}


def layer_center_mode(options: dict, domain: int) -> str:
    """默认逐高度独立定位；vertical 仅用于显式复现历史连续中心。"""
    return str(_resolved(options, domain)["layer_mode"])


def layer_center_algorithm(options: dict, domain: int) -> tuple[CenterAlgorithm, dict]:
    """返回层中心（layer_center）使用的算法及参数；默认 bezier_pcc_v1。"""
    resolved = _resolved(options, domain)
    algorithm = CENTER_ALGORITHMS[resolved["layer_algorithm"]]
    return algorithm, _parameters(algorithm, resolved)


def center_algorithm_options(options: dict, domain: int) -> dict:
    """返回某个 domain 的 v1（Bezier PCC）参数。

    全局 ``center_*`` 参数仍是兼容旧配置的后备值；
    ``center_options_by_domain`` 中同一 domain 的条目优先级更高。
    """
    return _parameters(CENTER_ALGORITHMS["bezier_pcc_v1"], _resolved(options, domain))


def slp_center_algorithm(options: dict, domain: int) -> tuple[str, dict]:
    """返回地面中心（slp_center）使用的算法名称和参数。"""
    resolved = _resolved(options, domain)
    algorithm = CENTER_ALGORITHMS[resolved["algorithm"]]
    return algorithm.name, _parameters(algorithm, resolved)
