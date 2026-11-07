"""中心方案（2026-11-02）：同一批试验可以用多套定中心方法/参数，各自得到中心和依赖中心的变量。

- 方案目录在 ``处理数据_v3/center_schemes.json``：编号 → 中文名、说明、按 domain 的完整中心参数。
- 变量名不带方案：``vt1_polar`` 永远是“相对逐层中心的切向风”，方案是与试验、domain、时次、高度并列的另一维。
- 存放位置（``processed_path`` 是唯一的路径规则，处理与绘图共用）::

      <根>/<试验>/dXX/<变量>.nc                     base 方案，以及所有与中心无关的变量（ua、theta…）
      <根>/centers/<方案>/<试验>/dXX/<变量>.nc       其他方案下依赖中心的变量（含 slp_center、layer_center）
      <根>/centers/<方案>/scheme.json               方案参数快照，首次写入后不可改

- 绘图里“试验 + 方案”写成一个成员键 ``WRF_315_land@fpcc_v2``；base 不写后缀。
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

BASE_CENTER_SCHEME = "base"
CENTERS_DIRNAME = "centers"
MEMBER_SEPARATOR = "@"
SCHEME_FILE = Path(__file__).resolve().parents[1] / "center_schemes.json"
_SCHEME_ID = re.compile(r"[a-z][a-z0-9_]{1,31}")


def validate_scheme_id(scheme: str) -> str:
    scheme = str(scheme or BASE_CENTER_SCHEME)
    if not _SCHEME_ID.fullmatch(scheme):
        raise ValueError(f"中心方案编号只能用小写字母开头的 2–32 位小写字母、数字或下划线：{scheme!r}")
    return scheme


def _canonical(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, dict):
            return {str(key): normalize(entry) for key, entry in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(entry) for entry in item]
        if isinstance(item, float) and math.isfinite(item) and item.is_integer():
            return int(item)
        return item

    return json.dumps(normalize(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _int_domains(table: dict[Any, Any]) -> dict[int, dict[str, Any]]:
    return {int(domain): dict(entry) for domain, entry in table.items()}


# 功能说明：读取并校验方案目录（编号、算法名、参数名）；返回 {编号: {label, description, center_options_by_domain}}。
@lru_cache(maxsize=4)
def _load(path: str, mtime_ns: int) -> dict[str, dict[str, Any]]:
    from .variables._center_options import validate_center_options

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    schemes: dict[str, dict[str, Any]] = {}
    for scheme, entry in raw.items():
        if scheme.startswith("_"):
            continue
        validate_scheme_id(scheme)
        if not isinstance(entry, dict) or not isinstance(entry.get("center_options_by_domain"), dict):
            raise ValueError(f"中心方案 {scheme} 缺少 center_options_by_domain")
        table = _int_domains(entry["center_options_by_domain"])
        for domain in table:
            validate_center_options({"center_options_by_domain": table}, domain)
        schemes[scheme] = {
            "id": scheme,
            "label": str(entry.get("label") or scheme),
            "description": str(entry.get("description") or ""),
            "center_options_by_domain": table,
        }
    if BASE_CENTER_SCHEME not in schemes:
        raise ValueError("中心方案目录必须包含 base")
    return schemes


def load_center_schemes(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    file = Path(path or SCHEME_FILE)
    return _load(str(file), file.stat().st_mtime_ns)


def center_scheme(scheme: str | None, path: str | Path | None = None) -> dict[str, Any]:
    scheme = validate_scheme_id(scheme or BASE_CENTER_SCHEME)
    schemes = load_center_schemes(path)
    if scheme not in schemes:
        raise KeyError(f"未登记的中心方案：{scheme}（可选 {', '.join(sorted(schemes))}）")
    return schemes[scheme]


def base_center_options() -> dict[int, dict[str, Any]]:
    """base 方案的中心参数（project_config 由此取值，保证只有一处定义）。"""
    return _int_domains(center_scheme(BASE_CENTER_SCHEME)["center_options_by_domain"])


_CENTER_OPTION_KEYS = (
    "center_options_by_domain", "center_search_radius_km", "center_cross_radius_grid",
    "center_pcc_radius_grid", "center_boundary_width", "center_max_iterations",
)


# 功能说明：把方案的中心参数并入处理选项；选项里已有、但与方案不同的中心参数直接报错，避免两套参数混用。
def apply_center_scheme(options: dict[str, Any], scheme: str | None) -> dict[str, Any]:
    scheme = validate_scheme_id(scheme or options.get("center_scheme") or BASE_CENTER_SCHEME)
    if options.get("center_scheme") not in (None, scheme):
        raise ValueError(f"处理选项里的 center_scheme={options['center_scheme']!r} 与请求的中心方案 {scheme!r} 不一致")
    expected = center_scheme(scheme)["center_options_by_domain"]
    merged = dict(options)
    given = merged.get("center_options_by_domain")
    if given is not None and _canonical(given) != _canonical(expected):
        raise ValueError(
            f"处理选项里的 center_options_by_domain 与中心方案 {scheme!r} 不一致；"
            "换中心参数请在 处理数据_v3/center_schemes.json 新增方案，不要在选项里改"
        )
    for key in _CENTER_OPTION_KEYS[1:]:
        if key in merged:
            raise ValueError(f"处理选项不能再单独给 {key}；中心参数统一由中心方案（{scheme}）决定")
    merged["center_options_by_domain"] = {domain: dict(entry) for domain, entry in expected.items()}
    if scheme == BASE_CENTER_SCHEME:
        merged.pop("center_scheme", None)  # base 不写入，已有切片的元数据与参数哈希保持不变
    else:
        merged["center_scheme"] = scheme
    return merged


# 功能说明：按方案和“是否依赖中心”给出变量文件路径。处理端（NetCDFStorage）与绘图读取共用这一条规则。
def processed_path(
    root: str | Path,
    experiment: str,
    domain: int,
    variable: str,
    *,
    center_scheme: str = BASE_CENTER_SCHEME,
    center_dependent: Callable[[str], bool] | None = None,
) -> Path:
    base = Path(root)
    scheme = validate_scheme_id(center_scheme)
    if scheme != BASE_CENTER_SCHEME:
        if center_dependent is None:
            raise ValueError("非 base 中心方案需要提供“变量是否依赖中心”的判断")
        if center_dependent(variable):
            base = scheme_root(root, scheme)
    return base / experiment / f"d{int(domain):02d}" / f"{variable}.nc"


def scheme_root(root: str | Path, scheme: str) -> Path:
    return Path(root) / CENTERS_DIRNAME / validate_scheme_id(scheme)


# 功能说明：在方案目录写入参数快照 scheme.json；已存在但参数不同则拒绝（方案一旦有数据就不可改）。
def ensure_scheme_manifest(root: str | Path, scheme: str) -> Path | None:
    scheme = validate_scheme_id(scheme)
    if scheme == BASE_CENTER_SCHEME:
        return None
    entry = center_scheme(scheme)
    directory = scheme_root(root, scheme)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "scheme.json"
    data = {"id": scheme, "center_options_by_domain": entry["center_options_by_domain"]}
    if path.exists():
        stored = json.loads(path.read_text(encoding="utf-8"))
        if _canonical(stored.get("center_options_by_domain")) != _canonical(data["center_options_by_domain"]):
            raise ValueError(
                f"{path} 记录的中心参数与 center_schemes.json 里的 {scheme} 不同；"
                "方案已有数据时不能改参数，请另起一个方案编号"
            )
        return path
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({**data, "label": entry["label"], "description": entry["description"]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def split_member(member: str) -> tuple[str, str]:
    """绘图成员键 ``试验@方案`` → (试验, 方案)；没有 @ 时方案为 base。"""
    text = str(member)
    if MEMBER_SEPARATOR not in text:
        return text, BASE_CENTER_SCHEME
    experiment, scheme = text.split(MEMBER_SEPARATOR, 1)
    if not experiment:
        raise ValueError(f"成员键缺少试验名：{member!r}")
    return experiment, validate_scheme_id(scheme)


def member_key(experiment: str, scheme: str | None) -> str:
    scheme = validate_scheme_id(scheme or BASE_CENTER_SCHEME)
    return experiment if scheme == BASE_CENTER_SCHEME else f"{experiment}{MEMBER_SEPARATOR}{scheme}"
