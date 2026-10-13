"""Task-scoped message HTTP with synthetic bridge, real SQLite and context."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import threading
import uuid
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

from rag_eval.workbench.api import create_app
from rag_eval.workbench.context import resolve_context
from rag_eval.workbench.repository import WorkbenchRepository
from test_workbench_agent_messages import Bridge, acknowledged


AGENT = 'b7abb649-7b0d-4ead-9368-b2a4c7c58209'
HEADERS = {'Authorization': 'Bearer test-access', 'X-Workbench': '1'}


def config(tmp_path):
    return {'workspace': {'title': 'local', 'location': str(tmp_path / 'checkout ')}}


def app(tmp_path, bridge=None, *, actor='alice', settings=None):
    return create_app(tmp_path / 'state', settings or config(tmp_path), actor=actor,
                      access_token='test-access', agent_message_bridge=bridge)


def task(client, key='task'):
    workspace = client.get('/api/bootstrap', headers=HEADERS).json()['workspaces'][0]
    response = client.post('/api/tasks', headers=HEADERS, json={
        'workspace_id': workspace['id'], 'title': key, 'goal': 'Not implicit', 'request_key': key})
    assert response.status_code == 200
    return response.json()


def session(client, local, key='link'):
    response = client.post('/api/tasks/' + local['id'] + '/sessions', headers=HEADERS,
                           json={'agent_id': AGENT, 'request_key': key})
    assert response.status_code == 200
    return response.json()


def payload(link, **changes):
    return {'session_id': link['id'], 'text': '  用户原文\n', 'contexts': [],
            'request_key': 'send', 'active_turn_behavior': 'steer', **changes}


def path(local):
    return '/api/tasks/' + local['id'] + '/agent-messages'


def state(tmp_path, local):
    repo = WorkbenchRepository(tmp_path / 'state')
    with repo.connect() as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_message_intents'").fetchone()
        rows = ([dict(row) for row in conn.execute('SELECT * FROM agent_message_intents WHERE task_id=? ORDER BY rowid', (local['id'],))]
                if exists else [])
    return rows, repo.task(local['id'])['history']


def safe(record):
    assert set(record) == {'id', 'task_id', 'actor', 'session_id', 'agent_id', 'message_id',
                           'text', 'contexts', 'request_key', 'active_turn_behavior', 'body_format',
                           'input_sha256', 'body_sha256', 'status', 'envelope', 'prepared_at',
                           'started_at', 'finished_at'}
    encoded = json.dumps(record)
    for forbidden in ('expectedCwd', 'command', 'snapshots', 'source_refs', 'workspace_id',
                      'modelbody', 'private-secret', 'credentials'):
        assert forbidden not in encoded


def test_get_route_behavior_red_without_missing_injection_precheck(tmp_path):
    application = create_app(tmp_path / 'state', config(tmp_path), access_token='test-access')
    with TestClient(application) as client:
        local = task(client)
        response = client.get(path(local), headers=HEADERS)
        assert response.status_code == 200
        assert response.json() == []


@pytest.mark.parametrize('release_failed', [False, True])
def test_ack_exact_command_safe_binding_and_explicit_context(tmp_path, release_failed):
    bridge = Bridge(lambda command: acknowledged(command, release_failed))
    checkout = tmp_path / 'checkout '
    checkout.mkdir()
    (checkout / 'source.txt').write_text('  selected evidence\n')
    with TestClient(app(tmp_path, bridge)) as client:
        local = task(client)
        link = session(client, local)
        repo = WorkbenchRepository(tmp_path / 'state')
        sources = [resolve_context(repo, local['id'], ref) for ref in (
            {'kind': 'file', 'path': 'source.txt'}, {'kind': 'goal'})]
        selected = [{'reference': s['reference'], 'sha256': s['sha256']} for s in sources]
        command = payload(link, contexts=selected, request_key=' key ', active_turn_behavior='interrupt')
        response = client.post(path(local), headers=HEADERS, json=command)
        assert response.status_code == 200
        record = response.json()
        safe(record)
        assert record['actor'] == 'alice' and record['text'] == command['text']
        assert record['contexts'] == selected and record['request_key'] == ' key '
        assert record['message_id'] == str(uuid.UUID(record['message_id']))
        sent = bridge.commands[0]
        assert set(sent) == {'op', 'agentId', 'expectedCwd', 'text', 'messageId', 'activeTurnBehavior'}
        assert sent['agentId'] == AGENT and sent['messageId'] == record['message_id']
        assert sent['expectedCwd'] == str(checkout.resolve())
        assert sent['activeTurnBehavior'] == 'interrupt'
        body = json.loads(sent['text'])
        assert body['message'] == command['text']
        assert body['context'] == [{k: s[k] for k in ('reference', 'title', 'text', 'sha256')} for s in sources]
        assert record['body_sha256'] == hashlib.sha256(sent['text'].encode()).hexdigest()
        rows, history = state(tmp_path, local)
        assert record['input_sha256'] == rows[0]['input_sha256']
        assert record['status'] == 'acknowledged'
        assert record['envelope'] == acknowledged(sent, release_failed)
        assert 'turnCompleted' not in record['envelope']['result']
        assert record['message_id'] not in json.dumps(history)
        before = state(tmp_path, local)
        assert client.get(path(local), headers=HEADERS).json() == [record]
        assert client.get(path(local) + '/' + record['id'], headers=HEADERS).json() == record
        assert state(tmp_path, local) == before and len(bridge.commands) == 1
        (checkout / 'source.txt').unlink()
        # Existing intent returns its frozen receipt without re-reading source.
        assert client.post(path(local), headers=HEADERS, json=command).json() == record
        conflict = client.post(path(local), headers=HEADERS, json={**command, 'text': 'changed'})
        assert conflict.status_code == 409
        assert conflict.json().get('code') != 'MESSAGE_CONTEXT_CHANGED_PREWRITE'
        assert len(bridge.commands) == 1


@pytest.mark.parametrize('status', ['unknown', 'not_attempted', 'acknowledged'])
def test_receipt_reopen_without_config_and_exact_keys_no_resend(tmp_path, status):
    def reply(command):
        if status == 'acknowledged':
            return acknowledged(command, True)
        result = {'status': status, 'agentId': command['agentId'], 'messageId': command['messageId']}
        result.update({'code': 'PERMISSION_REQUIRED'} if status == 'not_attempted' else
                      {'code': 'SEND_OUTCOME_UNKNOWN', 'cause': 'CONNECTION_UNAVAILABLE', 'observed': {'sdkResolved': True}})
        return {'result': result, 'release': {'status': 'ok'}}
    bridge = Bridge(reply)
    with TestClient(app(tmp_path, bridge)) as client:
        local = task(client)
        link = session(client, local)
        for key in (' key ', 'key'):
            response = client.post(path(local), headers=HEADERS, json=payload(link, request_key=key))
            assert response.status_code == 200
            # POST receipt discarded, no intent id available to caller.
        records = client.get(path(local), headers=HEADERS).json()
        assert {r['request_key'] for r in records} == {' key ', 'key'}
        assert len({r['message_id'] for r in records}) == 2
        for record in records:
            safe(record)
            assert record['status'] == status
    with TestClient(app(tmp_path)) as reopened:
        before = state(tmp_path, local)
        assert reopened.get(path(local), headers=HEADERS).json() == records
        for key in (' key ', 'key'):
            record = next(r for r in records if r['request_key'] == key)
            assert reopened.get(path(local) + '/' + record['id'], headers=HEADERS).json() == record
            assert reopened.post(path(local), headers=HEADERS, json=payload(link, request_key=key)).json() == record
        assert state(tmp_path, local) == before and len(bridge.commands) == 2


def test_no_configuration_no_prepared_write_and_existing_read_adapter_not_send(tmp_path):
    read_bridge = Bridge()
    application = create_app(tmp_path / 'state', config(tmp_path), access_token='test-access', agent_bridge=read_bridge)
    with TestClient(application) as client:
        local = task(client)
        link = session(client, local)
        before = state(tmp_path, local)
        response = client.post(path(local), headers=HEADERS, json=payload(link))
        assert response.status_code == 422
        assert response.json() == {
            'error': 'Message bridge is not configured',
            'code': 'MESSAGE_BRIDGE_UNCONFIGURED_PREWRITE',
            'request_key': payload(link)['request_key'],
        }
        assert state(tmp_path, local) == before and read_bridge.commands == []


@pytest.mark.parametrize('extra', ['agentId', 'expectedCwd', 'messageId', 'config', 'actor',
                                  'op', 'command', 'executable', 'context_text', 'prompt'])
def test_extra_fields_rejected_without_writes_or_echo(tmp_path, extra):
    bridge = Bridge()
    with TestClient(app(tmp_path, bridge)) as client:
        local = task(client)
        link = session(client, local)
        before = state(tmp_path, local)
        response = client.post(path(local), headers=HEADERS, json={**payload(link), extra: 'private-secret'})
        assert response.status_code == 422 and 'private-secret' not in response.text
        assert state(tmp_path, local) == before and bridge.commands == []


@pytest.mark.parametrize('changes', [
    {'session_id': 1}, {'text': 1}, {'text': ' '}, {'text': '\0'}, {'text': '中' * 2731},
    {'text': 'x' * 8193}, {'request_key': ''}, {'request_key': ' '}, {'active_turn_behavior': 'other'},
    {'active_turn_behavior': None}, {'contexts': {}},
    {'contexts': [{'reference': {'kind': 'goal'}, 'sha256': 'a' * 64, 'text': 'free text'}]},
    {'contexts': [{'reference': {'kind': 'goal', 'text': 'free text'}, 'sha256': 'a' * 64}]},
    {'contexts': [{'reference': {'kind': 'goal'}, 'sha256': 'bad'}]},
    {'contexts': [{'reference': {'kind': 'file', 'path': 1}, 'sha256': 'a' * 64}]},
    {'contexts': [{'reference': {'kind': 'goal'}, 'sha256': 'a' * 64}] * 5},
])
def test_invalid_strict_input_zero_send_and_writes(tmp_path, changes):
    bridge = Bridge()
    with TestClient(app(tmp_path, bridge)) as client:
        local = task(client)
        link = session(client, local)
        before = state(tmp_path, local)
        command = {**payload(link), **changes}
        response = client.post(path(local), headers=HEADERS, json=command)
        assert response.status_code == 422
        if changes.get('request_key', command['request_key']).strip():
            assert response.json() == {
                'error': 'Message input rejected before write',
                'code': 'MESSAGE_INPUT_REJECTED_PREWRITE',
                'request_key': command['request_key'],
            }
        else:
            assert 'code' not in response.json()
        assert state(tmp_path, local) == before and bridge.commands == []


@pytest.mark.parametrize('missing', ['contexts', 'active_turn_behavior', 'session_id', 'text', 'request_key'])
def test_missing_fields_rejected(tmp_path, missing):
    bridge = Bridge()
    with TestClient(app(tmp_path, bridge)) as client:
        local = task(client)
        link = session(client, local)
        command = payload(link)
        del command[missing]
        assert client.post(path(local), headers=HEADERS, json=command).status_code == 422
        assert state(tmp_path, local)[0] == [] and bridge.commands == []


def test_task_actor_workspace_real_scope_and_auth_origin(tmp_path):
    bridge = Bridge()
    with TestClient(app(tmp_path, bridge)) as alice:
        local, other = task(alice), task(alice, 'task-b')
        link = session(alice, local)
        assert alice.post(path(local), json=payload(link), headers={'X-Workbench': '1'}).status_code == 401
        assert alice.post(path(local), json=payload(link), headers={**HEADERS, 'Origin': 'https://foreign.invalid'}).status_code == 403
        assert alice.post(path(other), json=payload(link), headers=HEADERS).status_code == 422
        record = alice.post(path(local), json=payload(link), headers=HEADERS).json()
        assert alice.get(path(other), headers=HEADERS).json() == []
        assert alice.get(path(other) + '/' + record['id'], headers=HEADERS).status_code == 422
        assert alice.get('/api/tasks/missing/agent-messages', headers=HEADERS).status_code == 422
    with TestClient(app(tmp_path, bridge, actor='bob')) as bob:
        assert bob.get(path(local), headers=HEADERS).json() == []
        assert bob.get(path(local) + '/' + record['id'], headers=HEADERS).status_code == 422
        own = bob.post(path(local), json=payload(link), headers=HEADERS).json()
        assert own['actor'] == 'bob' and own['message_id'] != record['message_id']
    repo = WorkbenchRepository(tmp_path / 'state')
    foreign = repo.register_workspace('other', tmp_path / 'foreign')
    foreign_task = repo.create_task(foreign['id'], 'foreign', '', request_key='foreign')
    foreign_link = repo.attach_session(foreign_task['id'], AGENT, actor='alice', request_key='link')
    with TestClient(app(tmp_path, bridge)) as alice:
        before = state(tmp_path, foreign_task)
        assert alice.post(path(foreign_task), json=payload(foreign_link), headers=HEADERS).status_code == 422
        assert alice.get(path(foreign_task), headers=HEADERS).status_code == 422
        assert alice.get(path(foreign_task) + '/' + record['id'], headers=HEADERS).status_code == 422
        assert state(tmp_path, foreign_task) == before
    assert len(bridge.commands) == 2


def test_inflight_unknown_committed_visible_and_duplicate_zero_new_call(tmp_path):
    entered, released = threading.Event(), threading.Event()
    def reply(command):
        entered.set()
        assert released.wait(10)
        return acknowledged(command)
    bridge = Bridge(reply)
    application = app(tmp_path, bridge)
    with TestClient(application) as sender, TestClient(application) as reader, ThreadPoolExecutor(max_workers=1) as workers:
        local = task(reader)
        link = session(reader, local)
        pending = workers.submit(sender.post, path(local), json=payload(link), headers=HEADERS)
        try:
            assert entered.wait(5)
            current = reader.get(path(local), headers=HEADERS).json()[0]
            assert current['status'] == 'unknown' and current['envelope'] is None
            assert current['started_at'] is not None
            assert reader.get(path(local) + '/' + current['id'], headers=HEADERS).json() == current
            assert reader.post(path(local), json=payload(link), headers=HEADERS).json() == current
            with TestClient(app(tmp_path, bridge)) as reopened:
                assert reopened.post(path(local), json=payload(link), headers=HEADERS).json() == current
            assert len(bridge.commands) == 1
        finally:
            released.set()
        assert pending.result(timeout=5).json()['status'] == 'acknowledged'


def test_source_hash_changed_new_intent_zero_send_and_unselected_goal_absent(tmp_path):
    bridge = Bridge()
    checkout = tmp_path / 'checkout '
    checkout.mkdir()
    source_path = checkout / 'source.txt'
    source_path.write_text('original evidence')
    with TestClient(app(tmp_path, bridge)) as client:
        local = task(client)
        link = session(client, local)
        source = resolve_context(WorkbenchRepository(tmp_path / 'state'), local['id'], {'kind': 'file', 'path': 'source.txt'})
        source_path.write_text('changed evidence')
        before = state(tmp_path, local)
        command = payload(link, contexts=[{'reference': source['reference'], 'sha256': source['sha256']}])
        rejected = client.post(path(local), json=command, headers=HEADERS)
        assert rejected.status_code == 409
        assert rejected.json() == {
            'error': 'Selected message context changed before write',
            'code': 'MESSAGE_CONTEXT_CHANGED_PREWRITE',
            'request_key': command['request_key'],
        }
        assert state(tmp_path, local) == before and bridge.commands == []
        assert client.post(path(local), json=payload(link), headers=HEADERS).status_code == 200
        assert json.loads(bridge.commands[0]['text']) == {'message': payload(link)['text'], 'context': []}


def test_selected_file_removed_is_typed_prewrite_refusal(tmp_path):
    bridge = Bridge()
    checkout = tmp_path / 'checkout '
    checkout.mkdir()
    source_path = checkout / 'source.txt'
    source_path.write_text('original evidence')
    with TestClient(app(tmp_path, bridge)) as client:
        local = task(client)
        link = session(client, local)
        source = resolve_context(WorkbenchRepository(tmp_path / 'state'), local['id'],
                                 {'kind': 'file', 'path': 'source.txt'})
        source_path.unlink()
        command = payload(link, request_key=' original key ', contexts=[
            {'reference': source['reference'], 'sha256': source['sha256']}])
        before = state(tmp_path, local)
        rejected = client.post(path(local), json=command, headers=HEADERS)
        assert rejected.status_code == 422
        assert rejected.json() == {
            'error': 'Selected message context unavailable before write',
            'code': 'MESSAGE_CONTEXT_UNAVAILABLE_PREWRITE',
            'request_key': command['request_key'],
        }
        assert state(tmp_path, local) == before and bridge.commands == []
        assert client.get(path(local), headers=HEADERS).json() == []


@pytest.mark.parametrize('failure', ['conflict', 'context_conflict', 'unexpected'])
def test_after_prepare_failure_is_not_prewrite_refusal(tmp_path, monkeypatch, failure):
    from rag_eval.workbench.agent_messages import AgentMessageStore, MessageContextChanged
    from rag_eval.workbench.repository import Conflict

    def rejected_attempt(*args, **kwargs):
        if failure == 'unexpected':
            raise RuntimeError('Synthetic post-prepare failure')
        if failure == 'context_conflict':
            raise MessageContextChanged('Synthetic post-prepare context conflict')
        raise Conflict('Synthetic post-prepare authorization conflict')

    monkeypatch.setattr(AgentMessageStore, 'attempt', rejected_attempt)
    bridge = Bridge()
    with TestClient(app(tmp_path, bridge), raise_server_exceptions=False) as client:
        local = task(client)
        link = session(client, local)
        response = client.post(path(local), json=payload(link), headers=HEADERS)
        assert response.status_code == (500 if failure == 'unexpected' else 409)
        assert 'PREWRITE' not in response.text
        rows, history = state(tmp_path, local)
        assert len(rows) == 1 and rows[0]['status'] == 'prepared'
        assert len([e for e in history if e['kind'] == 'agent.message.prepared']) == 1
        assert bridge.commands == []


def test_case_context_only_existing_safe_projection_no_gold(tmp_path):
    from test_workbench import import_fixture
    from rag_eval.workbench.ingest import read_result
    imported = import_fixture(tmp_path)
    bridge = Bridge()
    with TestClient(app(tmp_path, bridge)) as client:
        local = task(client)
        link = session(client, local)
        repo = WorkbenchRepository(tmp_path / 'state')
        repo.attach_result(local['id'], imported['id'])
        case_id = read_result(repo.root, imported['id'])['cases'][0]['case_id']
        reference = {'kind': 'case', 'result_id': imported['id'], 'case_id': case_id}
        source = resolve_context(repo, local['id'], reference)
        response = client.post(path(local), json=payload(link, contexts=[{'reference': reference, 'sha256': source['sha256']}]), headers=HEADERS)
        assert response.status_code == 200
        text = json.loads(bridge.commands[0]['text'])['context'][0]['text']
        assert set(json.loads(text)) == {'case_id', 'question', 'status', 'answer', 'answer_source'}
        assert 'gold' not in text and 'references' not in text
        safe(response.json())


def test_escaped_command_plus_config_budget_before_claim_and_frozen_config(tmp_path):
    settings = config(tmp_path)
    settings['agent_bridge'] = {'url': 'ws://synthetic.invalid', 'password': '中' * 8192}
    bridge = Bridge()
    application = app(tmp_path, bridge, settings=settings)
    settings['agent_bridge']['password'] = ''
    with TestClient(application) as client:
        local = task(client)
        link = session(client, local)
        command = payload(link, text='中' * 2730)
        initial = state(tmp_path, local)
        response = client.post(path(local), json=command, headers=HEADERS)
        assert response.status_code == 422
        assert response.json() == {
            'error': 'Message input rejected before write',
            'code': 'MESSAGE_INPUT_REJECTED_PREWRITE',
            'request_key': command['request_key'],
        }
        records = client.get(path(local), headers=HEADERS).json()
        assert records == [], 'preflight rejection must precede domain INSERT'
        rows, history = state(tmp_path, local)
        assert rows == []
        assert not any(e['kind'].startswith('agent.message.') for e in history)
        assert state(tmp_path, local) == initial
        assert bridge.commands == []
        before = state(tmp_path, local)
        assert client.post(path(local), json=command, headers=HEADERS).status_code == 422
        assert state(tmp_path, local) == before and bridge.commands == []
        # No prior intent exists: explicitly corrected content is a new first
        # preparation, even with the original key.
        corrected = client.post(path(local), json={**command, 'text': 'corrected'}, headers=HEADERS)
        assert corrected.status_code == 200 and corrected.json()['status'] == 'acknowledged'
        record = corrected.json()
        assert record['request_key'] == command['request_key'] and record['text'] == 'corrected'
        assert client.post(path(local), json={**command, 'text': 'corrected'}, headers=HEADERS).json() == record
        assert client.post(path(local), json=command, headers=HEADERS).status_code == 409
        assert len(bridge.commands) == 1


@pytest.mark.parametrize('failure', [ValueError, TypeError, RecursionError])
def test_preflight_failure_is_masked_and_has_no_domain_write(tmp_path, failure):
    class RejectingBridge(Bridge):
        def validate_request(self, command):
            raise failure('private-secret /private/source ws://private.invalid')

    bridge = RejectingBridge()
    with TestClient(app(tmp_path, bridge), raise_server_exceptions=False) as client:
        local = task(client)
        link = session(client, local)
        before = state(tmp_path, local)
        response = client.post(path(local), json=payload(link), headers=HEADERS)
        assert response.status_code == 422
        assert response.json() == {
            'error': 'Message input rejected before write',
            'code': 'MESSAGE_INPUT_REJECTED_PREWRITE',
            'request_key': payload(link)['request_key'],
        }
        assert state(tmp_path, local) == before
        assert client.get(path(local), headers=HEADERS).json() == []
        assert bridge.commands == []


def test_existing_receipt_bypasses_changed_preflight_and_preserves_conflict(tmp_path):
    from rag_eval.workbench.message_bridge import validate_send_request
    calls = []
    class CheckedBridge(Bridge):
        def validate_request(self, command):
            calls.append(command)
            if len(calls) != 1:
                raise AssertionError('existing receipt invoked preflight')
            return validate_send_request(command, {'url': 'ws://synthetic.invalid'})
    bridge = CheckedBridge()
    with TestClient(app(tmp_path, bridge)) as client:
        local = task(client)
        link = session(client, local)
        record = client.post(path(local), json=payload(link), headers=HEADERS).json()
        assert record['status'] == 'acknowledged'
        before = state(tmp_path, local)
        assert client.post(path(local), json=payload(link), headers=HEADERS).json() == record
        assert client.post(path(local), json=payload(link, text='different'), headers=HEADERS).status_code == 409
        assert client.get(path(local), headers=HEADERS).json() == [record]
        assert state(tmp_path, local) == before
        assert len(calls) == 1 and len(bridge.commands) == 1


def test_bootstrap_send_is_configuration_only_and_automatic_adapter_independent(tmp_path, monkeypatch):
    import rag_eval.workbench.message_bridge as module
    constructed = []
    def factory(server_config):
        constructed.append(dict(server_config))
        return Bridge()
    monkeypatch.setattr(module, 'AgentMessageBridgeAdapter', factory)
    settings = config(tmp_path)
    settings['agent_bridge'] = {'url': 'ws://synthetic.invalid', 'password': 'private-secret'}
    with TestClient(app(tmp_path, settings=settings)) as client:
        bootstrap = client.get('/api/bootstrap', headers=HEADERS).json()
        assert bootstrap['capabilities']['agent_send'] is True
        assert bootstrap['capabilities']['execution'] is False
        assert bootstrap['capabilities']['agent_create'] is False
        assert 'private-secret' not in json.dumps(bootstrap)
        assert 'synthetic.invalid' not in json.dumps(bootstrap)
    assert constructed == [settings['agent_bridge']]
    with TestClient(app(tmp_path)) as client:
        assert client.get('/api/bootstrap', headers=HEADERS).json()['capabilities']['agent_send'] is False


def test_get_receipts_issue_only_reads_and_no_process(tmp_path, monkeypatch):
    bridge = Bridge()
    with TestClient(app(tmp_path, bridge)) as client:
        local = task(client)
        link = session(client, local)
        record = client.post(path(local), json=payload(link), headers=HEADERS).json()
        statements = []
        original = WorkbenchRepository.connect
        @contextmanager
        def read_only(repository, *, write=False):
            assert not write, 'GET started write transaction'
            with original(repository, write=write) as conn:
                conn.set_trace_callback(statements.append)
                yield conn
        def forbidden(*args, **kwargs):
            raise AssertionError('GET spawned process')
        monkeypatch.setattr(WorkbenchRepository, 'connect', read_only)
        monkeypatch.setattr(subprocess, 'Popen', forbidden)
        assert client.get(path(local), headers=HEADERS).json() == [record]
        assert client.get(path(local) + '/' + record['id'], headers=HEADERS).json() == record
        assert all(sql.lstrip().split()[0].upper() == 'SELECT' for sql in statements)
        assert len(bridge.commands) == 1


def test_http_to_owned_adapter_exact_preflight_and_stdin_without_daemon(tmp_path):
    from rag_eval.workbench.message_bridge import AgentMessageBridgeAdapter
    capture = tmp_path / 'request.json'
    script = tmp_path / 'transport.py'
    # Real owned subprocess; this fixture only captures synthetic input and ACKs
    # the frozen intent. It exercises transport, not the real public SDK.
    script.write_text('import json,sys\nfrom pathlib import Path\n'
                      'request=json.load(sys.stdin)\n'
                      f'Path({str(capture)!r}).write_text(json.dumps(request))\n'
                      'command=request["command"]\n'
                      'print(json.dumps({"result":{"status":"acknowledged","agentId":command["agentId"],'
                      '"messageId":command["messageId"]},"release":{"status":"ok"}}))\n')
    server_config = {'url': 'ws://synthetic.invalid', 'password': 'private-secret'}
    adapter = AgentMessageBridgeAdapter(server_config, command=[sys.executable, str(script)])
    with TestClient(app(tmp_path, adapter)) as client:
        local = task(client)
        link = session(client, local)
        response = client.post(path(local), json=payload(link), headers=HEADERS)
        assert response.status_code == 200
        record = response.json()
        rows, _ = state(tmp_path, local)
        frozen = json.loads(rows[0]['command'])
        request = json.loads(capture.read_text())
        assert request == {'config': server_config, 'command': frozen}
        assert adapter.validate_request(frozen) == json.dumps(request, ensure_ascii=True).encode()
        assert record['message_id'] == frozen['messageId'] and record['envelope'] == acknowledged(frozen)
        safe(record)
