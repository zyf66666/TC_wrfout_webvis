"""绘图端自动识别网格间距和极坐标径向间隔（2026-11-04）。

以前绘图不读数据里的网格信息：请求不带 domain_config 时一律按默认表（d01 25 km、d02 5 km、
d03 1 km、805 格），极坐标径向间隔留空时又按这个 dx 换算。换了网格间距的模拟会画出空间尺度错的图，
而且不报错。现在按下面的顺序自动识别：

1. 水平网格间距：本次要画的处理文件里，已完成切片的 metadata_json["grid_spacing"]
   （处理时从 wrfout 的 DX/DY 写入，派生变量逐级继承）；格点数取笛卡尔文件的 y/x 维度。
2. 读不到时：该试验 wrfout 的 DX/DY（请求给了 wrfout_file / wrfout_root 时用它们，
   否则用 project_config.SOURCE_DIRECTORIES 与网页添加的试验）。
3. 还读不到：默认表，并在绘图警告里写明。
4. 极坐标径向间隔：极坐标文件里的 radius_km 坐标。网页填的值与文件不同时以文件为准并给出警告。

同一张图里各成员的网格间距不同（或极坐标径向间隔不同）时报错，请分开画；目前还不支持一张图混用。
决策部分（choose_*）是纯函数，不依赖 netCDF4，方便测试。
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, Callable, Iterable

# 两个间距相对差小于这个值视为相同（float32 存储、km/m 换算的舍入）。
_RELATIVE_TOLERANCE = 1.0e-4


def _same(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= _RELATIVE_TOLERANCE * max(abs(float(a)), abs(float(b)), 1.0e-12)


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def spacing_from_metadata(metadata: dict[str, Any] | None) -> tuple[float, float] | None:
    """从切片元数据取 (dx_km, dy_km)；没有或不合法时返回 None。"""
    spacing = (metadata or {}).get("grid_spacing")
    if not isinstance(spacing, dict):
        return None
    dx = _positive(spacing.get("dx_km"))
    dy = _positive(spacing.get("dy_km", spacing.get("dx_km")))
    if dx is None or dy is None:
        return None
    return dx, dy


# 功能说明：决定本次绘图的水平网格配置。
# member_infos(member) 依次给出该成员各处理文件的信息字典（键：grid_spacing_km=(dx,dy)、nx、ny、path），
# wrfout_config(member) 给出 wrfout 读到的 {"dx_km","dy_km","nx","ny","wrfout_path"} 或 None，
# fallback 是默认表中该 domain 的条目。返回 (配置字典, 警告列表)；成员之间间距不同时抛 ValueError。
def choose_domain_config(
    domain: int,
    members: Iterable[str],
    member_infos: Callable[[str], Iterable[dict[str, Any]]],
    wrfout_config: Callable[[str], dict[str, Any] | None],
    fallback: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    found: dict[str, dict[str, Any]] = {}
    fallback_members: list[str] = []
    for member in members:
        entry: dict[str, Any] | None = None
        shape: tuple[int, int] | None = None
        for info in member_infos(member):
            if shape is None and info.get("nx") and info.get("ny"):
                shape = (int(info["ny"]), int(info["nx"]))
            if entry is None and info.get("grid_spacing_km"):
                dx, dy = info["grid_spacing_km"]
                entry = {"dx_km": float(dx), "dy_km": float(dy), "source": "processed_metadata",
                         "evidence": str(info.get("path", ""))}
            if entry is not None and shape is not None:
                break
        if entry is None:
            wrf = wrfout_config(member)
            dx = _positive((wrf or {}).get("dx_km"))
            if dx is not None:
                dy = _positive(wrf.get("dy_km")) or dx
                entry = {"dx_km": dx, "dy_km": dy, "source": "wrfout",
                         "evidence": str(wrf.get("wrfout_path") or "")}
                if shape is None and wrf.get("nx") and wrf.get("ny"):
                    shape = (int(wrf["ny"]), int(wrf["nx"]))
        if entry is None:
            fallback_members.append(member)
            continue
        if shape is not None:
            entry["ny"], entry["nx"] = shape
        found[member] = entry

    if found:
        items = list(found.items())
        first_member, first = items[0]
        for member, entry in items[1:]:
            if not (_same(entry["dx_km"], first["dx_km"]) and _same(entry["dy_km"], first["dy_km"])):
                raise ValueError(
                    "所选成员的网格间距不同："
                    + "；".join(f"{name} dx={value['dx_km']:g} km, dy={value['dy_km']:g} km" for name, value in items)
                    + "。同一张图暂不支持混用不同网格间距，请把这些成员分开画。"
                )
        if fallback_members:
            dx = _positive(fallback.get("dx_km"))
            if dx is not None and not _same(dx, first["dx_km"]):
                raise ValueError(
                    f"{'、'.join(fallback_members)} 读不到网格间距（默认 {dx:g} km），"
                    f"而 {first_member} 是 {first['dx_km']:g} km；请先确认这些成员的数据或分开画。"
                )
            warnings.append(f"{'、'.join(fallback_members)} 的处理文件和 wrfout 都没读到网格间距，按 {first_member} 的值处理")
        result = {
            "domain": int(domain),
            "dx_km": first["dx_km"],
            "dy_km": first["dy_km"],
            "nx": first.get("nx"),
            "ny": first.get("ny"),
            "source": first["source"],
        }
        if first["source"] == "wrfout":
            result["wrfout_path"] = first["evidence"] or None
            warnings.append(
                f"处理文件里没有网格间距元数据，绘图按 wrfout 的 DX/DY：dx={first['dx_km']:g} km"
            )
        return result, warnings

    dx = _positive(fallback.get("dx_km"))
    dy = _positive(fallback.get("dy_km", fallback.get("dx_km")))
    result = {
        "domain": int(domain),
        "dx_km": dx,
        "dy_km": dy or dx,
        "nx": fallback.get("nx", fallback.get("grid_number")),
        "ny": fallback.get("ny", fallback.get("grid_number")),
        "source": "fallback",
    }
    if dx is not None:
        warnings.append(
            f"处理文件和 wrfout 都没读到 d{int(domain):02d} 的网格间距，按默认 {dx:g} km 绘图；"
            "如果你的网格间距不是这个值，图上的距离会错"
        )
    return result, warnings


# 功能说明：决定极坐标径向间隔。file_resolutions 是各极坐标文件 radius_km 坐标的间隔（{文件路径: km}），
# requested 是请求里填的值（可为 None）。文件为准；文件之间不同则报错。返回 (间隔或 None, 警告列表)。
def choose_polar_resolution(
    file_resolutions: dict[str, float],
    requested: float | None,
) -> tuple[float | None, list[str]]:
    values = {path: float(value) for path, value in file_resolutions.items() if _positive(value) is not None}
    if not values:
        return requested, []
    distinct: list[float] = []
    for value in values.values():
        if not any(_same(value, other) for other in distinct):
            distinct.append(value)
    if len(distinct) > 1:
        detail = "；".join(f"{Path(path).name}: {value:g} km" for path, value in values.items())
        raise ValueError(f"本次要画的极坐标文件径向间隔不同（{detail}），请分开画。")
    detected = round(distinct[0], 6)
    warnings: list[str] = []
    if requested is not None and not _same(float(requested), detected):
        warnings.append(
            f"填写的极坐标径向间隔 {float(requested):g} km 与数据文件的 {detected:g} km 不一致，已按文件的 {detected:g} km 绘图"
        )
    return detected, warnings


def _wrfout_lookup(request: Any) -> Callable[[str], dict[str, Any] | None]:
    """按成员找 wrfout 并读 DX/DY；结果按试验缓存。找不到或读不了时返回 None。"""
    from 处理数据_v3.wrf_processing_v3.center_schemes import split_member

    from ..core.paths import WrfoutDomainReader

    cache: dict[str, dict[str, Any] | None] = {}
    directories: dict[str, Path] | None = None

    def source_directories() -> dict[str, Path]:
        nonlocal directories
        if directories is None:
            # 测试里设 WRF_WEBVIS_PLOT_GRID_SOURCE_LOOKUP=0（见 conftest.py），不去读外接盘上真实试验的 wrfout。
            if os.environ.get("WRF_WEBVIS_PLOT_GRID_SOURCE_LOOKUP", "1") == "0":
                directories = {}
                return directories
            try:
                from .v3_management import V3ManagementService

                directories = dict(V3ManagementService().source_directories)
            except Exception:  # noqa: BLE001 - 只是兜底来源，读不到就用默认表
                directories = {}
        return directories

    def lookup(member: str) -> dict[str, Any] | None:
        experiment = split_member(member)[0]
        if experiment in cache:
            return cache[experiment]
        domain = int(request.domain)
        config = None
        try:
            if getattr(request, "wrfout_file", None) or getattr(request, "wrfout_root", None):
                config = WrfoutDomainReader.read_domain_config(
                    domain=domain, wrfout_root=request.wrfout_root, wrfout_file=request.wrfout_file,
                    fallback={},
                )
            else:
                root = source_directories().get(experiment)
                if root is not None and Path(root).is_dir():
                    config = WrfoutDomainReader.read_domain_config(domain=domain, wrfout_root=root, fallback={})
        except Exception:  # noqa: BLE001
            config = None
        value = None
        if config is not None and getattr(config, "source", "") == "wrfout" and config.dx_km:
            value = {"dx_km": config.dx_km, "dy_km": config.dy_km, "nx": config.nx, "ny": config.ny,
                     "wrfout_path": config.wrfout_path}
        cache[experiment] = value
        return value

    return lookup


# 功能说明：data_manager.plan_for_plot 调用：用本次绘图需要的已完成切片识别网格。
# required 是 RequiredFile 列表（只看 exists 且带 reference 的）；reader 是 ProcessedDataReader。
# 返回 (DomainConfig 字典, 极坐标径向间隔或 None, 警告列表)。
def detect_plot_grid(
    request: Any,
    members: list[str],
    required: list[Any],
    reader: Any,
    fallback: dict[str, Any],
) -> tuple[dict[str, Any], float | None, list[str]]:
    # 每个成员、每个文件只取第一个已完成切片。
    by_member: dict[str, dict[Path, Any]] = {}
    for item in required:
        reference = getattr(item, "reference", None)
        if not getattr(item, "exists", False) or reference is None:
            continue
        by_member.setdefault(reference.task.experiment, {}).setdefault(Path(reference.path), reference)

    infos: dict[Path, dict[str, Any]] = {}

    def info_for(path: Path, reference: Any) -> dict[str, Any]:
        if path not in infos:
            try:
                infos[path] = reader.grid_info(reference)
            except Exception:  # noqa: BLE001 - 单个文件读不了不影响绘图本身，换下一个文件
                infos[path] = {"path": str(path)}
        return infos[path]

    def member_infos(member: str):
        for path, reference in by_member.get(member, {}).items():
            yield info_for(path, reference)

    domain_config, warnings = choose_domain_config(
        int(request.domain), members, member_infos, _wrfout_lookup(request), fallback,
    )
    polar = {}
    for member in members:
        for info in member_infos(member):
            if str(info.get("layout", "")).startswith("polar") and info.get("radius_resolution_km"):
                polar[str(info.get("path"))] = float(info["radius_resolution_km"])
    resolution, polar_warnings = choose_polar_resolution(polar, getattr(request, "polar_radius_resolution_km", None))
    return domain_config, resolution, [*warnings, *polar_warnings]
