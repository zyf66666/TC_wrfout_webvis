from __future__ import annotations

import os
from pathlib import Path


# 功能说明：读取环境变量里的目录；没设置时使用本机默认路径。换机器时设置环境变量即可，无需改代码。
def _path_from_env(name: str, default: str) -> Path:
    value = os.environ.get(name, "").strip()
    # 默认值里的 “~” 也要展开，否则会被当成名为 “~” 的相对目录。
    return Path(value or default).expanduser()


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = PROJECT_ROOT / "app"
STATIC_ROOT = APP_ROOT / "static"
FIGURE_ROOT = STATIC_ROOT / "figures"
STORAGE_ROOT = PROJECT_ROOT / "storage_data"
# 旧版 NPY 处理入口的默认输出目录（网页绘图默认使用下面的 v3 目录）。
DEFAULT_PROCESSED_ROOT = _path_from_env("WRF_WEBVIS_LEGACY_PROCESSED_ROOT", "/Volumes/land_1/processed_v2")
DEFAULT_PLOT_PROCESSED_ROOT = _path_from_env("WRF_WEBVIS_PROCESSED_ROOT", "/Volumes/land_1/processed_v3")

REPOSITORY_ROOT = PROJECT_ROOT.parent
DEFAULT_EXPERIMENTS_ROOT = _path_from_env("WRF_WEBVIS_EXPERIMENTS_ROOT", "/Volumes/WRF")

# 功能说明：展开用户路径并转换成绝对路径对象。
def absolute_path(value: str | Path) -> Path:
    """Resolve local paths against the repository, independently of the shell cwd."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    return path.resolve()


# 2026-11-01：绘图只读 processed_v3（v2 NPY 已停用）。旧请求里的数据根目录按下面规则改指 v3：
#   …/processed_v2 → 同级 …/processed_v3；…/processed（更早的 v1，如 /Volumes/WRF/processed）→ 同级
#   processed_v3（存在时），否则默认 v3 根目录。其他目录（processed_v3、profiles/<配置>、测试临时目录）原样使用。
LEGACY_PROCESSED_ROOT_NAMES = ("processed_v2", "processed")


# 功能说明：返回绘图实际使用的 processed_v3 根目录；旧版 v1/v2 根目录自动改指 v3。
def resolve_plot_processed_root(value: str | Path | None) -> Path:
    root = absolute_path(value if value not in (None, "") else DEFAULT_PLOT_PROCESSED_ROOT)
    if root.name == "processed_v2":
        return root.with_name("processed_v3")
    if root.name == "processed":
        sibling = root.with_name("processed_v3")
        return sibling if sibling.is_dir() else absolute_path(DEFAULT_PLOT_PROCESSED_ROOT)
    return root


# 功能说明：旧数据根目录被改指 v3 时返回一句提示，否则返回 None。
def legacy_processed_root_warning(value: str | Path | None) -> str | None:
    if value in (None, ""):
        return None
    original = absolute_path(value)
    if original.name not in LEGACY_PROCESSED_ROOT_NAMES:
        return None
    return f"数据根目录 {original} 是已停用的旧版目录，已改读 processed_v3：{resolve_plot_processed_root(original)}"


# 功能说明：复制请求字典并把其中路径字段统一转换成绝对路径。
def normalize_path_options(value):
    """Normalize file overrides in nested request/config dictionaries."""
    if isinstance(value, list):
        return [normalize_path_options(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if isinstance(key, str) and key.endswith(("_path", "_root", "_file")) and isinstance(item, (str, Path)) and item:
            result[key] = str(absolute_path(item))
        else:
            result[key] = normalize_path_options(item)
    return result
