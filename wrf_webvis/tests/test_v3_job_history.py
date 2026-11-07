from __future__ import annotations

from wrf_webvis.app.services.progress import ProgressStore


def test_job_history_persists_and_marks_running_jobs_interrupted(tmp_path):
    database = tmp_path / "jobs.sqlite"
    first = ProgressStore(database_path=database)
    first.start("job-1", "处理 exp", status="queued", kind="processing", request={"experiment": "exp"})
    first.running("job-1")
    first.add("job-1", "1/2 切片完成")

    restarted = ProgressStore(database_path=database)
    job = restarted.get("job-1")
    assert job["status"] == "interrupted"
    assert job["kind"] == "processing"
    assert job["request"] == {"experiment": "exp"}
    assert job["messages"][-1]["message"] == "服务重启，任务已中断；可重新提交并复用完成切片"
    assert restarted.list_jobs()[0]["id"] == "job-1"


def test_job_history_keeps_completed_result(tmp_path):
    database = tmp_path / "jobs.sqlite"
    first = ProgressStore(database_path=database)
    first.start("job-2", "处理 exp", kind="processing")
    first.complete("job-2", {"profile_id": "default"})
    restarted = ProgressStore(database_path=database)
    assert restarted.get("job-2")["status"] == "complete"
    assert restarted.get("job-2")["result"] == {"profile_id": "default"}


def test_job_history_filters_processing_after_restart(tmp_path):
    database = tmp_path / "jobs.sqlite"
    first = ProgressStore(database_path=database)
    first.start("process-old", "processing", kind="processing")
    first.start("plot-new", "plot", kind="plot")
    restarted = ProgressStore(database_path=database)
    assert [job["id"] for job in restarted.list_jobs(kind="processing")] == ["process-old"]
    assert [job["id"] for job in restarted.list_jobs()] == ["plot-new", "process-old"]
