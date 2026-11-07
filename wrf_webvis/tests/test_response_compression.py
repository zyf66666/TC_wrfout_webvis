from fastapi.testclient import TestClient
from wrf_webvis.app.main import app


def test_dashboard_response_is_compressed_without_changing_payload(tmp_path, monkeypatch):
    from wrf_webvis.app.api import dashboards
    from wrf_webvis.app.storage.dashboard_store import DashboardStore
    store = DashboardStore(tmp_path)
    store.create(title='saved plot parameters ' * 300)
    monkeypatch.setattr(dashboards, 'store', store)
    with TestClient(app, client=('127.0.0.1', 50000)) as client:  # 本机直连＝root（2026-11-06 起公网要登录）
        plain = client.get('/api/dashboards', headers={'Accept-Encoding': 'identity'})
        compressed = client.get('/api/dashboards', headers={'Accept-Encoding': 'gzip'})
    assert compressed.status_code == 200
    assert compressed.headers.get('content-encoding') == 'gzip'
    assert compressed.json() == plain.json()
    assert int(compressed.headers['content-length']) < len(plain.content) / 2
