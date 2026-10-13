"""Persist creation uncertainty before external effects; never replay an intent."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

import pytest

from rag_eval.workbench.repository import Conflict, WorkbenchRepository


def setup(tmp_path):
    from rag_eval.workbench.agent_creations import AgentCreationStore
    repo = WorkbenchRepository(tmp_path / 'state')
    workspace = repo.register_workspace('local', tmp_path)
    task = repo.create_task(workspace['id'], 'task', '', request_key='task')
    bindings = {workspace['id']: {'workspaceId': 'workspace-example', 'profiles': {
        'allowed': {'provider': 'codex/model-example', 'modeId': 'mode-example',
                    'thinkingOptionId': 'effort-example'}}}}
    return repo, task, bindings, AgentCreationStore(repo, bindings)


def prepared(store, task, key='create'):
    return store.prepare(task['id'], 'allowed', actor='alice', request_key=key, title='Example')


def created(command, *, ready=True, release_failed=False):
    agent = {'id': command['agentId'], 'cwd': command['expectedCwd'],
             'workspaceId': command['workspaceId'], 'status': 'idle' if ready else 'running',
             'title': 'Example', 'provider': 'codex', 'model': 'model-example',
             'archivedAt': None, 'activeTurn': None,
             'capabilities': {key: None for key in (
                 'supportsStreaming', 'supportsSessionPersistence', 'supportsSessionListing',
                 'supportsDynamicModes', 'supportsMcpServers', 'supportsReasoningStream',
                 'supportsToolInvocations', 'supportsRewindConversation', 'supportsRewindFiles',
                 'supportsRewindBoth')}}
    return {'result': {'status': 'created', 'agentId': command['agentId'], 'agent': agent,
                       'ready': ready, 'readiness': 'idle' if ready else 'not_idle'},
            'release': {'status': 'unavailable', 'code': 'CLOSE_FAILED'} if release_failed else {'status': 'ok'}}


class Bridge:
    def __init__(self, reply):
        self.reply = reply
        self.commands = []

    def call(self, command):
        self.commands.append(copy.deepcopy(command))
        return self.reply(command)


def test_prepare_freezes_server_scope_identity_and_exact_request_key(tmp_path):
    repo, task, bindings, store = setup(tmp_path)
    intent = prepared(store, task, ' key ')
    identity = str(uuid.UUID(intent['command']['agentId']))
    assert intent['command'] == {
        'op': 'createAgent', 'workspaceId': 'workspace-example', 'expectedCwd': str(tmp_path.resolve()),
        'agentId': identity, 'idempotencyKey': intent['command']['idempotencyKey'],
        'config': {'provider': 'codex/model-example', 'modeId': 'mode-example',
                   'thinkingOptionId': 'effort-example'}, 'title': 'Example'}
    assert intent['status'] == 'prepared' and intent['envelope'] is None
    frozen = copy.deepcopy(intent)
    intent['command']['config']['provider'] = 'tampered/model'
    bindings[task['workspace_id']]['profiles']['allowed']['provider'] = 'tampered/model'
    assert prepared(store, task, ' key ') == frozen
    other = prepared(store, task, 'key')
    assert other['id'] != frozen['id'] and other['command']['agentId'] != identity
    with pytest.raises(Conflict):
        store.prepare(task['id'], 'allowed', actor='alice', request_key=' key ', title='Different')
    assert [event['kind'] for event in repo.task(task['id'])['history']] == [
        'task.created', 'agent.creation.prepared', 'agent.creation.prepared']


def test_crash_after_external_effect_survives_reopen_without_replay(tmp_path):
    repo, task, bindings, store = setup(tmp_path)
    intent = prepared(store, task)
    marker = tmp_path / 'external-effect'
    script = tmp_path / 'crash.py'
    script.write_text('''import json,os,sys
from pathlib import Path
from rag_eval.workbench.repository import WorkbenchRepository
from rag_eval.workbench.agent_creations import AgentCreationStore
class Bridge:
    def call(self, command):
        Path(sys.argv[5]).write_text(command['agentId'])
        os._exit(23)
store=AgentCreationStore(WorkbenchRepository(sys.argv[1]),json.loads(sys.argv[2]))
store.attempt(sys.argv[3],sys.argv[4],actor='alice',bridge=Bridge())
''')
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
    child = subprocess.run([sys.executable, str(script), str(repo.root), json.dumps(bindings),
                            task['id'], intent['id'], str(marker)], env=environment, timeout=10)
    assert child.returncode == 23 and marker.read_text() == intent['command']['agentId']
    from rag_eval.workbench.agent_creations import AgentCreationStore
    reopened = AgentCreationStore(WorkbenchRepository(repo.root), bindings)
    current = reopened.get(task['id'], intent['id'], actor='alice')
    assert current['status'] == 'unknown' and current['started_at'] is not None
    bridge = Bridge(lambda command: created(command))
    assert reopened.attempt(task['id'], intent['id'], actor='alice', bridge=bridge) == current
    assert bridge.commands == [] and repo.task(task['id'])['sessions'] == []


def test_concurrent_attempts_claim_once_and_atomically_link_created_fact(tmp_path):
    repo, task, _, store = setup(tmp_path)
    intent = prepared(store, task)
    entered, release = threading.Event(), threading.Event()

    def reply(command):
        entered.set()
        assert release.wait(5)
        return created(command, release_failed=True)

    bridge = Bridge(reply)
    with ThreadPoolExecutor(max_workers=5) as workers:
        first = workers.submit(store.attempt, task['id'], intent['id'], actor='alice', bridge=bridge)
        try:
            assert entered.wait(5)
            others = list(workers.map(lambda _: store.attempt(task['id'], intent['id'], actor='alice', bridge=bridge), range(4)))
            assert all(value['status'] == 'unknown' for value in others)
            # A separate connection must already see uncertainty while RPC is in flight.
            assert store.get(task['id'], intent['id'], actor='alice')['status'] == 'unknown'
        finally:
            release.set()
        result = first.result()
    assert len(bridge.commands) == 1 and result['status'] == 'created'
    assert result['envelope']['release'] == {'status': 'unavailable', 'code': 'CLOSE_FAILED'}
    persisted = repo.task(task['id'])
    assert len(persisted['sessions']) == 1
    assert persisted['sessions'][0]['id'] == result['session_id']
    assert persisted['sessions'][0]['agent_id'] == intent['command']['agentId']
    assert [event['kind'] for event in persisted['history']] == [
        'task.created', 'agent.creation.prepared', 'agent.creation.started',
        'session.attached', 'agent.creation.observed']
    assert store.attempt(task['id'], intent['id'], actor='alice', bridge=bridge) == result
    assert len(bridge.commands) == 1


def test_not_idle_created_preserves_existing_local_reference(tmp_path):
    repo, task, _, store = setup(tmp_path)
    intent = prepared(store, task)
    session = repo.attach_session(task['id'], intent['command']['agentId'], actor='bob', request_key='attach')
    bridge = Bridge(lambda command: created(command, ready=False))
    result = store.attempt(task['id'], intent['id'], actor='alice', bridge=bridge)
    assert result['status'] == 'created' and result['session_id'] == session['id']
    assert result['envelope']['result']['ready'] is False
    assert repo.task(task['id'])['sessions'] == [session]
    assert sum(event['kind'] == 'session.attached' for event in repo.task(task['id'])['history']) == 1


@pytest.mark.parametrize('status', ['not_attempted', 'unknown'])
def test_no_link_or_replay_from_existing_or_uncertain_remote_agent(tmp_path, status):
    repo, task, _, store = setup(tmp_path)
    intent = prepared(store, task)

    def reply(command):
        result = ({'status': 'not_attempted', 'code': 'AGENT_ALREADY_EXISTS', 'agentId': command['agentId']}
                  if status == 'not_attempted' else
                  {'status': 'unknown', 'code': 'CREATE_OUTCOME_UNKNOWN', 'cause': 'CONNECTION_UNAVAILABLE',
                   'agentId': command['agentId'], 'observed': {'handleId': command['agentId']}})
        return {'result': result, 'release': {'status': 'ok'}}

    bridge = Bridge(reply)
    result = store.attempt(task['id'], intent['id'], actor='alice', bridge=bridge)
    assert result['status'] == status and result['session_id'] is None
    assert result['envelope'] == reply(intent['command'])
    assert store.attempt(task['id'], intent['id'], actor='alice', bridge=bridge) == result
    assert len(bridge.commands) == 1 and repo.task(task['id'])['sessions'] == []


def test_wrong_identity_and_adapter_exception_remain_unknown_without_private_error(tmp_path):
    repo, task, _, store = setup(tmp_path)
    for kind in ('identity', 'exception'):
        intent = prepared(store, task, kind)

        def reply(command):
            if kind == 'exception':
                raise RuntimeError('private-path-and-secret')
            envelope = created(command)
            envelope['result']['agent']['id'] = 'b7abb649-7b0d-4ead-9368-b2a4c7c58209'
            return envelope

        bridge = Bridge(reply)
        result = store.attempt(task['id'], intent['id'], actor='alice', bridge=bridge)
        assert result['status'] == 'unknown' and result['session_id'] is None
        assert result['envelope']['result']['code'] == 'CREATE_OUTCOME_UNKNOWN'
        assert result['envelope']['result']['agentId'] == intent['command']['agentId']
        assert 'private-path-and-secret' not in json.dumps(result)
        assert store.attempt(task['id'], intent['id'], actor='alice', bridge=bridge) == result
        assert len(bridge.commands) == 1
    assert repo.task(task['id'])['sessions'] == []


def test_task_actor_and_current_authorization_checked_before_dispatch(tmp_path):
    repo, task, bindings, store = setup(tmp_path)
    intent = prepared(store, task)
    other = repo.create_task(task['workspace_id'], 'other', '', request_key='other')
    bridge = Bridge(lambda command: created(command))
    for target, actor in ((other['id'], 'alice'), (task['id'], 'bob')):
        with pytest.raises(ValueError):
            store.attempt(target, intent['id'], actor=actor, bridge=bridge)
    from rag_eval.workbench.agent_creations import AgentCreationStore
    bindings[task['workspace_id']]['profiles']['allowed']['provider'] = 'other/model'
    changed = AgentCreationStore(repo, bindings)
    with pytest.raises(Conflict):
        changed.attempt(task['id'], intent['id'], actor='alice', bridge=bridge)
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE workspaces SET location=? WHERE id=?', (str(tmp_path / 'other'), task['workspace_id']))
    with pytest.raises(Conflict):
        store.attempt(task['id'], intent['id'], actor='alice', bridge=bridge)
    assert bridge.commands == []
    assert store.get(task['id'], intent['id'], actor='alice')['status'] == 'prepared'


def test_invalid_profile_creates_no_intent_or_history_and_old_state_survives(tmp_path):
    repo, task, _, store = setup(tmp_path)
    note = repo.save_note(task['id'], 'preserve', actor='alice', request_key='note')
    before = repo.task(task['id'])
    with pytest.raises(ValueError):
        store.prepare(task['id'], 'unregistered', actor='alice', request_key='bad')
    assert repo.task(task['id']) == before
    with repo.connect(write=True) as conn:
        assert conn.execute('SELECT count(*) FROM agent_creation_intents').fetchone()[0] == 0
        conn.execute('PRAGMA user_version=1')
    reopened = WorkbenchRepository(repo.root)
    assert reopened.task(task['id'])['history'][-1]['content'] == note
    assert prepared(store, task)['status'] == 'prepared'


def test_local_commit_failure_rolls_back_link_and_never_replays_remote_creation(tmp_path, monkeypatch):
    repo, task, _, store = setup(tmp_path)
    intent = prepared(store, task)
    original = repo._event

    def fail_observation(conn, task_id, kind, object_id, content):
        if kind == 'agent.creation.observed':
            raise OSError('simulated-local-write-failure')
        return original(conn, task_id, kind, object_id, content)

    monkeypatch.setattr(repo, '_event', fail_observation)
    bridge = Bridge(lambda command: created(command))
    with pytest.raises(OSError):
        store.attempt(task['id'], intent['id'], actor='alice', bridge=bridge)
    # The external call succeeded, but the local outcome/link transaction did
    # not commit. Uncertainty is the only durable fact; do not create again.
    current = store.get(task['id'], intent['id'], actor='alice')
    assert current['status'] == 'unknown' and current['envelope'] is None
    assert repo.task(task['id'])['sessions'] == []
    assert [event['kind'] for event in repo.task(task['id'])['history']] == [
        'task.created', 'agent.creation.prepared', 'agent.creation.started']
    monkeypatch.setattr(repo, '_event', original)
    assert store.attempt(task['id'], intent['id'], actor='alice', bridge=bridge) == current
    assert len(bridge.commands) == 1


def test_bridge_cannot_mutate_frozen_intent_and_history_excludes_private_scope(tmp_path):
    repo, task, _, store = setup(tmp_path)
    intent = prepared(store, task)

    def reply(command):
        envelope = created(command)
        command['agentId'] = 'b7abb649-7b0d-4ead-9368-b2a4c7c58209'
        command['expectedCwd'] = '/different'
        command['config']['provider'] = 'different/model'
        return envelope

    result = store.attempt(task['id'], intent['id'], actor='alice', bridge=Bridge(reply))
    assert result['status'] == 'created' and result['command'] == intent['command']
    history = json.dumps(repo.task(task['id'])['history'])
    assert str(tmp_path) not in history
    assert 'expectedCwd' not in history and 'capabilities' not in history
    assert intent['command']['idempotencyKey'] not in history
