"""访问控制（2026-11-06 改为账号登录）：本机直连＝root；经 nginx 转发的公网访问必须登录；
普通账号默认拒绝，只开放勾选的看板、页面与批注。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app(monkeypatch, tmp_path):
    from wrf_webvis.app.main import app
    from wrf_webvis.app.api import dashboards
    from wrf_webvis.app.storage import user_store
    from wrf_webvis.app.storage.dashboard_store import DashboardStore

    monkeypatch.setattr(dashboards, "store", DashboardStore(tmp_path / "dashboards"))
    monkeypatch.setattr(user_store, "store", user_store.UserStore(tmp_path / "users"))
    return app


def forwarded(ip):
    return {"X-Real-IP": ip, "X-Forwarded-For": ip}


def public(app, ip="203.0.113.8"):
    """经 nginx 隧道进来的公网访问（对端是本机回环，带转发头）。"""
    return TestClient(app, client=("127.0.0.1", 50000), headers=forwarded(ip))


def login(app, username, password="123456", ip="203.0.113.8"):
    client = public(app, ip)
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def test_anonymous_public_visitor_cannot_read_or_write(app):
    from wrf_webvis.app.api.dashboards import store

    reader = public(app)
    before = store.list()
    response = reader.post("/api/dashboards", json={"title": "unauthorized"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "LOGIN_REQUIRED"
    assert store.list() == before
    assert reader.get("/api/dashboards").status_code == 401
    page = reader.get("/static/index.html", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"].startswith("/login?next=")
    figure = reader.get("/static/figures/anything.png", follow_redirects=False)
    assert figure.status_code == 303
    assert reader.get("/login").status_code == 200


@pytest.mark.parametrize("ip", ["216.36.108.120", "45.205.25.127", "127.0.0.1"])
def test_former_whitelisted_ips_now_need_login(app, ip):
    response = public(app, ip).post("/api/dashboards", json={"title": "x"})
    assert response.status_code == 401


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
def test_direct_local_access_is_root(app, peer):
    client = TestClient(app, client=(peer, 50000))
    access = client.get("/api/access").json()
    assert access["can_write"] is True and access["read_only"] is False and access["is_local"] is True
    assert client.post("/api/dashboards", json={"title": "local"}).status_code == 200


@pytest.mark.parametrize("peer,headers", [
    ("203.0.113.8", {}),
    ("203.0.113.8", forwarded("127.0.0.1")),
    ("127.0.0.1", forwarded("203.0.113.8")),
    ("127.0.0.1", {"X-Real-IP": "127.0.0.1"}),
    ("127.0.0.1", {"X-Real-IP": "216.36.108.120", "X-Forwarded-For": "203.0.113.8"}),
    ("127.0.0.1", forwarded("invalid")),
    ("127.0.0.1", [("X-Real-IP", "127.0.0.1"), ("X-Real-IP", "203.0.113.8"), ("X-Forwarded-For", "127.0.0.1")]),
    ("testclient", {}),
])
def test_permissions_fail_closed_and_cannot_be_spoofed(app, peer, headers):
    response = TestClient(app, client=(peer, 50000)).get("/api/access", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["authenticated"] is False and payload["can_write"] is False and payload["read_only"] is True
    assert response.headers["cache-control"] == "no-store"


def test_every_registered_mutation_is_blocked_before_validation(app):
    import re
    from wrf_webvis.app.storage import user_store
    user_store.store.create_user("viewer", "123456", "user", dashboards="*")
    anonymous = public(app)
    viewer = login(app, "viewer")
    checked = []
    for template, operations in app.openapi()["paths"].items():
        if template.startswith("/api/auth/"):
            continue
        for method in set(operations) & {"post", "put", "patch", "delete"}:
            path = re.sub(r"\{[^}]+\}", "missing", template)
            response = anonymous.request(method.upper(), path, json={})
            assert response.status_code == 401, (method, path, response.text)
            response = viewer.request(method.upper(), path, json={})
            assert response.status_code == 403, (method, path, response.text)
            assert response.json()["detail"]["code"] == "READ_ONLY"
            checked.append((method, path))
    assert len(checked) >= 20


def test_cross_site_posts_are_rejected_even_for_local_root(app):
    client = TestClient(app, client=("127.0.0.1", 50000))
    response = client.post("/api/dashboards", json={"title": "x"},
                           headers={"Origin": "https://evil.example", "Host": "127.0.0.1:8000"})
    assert response.status_code == 403 and response.json()["detail"]["code"] == "BAD_ORIGIN"
    same = client.post("/api/dashboards", json={"title": "x"},
                       headers={"Origin": "http://127.0.0.1:8000", "Host": "127.0.0.1:8000"})
    assert same.status_code == 200


def test_data_page_permission_is_read_only(app):
    from wrf_webvis.app.storage import user_store
    user_store.store.create_user("reader", "123456", "user", pages=["data"])
    reader = login(app, "reader")
    assert reader.get("/api/data/catalog").status_code == 200
    assert reader.post("/api/processing/v3/plan", json={}).status_code == 403
    assert public(app).get("/api/data/catalog").status_code == 401


def test_invalid_proxy_config_is_rejected(monkeypatch):
    from wrf_webvis.app.core.access import AccessPolicy
    monkeypatch.setenv("WRF_WEBVIS_TRUSTED_PROXY_IPS", "bad")
    with pytest.raises(ValueError, match="WRF_WEBVIS_TRUSTED_PROXY_IPS"):
        AccessPolicy.from_environment()
