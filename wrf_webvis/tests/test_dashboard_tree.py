"""子看板（两级看板树，2026-11-06）：旧库迁移、建子看板、移动规则、删除保护、接口。"""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient


def _old_database(root):
    """建一个 2026-11-06 之前格式的看板库（dashboards 表没有 parent_id）。"""
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "dashboards.sqlite")
    conn.executescript("""
        CREATE TABLE dashboards (id TEXT PRIMARY KEY, position INTEGER NOT NULL, title TEXT NOT NULL,
                                 metadata TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE images (dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE, id TEXT NOT NULL,
                             position INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY (dashboard_id, id));
        CREATE TABLE store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO store_meta VALUES ('legacy_json_imported', '{}');
        INSERT INTO dashboards VALUES ('teacher', 0, '和老师讨论', '{}'), ('other', 1, '其他', '{}');
    """)
    image = {"id": "img", "title": "图", "image_path": "/static/figures/a.png", "request": {}, "metadata": {}}
    conn.execute("INSERT INTO images VALUES ('teacher', 'img', 0, ?)", (json.dumps(image),))
    conn.commit()
    conn.close()


def test_old_database_gets_parent_column_after_a_backup(tmp_path):
    from wrf_webvis.app.storage.dashboard_store import DashboardStore
    _old_database(tmp_path)
    store = DashboardStore(tmp_path)
    boards = store.list()
    assert [(item.id, item.title, item.parent_id) for item in boards] == [("teacher", "和老师讨论", None), ("other", "其他", None)]
    assert [image.id for image in boards[0].images] == ["img"]
    backups = sorted(tmp_path.glob("dashboards.sqlite.bak-*"))
    assert len(backups) == 1
    old = sqlite3.connect(backups[0])
    assert "parent_id" not in {row[1] for row in old.execute("PRAGMA table_info(dashboards)")}
    assert old.execute("SELECT count(*) FROM images").fetchone()[0] == 1
    DashboardStore(tmp_path).list()
    assert len(list(tmp_path.glob("dashboards.sqlite.bak-*"))) == 1  # 只迁移、备份一次


def test_new_database_needs_no_backup(tmp_path):
    from wrf_webvis.app.storage.dashboard_store import DashboardStore
    DashboardStore(tmp_path).create("x")
    assert not list(tmp_path.glob("*.bak-*"))


def test_two_level_rules(tmp_path):
    from wrf_webvis.app.storage.dashboard_store import DashboardStore, DashboardTreeError
    store = DashboardStore(tmp_path)
    teacher = store.create("和老师讨论")
    other = store.create("其他")
    child = store.create("试验强度", parent_id=teacher.id)
    assert child.parent_id == teacher.id and store.get(child.id).parent_id == teacher.id
    with pytest.raises(DashboardTreeError):
        store.create("三级", parent_id=child.id)
    with pytest.raises(KeyError):
        store.create("x", parent_id="missing")
    with pytest.raises(DashboardTreeError):
        store.set_parent(teacher.id, other.id)  # 有子看板的不能再挂到别人下面
    with pytest.raises(DashboardTreeError):
        store.set_parent(other.id, other.id)
    with pytest.raises(DashboardTreeError):
        store.set_parent(other.id, child.id)
    assert store.set_parent(other.id, teacher.id).parent_id == teacher.id
    assert store.parent_map() == {teacher.id: None, other.id: teacher.id, child.id: teacher.id}
    assert store.set_parent(other.id, None).parent_id is None
    with pytest.raises(DashboardTreeError):
        store.delete(teacher.id)  # 不级联删除
    store.delete(child.id)
    store.delete(teacher.id)
    assert [item.id for item in store.list()] == [other.id]


def test_tree_api(tmp_path, monkeypatch):
    from wrf_webvis.app.main import app
    from wrf_webvis.app.api import dashboards
    from wrf_webvis.app.storage.dashboard_store import DashboardStore
    monkeypatch.setattr(dashboards, "store", DashboardStore(tmp_path))
    client = TestClient(app, client=("127.0.0.1", 50000))
    teacher = client.post("/api/dashboards", json={"title": "和老师讨论"}).json()
    child = client.post("/api/dashboards", json={"title": "涡旋倾斜", "parent_id": teacher["id"]})
    assert child.status_code == 200 and child.json()["parent_id"] == teacher["id"]
    assert client.post("/api/dashboards", json={"title": "x", "parent_id": child.json()["id"]}).status_code == 409
    assert client.post("/api/dashboards", json={"title": "x", "parent_id": "missing"}).status_code == 404
    assert client.post("/api/dashboards", json={"title": "x", "parent_id": 3}).status_code == 422
    other = client.post("/api/dashboards", json={"title": "其他"}).json()
    moved = client.patch(f"/api/dashboards/{other['id']}/parent", json={"parent_id": teacher["id"]})
    assert moved.status_code == 200 and moved.json()["parent_id"] == teacher["id"]
    assert client.patch(f"/api/dashboards/{teacher['id']}/parent", json={"parent_id": other["id"]}).status_code == 409
    assert client.patch(f"/api/dashboards/{other['id']}/parent", json={"parent_id": None}).json()["parent_id"] is None
    assert client.patch("/api/dashboards/missing/parent", json={"parent_id": None}).status_code == 404
    assert client.delete(f"/api/dashboards/{teacher['id']}").status_code == 409
    listed = {item["id"]: item["parent_id"] for item in client.get("/api/dashboards").json()}
    assert listed == {teacher["id"]: None, child.json()["id"]: teacher["id"], other["id"]: None}


def test_backup_export_keeps_parent(tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server_tools"))
    try:
        import backup_research
    finally:
        sys.path.pop(0)
    from wrf_webvis.app.storage.dashboard_store import DashboardStore
    store = DashboardStore(tmp_path)
    parent = store.create("父")
    store.create("子", parent_id=parent.id)
    boards = backup_research.read_dashboards(sqlite3.connect(store.path))
    assert [board["parent_id"] for board in boards] == [None, parent.id]
    _old_database(tmp_path / "old")
    old = backup_research.read_dashboards(sqlite3.connect(tmp_path / "old" / "dashboards.sqlite"))
    assert [board["parent_id"] for board in old] == [None, None]


def test_summary_list_and_light_dashboard(tmp_path, monkeypatch):
    """加速（2026-11-06）：侧栏只取摘要；打开看板时去掉 used_files。"""
    from wrf_webvis.app.main import app
    from wrf_webvis.app.api import dashboards
    from wrf_webvis.app.core.schemas import DashboardImage
    from wrf_webvis.app.storage.dashboard_store import DashboardStore
    store = DashboardStore(tmp_path)
    monkeypatch.setattr(dashboards, "store", store)
    parent = store.create("父")
    child = store.create("子", parent_id=parent.id)
    store.add_image(parent.id, DashboardImage(id="img", title="图", image_path="/static/figures/a.png", request={"x": 1},
                                              metadata={"used_files": ["a.nc", "b.nc"], "axes": {"panels": []}}))
    client = TestClient(app, client=("127.0.0.1", 50000))
    assert client.get("/api/dashboards?summary=1").json() == [
        {"id": parent.id, "title": "父", "parent_id": None, "image_count": 1},
        {"id": child.id, "title": "子", "parent_id": parent.id, "image_count": 0},
    ]
    light = client.get(f"/api/dashboards/{parent.id}?light=1").json()["images"][0]
    assert "used_files" not in light["metadata"] and light["metadata"]["used_files_count"] == 2
    assert light["metadata"]["axes"] == {"panels": []} and light["request"] == {"x": 1}
    assert client.get(f"/api/dashboards/{parent.id}").json()["images"][0]["metadata"]["used_files"] == ["a.nc", "b.nc"]
    assert store.get(parent.id).images[0].metadata["used_files"] == ["a.nc", "b.nc"]  # 库里不动


def test_figure_thumbnail_is_small_cached_and_needs_login(tmp_path, monkeypatch):
    from PIL import Image
    from wrf_webvis.app.main import app
    from wrf_webvis.app.api import figures
    figure_root = tmp_path / "figures"
    figure_root.mkdir()
    Image.new("RGBA", (2400, 1200), (200, 30, 30, 255)).save(figure_root / "abc_1.png")
    monkeypatch.setattr(figures, "FIGURE_ROOT", figure_root)
    monkeypatch.setattr(figures, "THUMB_ROOT", tmp_path / "thumbs")
    local = TestClient(app, client=("127.0.0.1", 50000))
    response = local.get("/api/figures/thumb/abc_1.png?w=800")
    assert response.status_code == 200 and "max-age" in response.headers["cache-control"]
    from io import BytesIO
    with Image.open(BytesIO(response.content)) as thumb:
        assert thumb.size == (800, 400)
    cached = list((tmp_path / "thumbs").iterdir())
    assert len(cached) == 1
    local.get("/api/figures/thumb/abc_1.png?w=800")
    assert list((tmp_path / "thumbs").iterdir()) == cached
    assert local.get("/api/figures/thumb/../secret.png").status_code == 404
    assert local.get("/api/figures/thumb/missing.png").status_code == 404
    public = TestClient(app, client=("127.0.0.1", 50000), headers={"X-Real-IP": "203.0.113.8", "X-Forwarded-For": "203.0.113.8"})
    assert public.get("/api/figures/thumb/abc_1.png").status_code == 401
