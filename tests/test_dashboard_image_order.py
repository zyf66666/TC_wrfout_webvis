import pytest

from wrf_webvis.app.core.schemas import DashboardImage
from wrf_webvis.app.storage.dashboard_store import DashboardStore


def setup_store(tmp_path):
    store = DashboardStore(tmp_path)
    dashboard = store.create('Shared')
    for image_id in ['a', 'b', 'c']:
        store.add_image(dashboard.id, DashboardImage(id=image_id, title=image_id, image_path=f'/{image_id}.png', request={}))
    return store, dashboard.id


def test_image_move_persists_for_new_reader_and_keeps_metadata(tmp_path):
    store, dashboard_id = setup_store(tmp_path)
    assert callable(getattr(store, 'move_image', None)), 'Shared image ordering is missing'
    store.move_image(dashboard_id, 'c', 'a')
    saved = DashboardStore(tmp_path).get(dashboard_id)
    assert [image.id for image in saved.images] == ['c', 'a', 'b']
    assert saved.images[0].image_path == '/c.png'
    store.move_image(dashboard_id, 'c', None)
    assert [image.id for image in store.get(dashboard_id).images] == ['a', 'b', 'c']


def test_move_preserves_new_images_and_rejects_deleted_targets(tmp_path):
    store, dashboard_id = setup_store(tmp_path)
    assert callable(getattr(store, 'move_image', None)), 'Shared image ordering is missing'
    store.add_image(dashboard_id, DashboardImage(id='d', title='d', image_path='/d.png', request={}))
    store.move_image(dashboard_id, 'b', 'a')
    assert [image.id for image in store.get(dashboard_id).images] == ['b', 'a', 'c', 'd']
    with pytest.raises(KeyError):
        store.move_image(dashboard_id, 'b', 'deleted')
    assert [image.id for image in DashboardStore(tmp_path).get(dashboard_id).images] == ['b', 'a', 'c', 'd']
    with pytest.raises(KeyError):
        store.move_image(dashboard_id, 'deleted', None)
    with pytest.raises(KeyError):
        store.move_image('missing', 'b', None)


def test_order_api_is_shared_between_clients(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from wrf_webvis.app.api import dashboards

    store, dashboard_id = setup_store(tmp_path)
    monkeypatch.setattr(dashboards, 'store', store)
    app = FastAPI()
    app.include_router(dashboards.router)
    first, second = TestClient(app), TestClient(app)
    response = first.patch(f'/api/dashboards/{dashboard_id}/images/c/order', json={'before_id': 'a'})
    assert response.status_code == 200
    assert [image['id'] for image in second.get(f'/api/dashboards/{dashboard_id}').json()['images']] == ['c', 'a', 'b']
    assert first.patch(f'/api/dashboards/{dashboard_id}/images/c/order', json={'before_id':'missing'}).status_code == 404
    assert first.patch(f'/api/dashboards/{dashboard_id}/images/c/order', json={'before_id':[]}).status_code == 422


# 2026-11-06：把图片拖到别的看板
def test_transfer_moves_image_and_annotations_to_other_dashboard(tmp_path):
    store, source_id = setup_store(tmp_path)
    target = store.create('Child', parent_id=source_id)
    store.add_image(target.id, DashboardImage(id='x', title='x', image_path='/x.png', request={}))
    store.add_image(source_id, DashboardImage(id='r', title='r', image_path='/r.png',
                                              request={'dashboard_id': source_id}))
    note = store.add_annotation(source_id, 'r', 'user', [{'kind': 'pin', 'points': [[0.5, 0.5]]}], 'hi')
    store.set_image_note(source_id, 'r', '文字批注')

    result = store.transfer_image(source_id, 'r', target.id)
    assert [image.id for image in result.images] == ['x', 'r']
    fresh = DashboardStore(tmp_path)
    assert [image.id for image in fresh.get(source_id).images] == ['a', 'b', 'c']
    moved = fresh.get_image(target.id, 'r')
    assert moved['note'] == '文字批注'
    assert moved['request']['dashboard_id'] == target.id
    assert [item['id'] for item in fresh.list_annotations(target.id, 'r')] == [note['id']]
    assert fresh.list_annotations(source_id) == []

    store.transfer_image(source_id, 'a', target.id, before_id='x')
    assert [image.id for image in store.get(target.id).images] == ['a', 'x', 'r']


def test_transfer_rejects_bad_targets_without_changes(tmp_path):
    from wrf_webvis.app.storage.dashboard_store import ImageTransferError

    store, source_id = setup_store(tmp_path)
    other = store.create('Other')
    store.add_image(other.id, DashboardImage(id='b', title='same id', image_path='/b2.png', request={}))
    with pytest.raises(ImageTransferError):
        store.transfer_image(source_id, 'a', source_id)
    with pytest.raises(ImageTransferError):
        store.transfer_image(source_id, 'b', other.id)
    with pytest.raises(KeyError):
        store.transfer_image(source_id, 'missing', other.id)
    with pytest.raises(KeyError):
        store.transfer_image(source_id, 'a', 'missing')
    with pytest.raises(KeyError):
        store.transfer_image(source_id, 'a', other.id, before_id='missing')
    assert [image.id for image in store.get(source_id).images] == ['a', 'b', 'c']
    assert [image.id for image in store.get(other.id).images] == ['b']


def test_transfer_api(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from wrf_webvis.app.api import dashboards

    store, source_id = setup_store(tmp_path)
    other = store.create('Other')
    monkeypatch.setattr(dashboards, 'store', store)
    app = FastAPI()
    app.include_router(dashboards.router)
    client = TestClient(app)
    url = f'/api/dashboards/{source_id}/images/c/transfer'
    response = client.post(url, json={'target_dashboard_id': other.id})
    assert response.status_code == 200
    assert [image['id'] for image in response.json()['images']] == ['c']
    assert client.post(url, json={'target_dashboard_id': other.id}).status_code == 404
    assert client.post(f'/api/dashboards/{source_id}/images/a/transfer',
                       json={'target_dashboard_id': source_id}).status_code == 409
