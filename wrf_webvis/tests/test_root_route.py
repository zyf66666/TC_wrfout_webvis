from fastapi.testclient import TestClient

from app.main import app


# 功能说明：访问根路由，确认服务器把浏览器重定向到网页界面。
def test_root_redirects_to_the_web_interface():
    client = TestClient(app, client=("127.0.0.1", 50000))  # 本机直连；公网未登录会被带到登录页

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/static/index.html"
