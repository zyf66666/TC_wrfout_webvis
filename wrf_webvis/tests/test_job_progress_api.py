from fastapi.testclient import TestClient

from app.main import app
from app.services.progress import progress_store


# 功能说明：创建共享进度任务后由网页观察器读取并取消，确认两端看到同一任务状态。
def test_web_job_watcher_can_read_and_cancel_shared_progress_jobs():
    job_id = "web-watcher-test"
    progress_store.start(job_id, "web watcher test")
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.get(f"/api/jobs/{job_id}/progress")

    assert response.status_code == 200
    assert response.json()["status"] == "running"

    response = client.post(f"/api/jobs/{job_id}/cancel")

    assert response.status_code == 200
    assert response.json()["cancel_requested"] is True
