"""HTTP owns profile selection, SQLite owns uncertainty; no daemon is used."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import sys
import threading
import uuid

from fastapi.testclient import TestClient
import pytest

from rag_eval.workbench.api import create_app
from rag_eval.workbench.agent_creations import AgentCreationStore
from rag_eval.workbench.repository import WorkbenchRepository
from test_workbench_agent_creations import Bridge, created


HEADERS = {'Authorization': 'Bearer test-access', 'X-Workbench': '1'}
PAYLOAD = {'profile_id': 'allowed', 'title': 'Example', 'request_key': 'create'}


def configuration(tmp_path):
    return {'workspace': {'title': 'local', 'location': str(tmp_path / 'registered ')},
            'agent_creation': {'workspaceId': 'remote-private-workspace', 'profiles': {
                'allowed': {'provider': 'codex/model-example', 'modeId': 'mode-example',
                            'thinkingOptionId': 'effort-example'},
                'second': {'provider': 'other/second-model'}}}}


def application(tmp_path, config=None, bridge=None, actor='alice'):
    return create_app(tmp_path / 'state', config or configuration(tmp_path), actor=actor,
                      access_token='test-access', agent_creation_bridge=bridge)


def task(client, key='task'):
    workspace = client.get('/api/bootstrap', headers=HEADERS).json()['workspaces'][0]
    response = client.post('/api/tasks', headers=HEADERS, json={
        'workspace_id': workspace['id'], 'title': key, 'request_key': key})
    assert response.status_code == 200
    return response.json()


def path(task):
    return '/api/tasks/' + task['id'] + '/agent-creations'


def persisted(tmp_path, task):
    repo = WorkbenchRepository(tmp_path / 'state')
    with repo.connect() as conn:
        rows = [dict(row) for row in conn.execute(
            'SELECT * FROM agent_creation_intents WHERE task_id=?', (task['id'],))]
    return rows, repo.task(task['id'])['history']


def assert_safe(record):
    assert set(record) == {'id', 'task_id', 'actor', 'profile_id', 'title', 'agent_id',
                           'request_key', 'status', 'prepared_at', 'started_at', 'finished_at',
                           'session_id', 'envelope'}
    encoded = json.dumps(record)
    for forbidden in ('expectedCwd', 'workspaceId', 'idempotencyKey', 'capabilities',
                      'remote-private-workspace', 'private-secret', 'command', 'idempotencyKey', 'cwd'):
        assert forbidden not in encoded
    envelope = record['envelope']
    if envelope is not None and envelope['result']['status'] == 'created':
        assert set(envelope['result']['agent']) <= {'id', 'title', 'status', 'provider', 'model'}


def test_routes_missing_is_behavior_red_without_injection_precheck(tmp_path):
    # This must fail with HTTP 404 before implementation, separately from the
    # missing create_app keyword precheck exercised by the remaining tests.
    config = configuration(tmp_path)
    app = create_app(tmp_path / 'state', config, access_token='test-access')
    with TestClient(app) as client:
        local = task(client)
        response = client.get(path(local), headers=HEADERS)
        assert response.status_code == 200
        assert response.json() == []


@pytest.mark.parametrize('ready,release_failed', [(True, False), (True, True), (False, True)])
def test_create_exact_frozen_intent_and_safe_created_release_projection(tmp_path, ready, release_failed):
    bridge = Bridge(lambda command: created(command, ready=ready, release_failed=release_failed))
    config = configuration(tmp_path)
    app = application(tmp_path, config, bridge)
    config['agent_creation']['workspaceId'] = 'mutated-remote'
    config['agent_creation']['profiles']['allowed']['provider'] = 'mutated/model'
    with TestClient(app) as client:
        local = task(client)
        response = client.post(path(local), json=PAYLOAD, headers=HEADERS)
        assert response.status_code == 200
        record = response.json()
        command = bridge.commands[0]
        assert command == {'op': 'createAgent', 'workspaceId': 'remote-private-workspace',
                           'expectedCwd': str((tmp_path / 'registered ').resolve()),
                           'agentId': str(uuid.UUID(record['agent_id'])),
                           'idempotencyKey': 'creation:' + record['id'],
                           'config': {'provider': 'codex/model-example', 'modeId': 'mode-example',
                                      'thinkingOptionId': 'effort-example'}, 'title': 'Example'}
        rows, history = persisted(tmp_path, local)
        assert json.loads(rows[0]['command']) == command
        assert record['status'] == 'created' and record['actor'] == 'alice'
        assert record['envelope']['result']['ready'] is ready
        assert record['envelope']['result']['readiness'] == ('idle' if ready else 'not_idle')
        assert record['envelope']['release'] == ({'status': 'unavailable', 'code': 'CLOSE_FAILED'}
                                               if release_failed else {'status': 'ok'})
        detail = client.get('/api/tasks/' + local['id'], headers=HEADERS).json()
        assert detail['sessions'][0]['id'] == record['session_id']
        assert detail['sessions'][0]['agent_id'] == record['agent_id']
        assert history[-1]['content']['ready'] is ready
        assert_safe(record)
        assert client.get(path(local), headers=HEADERS).json() == [record]
        assert client.get(path(local) + '/' + record['id'], headers=HEADERS).json() == record
        assert client.post(path(local), json=PAYLOAD, headers=HEADERS).json() == record
        assert len(bridge.commands) == 1
        assert client.post(path(local), json={**PAYLOAD, 'title': 'Different'}, headers=HEADERS).status_code == 409
        assert len(persisted(tmp_path, local)[0]) == 1


@pytest.mark.parametrize('status', ['unknown', 'not_attempted', 'created'])
def test_receipts_survive_reopen_missing_configuration_without_replay(tmp_path, status):
    def reply(command):
        if status == 'created':
            return created(command)
        result = ({'status': 'not_attempted', 'code': 'CONNECT_FAILED', 'agentId': command['agentId']}
                  if status == 'not_attempted' else
                  {'status': 'unknown', 'code': 'CREATE_OUTCOME_UNKNOWN', 'cause': 'BRIDGE_TIMEOUT',
                   'agentId': command['agentId'], 'observed': {
                       'handleId': command['agentId'], 'agent': created(command)['result']['agent']}})
        return {'result': result, 'release': {'status': 'ok'}}
    bridge = Bridge(reply)
    with TestClient(application(tmp_path, bridge=bridge)) as client:
        local = task(client)
        response = client.post(path(local), json=PAYLOAD, headers=HEADERS)
        assert response.status_code == 200
        record = response.json()
        assert record['status'] == status
        assert_safe(record)
        if status == 'unknown':
            assert record['envelope']['result']['observed'] == {'handleId': record['agent_id']}
        if status != 'created':
            assert record['session_id'] is None
    config = configuration(tmp_path)
    config.pop('agent_creation')
    with TestClient(application(tmp_path, config)) as reopened:
        assert reopened.get(path(local), headers=HEADERS).json() == [record]
        assert reopened.get(path(local) + '/' + record['id'], headers=HEADERS).json() == record
        assert reopened.post(path(local), json=PAYLOAD, headers=HEADERS).json() == record
        assert reopened.post(path(local), json={**PAYLOAD, 'profile_id': 'second'}, headers=HEADERS).status_code == 409
    assert len(bridge.commands) == 1


def test_no_bridge_prepared_intent_does_not_claim_and_exact_space_keys(tmp_path):
    with TestClient(application(tmp_path)) as client:
        local = task(client)
        for key in (' key ', 'key'):
            response = client.post(path(local), json={**PAYLOAD, 'request_key': key}, headers=HEADERS)
            assert response.status_code == 422
            assert response.json() == {'error': 'Creation bridge is not configured'}
        records = client.get(path(local), headers=HEADERS).json()
        assert len(records) == 2 and len({value['agent_id'] for value in records}) == 2
        assert all(value['status'] == 'prepared' and value['started_at'] is None for value in records)
        assert all(value['envelope'] is None for value in records)
        rows, history = persisted(tmp_path, local)
        assert {row['request_key'] for row in rows} == {' key ', 'key'}
        assert [event['kind'] for event in history] == ['task.created', 'agent.creation.prepared', 'agent.creation.prepared']


def test_dropped_create_responses_are_claimable_by_exact_local_request_key(tmp_path):
    bridge = Bridge(created)
    with TestClient(application(tmp_path, bridge=bridge)) as client:
        local = task(client)
        for key in (' key ', 'key'):
            response = client.post(path(local), json={**PAYLOAD, 'request_key': key}, headers=HEADERS)
            assert response.status_code == 200
            # The caller discards this response and therefore has no intent id.
        records = client.get(path(local), headers=HEADERS).json()
        assert [(record['profile_id'], record['title'], record['request_key']) for record in records] == [
            ('allowed', 'Example', ' key '), ('allowed', 'Example', 'key')]
        assert client.get(path(local) + '/' + records[0]['id'], headers=HEADERS).json()['request_key'] == ' key '
        assert client.get(path(local) + '/' + records[1]['id'], headers=HEADERS).json()['request_key'] == 'key'
        assert len(bridge.commands) == 2


@pytest.mark.parametrize('extra', ['actor', 'config', 'workspaceId', 'agentId', 'idempotencyKey',
                                  'expectedCwd', 'op', 'provider', 'command', 'executable'])
def test_extra_browser_fields_rejected_before_intent_writes(tmp_path, extra):
    bridge = Bridge(created)
    with TestClient(application(tmp_path, bridge=bridge)) as client:
        local = task(client)
        response = client.post(path(local), json={**PAYLOAD, extra: 'forged-private-secret'}, headers=HEADERS)
        assert response.status_code == 422
        assert bridge.commands == []
        rows, history = persisted(tmp_path, local)
        assert rows == [] and len(history) == 1


@pytest.mark.parametrize('change', [{'profile_id': 'unknown'}, {'profile_id': ' allowed'},
                                  {'title': ''}, {'title': ' '}, {'title': ' bad '},
                                  {'title': '😀' * 101}, {'request_key': ''}, {'request_key': ' '}])
def test_invalid_choices_leave_no_intent_or_history(tmp_path, change):
    bridge = Bridge(created)
    with TestClient(application(tmp_path, bridge=bridge)) as client:
        local = task(client)
        assert client.post(path(local), json={**PAYLOAD, **change}, headers=HEADERS).status_code == 422
        assert bridge.commands == []
        rows, history = persisted(tmp_path, local)
        assert rows == [] and len(history) == 1


def test_real_task_and_server_actor_scope_and_read_only_list(tmp_path):
    bridge = Bridge(created)
    with TestClient(application(tmp_path, bridge=bridge)) as alice:
        local, other = task(alice), task(alice, 'task-b')
        record = alice.post(path(local), json=PAYLOAD, headers=HEADERS).json()
        before = persisted(tmp_path, local)
        assert alice.get(path(other), headers=HEADERS).json() == []
        assert alice.get(path(other) + '/' + record['id'], headers=HEADERS).status_code == 422
        assert alice.get('/api/tasks/nonexistent/agent-creations', headers=HEADERS).status_code == 422
        assert persisted(tmp_path, local) == before
    with TestClient(application(tmp_path, bridge=bridge, actor='bob')) as bob:
        assert bob.get(path(local), headers=HEADERS).json() == []
        assert bob.get(path(local) + '/' + record['id'], headers=HEADERS).status_code == 422
        own = bob.post(path(local), json=PAYLOAD, headers=HEADERS).json()
        assert own['actor'] == 'bob' and own['agent_id'] != record['agent_id']
    repo = WorkbenchRepository(tmp_path / 'state')
    foreign_ws = repo.register_workspace('foreign', tmp_path / 'foreign')
    foreign_task = repo.create_task(foreign_ws['id'], 'foreign', '', request_key='foreign')
    with TestClient(application(tmp_path, bridge=bridge)) as alice:
        before = persisted(tmp_path, foreign_task)
        for method, url, body in [('get', path(foreign_task), None),
                                  ('get', path(foreign_task) + '/' + record['id'], None),
                                  ('post', path(foreign_task), PAYLOAD)]:
            response = (alice.get(url, headers=HEADERS) if method == 'get' else
                        alice.post(url, json=body, headers=HEADERS))
            assert response.status_code == 422
        assert persisted(tmp_path, foreign_task) == before
    assert len(bridge.commands) == 2


def test_http_inflight_unknown_visible_no_second_call_and_reopen_never_resends(tmp_path):
    entered, released = threading.Event(), threading.Event()
    def reply(command):
        entered.set()
        assert released.wait(10)
        return {'result': {'status': 'unknown', 'code': 'CREATE_OUTCOME_UNKNOWN',
                           'cause': 'BRIDGE_TIMEOUT', 'agentId': command['agentId']},
                'release': {'status': 'unavailable', 'code': 'BRIDGE_RELEASE_UNKNOWN'}}
    bridge = Bridge(reply)
    app = application(tmp_path, bridge=bridge)
    with TestClient(app) as first, TestClient(app) as reader, ThreadPoolExecutor(max_workers=1) as workers:
        local = task(reader)
        pending = workers.submit(first.post, path(local), json=PAYLOAD, headers=HEADERS)
        try:
            assert entered.wait(5)
            current = reader.get(path(local), headers=HEADERS).json()[0]
            assert current['status'] == 'unknown' and current['envelope'] is None
            assert current['started_at'] is not None
            assert reader.get(path(local) + '/' + current['id'], headers=HEADERS).json() == current
            assert reader.post(path(local), json=PAYLOAD, headers=HEADERS).json() == current
            assert len(bridge.commands) == 1
            # New app + separate SQLite connection while original dispatch is blocked.
            with TestClient(application(tmp_path, bridge=bridge)) as reopened:
                assert reopened.post(path(local), json=PAYLOAD, headers=HEADERS).json() == current
                assert len(bridge.commands) == 1
        finally:
            released.set()
        settled = pending.result(timeout=5).json()
        assert settled['status'] == 'unknown' and settled['session_id'] is None
    with TestClient(application(tmp_path, bridge=bridge)) as reopened:
        assert reopened.post(path(local), json=PAYLOAD, headers=HEADERS).json() == settled
    assert len(bridge.commands) == 1


def test_bootstrap_public_profiles_are_frozen_configuration_capability(tmp_path):
    config = configuration(tmp_path)
    with TestClient(application(tmp_path, config, Bridge(created))) as client:
        config['agent_creation']['profiles']['allowed']['modeId'] = 'mutated'
        bootstrap = client.get('/api/bootstrap', headers=HEADERS).json()
        assert bootstrap['capabilities']['agent_create'] is True
        assert bootstrap['capabilities']['agent'] is False
        assert bootstrap['capabilities']['agent_read'] is False
        assert bootstrap['capabilities']['execution'] is False
        assert bootstrap['creation_profiles'] == [
            {'id': 'allowed', 'provider': 'codex', 'model': 'model-example',
             'modeId': 'mode-example', 'thinkingOptionId': 'effort-example'},
            {'id': 'second', 'provider': 'other', 'model': 'second-model'}]
        assert 'remote-private-workspace' not in json.dumps(bootstrap)
    for missing in ('agent_creation', 'bridge'):
        config = configuration(tmp_path)
        if missing == 'agent_creation':
            config.pop(missing)
        with TestClient(application(tmp_path, config)) as client:
            assert client.get('/api/bootstrap', headers=HEADERS).json()['capabilities']['agent_create'] is False


@pytest.mark.parametrize('binding', [None, {}, {'workspaceId': 'remote', 'profiles': {}, 'extra': 1},
    {'workspaceId': ' remote ', 'profiles': {'allowed': {'provider': 'codex/model'}}},
    {'workspaceId': 'remote', 'profiles': {' bad ': {'provider': 'codex/model'}}},
    {'workspaceId': 'remote', 'profiles': {'allowed': {'provider': 'codex'}}},
    {'workspaceId': 'remote', 'profiles': {'allowed': {'provider': 'codex/model', 'cwd': '/evil'}}},
    {'workspaceId': 'remote', 'profiles': {'allowed': {'provider': 'codex/model', 'modeId': '😀' * 257}}}])
def test_invalid_server_config_rejected_at_startup_without_dispatch(tmp_path, binding):
    config = configuration(tmp_path)
    config['agent_creation'] = binding
    bridge = Bridge(created)
    with pytest.raises(ValueError):
        application(tmp_path, config, bridge)
    assert bridge.commands == []


def test_automatic_adapter_uses_exact_server_only_stdin(tmp_path, monkeypatch):
    import rag_eval.workbench.creation_bridge as module
    from rag_eval.workbench.creation_bridge import AgentCreationBridgeAdapter
    capture = tmp_path / 'stdin.json'
    script = tmp_path / 'owned_fixture.py'
    # A real owned subprocess, not the SDK or a daemon. Captures only synthetic data.
    script.write_text('import json,sys\nfrom pathlib import Path\n'
                      'request=json.load(sys.stdin)\n'
                      'Path(sys.argv[1]).write_text(json.dumps(request))\n'
                      'print(json.dumps({"result":{"status":"not_attempted","code":"CONNECT_FAILED",'
                      '"agentId":request["command"]["agentId"]},"release":{"status":"ok"}}))\n')
    def factory(config):
        return AgentCreationBridgeAdapter(config, command=[sys.executable, str(script), str(capture)])
    monkeypatch.setattr(module, 'AgentCreationBridgeAdapter', factory)
    config = configuration(tmp_path)
    config['agent_bridge'] = {'url': 'ws://synthetic.invalid', 'password': 'private-secret'}
    with TestClient(application(tmp_path, config)) as client:
        local = task(client)
        response = client.post(path(local), json=PAYLOAD, headers=HEADERS)
        assert response.status_code == 200
        record = response.json()
        assert record['status'] == 'not_attempted'
        request = json.loads(capture.read_text())
        rows, _ = persisted(tmp_path, local)
        assert request == {'config': config['agent_bridge'], 'command': json.loads(rows[0]['command'])}
        assert request['command']['expectedCwd'] == str((tmp_path / 'registered ').resolve())
        assert request['command']['agentId'] == record['agent_id']
        assert_safe(record)
