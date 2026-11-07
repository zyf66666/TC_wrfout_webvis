from fastapi import FastAPI
from fastapi.testclient import TestClient

def test_preferences_persist_merge_and_permissions(tmp_path, monkeypatch):
    import wrf_webvis.app.api.variable_preferences as api
    monkeypatch.setattr(api, 'DB_PATH', tmp_path / 'preferences.sqlite')
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app, client=('127.0.0.1', 123)) as client:
        assert client.get('/api/variable-preferences').json() == {}
        def put(kind, rows):
            return client.put('/api/variable-preferences/' + kind, json={'variables':rows,'quiver':[]})
        assert put('2',[{'name':'heating','plot_type':'contourf','time':'30','enabled':True,'scale_factor':'3600'}]).status_code == 200
        assert put('0',[{'name':'dbz'}]).status_code == 200
        assert put('2',[{'name':'vr','plot_type':'contour'}]).status_code == 200
        result = client.get('/api/variable-preferences').json()
        assert [r['name'] for r in result['2']['variables']] == ['heating','vr']
        assert 'time' not in result['2']['variables'][0]
        assert result['2']['variables'][0]['enabled'] is False
        assert result['0']['variables'][0]['name'] == 'dbz'
        assert put('invalid',[]).status_code == 422
    with TestClient(app, client=('192.0.2.1', 123)) as client:
        assert client.put('/api/variable-preferences/2',json={'variables':[]}).status_code == 403
