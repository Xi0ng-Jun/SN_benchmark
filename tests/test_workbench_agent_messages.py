"""Durable single dispatch; fake external effects, real SQLite and processes."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from rag_eval.workbench.context import resolve_context
from rag_eval.workbench.repository import Conflict, WorkbenchRepository


AGENT = '8fd05bb1-5a60-4dcd-81cd-d2ed82ba59e4'  # Synthetic local reference only.


def setup(tmp_path):
    from rag_eval.workbench.agent_messages import AgentMessageStore
    repo = WorkbenchRepository(tmp_path / 'state')
    workspace = repo.register_workspace('local', tmp_path)
    task = repo.create_task(workspace['id'], 'task', 'Goal is not implicit', request_key='task')
    session = repo.attach_session(task['id'], AGENT, actor='alice', request_key='session')
    return repo, task, session, AgentMessageStore(repo, workspace_ids=[workspace['id']])


def prepare(store, task, session, *, key='message', text='  原值消息\n  ', contexts=None, behavior='steer'):
    return store.prepare(task['id'], session['id'], text, contexts or [], behavior,
                         actor='alice', request_key=key)


def acknowledged(command, release_failed=False):
    return {'result': {'status': 'acknowledged', 'agentId': command['agentId'], 'messageId': command['messageId']},
            'release': {'status': 'unavailable', 'code': 'CLOSE_FAILED'} if release_failed else {'status': 'ok'}}


class Bridge:
    def __init__(self, reply=acknowledged):
        self.reply = reply
        self.commands = []
        self.lock = threading.Lock()

    def call(self, command):
        with self.lock:
            self.commands.append(copy.deepcopy(command))
        return self.reply(command)


def test_frozen_explicit_inputs_exact_keys_and_no_default_goal(tmp_path):
    repo, task, session, store = setup(tmp_path)
    source = resolve_context(repo, task['id'], {'kind': 'goal'})
    selection = [{'reference': source['reference'], 'sha256': source['sha256']}]
    empty = prepare(store, task, session, key=' key ')
    selected = prepare(store, task, session, key='key', contexts=selection, behavior='interrupt')
    assert empty['id'] != selected['id'] and empty['request_key'] == ' key '
    assert empty['text'] == '  原值消息\n  '
    assert 'Goal is not implicit' not in empty['command']['text']
    assert json.loads(selected['command']['text'])['context'][0]['text'] == source['text']
    assert selected['snapshots'][0]['source_refs'] == source['source_refs']
    assert selected['command']['activeTurnBehavior'] == 'interrupt'
    assert set(selected['command']) == {'op', 'agentId', 'expectedCwd', 'text', 'messageId', 'activeTurnBehavior'}
    assert selected['body_sha256'] == hashlib.sha256(selected['command']['text'].encode()).hexdigest()
    selected['command']['text'] = 'mutated'
    selection[0]['reference']['kind'] = 'file'
    assert prepare(store, task, session, key='key', contexts=[{'reference': {'kind': 'goal'}, 'sha256': source['sha256']}],
                   behavior='interrupt')['command']['text'] != 'mutated'
    with pytest.raises(Conflict):
        prepare(store, task, session, key='key', contexts=[{'reference': {'kind': 'goal'}, 'sha256': source['sha256']}], behavior='steer')
    history = repo.task(task['id'])['history']
    assert all('expectedCwd' not in json.dumps(event) and '原值消息' not in json.dumps(event, ensure_ascii=False) for event in history)


def test_changed_source_rejects_new_intent_but_old_frozen_receipt_survives(tmp_path):
    repo, task, session, store = setup(tmp_path)
    path = tmp_path / 'source.txt'; path.write_text('  旧引用\n')
    source = resolve_context(repo, task['id'], {'kind': 'file', 'path': 'source.txt'})
    selections = [{'reference': source['reference'], 'sha256': source['sha256']}]
    frozen = prepare(store, task, session, contexts=selections)
    path.write_text('新引用')
    assert prepare(store, task, session, contexts=selections)['id'] == frozen['id']
    with pytest.raises((ValueError, Conflict)):
        prepare(store, task, session, key='new', contexts=selections)
    assert len(store.list(task['id'], actor='alice')) == 1
    assert json.loads(frozen['command']['text'])['context'][0]['text'] == '  旧引用\n'


@pytest.mark.parametrize('change', [
    {'text': ' '}, {'text': '\0'}, {'text': 'x' * 8193}, {'behavior': None}, {'behavior': 'other'},
    {'contexts': [{'reference': {'kind': 'goal'}, 'sha256': 'bad'}]},
    {'contexts': [{'reference': {'kind': 'goal'}, 'sha256': 'a' * 64, 'text': 'free source'}]},
])
def test_invalid_inputs_do_not_persist(tmp_path, change):
    repo, task, session, store = setup(tmp_path)
    with pytest.raises((ValueError, Conflict)):
        prepare(store, task, session, **change)
    assert store.list(task['id'], actor='alice') == []


def test_total_budget_and_real_foreign_scope_before_dispatch(tmp_path):
    repo, task, session, store = setup(tmp_path)
    for i in range(5):
        (tmp_path / f'{i}.txt').write_text('字' * 3000)
    selections = []
    for i in range(5):
        snapshot = resolve_context(repo, task['id'], {'kind': 'file', 'path': f'{i}.txt'})
        selections.append({'reference': snapshot['reference'], 'sha256': snapshot['sha256']})
    for over in [selections, selections[:4]]:
        with pytest.raises(ValueError):
            prepare(store, task, session, contexts=over)
    other = repo.create_task(task['workspace_id'], 'other', '', request_key='other')
    with pytest.raises(ValueError):
        prepare(store, other, session)
    foreign = repo.register_workspace('foreign', tmp_path / 'foreign')
    foreign_task = repo.create_task(foreign['id'], 'foreign', '', request_key='foreign')
    foreign_session = repo.attach_session(foreign_task['id'], AGENT, actor='alice', request_key='foreign')
    with pytest.raises(ValueError):
        prepare(store, foreign_task, foreign_session)
    assert store.list(task['id'], actor='alice') == []


@pytest.mark.parametrize('source', ['x' * 16300, '字' * 10000], ids=['utf16', 'utf8'])
def test_final_body_obeys_actual_bridge_units_and_bytes_before_persistence(tmp_path, source):
    repo, task, session, store = setup(tmp_path)
    (tmp_path / 'body.txt').write_text(source)
    snapshot = resolve_context(repo, task['id'], {'kind': 'file', 'path': 'body.txt'})
    selection = [{'reference': snapshot['reference'], 'sha256': snapshot['sha256']}]
    # Each input separately meets the UI/store selection budgets. Their actual
    # JSON send body exceeds either A07a's UTF-16 or UTF-8 bound.
    with pytest.raises(ValueError):
        prepare(store, task, session, text='m' * 8192, contexts=selection)
    assert store.list(task['id'], actor='alice') == []
    assert all(event['kind'] != 'agent.message.prepared' for event in repo.task(task['id'])['history'])


def test_concurrent_claim_and_read_inflight_never_replay(tmp_path):
    repo, task, session, store = setup(tmp_path)
    intent = prepare(store, task, session)
    reached, finish = threading.Event(), threading.Event()
    def reply(command):
        reached.set(); assert finish.wait(10)
        return acknowledged(command, release_failed=True)
    bridge = Bridge(reply)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(store.attempt, task['id'], intent['id'], actor='alice', bridge=bridge)
        assert reached.wait(10)
        try:
            inflight = store.get(task['id'], intent['id'], actor='alice')
            assert inflight['status'] == 'unknown' and inflight['envelope'] is None
            assert store.attempt(task['id'], intent['id'], actor='alice', bridge=bridge)['status'] == 'unknown'
            assert len(bridge.commands) == 1
        finally:
            finish.set()
        saved = first.result()
    assert saved['status'] == 'acknowledged' and saved['envelope']['release']['code'] == 'CLOSE_FAILED'
    assert store.attempt(task['id'], intent['id'], actor='alice', bridge=bridge) == saved
    assert len(bridge.commands) == 1
    with pytest.raises(ValueError):
        store.get(task['id'], intent['id'], actor='bob')


def test_concurrent_prepare_returns_one_frozen_intent(tmp_path):
    repo, task, session, store = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(lambda _: prepare(store, task, session), range(8)))
    assert len({record['id'] for record in records}) == 1
    assert len({record['message_id'] for record in records}) == 1
    assert len(store.list(task['id'], actor='alice')) == 1
    assert len([event for event in repo.task(task['id'])['history']
                if event['kind'] == 'agent.message.prepared']) == 1


@pytest.mark.parametrize('change', ['agent', 'cwd', 'session_task', 'workspace_scope'])
def test_changed_authorization_rejects_before_claim(tmp_path, change):
    repo, task, session, store = setup(tmp_path)
    intent = prepare(store, task, session)
    other_workspace = repo.register_workspace('other', tmp_path / 'other')
    other_task = repo.create_task(other_workspace['id'], 'other', '', request_key='other')
    with repo.connect(write=True) as conn:
        if change == 'agent':
            conn.execute('UPDATE task_sessions SET agent_id=? WHERE id=?',
                         ('b7abb649-7b0d-4ead-9368-b2a4c7c58209', session['id']))
        elif change == 'cwd':
            conn.execute('UPDATE workspaces SET location=? WHERE id=?',
                         (str(tmp_path / 'changed'), task['workspace_id']))
        elif change == 'session_task':
            conn.execute('UPDATE task_sessions SET task_id=? WHERE id=?', (other_task['id'], session['id']))
        else:
            conn.execute('UPDATE tasks SET workspace_id=? WHERE id=?', (other_workspace['id'], task['id']))
    bridge = Bridge()
    with pytest.raises(ValueError):
        store.attempt(task['id'], intent['id'], actor='alice', bridge=bridge)
    assert bridge.commands == []
    with repo.connect() as conn:
        saved = conn.execute('SELECT status,started_at FROM agent_message_intents WHERE id=?', (intent['id'],)).fetchone()
    assert saved['status'] == 'prepared' and saved['started_at'] is None


def test_registered_workspace_identity_is_frozen_before_dispatch(tmp_path):
    repo, task, session, store = setup(tmp_path)
    intent = prepare(store, task, session)
    alternate = repo.register_workspace('alternate', tmp_path / 'alternate')
    with repo.connect(write=True) as conn:
        # Keep the same path so a cwd-only check cannot detect the scope swap.
        conn.execute('UPDATE workspaces SET location=? WHERE id=?',
                     (str(tmp_path / 'swap-original'), task['workspace_id']))
        conn.execute('UPDATE workspaces SET location=? WHERE id=?',
                     (str(tmp_path.resolve()), alternate['id']))
        conn.execute('UPDATE tasks SET workspace_id=? WHERE id=?', (alternate['id'], task['id']))
    bridge = Bridge(lambda command: acknowledged(command))
    from rag_eval.workbench.agent_messages import AgentMessageStore
    reopened_scope = AgentMessageStore(repo, workspace_ids=[task['workspace_id'], alternate['id']])
    with pytest.raises(Conflict):
        reopened_scope.attempt(task['id'], intent['id'], actor='alice', bridge=bridge)
    assert bridge.commands == []
    assert reopened_scope.get(task['id'], intent['id'], actor='alice')['status'] == 'prepared'


def test_message_id_never_enters_task_history(tmp_path):
    repo, task, session, store = setup(tmp_path)
    intent = prepare(store, task, session)
    bridge = Bridge(lambda command: acknowledged(command))
    store.attempt(task['id'], intent['id'], actor='alice', bridge=bridge)
    history = json.dumps(repo.task(task['id'])['history'], ensure_ascii=False)
    assert intent['message_id'] not in history


def test_pure_preflight_failure_precedes_intent_insert_and_history(tmp_path):
    repo, task, session, store = setup(tmp_path)
    def reject(command):
        assert command['op'] == 'sendAgent' and json.loads(command['text'])['message'] == 'exact'
        raise ValueError('serialization budget')
    before = repo.task(task['id'])['history']
    with pytest.raises(ValueError):
        store.prepare(task['id'], session['id'], 'exact', [], 'interrupt',
                      actor='alice', request_key='preflight', preflight=reject)
    assert store.list(task['id'], actor='alice') == []
    assert repo.task(task['id'])['history'] == before
    frozen = prepare(store, task, session)
    # Recovery consumes the original frozen record, even if transport config
    # has subsequently disappeared or cannot serialize this request anymore.
    def never(_command):
        raise AssertionError('old intent cannot be revalidated or dispatched')
    assert store.prepare(task['id'], session['id'], frozen['text'], [], 'steer',
                         actor='alice', request_key=frozen['request_key'], preflight=never) == frozen


def test_legacy_prepared_without_frozen_workspace_is_readable_but_never_dispatches(tmp_path):
    from rag_eval.workbench.agent_messages import AgentMessageStore
    repo, task, session, store = setup(tmp_path)
    intent = prepare(store, task, session)
    alternate = repo.register_workspace('alternate', tmp_path / 'alternate')
    with repo.connect(write=True) as conn:
        conn.execute('ALTER TABLE agent_message_intents DROP COLUMN workspace_id')
        conn.execute('UPDATE workspaces SET location=? WHERE id=?',
                     (str(tmp_path / 'swap-original'), task['workspace_id']))
        conn.execute('UPDATE workspaces SET location=? WHERE id=?',
                     (str(tmp_path.resolve()), alternate['id']))
        conn.execute('UPDATE tasks SET workspace_id=? WHERE id=?', (alternate['id'], task['id']))
    reopened = AgentMessageStore(repo, workspace_ids=[task['workspace_id'], alternate['id']])
    saved = reopened.get(task['id'], intent['id'], actor='alice')
    assert saved['id'] == intent['id'] and saved['command'] == intent['command']
    bridge = Bridge()
    with pytest.raises(Conflict):
        reopened.attempt(task['id'], intent['id'], actor='alice', bridge=bridge)
    assert bridge.commands == []
    assert reopened.get(task['id'], intent['id'], actor='alice')['status'] == 'prepared'


def test_crash_immediately_after_claim_commit_does_not_dispatch_after_reopen(tmp_path):
    repo, task, session, store = setup(tmp_path)
    intent = prepare(store, task, session)
    marker = tmp_path / 'unexpected-effect'
    script = '''
import os,sys
from pathlib import Path
from contextlib import contextmanager
from rag_eval.workbench.repository import WorkbenchRepository
from rag_eval.workbench.agent_messages import AgentMessageStore
repo=WorkbenchRepository(sys.argv[1])
store=AgentMessageStore(repo,workspace_ids=[sys.argv[2]])
original=repo.connect
@contextmanager
def crash_commit(*,write=False):
 with original(write=write) as conn:
  yield conn
 if write:
  os._exit(24)
repo.connect=crash_commit
class Bridge:
 def call(self,command):
  Path(sys.argv[5]).write_text('unexpected')
store.attempt(sys.argv[3],sys.argv[4],actor='alice',bridge=Bridge())
'''
    result = subprocess.run([sys.executable, '-c', script, str(repo.root), task['workspace_id'], task['id'], intent['id'], str(marker)],
                            cwd=Path(__file__).resolve().parents[1], timeout=15, capture_output=True,
                            env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')})
    assert result.returncode == 24 and not marker.exists()
    bridge = Bridge()
    assert store.get(task['id'], intent['id'], actor='alice')['status'] == 'unknown'
    assert store.attempt(task['id'], intent['id'], actor='alice', bridge=bridge)['status'] == 'unknown'
    assert bridge.commands == []


def test_crash_after_external_effect_reopen_preserves_unknown(tmp_path):
    repo, task, session, store = setup(tmp_path)
    intent = prepare(store, task, session)
    marker = tmp_path / 'effect'
    script = '''
import os,sys
from pathlib import Path
from rag_eval.workbench.repository import WorkbenchRepository
from rag_eval.workbench.agent_messages import AgentMessageStore
repo=WorkbenchRepository(sys.argv[1])
store=AgentMessageStore(repo,workspace_ids=[sys.argv[2]])
class Bridge:
 def call(self,command):
  Path(sys.argv[5]).write_text(command['messageId'])
  os._exit(23)
store.attempt(sys.argv[3],sys.argv[4],actor='alice',bridge=Bridge())
'''
    result = subprocess.run([sys.executable, '-c', script, str(repo.root), task['workspace_id'], task['id'], intent['id'], str(marker)],
                            cwd=Path(__file__).resolve().parents[1], timeout=15, capture_output=True,
                            env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')})
    assert result.returncode == 23 and marker.read_text() == intent['message_id']
    from rag_eval.workbench.agent_messages import AgentMessageStore
    reopened = AgentMessageStore(WorkbenchRepository(repo.root), workspace_ids=[task['workspace_id']])
    bridge = Bridge()
    assert reopened.get(task['id'], intent['id'], actor='alice')['status'] == 'unknown'
    assert reopened.attempt(task['id'], intent['id'], actor='alice', bridge=bridge)['status'] == 'unknown'
    assert bridge.commands == []


def test_result_transaction_failure_does_not_erase_committed_uncertainty(tmp_path, monkeypatch):
    repo, task, session, store = setup(tmp_path)
    intent = prepare(store, task, session)
    original = repo._event
    def event(conn, task_id, kind, object_id, content):
        if kind == 'agent.message.observed':
            raise RuntimeError('local transaction failure')
        return original(conn, task_id, kind, object_id, content)
    monkeypatch.setattr(repo, '_event', event)
    bridge = Bridge()
    with pytest.raises(RuntimeError):
        store.attempt(task['id'], intent['id'], actor='alice', bridge=bridge)
    assert store.get(task['id'], intent['id'], actor='alice')['status'] == 'unknown'
    assert store.get(task['id'], intent['id'], actor='alice')['envelope'] is None
    store.attempt(task['id'], intent['id'], actor='alice', bridge=bridge)
    assert len(bridge.commands) == 1
