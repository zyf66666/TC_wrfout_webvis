"""看板图片批注：存储、接口权限、数据坐标换算、坐标轴旁注文件。"""
from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient


def test_save_figure_records_axes_that_match_pixels(tmp_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image

    from wrf_webvis.app.plotting.figure_axes import data_to_image, read_axes_sidecar
    from wrf_webvis.app.plotting.rendering import save_figure

    fig, axes = plt.subplots(1, 2, figsize=(9, 4))
    for ax in axes:
        ax.set_xlim(0, 200)
        ax.set_ylim(0, 72)
        ax.set_xlabel("radius (km)")
    mesh = axes[1].pcolormesh(np.linspace(0, 200, 5), np.linspace(0, 72, 5), np.ones((4, 4)), cmap="Greys", vmin=0, vmax=9)
    fig.colorbar(mesh, ax=axes[1])
    axes[0].plot([50], [36], marker="s", color="red", markersize=8)
    target = tmp_path / "figure.png"
    save_figure(fig, target, dpi=100, bbox_inches="tight")
    plt.close(fig)
    meta = read_axes_sidecar(target)
    assert meta is not None and [panel["colorbar"] for panel in meta["panels"]] == [False, False, True]
    assert meta["panels"][0]["xlim"] == [0.0, 200.0] and meta["panels"][0]["xlabel"] == "radius (km)"
    image = np.asarray(Image.open(target).convert("RGB")).astype(int)
    red = (image[:, :, 0] > 200) & (image[:, :, 1] < 80) & (image[:, :, 2] < 80)
    ys, xs = np.nonzero(red)
    x, y = data_to_image(meta["panels"][0], 50, 36)
    assert abs(x * image.shape[1] - xs.mean()) < 3
    assert abs(y * image.shape[0] - ys.mean()) < 3


def test_data_to_image_handles_log_and_rejects_polar():
    from wrf_webvis.app.plotting.figure_axes import data_to_image

    panel = {"index": 0, "bbox": [0.1, 0.1, 0.9, 0.9], "xlim": [1, 100], "ylim": [10, 0], "xscale": "log", "yscale": "linear"}
    x, y = data_to_image(panel, 10, 10)  # ylim=(10, 0)：y 轴倒置，底边是 10
    assert x == pytest.approx(0.5) and y == pytest.approx(0.9)
    assert data_to_image(panel, 10, 0)[1] == pytest.approx(0.1)
    with pytest.raises(ValueError):
        data_to_image({"index": 1, "bbox": [0, 0, 1, 1], "projection": "polar"}, 0, 0)


@pytest.fixture
def client(monkeypatch, tmp_path):
    from wrf_webvis.app.main import app
    from wrf_webvis.app.api import dashboards
    from wrf_webvis.app.core.schemas import DashboardImage
    from wrf_webvis.app.storage.dashboard_store import DashboardStore

    store = DashboardStore(tmp_path / "dashboards")
    monkeypatch.setattr(dashboards, "store", store)
    monkeypatch.setattr(dashboards, "FIGURE_ROOT", tmp_path / "figures")
    board = store.create("批注测试")
    axes = {"version": 1, "pixel_size": [1000, 500], "panels": [
        {"index": 0, "bbox": [0.1, 0.1, 0.45, 0.9], "xlim": [0, 200], "ylim": [0, 72], "xscale": "linear",
         "yscale": "linear", "projection": "rectilinear", "colorbar": False, "title": "land", "xlabel": "r", "ylabel": "t"}]}
    store.add_image(board.id, DashboardImage(id="img1", title="time-radius", image_path="/static/figures/a.png",
                                             request={}, metadata={"axes": axes}))
    store.add_image(board.id, DashboardImage(id="old", title="old image", image_path="/static/figures/b.png",
                                             request={}, metadata={}))
    from wrf_webvis.app.storage import user_store
    monkeypatch.setattr(user_store, "store", user_store.UserStore(tmp_path / "users"))
    user_store.store.create_user("viewer", "123456", "user", dashboards="*")
    return TestClient(app, client=("127.0.0.1", 50000)), board.id, store


def _viewer(app):
    """公网登录的只读账号（可看全部看板，不可批注）。"""
    client = TestClient(app, client=("127.0.0.1", 50000), headers={"X-Real-IP": "203.0.113.8", "X-Forwarded-For": "203.0.113.8"})
    assert client.post("/api/auth/login", json={"username": "viewer", "password": "123456"}).status_code == 200
    return client


def test_annotation_crud_and_data_coordinates(client):
    editor, board, store = client
    base = f"/api/dashboards/{board}/images/img1"
    revision = editor.get(f"/api/dashboards/{board}/revision").json()["revision"]
    response = editor.post(f"{base}/annotations", json={
        "author": "ai", "note": "RMW 收缩",
        "shapes": [{"kind": "arrow", "coords": "data", "panel": 0, "points": [[100, 36], [50, 36]], "text": "RMW"},
                   {"kind": "rect", "points": [[0.2, 0.2], [0.3, 0.3]]}],
    })
    assert response.status_code == 200, response.text
    annotation = response.json()
    arrow = annotation["shapes"][0]
    assert arrow["coords"] == "image" and arrow["data_points"] == [[100, 36], [50, 36]]
    assert arrow["points"][0] == pytest.approx([0.275, 0.5]) and arrow["points"][1] == pytest.approx([0.1875, 0.5])
    assert editor.get(f"/api/dashboards/{board}/revision").json()["revision"] != revision
    listed = editor.get(f"/api/dashboards/{board}/annotations").json()
    assert [item["id"] for item in listed] == [annotation["id"]] and listed[0]["author"] == "ai"
    changed = editor.patch(f"{base}/annotations/{annotation['id']}", json={"note": "改过"}).json()
    assert changed["note"] == "改过" and len(changed["shapes"]) == 2
    assert editor.get(f"{base}/axes").json()["has_axes"] is True
    assert editor.delete(f"{base}/annotations/{annotation['id']}").status_code == 200
    assert editor.get(f"{base}/annotations").json() == []
    assert len(store.get(board).images) == 2  # 删批注不动图片


@pytest.mark.parametrize("shape, message", [
    ({"kind": "arrow", "points": [[0.1, 0.1]]}, "需要 2 个点"),
    ({"kind": "text", "points": [[1.5, 0.1]], "text": "x"}, "超出图片范围"),
    ({"kind": "arrow", "coords": "data", "panel": 3, "points": [[1, 1], [2, 2]]}, "没有编号为 3"),
])
def test_annotation_validation(client, shape, message):
    editor, board, _ = client
    response = editor.post(f"/api/dashboards/{board}/images/img1/annotations", json={"shapes": [shape]})
    assert response.status_code == 422 and message in response.text


def test_old_images_need_image_coordinates(client):
    editor, board, _ = client
    response = editor.post(f"/api/dashboards/{board}/images/old/annotations",
                           json={"shapes": [{"kind": "pin", "coords": "data", "panel": 0, "points": [[1, 1]]}]})
    assert response.status_code == 422 and "图片坐标" in response.text
    ok = editor.post(f"/api/dashboards/{board}/images/old/annotations",
                     json={"shapes": [{"kind": "pin", "points": [[0.5, 0.5]], "text": "看这里"}]})
    assert ok.status_code == 200


def test_readers_can_view_but_not_write_annotations(client):
    editor, board, _ = client
    editor.post(f"/api/dashboards/{board}/images/img1/annotations", json={"shapes": [{"kind": "pin", "points": [[0.5, 0.5]]}]})
    reader = _viewer(editor.app)
    assert len(reader.get(f"/api/dashboards/{board}/annotations").json()) == 1
    response = reader.post(f"/api/dashboards/{board}/images/img1/annotations", json={"shapes": [{"kind": "pin", "points": [[0.5, 0.5]]}]})
    assert response.status_code == 403
    assert json.loads(response.text)["detail"]["code"] == "READ_ONLY"


def test_deleted_image_can_be_restored_in_place(client):
    from wrf_webvis.app.core.schemas import DashboardImage

    editor, board, store = client
    store.add_image(board, DashboardImage(id="third", title="third", image_path="/static/figures/c.png", request={}))
    editor.post(f"/api/dashboards/{board}/images/img1/annotations", json={"shapes": [{"kind": "pin", "points": [[0.5, 0.5]]}]})
    assert editor.delete(f"/api/dashboards/{board}/images/img1").status_code == 200
    assert [image.id for image in store.get(board).images] == ["old", "third"]
    trash = editor.get(f"/api/dashboards/{board}/deleted-images").json()
    assert [row["id"] for row in trash] == ["img1"] and trash[0]["title"] == "time-radius"
    assert "a.png" in store.referenced_figure_names()  # 可撤销的图不会被清理
    restored = editor.post(f"/api/dashboards/{board}/images/img1/restore").json()
    assert [image["id"] for image in restored["images"]] == ["img1", "old", "third"]
    assert editor.get(f"/api/dashboards/{board}/deleted-images").json() == []
    assert len(editor.get(f"/api/dashboards/{board}/images/img1/annotations").json()) == 1  # 批注一起回来
    assert editor.post(f"/api/dashboards/{board}/images/img1/restore").status_code == 404
    editor.delete(f"/api/dashboards/{board}/images/third")
    restored = editor.post(f"/api/dashboards/{board}/images/third/restore").json()
    assert [image["id"] for image in restored["images"]] == ["img1", "old", "third"]


def test_image_note_is_saved_and_shown_to_readers(client):
    editor, board, store = client
    revision = editor.get(f"/api/dashboards/{board}/revision").json()["revision"]
    updated = editor.put(f"/api/dashboards/{board}/images/img1/note", json={"note": "  36 h 后 RMW 收缩  "}).json()
    image = next(item for item in updated["images"] if item["id"] == "img1")
    assert image["note"] == "36 h 后 RMW 收缩" and image["note_updated_at"]
    assert editor.get(f"/api/dashboards/{board}/revision").json()["revision"] != revision
    reader = _viewer(editor.app)
    shown = reader.get(f"/api/dashboards/{board}").json()
    assert next(item for item in shown["images"] if item["id"] == "img1")["note"] == "36 h 后 RMW 收缩"
    assert reader.put(f"/api/dashboards/{board}/images/img1/note", json={"note": "x"}).status_code == 403
    assert reader.post(f"/api/dashboards/{board}/images/img1/restore").status_code == 403
    cleared = editor.put(f"/api/dashboards/{board}/images/img1/note", json={"note": ""}).json()
    assert next(item for item in cleared["images"] if item["id"] == "img1")["note"] == ""
    assert editor.put(f"/api/dashboards/{board}/images/missing/note", json={"note": "x"}).status_code == 404
