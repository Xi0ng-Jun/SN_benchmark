"""J02 shared seams preserve v1 Job facts and use the caller's transaction."""
import json
import sqlite3
import sys
from types import ModuleType
from unittest.mock import patch

import pytest

from rag_eval.workbench.evaluation_jobs import EvaluationJobStore, JobRejected
from rag_eval.workbench.repository import WorkbenchRepository
from test_workbench_evaluation_jobs import environment


def test_schema4_adds_nullable_probe_without_rewriting_version3_facts(environment):
    repo, _, task, _, _, _, command = environment
    store = EvaluationJobStore(repo)
    job = store.prepare(**command)
    with repo.connect(write=True) as conn:
        columns = [row['name'] for row in conn.execute('PRAGMA table_info(jobs)')]
        if 'probe' in columns:
            conn.execute('ALTER TABLE jobs DROP COLUMN probe')
        conn.execute('PRAGMA user_version=3')
        before = {table: [tuple(row) for row in conn.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                  for table in ('jobs', 'history', 'requests', 'tasks', 'workspaces')}
    reopened = WorkbenchRepository(repo.root)
    with reopened.connect() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 4
        assert [row['name'] for row in conn.execute('PRAGMA table_info(jobs)')] == [
            'id', 'task_id', 'workspace_id', 'record', 'probe']
        assert conn.execute('SELECT probe FROM jobs').fetchone()[0] is None
        assert [tuple(row) for row in conn.execute('SELECT id,task_id,workspace_id,record FROM jobs')] == before['jobs']
        for table in ('history', 'requests', 'tasks', 'workspaces'):
            assert [tuple(row) for row in conn.execute('SELECT * FROM ' + table + ' ORDER BY rowid')] == before[table]
    assert EvaluationJobStore(reopened).get(task['id'], job['job_id']) == job
    assert EvaluationJobStore(reopened).prepare(**command) == job


def test_current_job_seam_uses_uncommitted_row_and_no_nested_connection(environment):
    repo, _, task, _, _, _, command = environment
    store = EvaluationJobStore(repo)
    job = store.prepare(**command)
    cancelled = dict(job, state='cancelled', state_version=2,
                     cancelled_by='operator', cancelled_at='2026-10-12T00:00:00+00:00')
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE jobs SET record=? WHERE id=?', (json.dumps(cancelled), job['job_id']))
        with patch.object(repo, 'connect', side_effect=AssertionError('nested connection')):
            assert store.get_in_transaction(conn, task['id'], job['job_id']) == cancelled
        conn.rollback()
    assert store.get(task['id'], job['job_id']) == job


def test_current_job_seam_rejects_wrong_task_and_corrupt_record(environment):
    repo, workspace, task, _, _, _, command = environment
    store = EvaluationJobStore(repo)
    job = store.prepare(**command)
    other = repo.create_task(workspace['id'], 'Other task', '', request_key='other-task')
    with repo.connect(write=True) as conn:
        with pytest.raises(JobRejected):
            store.get_in_transaction(conn, other['id'], job['job_id'])
        conn.execute('UPDATE jobs SET record=? WHERE id=?', ('{}', job['job_id']))
        with pytest.raises(JobRejected):
            store.get_in_transaction(conn, task['id'], job['job_id'])
        conn.rollback()


@pytest.mark.parametrize('error', [ValueError('internal read bug'), FileNotFoundError('internal dependency bug')])
def test_current_job_seam_keeps_program_error_identity(environment, error):
    repo, _, task, _, _, _, command = environment
    store = EvaluationJobStore(repo)
    job = store.prepare(**command)
    with repo.connect() as conn, patch.object(repo, '_require', side_effect=error):
        with pytest.raises(type(error)) as caught:
            store.get_in_transaction(conn, task['id'], job['job_id'])
        assert caught.value is error


def install_cancel_hook(monkeypatch, callback):
    module = ModuleType('rag_eval.workbench.job_lifecycle_probe')
    module.request_probe_cancel_in_transaction = callback
    monkeypatch.setitem(sys.modules, module.__name__, module)


def test_job_cancel_calls_probe_hook_once_in_same_transaction(environment, monkeypatch):
    repo, _, task, _, _, _, command = environment
    store = EvaluationJobStore(repo)
    job = store.prepare(**command)
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE jobs SET probe=? WHERE id=?', ('{}', job['job_id']))
    calls = []
    def callback(repository, conn, task_id, job_id):
        assert repository is repo
        assert store.get_in_transaction(conn, task_id, job_id)['state'] == 'cancelled'
        calls.append(job_id)
        conn.execute('UPDATE jobs SET probe=? WHERE id=?', ('{"cancel_requested":true}', job_id))
    install_cancel_hook(monkeypatch, callback)
    cancel = dict(task_id=task['id'], job_id=job['job_id'], expected_state_version=1,
                  actor='operator', request_key='cancel')
    cancelled = store.cancel(**cancel)
    assert calls == [job['job_id']]
    assert store.cancel(**cancel) == cancelled
    assert store.cancel(**dict(cancel, expected_state_version=2, request_key='ack')) == cancelled
    assert calls == [job['job_id']]
    with repo.connect() as conn:
        assert json.loads(conn.execute('SELECT probe FROM jobs').fetchone()[0]) == {'cancel_requested': True}
    assert store.prepare(**command) == job


def test_job_without_probe_does_not_import_or_call_lifecycle(environment, monkeypatch):
    repo, _, task, _, _, _, command = environment
    store = EvaluationJobStore(repo)
    job = store.prepare(**command)
    def forbidden(*args):
        raise AssertionError('No probe capability was requested')
    install_cancel_hook(monkeypatch, forbidden)
    assert store.cancel(task['id'], job['job_id'], 1, actor='operator', request_key='cancel')['state'] == 'cancelled'


@pytest.mark.parametrize('error', [ValueError('internal probe bug'), FileNotFoundError('internal probe dependency'),
                                 sqlite3.OperationalError('internal probe write failure')])
def test_probe_hook_failure_rolls_back_entire_job_cancel(environment, monkeypatch, error):
    repo, _, task, _, _, _, command = environment
    store = EvaluationJobStore(repo)
    job = store.prepare(**command)
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE jobs SET probe=? WHERE id=?', ('{}', job['job_id']))
    def rows():
        with repo.connect() as conn:
            return {table: [tuple(row) for row in conn.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                    for table in ('jobs', 'history', 'requests')}
    before = rows()
    def fail(repository, conn, task_id, job_id):
        conn.execute('UPDATE jobs SET probe=? WHERE id=?', ('{"cancel_requested":true}', job_id))
        repository._event(conn, task_id, 'probe.cancel_requested', job_id, {'synthetic': True})
        raise error
    install_cancel_hook(monkeypatch, fail)
    with pytest.raises(type(error)) as caught:
        store.cancel(task['id'], job['job_id'], 1, actor='operator', request_key='cancel')
    assert caught.value is error
    assert rows() == before


def http_probe(tmp_path, mode):
    from test_workbench_jobs_api_cli import fixture, HEADERS
    from rag_eval.workbench.job_lifecycle_probe import JobLifecycleProbeStore
    from rag_eval.workbench.probe_subprocess import ProbeSubprocess
    client, config, task, plan, payload = fixture(tmp_path)
    path = f"/api/tasks/{task['id']}/jobs"
    original = client.post(path, json=payload, headers=HEADERS)
    assert original.status_code == 200
    job = original.json()['record']
    repo = WorkbenchRepository(tmp_path / 'state')
    adapter = ProbeSubprocess()
    probes = JobLifecycleProbeStore(repo, adapter=adapter)
    claim = probes.claim(task['id'], job['job_id'], 1, actor='alice', request_key='probe', mode=mode)
    return client, config, task, plan, payload, path, original.json(), repo, adapter, probes, claim


def test_actual_http_cancel_persists_probe_intent_without_starting_helper(tmp_path):
    from test_workbench_jobs_api_cli import HEADERS
    client, _, task, plan, payload, path, original, repo, adapter, probes, claim = http_probe(tmp_path, 'wait_cancel')
    job_id = original['record']['job_id']
    cancelled = client.post(path + '/' + job_id + '/cancel', headers=HEADERS,
                            json={'expected_state_version': 1, 'request_key': 'cancel'})
    assert cancelled.status_code == 200 and cancelled.json()['record']['state'] == 'cancelled'
    current = probes.get(task['id'], job_id)
    assert current['cancel_requested'] is True and current['phase'] == 'claimed'
    stopped = probes.start_claimed(task['id'], job_id, adapter=adapter)
    assert stopped['phase'] == 'not_started' and stopped['start_consumed'] is False
    assert not list(adapter.directory.glob('*/started.json'))
    assert client.post(path, json=payload, headers=HEADERS).json() == original
    assert client.get(path + '/receipt', params={'operation': 'prepare', 'request_key': payload['request_key']},
                      headers=HEADERS).json() == original
    assert client.get(f"/api/tasks/{task['id']}/plans/{plan['plan_id']}",
                      params={'revision': plan['revision']}, headers=HEADERS).json() == plan


def test_actual_os_exit_and_late_http_cancel_preserve_v1_facts(tmp_path):
    from fastapi.testclient import TestClient
    from rag_eval.workbench.api import create_app
    from rag_eval.workbench.job_lifecycle_probe import JobLifecycleProbeStore
    from rag_eval.workbench.probe_subprocess import ProbeSubprocess
    from test_workbench_jobs_api_cli import HEADERS
    client, config, task, _, payload, path, original, repo, adapter, probes, claim = http_probe(tmp_path, 'normal')
    job_id = original['record']['job_id']
    started = probes.start_claimed(task['id'], job_id, adapter=adapter)
    observed = adapter.observe(claim['probe_id'], started['process_identity'], wait=True)
    assert observed['returncode'] == 0
    exited = probes.reconcile(task['id'], job_id, adapter=adapter)
    assert exited['phase'] == 'exited' and exited['exit_observation']['returncode'] == 0
    assert client.get(path + '/' + job_id, headers=HEADERS).json() == original['record']
    assert original['record']['executable'] is False and original['record']['state'] == 'not_dispatched'
    response = client.post(path + '/' + job_id + '/cancel', headers=HEADERS,
                           json={'expected_state_version': 1, 'request_key': 'late-cancel'})
    assert response.status_code == 200
    final = probes.reconcile(task['id'], job_id, adapter=adapter)
    assert final['phase'] == 'exited' and final['exit_observation'] == exited['exit_observation']
    assert final['cancel_requested'] is True
    reopened = JobLifecycleProbeStore(WorkbenchRepository(repo.root), adapter=ProbeSubprocess())
    assert reopened.reconcile(task['id'], job_id, adapter=reopened.adapter) == final
    with TestClient(create_app(repo.root, config, actor='alice', access_token='test-access')) as wire:
        assert wire.get(path + '/' + job_id, headers=HEADERS).json() == response.json()['record']
        assert wire.get(path + '/receipt', params={'operation': 'prepare', 'request_key': payload['request_key']},
                        headers=HEADERS).json() == original


@pytest.mark.parametrize('error', [ValueError('/private/probe-event'), FileNotFoundError('/private/probe-event'),
                                 sqlite3.OperationalError('/private/probe-event')])
def test_actual_probe_cancel_http_program_error_rolls_back_and_has_safe_wire(tmp_path, monkeypatch, error):
    from fastapi.testclient import TestClient
    from rag_eval.workbench.api import create_app
    from test_workbench_jobs_api_cli import HEADERS
    client, config, task, _, _, path, original, repo, _, probes, claim = http_probe(tmp_path, 'normal')
    job_id = original['record']['job_id']
    def rows():
        with repo.connect() as conn:
            return {table: [tuple(row) for row in conn.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                    for table in ('jobs', 'history', 'requests')}
    before = rows()
    real_event = WorkbenchRepository._event
    def fail(conn, task_id, kind, identity, payload):
        real_event(conn, task_id, kind, identity, payload)
        if kind == 'probe.cancel_requested':
            raise error
    monkeypatch.setattr(WorkbenchRepository, '_event', staticmethod(fail))
    cancel_path = path + '/' + job_id + '/cancel'
    command = {'expected_state_version': 1, 'request_key': 'atomic-cancel'}
    with pytest.raises(type(error)) as caught:
        client.post(cancel_path, json=command, headers=HEADERS)
    assert caught.value is error and rows() == before
    with TestClient(create_app(repo.root, config, actor='alice', access_token='test-access'),
                    raise_server_exceptions=False) as wire:
        response = wire.post(cancel_path, json=command, headers=HEADERS)
        assert response.status_code == 500
        assert '/private/' not in response.text and 'REJECTED' not in response.text
    assert rows() == before and probes.get(task['id'], job_id) == claim
