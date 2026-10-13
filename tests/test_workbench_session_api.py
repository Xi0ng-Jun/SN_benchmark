"""Same-origin reads use server configuration and a Task-local session id."""
from fastapi.testclient import TestClient

from rag_eval.workbench.api import create_app
from test_workbench_sessions import Reader, snapshot


def fixture(tmp_path, bridge=None):
    app = create_app(tmp_path / 'state', {'workspace': {'title': 'SN', 'location': str(tmp_path / 'code')}},
                     actor='alice', access_token='test-access', agent_bridge=bridge)
    return TestClient(app)


def create_link(client):
    client.post('/api/session', json={'token': 'test-access'})
    workspace = client.get('/api/bootstrap').json()['workspaces'][0]
    task = client.post('/api/tasks', json={'workspace_id': workspace['id'], 'title': 'Investigate', 'request_key': 'task'},
                       headers={'X-Workbench': '1'}).json()
    session = client.post('/api/tasks/' + task['id'] + '/sessions', json={'agent_id': 'agent-1', 'request_key': 'link'},
                          headers={'X-Workbench': '1'}).json()
    return task, session


def test_http_agent_read_uses_local_scope_and_projects_server_snapshot(tmp_path):
    reader = Reader(snapshot(tmp_path))
    with fixture(tmp_path, reader) as client:
        assert client.get('/api/tasks/unknown/sessions/unknown/agent').status_code == 401
        task, session = create_link(client)
        prefix = '/api/tasks/' + task['id'] + '/sessions/'
        assert client.get(prefix + 'unknown/agent').status_code == 422
        assert reader.calls == []
        response = client.get(prefix + session['id'] + '/agent', params={'agentId': 'foreign', 'config': 'not server config'})
        assert response.status_code == 200
        assert response.json()['result']['agent']['id'] == 'agent-1'
        assert set(response.json()['result']['agent']) == {'id', 'title', 'status', 'provider', 'model'}
        assert reader.calls == [{'op': 'readAgent', 'agentId': 'agent-1'}]
        exported = client.get('/api/tasks/' + task['id'] + '/export').json()
        assert [event['kind'] for event in exported['history']] == ['task.created', 'session.attached']


def test_http_unconfigured_bridge_reports_unavailable_and_keeps_link(tmp_path):
    with fixture(tmp_path) as client:
        task, session = create_link(client)
        response = client.get('/api/tasks/' + task['id'] + '/sessions/' + session['id'] + '/agent')
        assert response.status_code == 200
        assert response.json()['result'] == {'status': 'unavailable', 'code': 'BRIDGE_NOT_CONFIGURED'}
        assert response.json()['release'] == {'status': 'not_needed'}
        assert client.get('/api/tasks/' + task['id']).json()['sessions'] == [session]
