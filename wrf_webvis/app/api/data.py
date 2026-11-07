"""Read-only v3 source and processed-data inventory."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..services import datasets
from ..services.v3_management import V3ManagementService


router = APIRouter(prefix="/api/data", tags=["v3-data"])
# processed_v3（数据集 v3）。2026-11-02 起另有数据集 post（网页处理专用目录），见 services/datasets.py；
# 下面各接口的 dataset 参数不给时用 v3，与旧调用一致。
service = V3ManagementService()


def _service(dataset: str | None) -> V3ManagementService:
    try:
        return datasets.resolve_service(dataset, service)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/catalog")
def catalog(dataset: str = datasets.DEFAULT_API_DATASET):
    result = _service(dataset).catalog()
    result["dataset"] = dataset
    result["datasets"] = datasets.dataset_list(service)
    from .processing_v3 import MAX_PROCESSING_WORKERS, processing_slot

    # 同时运行的处理任务数（网页“同时运行任务数”可改，见 processing_v3.ProcessingSlots）
    result["processing_workers"] = processing_slot.limit
    result["max_processing_workers"] = MAX_PROCESSING_WORKERS
    return result


@router.get("/inventory")
def inventory(dataset: str | None = None):
    """不给 dataset 时合并所有数据集的清单；每项带 dataset 字段。"""
    names = [dataset] if dataset else datasets.dataset_ids()
    items = []
    for name in names:
        target = _service(name)
        if name != "v3" and not target.output_root.is_dir():
            continue  # post 还没处理过任何数据
        items.extend({**item, "dataset": name} for item in target.inventory()["items"])
    return {"items": items}


@router.get("/inventory/details")
def inventory_details(key: list[str] = Query(default=[])):
    """每个 key 为 “数据集|配置|试验|domain|变量”（旧写法“配置|试验|domain|变量”视为 v3）；一次最多 60 个。"""
    if len(key) > 60:
        raise HTTPException(status_code=400, detail="一次最多查询 60 个文件")
    items = []
    for value in key:
        parts = value.split("|")
        if len(parts) == 4:
            parts = [datasets.DEFAULT_API_DATASET, *parts]
        if len(parts) != 5 or not parts[3].isdigit():
            raise HTTPException(status_code=400, detail=f"无效的文件标识：{value}")
        name, profile_id, experiment, domain, variable = parts[0], parts[1], parts[2], int(parts[3]), parts[4]
        try:
            items.append({**_service(name).file_summary(profile_id, experiment, domain, variable), "dataset": name})
        except (ValueError, KeyError, HTTPException) as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            items.append({"dataset": name, "profile_id": profile_id, "experiment": experiment, "domain": domain,
                          "variable": variable, "error": detail})
    return {"items": items}


class ExperimentPayload(BaseModel):
    name: str
    path: str


@router.get("/experiments")
def experiments():
    return {"items": service.experiment_details()}


@router.post("/experiments")
def add_experiment(payload: ExperimentPayload):
    try:
        return service.add_experiment(payload.name, payload.path)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/experiments/{name}")
def remove_experiment(name: str):
    """只移除网页登记，不删除任何 wrfout 或已处理数据。"""
    try:
        return service.remove_experiment(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/variable_info")
def variable_info(names: list[str] = Query(default=[])):
    return service.variable_info(names or None)


@router.get("/sources")
def sources(experiment: str, domain: int = 3):
    try:
        return service.source_coverage(experiment, domain)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/coverage")
def coverage(experiment: str, domain: int, variable: str, profile_id: str = "default",
             dataset: str = datasets.DEFAULT_API_DATASET):
    target = _service(dataset)
    try:
        return {**target.coverage(profile_id, experiment, domain, variable), "dataset": dataset}
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/slice")
def slice_metadata(
    experiment: str, domain: int, variable: str, time_minutes: int,
    height_m: int | None = None, profile_id: str = "default", dataset: str = datasets.DEFAULT_API_DATASET,
):
    target = _service(dataset)
    try:
        return target.slice_metadata(profile_id, experiment, domain, variable, time_minutes, height_m)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
