"""数据集（2026-11-02）：处理结果的输出根目录。

- ``post``：用户在网页上亲自选择处理的数据（``project_config.POST_ROOT``，默认 /Volumes/land_1/post）。
- ``v3``：原有 processed_v3（``project_config.OUTPUT_ROOT``），里面很多是以前让 AI 算的。

两个根目录结构相同、互不复用。``V3ManagementService`` 本身按 ``output_root`` 参数化，
所以每个数据集就是一个独立的服务实例；接口不带 dataset 时用 v3（保持 wrf-lab、旧历史任务的行为）。
"""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any

from .v3_management import V3ManagementService, default_summary_cache_path

DEFAULT_API_DATASET = "v3"
WEB_DEFAULT_DATASET = "post"
LABELS = {"post": "post（网页处理）", "v3": "processed_v3（旧数据）"}

# 测试可直接替换其中的服务实例（monkeypatch.setitem(datasets.SERVICES, "post", ...)）。
SERVICES: dict[str, V3ManagementService] = {}
_LOCK = RLock()


def dataset_roots() -> dict[str, Path]:
    import os

    from 处理数据_v3 import project_config

    roots = dict(getattr(project_config, "DATASETS", None) or {"v3": project_config.OUTPUT_ROOT})
    # 测试用：WRF_WEBVIS_POST_ROOT 把 post 指到临时目录，免得读到真实 /Volumes/land_1/post（根目录 conftest.py 设置）。
    override = os.environ.get("WRF_WEBVIS_POST_ROOT", "").strip()
    if override and "post" in roots:
        roots["post"] = override
    return {str(key): Path(value).expanduser() for key, value in roots.items()}


def resolve_service(dataset: str | None, v3_service: V3ManagementService) -> V3ManagementService:
    """v3 返回调用方模块里的服务（测试会替换它）；其他数据集按根目录各建一个服务实例并缓存。"""
    key = str(dataset or DEFAULT_API_DATASET)
    if key == "v3":
        return v3_service
    with _LOCK:
        if key in SERVICES:
            return SERVICES[key]
        roots = dataset_roots()
        if key not in roots:
            raise ValueError(f"未知数据集：{key}（可用：{', '.join(sorted(set(roots) | {'v3'}))}）")
        # 已处理文件摘要缓存按数据集分开，避免两个实例互相覆盖同一个 JSON。
        cache = default_summary_cache_path()
        service = V3ManagementService(
            output_root=roots[key],
            summary_cache_path=cache.with_name(f"{cache.stem}_{key}{cache.suffix}"),
        )
        SERVICES[key] = service
        return service


def dataset_ids() -> list[str]:
    roots = dataset_roots()
    ordered = [key for key in (WEB_DEFAULT_DATASET, "v3") if key in roots or key == "v3"]
    return ordered + sorted(key for key in roots if key not in ordered)


def dataset_list(v3_service: V3ManagementService) -> list[dict[str, Any]]:
    rows = []
    for key in dataset_ids():
        try:
            service = resolve_service(key, v3_service)
        except ValueError:
            continue
        rows.append({
            "id": key, "label": LABELS.get(key, key), "root": str(service.output_root),
            "exists": service.output_root.is_dir(), "web_default": key == WEB_DEFAULT_DATASET,
        })
    return rows
