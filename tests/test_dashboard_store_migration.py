from __future__ import annotations

import json

import pytest

from wrf_webvis.app.core.schemas import DashboardImage
from wrf_webvis.app.storage.dashboard_store import DashboardStore, DashboardStoreError


LEGACY = [
    {
        "id": "dash1",
        "title": "Land vs sea",
        "metadata": {"note": "kept"},
        "images": [
            {"id": "a", "title": "A", "image_path": "/static/figures/a.png", "request": {"domain": 3}, "metadata": {"html_static_path": "/static/figures/a.html"}},
            {"id": "b", "title": "B", "image_path": "/static/figures/b.png", "request": {}, "metadata": {}},
        ],
    },
    {"id": "dash2", "title": "Empty", "metadata": {}, "images": []},
]


def _write_legacy(tmp_path):
    path = tmp_path / "dashboards.json"
    path.write_text(json.dumps(LEGACY, ensure_ascii=False), encoding="utf-8")
    return path


# 功能说明：首次使用时导入旧版 JSON，内容和顺序不变，原文件保持原样。
def test_legacy_json_is_imported_once_and_left_untouched(tmp_path) -> None:
    legacy = _write_legacy(tmp_path)
    before = legacy.read_bytes()

    dashboards = DashboardStore(tmp_path).list()

    assert [d.id for d in dashboards] == ["dash1", "dash2"]
    assert [image.id for image in dashboards[0].images] == ["a", "b"]
    assert dashboards[0].metadata == {"note": "kept"}
    assert dashboards[0].images[0].request == {"domain": 3}
    assert legacy.read_bytes() == before


# 功能说明：导入后删除的看板不会因为旧 JSON 仍在而在下次启动时复活。
def test_deleted_dashboard_is_not_reimported(tmp_path) -> None:
    _write_legacy(tmp_path)
    DashboardStore(tmp_path).delete("dash1")
    DashboardStore(tmp_path).add_image("dash2", DashboardImage(id="c", title="C", image_path="/c.png", request={}))

    dashboards = DashboardStore(tmp_path).list()

    assert [d.id for d in dashboards] == ["dash2"]
    assert [image.id for image in dashboards[0].images] == ["c"]


# 功能说明：旧 JSON 损坏时明确报错，而不是悄悄以空看板启动。
def test_corrupt_legacy_json_raises_without_creating_empty_store(tmp_path) -> None:
    (tmp_path / "dashboards.json").write_text("[{broken", encoding="utf-8")
    with pytest.raises(DashboardStoreError):
        DashboardStore(tmp_path).list()
    (tmp_path / "dashboards.json").write_text(json.dumps(LEGACY), encoding="utf-8")
    assert [d.id for d in DashboardStore(tmp_path).list()] == ["dash1", "dash2"]


# 功能说明：改名图片时同步元数据标题，新建看板排在最后。
def test_rename_image_and_create_order(tmp_path) -> None:
    _write_legacy(tmp_path)
    store = DashboardStore(tmp_path)
    store.rename_image("dash1", "a", "Renamed")
    created = store.create("Third")

    image = store.get("dash1").images[0]
    assert image.title == "Renamed" and image.metadata["title"] == "Renamed"
    assert [d.id for d in store.list()] == ["dash1", "dash2", created.id]
    with pytest.raises(KeyError):
        store.rename_image("dash1", "missing", "x")


# 功能说明：收集所有图片记录引用的静态文件名，包括交互式 HTML。
def test_referenced_figure_names(tmp_path) -> None:
    _write_legacy(tmp_path)
    assert DashboardStore(tmp_path).referenced_figure_names() == {"a.png", "a.html", "b.png"}
