"""账号登录、账号管理接口、按账号限制看板与批注（2026-11-06）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def forwarded(ip="203.0.113.8"):
    return {"X-Real-IP": ip, "X-Forwarded-For": ip}


@pytest.fixture
def env(monkeypatch, tmp_path):
    from wrf_webvis.app.main import app
    from wrf_webvis.app.api import auth, dashboards
    from wrf_webvis.app.core.schemas import DashboardImage
    from wrf_webvis.app.storage import user_store
    from wrf_webvis.app.storage.dashboard_store import DashboardStore

    store = DashboardStore(tmp_path / "dashboards")
    users = user_store.UserStore(tmp_path / "users")
    monkeypatch.setattr(dashboards, "store", store)
    monkeypatch.setattr(user_store, "store", users)
    auth._failures.clear()
    teacher = store.create("和老师讨论")
    strength = store.create("试验强度", parent_id=teacher.id)
    other = store.create("其他")
    for board in (teacher, strength, other):
        store.add_image(board.id, DashboardImage(id="img", title="图", image_path="/static/figures/a.png", request={}))
    ids = {"teacher": teacher.id, "strength": strength.id, "other": other.id}
    local = TestClient(app, client=("127.0.0.1", 50000))
    return app, local, users, store, ids


def public(app):
    return TestClient(app, client=("127.0.0.1", 50000), headers=forwarded())


def login(app, username, password="123456"):
    client = public(app)
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def test_login_logout_and_cookie(env):
    app, local, users, _, ids = env
    users.create_user("qqli", "123456", "user", dashboards=[ids["teacher"]])
    client = public(app)
    assert client.get("/api/access").json()["authenticated"] is False
    bad = client.post("/api/auth/login", json={"username": "qqli", "password": "wrong"})
    assert bad.status_code == 401 and bad.json()["detail"]["code"] == "BAD_LOGIN"
    assert client.post("/api/auth/login", json={"username": "ghost", "password": "123456"}).status_code == 401
    ok = client.post("/api/auth/login", json={"username": "qqli", "password": "123456"})
    assert ok.status_code == 200 and ok.json()["username"] == "qqli" and ok.json()["can_write"] is False
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "max-age=" in cookie
    access = client.get("/api/access").json()
    assert access == {"authenticated": True, "username": "qqli", "role": "user", "is_local": False, "read_only": True,
                      "can_write": False, "can_annotate": False, "can_view_plot_settings": False, "can_plot": False,
                      "pages": []}
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/dashboards").status_code == 401


def test_cookie_is_secure_behind_https_proxy(env):
    app, _, users, _, _ = env
    users.create_user("qqli", "123456")
    client = TestClient(app, client=("127.0.0.1", 50000), headers={**forwarded(), "X-Forwarded-Proto": "https"})
    response = client.post("/api/auth/login", json={"username": "qqli", "password": "123456"})
    assert "secure" in response.headers["set-cookie"].lower()


def test_login_is_throttled_after_repeated_failures(env):
    app, _, users, _, _ = env
    users.create_user("qqli", "123456")
    client = public(app)
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "qqli", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "qqli", "password": "123456"}).status_code == 429


def test_login_page_redirects_logged_in_users_and_rejects_foreign_next(env):
    app, local, users, _, _ = env
    assert local.get("/login?next=//evil.example", follow_redirects=False).headers["location"] == "/"
    assert local.get("/login?next=/static/index.html", follow_redirects=False).headers["location"] == "/static/index.html"
    assert "loginForm" in public(app).get("/login").text


def test_viewer_sees_only_allowed_dashboards_and_children(env):
    app, _, users, store, ids = env
    users.create_user("qqli", "123456", "user", dashboards=[ids["teacher"]])
    client = login(app, "qqli")
    listed = client.get("/api/dashboards").json()
    assert sorted(item["id"] for item in listed) == sorted([ids["teacher"], ids["strength"]])
    assert client.get(f"/api/dashboards/{ids['strength']}").status_code == 200
    assert client.get(f"/api/dashboards/{ids['strength']}/revision").status_code == 200
    assert client.get(f"/api/dashboards/{ids['teacher']}/annotations").status_code == 200
    assert client.get(f"/api/dashboards/{ids['teacher']}/images/img/axes").status_code == 200
    for path in ("", "/images", "/revision", "/annotations", "/images/img/annotations", "/images/img/axes"):
        assert client.get(f"/api/dashboards/{ids['other']}{path}").status_code == 403, path
    assert client.get("/api/dashboards/does-not-exist").status_code == 403
    assert client.get(f"/api/dashboards/{ids['teacher']}/deleted-images").status_code == 403
    # 以后新建的子看板自动可见
    later = store.create("涡旋倾斜", parent_id=ids["teacher"])
    assert client.get(f"/api/dashboards/{later.id}").status_code == 200
    # 只勾子看板时看不到父看板
    users.update_user("qqli", dashboards=[ids["strength"]])
    assert [item["id"] for item in client.get("/api/dashboards").json()] == [ids["strength"]]
    assert client.get(f"/api/dashboards/{ids['teacher']}").status_code == 403
    users.update_user("qqli", dashboards="*")
    assert len(client.get("/api/dashboards").json()) == 4


def test_viewer_cannot_write_or_open_other_pages(env):
    app, _, users, _, ids = env
    users.create_user("qqli", "123456", "user", dashboards="*")
    client = login(app, "qqli")
    board = ids["teacher"]
    assert client.put(f"/api/dashboards/{board}/images/img/note", json={"note": "x"}).status_code == 403
    assert client.post(f"/api/dashboards/{board}/images/img/annotations",
                       json={"shapes": [{"kind": "pin", "points": [[0.5, 0.5]]}]}).status_code == 403
    assert client.delete(f"/api/dashboards/{board}/images/img").status_code == 403
    assert client.post("/api/dashboards", json={"title": "x"}).status_code == 403
    for path in ("/api/data/catalog", "/api/jobs", "/api/logs/recent", "/api/users", "/api/variable-preferences"):
        assert client.get(path).status_code == 403, path
    assert client.get("/static/app.js").status_code == 200


def test_data_page_permission_is_read_only(env):
    app, _, users, _, _ = env
    users.create_user("reader", "123456", "user", pages=["data"])
    client = login(app, "reader")
    assert client.get("/api/data/catalog").status_code == 200
    assert client.post("/api/processing/v3/plan", json={}).status_code == 403


def test_annotator_can_annotate_allowed_boards_and_only_change_own(env):
    app, local, users, store, ids = env
    users.create_user("qqli", "123456", "user", can_annotate=True, dashboards=[ids["teacher"]])
    client = login(app, "qqli")
    board = ids["strength"]  # 子看板，继承父看板权限
    shape = {"shapes": [{"kind": "pin", "points": [[0.5, 0.5]]}], "author": "ai"}
    mine = client.post(f"/api/dashboards/{board}/images/img/annotations", json=shape)
    assert mine.status_code == 200, mine.text
    assert mine.json()["created_by"] == "qqli" and mine.json()["author"] == "user"
    theirs = local.post(f"/api/dashboards/{board}/images/img/annotations", json=shape).json()
    assert theirs["created_by"] == "" and theirs["author"] == "ai"
    assert client.patch(f"/api/dashboards/{board}/images/img/annotations/{mine.json()['id']}", json={"note": "改"}).status_code == 200
    assert client.patch(f"/api/dashboards/{board}/images/img/annotations/{theirs['id']}", json={"note": "改"}).status_code == 403
    assert client.delete(f"/api/dashboards/{board}/images/img/annotations/{theirs['id']}").status_code == 403
    assert client.delete(f"/api/dashboards/{board}/images/img/annotations/{mine.json()['id']}").status_code == 200
    note = client.put(f"/api/dashboards/{board}/images/img/note", json={"note": "老师的意见"})
    assert note.status_code == 200
    image = next(item for item in note.json()["images"] if item["id"] == "img")
    assert image["note"] == "老师的意见" and image["note_updated_by"] == "qqli"
    assert client.put(f"/api/dashboards/{ids['other']}/images/img/note", json={"note": "x"}).status_code == 403
    assert client.post(f"/api/dashboards/{ids['other']}/images/img/annotations", json=shape).status_code == 403
    assert client.delete(f"/api/dashboards/{board}/images/img").status_code == 403


def test_users_api_is_root_only_and_manages_accounts(env):
    app, local, users, _, ids = env
    assert public(app).get("/api/users").status_code == 401
    users.create_user("viewer", "123456")
    assert login(app, "viewer").get("/api/users").status_code == 403
    created = local.post("/api/users", json={"username": "qqli", "password": "123456", "dashboards": [ids["teacher"]],
                                             "pages": ["data"], "can_annotate": True})
    assert created.status_code == 200, created.text
    assert "password_hash" not in created.text and "123456" not in created.text
    listing = local.get("/api/users").json()
    assert set(listing["pages"]) == {"data"}
    assert [user["username"] for user in listing["users"]] == ["qqli", "viewer"]
    assert local.post("/api/users", json={"username": "qqli", "password": "123456"}).status_code == 422
    assert local.post("/api/users", json={"username": "x", "password": "123"}).status_code == 422
    assert local.post("/api/users", json={"username": "x", "password": "123456", "pages": ["nope"]}).status_code == 422
    client = login(app, "qqli")
    assert local.patch("/api/users/qqli", json={"pages": []}).json()["pages"] == []
    assert client.get("/api/data/catalog").status_code == 403  # 改权限立即生效
    assert local.post("/api/users/qqli/password", json={"password": "abcdef"}).status_code == 200
    assert client.get("/api/dashboards").status_code == 401  # 重设密码后旧登录失效
    client = login(app, "qqli", "abcdef")
    assert local.patch("/api/users/qqli", json={"disabled": True}).status_code == 200
    assert client.get("/api/dashboards").status_code == 401
    assert public(app).post("/api/auth/login", json={"username": "qqli", "password": "abcdef"}).status_code == 401
    assert local.delete("/api/users/qqli").status_code == 200
    assert local.delete("/api/users/qqli").status_code == 404
    assert local.patch("/api/users/ghost", json={"role": "root"}).status_code == 404


def test_change_own_password(env):
    app, _, users, _, _ = env
    users.create_user("qqli", "123456")
    client = login(app, "qqli")
    assert client.post("/api/auth/password", json={"old_password": "bad", "new_password": "abcdef"}).status_code == 403
    assert client.post("/api/auth/password", json={"old_password": "123456", "new_password": "abc"}).status_code == 422
    assert client.post("/api/auth/password", json={"old_password": "123456", "new_password": "abcdef"}).status_code == 200
    assert client.get("/api/auth/me").json()["username"] == "qqli"  # 当前设备保持登录
    assert users.verify("qqli", "abcdef") and not users.verify("qqli", "123456")


def test_root_account_has_full_access_over_tunnel(env):
    app, _, users, _, ids = env
    users.create_user("root", "rootpass", "root")
    client = login(app, "root", "rootpass")
    assert len(client.get("/api/dashboards").json()) == 3
    assert client.post("/api/dashboards", json={"title": "新", "parent_id": ids["teacher"]}).status_code == 200
    assert client.get("/api/users").status_code == 200
    assert client.get(f"/api/dashboards/{ids['teacher']}/deleted-images").status_code == 200


def test_passwords_are_hashed_with_salt(tmp_path):
    from wrf_webvis.app.storage.user_store import UserStore, verify_password
    import sqlite3
    users = UserStore(tmp_path)
    users.create_user("a", "same-password")
    users.create_user("b", "same-password")
    rows = dict(sqlite3.connect(users.path).execute("select username, password_hash from users"))
    assert rows["a"] != rows["b"] and "same-password" not in rows["a"]
    assert rows["a"].startswith("scrypt$") and verify_password("same-password", rows["a"])


def test_cli_creates_root_account(tmp_path, monkeypatch, capsys):
    import sys
    from wrf_webvis.app import cli
    from wrf_webvis.app.storage import user_store
    monkeypatch.setattr(user_store, "store", user_store.UserStore(tmp_path))
    monkeypatch.setattr(cli, "_ask_password", lambda: "rootpass")
    monkeypatch.setattr(sys, "argv", ["cli", "users", "set-password", "root"])
    cli.main()
    assert user_store.store.get_user("root")["role"] == "root"
    assert user_store.store.verify("root", "rootpass")
    monkeypatch.setattr(sys, "argv", ["cli", "users", "list"])
    cli.main()
    assert "root\troot" in capsys.readouterr().out


# ---------------------------------------------------------------------- 看绘图设置 / 出图（11-06 下午）
@pytest.fixture
def plot_env(env, monkeypatch, tmp_path):
    from wrf_webvis.app.api import dashboards, plots, variable_preferences
    monkeypatch.setattr(variable_preferences, "DB_PATH", tmp_path / "preferences.sqlite")
    monkeypatch.setattr(dashboards, "_run_image_job", lambda *args, **kwargs: None)  # 不真的出图
    monkeypatch.setattr(plots.plot_service, "dry_run", lambda request: {"plan": {"missing_files": []}})
    return env


def test_plot_settings_viewer_can_read_preferences_but_not_plot(plot_env):
    app, _, users, _, ids = plot_env
    users.create_user("watcher", "123456", "user", dashboards=[ids["teacher"]], can_view_plot_settings=True)
    users.create_user("plain", "123456", "user", dashboards=[ids["teacher"]])
    watcher = login(app, "watcher")
    access = watcher.get("/api/access").json()
    assert access["can_view_plot_settings"] is True and access["can_plot"] is False
    assert watcher.get("/api/variable-preferences").status_code == 200
    assert watcher.post(f"/api/dashboards/{ids['teacher']}/images/jobs", json={}).status_code == 403
    assert watcher.post("/api/plots/dry-run", json={}).status_code == 403
    assert login(app, "plain").get("/api/variable-preferences").status_code == 403


def test_plotter_can_add_images_to_allowed_boards_only(plot_env):
    app, local, users, store, ids = plot_env
    users.create_user("plotter", "123456", "user", dashboards=[ids["teacher"]], can_plot=True)
    assert users.get_user("plotter")["can_view_plot_settings"] is True  # 能出图就能看参数
    client = login(app, "plotter")
    job = client.post(f"/api/dashboards/{ids['strength']}/images/jobs", json={"title": "老师的图"})
    assert job.status_code == 200, job.text
    job_id = job.json()["job_id"]
    assert client.get(f"/api/jobs/{job_id}/progress").status_code == 200
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    assert client.post("/api/plots/dry-run", json={}).status_code == 200
    # 别的看板、自定义代码、自定义目录、单独文件路径、wrfout 都不行
    assert client.post(f"/api/dashboards/{ids['other']}/images/jobs", json={}).status_code == 403
    assert client.post(f"/api/dashboards/{ids['teacher']}/images/jobs", json={"fig_type": 9, "custom_code": "print(1)"}).status_code == 403
    assert client.post(f"/api/dashboards/{ids['teacher']}/images/jobs", json={"data_root": "/etc"}).status_code == 403
    assert client.post("/api/plots/dry-run", json={"data_root": "/tmp"}).status_code == 403
    bad = client.post(f"/api/dashboards/{ids['teacher']}/images/jobs", json={"extra": {"rmw_path": "/etc/passwd"}})
    assert bad.status_code == 403 and "rmw_path" in bad.text
    assert client.post(f"/api/dashboards/{ids['teacher']}/images/jobs", json={"wrfout_root": "/Volumes/WRF"}).status_code == 403
    # 仍然不能删图、改名、建看板、走不带看板的绘图接口
    assert client.delete(f"/api/dashboards/{ids['teacher']}/images/img").status_code == 403
    assert client.patch(f"/api/dashboards/{ids['teacher']}/images/img", json={"title": "x"}).status_code == 403
    assert client.post("/api/dashboards", json={"title": "x"}).status_code == 403
    assert client.post("/api/plots/jobs", json={}).status_code == 403
    # 别人的任务看不到、停不了
    from wrf_webvis.app.services.progress import progress_store
    progress_store.start("root-job-x", "root 的任务")
    assert client.get("/api/jobs/root-job-x/progress").status_code == 403
    assert client.post("/api/jobs/root-job-x/cancel").status_code == 403
    assert client.get("/api/jobs").status_code == 403
    assert local.get("/api/jobs/root-job-x/progress").status_code == 200
    # 取消“可出图”后立即失效
    users.update_user("plotter", can_plot=False)
    assert client.post(f"/api/dashboards/{ids['teacher']}/images/jobs", json={}).status_code == 403


def test_users_api_saves_plot_permissions(plot_env):
    app, local, users, _, ids = plot_env
    created = local.post("/api/users", json={"username": "p", "password": "123456", "can_plot": True}).json()
    assert created["can_plot"] is True and created["can_view_plot_settings"] is True
    changed = local.patch("/api/users/p", json={"can_plot": False, "can_view_plot_settings": False}).json()
    assert changed["can_plot"] is False and changed["can_view_plot_settings"] is False


def test_old_user_database_gets_plot_columns(tmp_path):
    import sqlite3
    from wrf_webvis.app.storage.user_store import UserStore, hash_password
    conn = sqlite3.connect(tmp_path / "users.sqlite")
    conn.executescript("""
        CREATE TABLE users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL,
            can_annotate INTEGER NOT NULL DEFAULT 0, pages TEXT NOT NULL DEFAULT '[]', dashboards TEXT NOT NULL DEFAULT '[]',
            disabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    """)
    conn.execute("INSERT INTO users VALUES ('qqli', ?, 'user', 1, '[]', '[\"a\"]', 0, 'x', 'x')", (hash_password("123456"),))
    conn.commit(); conn.close()
    users = UserStore(tmp_path)
    user = users.get_user("qqli")
    assert user["can_annotate"] is True and user["can_plot"] is False and user["can_view_plot_settings"] is False
    assert users.verify("qqli", "123456")


def test_plotter_can_delete_and_restore_only_own_images(plot_env):
    from wrf_webvis.app.api import dashboards
    from wrf_webvis.app.core.schemas import DashboardImage
    app, local, users, store, ids = plot_env
    board = ids["teacher"]
    users.create_user("plotter", "123456", "user", dashboards=[board], can_plot=True)
    users.create_user("watcher", "123456", "user", dashboards=[board], can_view_plot_settings=True)
    store.add_image(board, DashboardImage(id="mine", title="我的图", image_path="/static/figures/m.png", request={},
                                          created_by="plotter"))
    client = login(app, "plotter")
    assert client.delete(f"/api/dashboards/{board}/images/img").status_code == 403  # root 出的图
    assert client.delete(f"/api/dashboards/{board}/images/mine").status_code == 200
    assert client.post(f"/api/dashboards/{board}/images/mine/restore").status_code == 200
    assert [image.id for image in store.get(board).images] == ["img", "mine"]
    local.delete(f"/api/dashboards/{board}/images/img")
    assert client.post(f"/api/dashboards/{board}/images/img/restore").status_code == 403
    assert client.delete(f"/api/dashboards/{board}/images/missing").status_code == 404
    assert client.patch(f"/api/dashboards/{board}/images/mine", json={"title": "x"}).status_code == 403
    assert login(app, "watcher").delete(f"/api/dashboards/{board}/images/mine").status_code == 403
    # 出图账号出的图会记下是谁出的
    captured = {}
    original = dashboards._run_image_job
    try:
        dashboards._run_image_job = lambda *args: captured.setdefault("args", args)
        assert client.post(f"/api/dashboards/{board}/images/jobs", json={"title": "t"}).status_code == 200
    finally:
        dashboards._run_image_job = original
    assert captured["args"][-1] == "plotter"
