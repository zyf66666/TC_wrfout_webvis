"""Web jobs for explicitly selected v3 processing batches."""

from __future__ import annotations

import json
import os
import uuid
from functools import partial
from pathlib import Path
from threading import Condition, Lock, Thread

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from ..services import datasets
from ..services.isolated_job_runner import run_isolated_job
from ..services.progress import JobCancelled, callback_for_job, progress_store
from ..services.v3_management import V3ManagementService


router = APIRouter(prefix="/api/processing/v3", tags=["v3-processing"])
# processed_v3（数据集 v3）；dataset="post" 等由 services/datasets.py 按根目录取服务。
service = V3ManagementService()


def _processing_workers() -> int:
    """同时运行的处理任务数（2026-11-02 起可配置，project_config.WEB_PROCESSING_WORKERS，默认 3）。

    每个任务在独立子进程里跑，wrf-python 单线程，所以并行几个任务大致按核数加速；
    同一文件的写入由 NetCDF 文件锁串行，读不会读到写了一半的切片。
    """
    try:
        from 处理数据_v3 import project_config

        return max(1, min(16, int(getattr(project_config, "WEB_PROCESSING_WORKERS", 1))))
    except Exception:  # noqa: BLE001 - 读不到配置时退回一次一个
        return 1


PROCESSING_WORKERS = _processing_workers()  # project_config 里的默认值
MAX_PROCESSING_WORKERS = 4  # 网页上可选 1–4（16 GB 内存、外接机械硬盘）


class ProcessingSlots:
    """同时运行的处理任务数上限；2026-11-04 起网页上可随时修改（用户要求）。

    调大：排队中的任务马上补位开始；调小：正在运行的不受影响，跑完后按新上限放行。
    acquire/release 的用法与原来的 BoundedSemaphore 相同。
    """

    def __init__(self, limit: int):
        self._condition = Condition()
        self._limit = int(limit)
        self._active = 0

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def active(self) -> int:
        return self._active

    def set_limit(self, limit: int) -> None:
        with self._condition:
            self._limit = int(limit)
            self._condition.notify_all()

    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        with self._condition:
            if self._active < self._limit:
                self._active += 1
                return True
            if not blocking:
                return False
            self._condition.wait(timeout)
            if self._active < self._limit:
                self._active += 1
                return True
            return False

    def release(self) -> None:
        with self._condition:
            self._active = max(0, self._active - 1)
            self._condition.notify_all()


def _settings_path() -> Path:
    """网页上选的并行数存在这里，网页重启后仍有效（测试时由环境变量指到临时目录）。"""
    override = os.environ.get("WRF_WEBVIS_PROCESSING_SETTINGS")
    if override:
        return Path(override)
    from ..core.config import STORAGE_ROOT

    return Path(STORAGE_ROOT) / "processing_settings.json"


def _saved_workers() -> int:
    try:
        value = int(json.loads(_settings_path().read_text(encoding="utf-8"))["workers"])
    except (OSError, ValueError, KeyError, TypeError):
        return PROCESSING_WORKERS
    return value if 1 <= value <= MAX_PROCESSING_WORKERS else PROCESSING_WORKERS


processing_slot = ProcessingSlots(_saved_workers())
_SETTINGS_LOCK = Lock()


class ProcessingSettingsPayload(BaseModel):
    workers: int = Field(ge=1, le=MAX_PROCESSING_WORKERS)


@router.get("/settings")
def processing_settings():
    return {"workers": processing_slot.limit, "running": processing_slot.active,
            "max_workers": MAX_PROCESSING_WORKERS, "default_workers": PROCESSING_WORKERS}


@router.put("/settings")
def update_processing_settings(payload: ProcessingSettingsPayload):
    with _SETTINGS_LOCK:
        path = _settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps({"workers": payload.workers}), encoding="utf-8")
        os.replace(temporary, path)
        processing_slot.set_limit(payload.workers)
    return processing_settings()


class V3ProcessPayload(BaseModel):
    experiment: str
    domain: int = Field(ge=1, le=99)
    variables: list[str]
    times_minutes: list[int]
    heights_m: list[int] = Field(default_factory=list)
    profile_id: str = "default"
    options: dict = Field(default_factory=dict)
    force: bool = False
    clip_to_source_end: bool = False
    plan_hash: str | None = None
    # 2026-11-02 中心方案编号（处理数据_v3/center_schemes.json）；不给时沿用参数配置里的中心参数。
    center_scheme: str | None = None
    # 2026-11-02 数据集：输出根目录。不给时 v3（processed_v3，wrf-lab 与旧历史任务）；网页表单总会显式发送（默认 post）。
    dataset: str = datasets.DEFAULT_API_DATASET
    # 只用于任务标题（处理套餐的组名），不影响计划和计算。
    label: str | None = None


class V3BatchPayload(BaseModel):
    jobs: list[V3ProcessPayload]


def _payload_dict(payload: V3ProcessPayload) -> dict:
    return payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()


def _service(dataset: str | None) -> V3ManagementService:
    try:
        return datasets.resolve_service(dataset, service)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/plan")
def plan(payload: V3ProcessPayload):
    values = _payload_dict(payload)
    target = _service(values["dataset"])
    try:
        # 计划里已含输出根目录 root，换数据集后 plan_hash 自然不同。
        return {**target.plan(values), "dataset": values["dataset"]}
    except (ValueError, KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs")
def create_job(payload: V3ProcessPayload, background_tasks: BackgroundTasks):
    values = _payload_dict(payload)
    if not values.get("plan_hash"):
        raise HTTPException(status_code=400, detail="请先预览处理计划")
    target = _service(values["dataset"])
    try:
        current = target.verify_plan(values)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if current["plan_hash"] != values["plan_hash"]:
        raise HTTPException(status_code=409, detail="数据或参数已变化，请重新预览处理计划")
    if current["missing_source_minutes"]:
        raise HTTPException(status_code=422, detail={
            "message": "所选时次缺少 wrfout 源记录", "missing_source_minutes": current["missing_source_minutes"]
        })
    job_id = _register_job(values)
    background_tasks.add_task(_run_job, values, job_id, target)
    return {"job_id": job_id, "profile_id": current["profile_id"], "root": current["root"], "dataset": values["dataset"]}


# 2026-11-02 批量提交：先逐个重新核对计划，全部通过才登记并启动任务。
# 逐个提交时，第一个任务一开始写文件，同一试验后面几个请求的计划指纹（含输出文件的大小/时间）
# 就会变化而被拒（409）；处理套餐的多个组、多个时间分块写的是同一批文件，所以必须批量提交。
@router.post("/jobs/batch")
def create_jobs(payload: V3BatchPayload):
    items = [_payload_dict(item) for item in payload.jobs]
    if not items or len(items) > 200:
        raise HTTPException(status_code=400, detail="一次提交 1–200 个处理任务")
    checked, problems = [], []
    for index, values in enumerate(items):
        name = f"{values['experiment']}" + (f" · {values['label']}" if values.get("label") else "")
        if not values.get("plan_hash"):
            problems.append({"index": index, "name": name, "message": "请先预览处理计划"})
            continue
        target = _service(values["dataset"])
        try:
            current = target.verify_plan(values)  # 刚预览过且文件没变时不重做计划
        except (ValueError, KeyError, FileNotFoundError) as exc:
            problems.append({"index": index, "name": name, "message": str(exc)})
            continue
        if current["plan_hash"] != values["plan_hash"]:
            problems.append({"index": index, "name": name, "message": "数据或参数已变化，请重新预览处理计划"})
        elif current["missing_source_minutes"]:
            problems.append({"index": index, "name": name, "message": "所选时次缺少 wrfout 源记录",
                             "missing_source_minutes": current["missing_source_minutes"]})
        else:
            checked.append((values, target, current))
    if problems:
        detail = "；".join(f"{item['name']}：{item['message']}" for item in problems[:5])
        raise HTTPException(status_code=409, detail={"message": f"{len(problems)} 个任务没有通过核对，全部未提交。{detail}", "problems": problems})
    jobs = []
    for values, target, current in checked:
        job_id = _register_job(values)
        # 不用 BackgroundTasks：同一请求的后台任务会一个接一个执行，无法并行。
        Thread(target=_run_job, args=(values, job_id, target), name=f"processing-{job_id}", daemon=True).start()
        jobs.append({"job_id": job_id, "profile_id": current["profile_id"], "root": current["root"],
                     "dataset": values["dataset"], "experiment": values["experiment"], "label": values.get("label")})
    return {"jobs": jobs, "workers": processing_slot.limit}


def _register_job(values: dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    title = f"处理 {values['experiment']} d{values['domain']:02d}"
    if values.get("dataset", datasets.DEFAULT_API_DATASET) != datasets.DEFAULT_API_DATASET:
        title += f" · {values['dataset']}"
    if values.get("label"):
        title += f" · {values['label']}"
    progress_store.start(job_id, title, status="queued", kind="processing", request=values)
    return job_id


def _child_process(values: dict, output_root, source_directories, default_options, progress) -> dict:
    return V3ManagementService(output_root, source_directories, default_options).run(values, progress=progress)


def _run_job(values: dict, job_id: str, target: V3ManagementService | None = None) -> None:
    target = target or service
    callback = callback_for_job(job_id)
    try:
        if not processing_slot.acquire(blocking=False):
            callback(f"已有 {processing_slot.active} 个处理任务在运行（同时运行上限 {processing_slot.limit}），正在排队")
            while not processing_slot.acquire(timeout=0.2):
                callback.check_cancelled()
        try:
            result = run_isolated_job(
                partial(
                    _child_process, values, target.output_root,
                    target.source_directories, target.default_options,
                ), progress=callback,
                cancel_check=callback.check_cancelled,
            )
        finally:
            processing_slot.release()
        progress_store.cancel_if_requested(job_id)
        progress_store.complete(job_id, {**result, "dataset": values.get("dataset", datasets.DEFAULT_API_DATASET)} if isinstance(result, dict) else result)
    except JobCancelled:
        progress_store.canceled(job_id)
    except Exception as exc:  # noqa: BLE001
        progress_store.fail(job_id, str(exc))
