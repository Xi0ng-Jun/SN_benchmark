"""Synthetic state and fixed helpers only; OS facts are distinct from intent."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import copy
import sqlite3
import threading
import multiprocessing
from concurrent.futures import ThreadPoolExecutor

import pytest

from rag_eval.workbench.evaluation_jobs import EvaluationJobStore
from rag_eval.workbench.repository import WorkbenchRepository
from rag_eval.workbench.repository import Conflict
from test_workbench_evaluation_plans import setup


def environment(tmp_path):
    repo, _, task, plans, _, save = setup(tmp_path)
    plan = plans.save(**save)
    job = EvaluationJobStore(repo).prepare(task['id'], plan['plan_id'], plan['revision'],
        plan['content_hash'], actor='alice', request_key='prepare')
    return repo, task['id'], job


_LOST_STATUS_OWNER = r'''
import json,os,signal,sys
from pathlib import Path
from rag_eval.workbench.repository import WorkbenchRepository,Conflict
from rag_eval.workbench.evaluation_jobs import EvaluationJobStore
from rag_eval.workbench.job_lifecycle_probe import JobLifecycleProbeStore
from rag_eval.workbench.probe_subprocess import ProbeSubprocess
state,task,job,kind,evidence=sys.argv[1:]
if kind=='ignored': signal.signal(signal.SIGCHLD,signal.SIG_IGN)
repo=WorkbenchRepository(state);adapter=ProbeSubprocess();store=JobLifecycleProbeStore(repo,adapter=adapter)
with repo.connect() as conn:
    before=[list(r) for r in conn.execute('SELECT scope,request_key,receipt FROM requests')]
command=dict(task_id=task,job_id=job,expected_job_version=1,actor='alice',request_key='claim',mode='nonzero')
claimed=store.claim(**command);running=store.start_claimed(task,job,adapter=adapter)
raw=None
if kind=='external_wait':
    pid,status=os.waitpid(running['process_identity']['pid'],0)
    raw=os.waitstatus_to_exitcode(status)
observed=adapter.observe(claimed['probe_id'],running['process_identity'],wait=True)
unknown=store.reconcile(task,job,adapter=adapter)
def forbidden(*args,**kwargs): raise AssertionError('lost status must not signal a PID')
os.kill=forbidden
cancelled=store.request_cancel(task,job,unknown['version'],actor='alice',request_key='cancel')
final=store.reconcile(task,job,adapter=adapter)
assert store.claim(**command)==claimed
assert store.start_claimed(task,job,adapter=adapter)==final
try: store.claim(**dict(command,request_key='new'))
except Conflict: pass
else: raise AssertionError('lost status authorized a second probe')
reopened_adapter=ProbeSubprocess();reopened=JobLifecycleProbeStore(WorkbenchRepository(state),adapter=reopened_adapter)
after_reopen=reopened.reconcile(task,job,adapter=reopened_adapter)
with repo.connect() as conn:
    after=[list(r) for r in conn.execute("SELECT scope,request_key,receipt FROM requests WHERE scope NOT LIKE 'probe.%'")]
Path(evidence).write_text(json.dumps(dict(raw=raw,observed=observed,unknown=unknown,cancelled=cancelled,
    final=final,after_reopen=after_reopen,before=before,after=after,
    starts=len(list(adapter.directory.glob('*/started.json'))),
    reopened_starts=len(list(reopened_adapter.directory.glob('*/started.json'))),
    job=EvaluationJobStore(repo).get(task,job))))
'''


@pytest.mark.parametrize('kind', ['ignored', 'external_wait'])
def test_lost_os_status_stays_unknown_without_replay_start_or_cancel_signal(tmp_path, kind):
    repo, task, job = environment(tmp_path)
    evidence = tmp_path / 'lost-status.json'
    owner = subprocess.Popen([sys.executable, '-B', '-c', _LOST_STATUS_OWNER,
        str(repo.root), task, job['job_id'], kind, str(evidence)],
        env={'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
             'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')})
    assert owner.wait(timeout=15) == 0
    facts = json.loads(evidence.read_text())
    if kind == 'external_wait':
        assert facts['raw'] == 7
    assert facts['observed'] is None
    assert facts['unknown']['phase'] == 'unknown'
    assert facts['unknown']['exit_observation'] is None
    assert facts['final'] == facts['cancelled']
    assert facts['final']['phase'] == 'unknown' and facts['final']['cancel_requested'] is True
    assert facts['final']['exit_observation'] is None
    assert facts['after_reopen'] == facts['final']
    assert facts['starts'] == 1 and facts['reopened_starts'] == 0
    assert facts['before'] == facts['after']
    assert facts['job'] == job


@pytest.fixture
def probe_env(tmp_path):
    from rag_eval.workbench.job_lifecycle_probe import JobLifecycleProbeStore
    from rag_eval.workbench.probe_subprocess import ProbeSubprocess
    repo, task, job = environment(tmp_path)
    adapter = ProbeSubprocess()
    return repo, task, job, adapter, JobLifecycleProbeStore(repo, adapter=adapter)


def claim(probe_env, mode='normal', **changes):
    repo, task, job, adapter, store = probe_env
    return store.claim(**dict(dict(task_id=task, job_id=job['job_id'], expected_job_version=1,
                                   actor='alice', request_key='claim', mode=mode), **changes))


def rows(repo):
    with repo.connect() as conn:
        return {table: [tuple(row) for row in conn.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                for table in ('jobs', 'history', 'requests')}


def finish(store, task, job, adapter):
    deadline = time.monotonic() + 5
    while True:
        record = store.reconcile(task, job['job_id'], adapter=adapter)
        if record['phase'] == 'exited':
            return record
        assert time.monotonic() < deadline
        time.sleep(.01)


def wait_file(path, process, timeout=10):
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert process.poll() is None, 'temporary owner exited before barrier'
        assert time.monotonic() < deadline, 'temporary owner never reached barrier'
        time.sleep(0.01)
    return json.loads(path.read_text())


def test_started_before_persistence_owner_crash_never_redispatches(tmp_path):
    repo, task_id, job = environment(tmp_path)
    # Owner dies at the production audit-write boundary after adapter handshake,
    # before started persistence commits. No test-only branch in production.
    script = r'''
import json,sys,time
from pathlib import Path
from rag_eval.workbench.repository import WorkbenchRepository
from rag_eval.workbench.job_lifecycle_probe import JobLifecycleProbeStore
from rag_eval.workbench.probe_subprocess import ProbeSubprocess
state, task, job, barrier = sys.argv[1:]
repo=WorkbenchRepository(state)
adapter=ProbeSubprocess()
store=JobLifecycleProbeStore(repo, adapter=adapter)
store.claim(task,job,1,actor='alice',request_key='claim',mode='ignore_cancel')
original=repo._event
def event(conn, task_id, kind, object_id, content):
    if kind == 'probe.started':
        Path(barrier).write_text(json.dumps({'probe':content,'directory':str(adapter.directory)}))
        while True: time.sleep(.05)
    return original(conn,task_id,kind,object_id,content)
repo._event=event
store.start_claimed(task,job,adapter=adapter)
'''
    barrier = tmp_path / 'owner-barrier.json'
    owner = subprocess.Popen([sys.executable, '-B', '-c', script, str(repo.root), task_id,
                              job['job_id'], str(barrier)], env={
                                  'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
                                  'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')})
    try:
        observed = wait_file(barrier, owner)
        owner.kill()  # Stable Popen handle for our temporary test owner only.
        owner.wait(timeout=10)
        from rag_eval.workbench.job_lifecycle_probe import JobLifecycleProbeStore
        from rag_eval.workbench.probe_subprocess import ProbeSubprocess
        reopened = WorkbenchRepository(repo.root)
        new_adapter = ProbeSubprocess()
        store = JobLifecycleProbeStore(reopened, adapter=new_adapter)
        current = store.reconcile(task_id, job['job_id'], adapter=new_adapter)
        assert current['phase'] == 'unknown' and current['start_consumed'] is True
        assert current['process_identity'] is None and current['exit_observation'] is None
        receipt = store.claim(task_id, job['job_id'], 1, actor='alice', request_key='claim', mode='ignore_cancel')
        assert receipt['phase'] == 'claimed' and receipt['start_consumed'] is False
        assert store.start_claimed(task_id, job['job_id'], adapter=new_adapter) == current
        directory = Path(observed['directory'])
        assert len(list(directory.glob('*/started.json'))) == 1
        assert list(new_adapter.directory.iterdir()) == []
        assert EvaluationJobStore(reopened).get(task_id, job['job_id']) == job
        assert EvaluationJobStore(reopened).receipt(task_id, actor='alice', request_key='prepare') == job
        # TTL/marker disappearance still supplies no new owner's OS exit fact.
        time.sleep(3.2)
        assert store.reconcile(task_id, job['job_id'], adapter=new_adapter) == current
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.wait(timeout=10)


def test_claim_cas_receipt_and_distinct_keys_never_create_second_probe(probe_env):
    repo, task, job, adapter, store = probe_env
    first = claim(probe_env)
    before = rows(repo)
    assert claim(probe_env) == first
    for change in (dict(mode='nonzero'), dict(expected_job_version=False)):
        with pytest.raises(Conflict):
            claim(probe_env, **change)
    for change in (dict(request_key='new'), dict(actor='bob')):
        with pytest.raises(Conflict):
            claim(probe_env, **change)
    assert rows(repo) == before
    assert list(adapter.directory.iterdir()) == []
    assert EvaluationJobStore(repo).get(task, job['job_id']) == job


@pytest.mark.parametrize('same_key', [True, False])
def test_actual_concurrent_claims_are_single_winner(probe_env, same_key):
    repo, task, job, adapter, store = probe_env
    barrier = threading.Barrier(2)
    def attempt(index):
        barrier.wait(timeout=5)
        try:
            return claim(probe_env, request_key='same' if same_key else str(index))
        except Conflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(r is not None for r in results) == (2 if same_key else 1)
    values = [r for r in results if r is not None]
    assert all(r == values[0] for r in values)
    assert len([h for h in repo.task(task)['history'] if h['kind'] == 'probe.claimed']) == 1


@pytest.mark.parametrize('job_cancel', [True, False])
def test_persistent_cancel_before_authorization_prevents_start(probe_env, job_cancel):
    repo, task, job, adapter, store = probe_env
    record = claim(probe_env)
    if job_cancel:
        EvaluationJobStore(repo).cancel(task, job['job_id'], 1, actor='bob', request_key='cancel')
    else:
        record = store.request_cancel(task, job['job_id'], record['version'], actor='bob', request_key='cancel')
        assert record['cancel_requested'] is True and record['phase'] == 'claimed'
    stopped = store.start_claimed(task, job['job_id'], adapter=adapter)
    assert stopped['phase'] == 'not_started' and stopped['start_consumed'] is False
    assert list(adapter.directory.iterdir()) == []
    before = rows(repo)
    assert store.reconcile(task, job['job_id'], adapter=adapter) == stopped
    assert rows(repo) == before


def test_job_cancel_before_claim_rejects_and_preserves_v1_receipt(probe_env):
    repo, task, job, adapter, store = probe_env
    EvaluationJobStore(repo).cancel(task, job['job_id'], 1, actor='bob', request_key='cancel')
    before = rows(repo)
    with pytest.raises(Conflict):
        claim(probe_env)
    assert rows(repo) == before
    assert EvaluationJobStore(repo).receipt(task, actor='alice', request_key='prepare') == job


@pytest.mark.parametrize('mode,code', [('normal', 0), ('nonzero', 7)])
def test_os_exit_is_substitute_only_and_survives_owner_reopen(probe_env, mode, code):
    repo, task, job, adapter, store = probe_env
    original = claim(probe_env, mode)
    running = store.start_claimed(task, job['job_id'], adapter=adapter)
    assert running['phase'] == 'running' and running['start_consumed'] is True
    exited = finish(store, task, job, adapter)
    assert exited['exit_observation']['returncode'] == code
    assert EvaluationJobStore(repo).get(task, job['job_id']) == job
    assert claim(probe_env, mode) == original
    from rag_eval.workbench.job_lifecycle_probe import JobLifecycleProbeStore
    from rag_eval.workbench.probe_subprocess import ProbeSubprocess
    new_adapter = ProbeSubprocess()
    reopened = JobLifecycleProbeStore(WorkbenchRepository(repo.root), adapter=new_adapter)
    before = rows(repo)
    assert reopened.reconcile(task, job['job_id'], adapter=new_adapter) == exited
    assert reopened.start_claimed(task, job['job_id'], adapter=new_adapter) == exited
    assert rows(repo) == before
    assert list(new_adapter.directory.iterdir()) == []


def test_cancel_receipt_cas_does_not_claim_helper_is_stopped(probe_env):
    repo, task, job, adapter, store = probe_env
    original = claim(probe_env, 'ignore_cancel')
    running = store.start_claimed(task, job['job_id'], adapter=adapter)
    cancelled = store.request_cancel(task, job['job_id'], running['version'], actor='bob', request_key='stop')
    assert cancelled['cancel_requested'] is True and cancelled['phase'] == 'running'
    before = rows(repo)
    assert store.request_cancel(task, job['job_id'], running['version'], actor='bob', request_key='stop') == cancelled
    with pytest.raises(Conflict):
        store.request_cancel(task, job['job_id'], running['version'], actor='bob', request_key='new')
    assert store.request_cancel(task, job['job_id'], cancelled['version'], actor='carol', request_key='ack') == cancelled
    assert rows(repo)['history'] == before['history']
    # SIGTERM was deliberately ignored by the actual helper.
    still_running = store.reconcile(task, job['job_id'], adapter=adapter)
    assert still_running['phase'] == 'running' and still_running['exit_observation'] is None
    exited = finish(store, task, job, adapter)
    assert exited['exit_observation']['returncode'] == 0 and exited['cancel_requested'] is True
    assert store.request_cancel(task, job['job_id'], running['version'], actor='bob', request_key='stop') == cancelled
    assert store.get(task, job['job_id']) == exited
    assert claim(probe_env, 'ignore_cancel') == original


def test_actual_wait_cancel_exit_and_cancel_after_exit_preserves_os_fact(probe_env):
    repo, task, job, adapter, store = probe_env
    claim(probe_env, 'wait_cancel')
    running = store.start_claimed(task, job['job_id'], adapter=adapter)
    store.request_cancel(task, job['job_id'], running['version'], actor='bob', request_key='stop')
    exited = finish(store, task, job, adapter)
    assert exited['exit_observation']['returncode'] < 0
    late = store.request_cancel(task, job['job_id'], exited['version'], actor='carol', request_key='late')
    assert late == exited


def test_pure_cancel_hook_uses_same_connection_and_rolls_back_with_job(probe_env, monkeypatch):
    repo, task, job, adapter, store = probe_env
    claimed = claim(probe_env)
    from rag_eval.workbench.job_lifecycle_probe import request_probe_cancel_in_transaction
    original_connect = repo.connect
    def forbidden(*args, **kwargs):
        raise AssertionError('pure hook must use caller connection')
    with original_connect(write=True) as conn:
        monkeypatch.setattr(repo, 'connect', forbidden)
        result = request_probe_cancel_in_transaction(repo, conn, task, job['job_id'])
        assert result['cancel_requested'] is True
        conn.rollback()
    monkeypatch.setattr(repo, 'connect', original_connect)
    assert store.get(task, job['job_id']) == claimed


def test_late_stale_observation_cannot_overwrite_newer_unknown_or_terminal(probe_env):
    repo, task, job, adapter, store = probe_env
    claim(probe_env)
    running = store.start_claimed(task, job['job_id'], adapter=adapter)
    cancelled = store.request_cancel(task, job['job_id'], running['version'], actor='bob', request_key='stop')
    newer = store._publish(task, job['job_id'], cancelled, phase='unknown', reason='handle_lost')
    assert store._publish(task, job['job_id'], running, phase='running', reason=None) == newer
    exited = finish(store, task, job, adapter)
    before = rows(repo)
    assert store._publish(task, job['job_id'], running, phase='running', reason=None) == exited
    assert rows(repo) == before


@pytest.mark.parametrize('change', [dict(expected_job_version=True), dict(expected_job_version=1.0),
    dict(expected_job_version=0), dict(mode='shell'), dict(mode=[]), dict(actor=False), dict(request_key=' '),
    dict(task_id='unknown'), dict(job_id='unknown')])
def test_invalid_claim_types_and_identities_are_typed_with_zero_writes(probe_env, change):
    repo, _, _, _, _ = probe_env
    from rag_eval.workbench.evaluation_jobs import JobRejected
    before = rows(repo)
    with pytest.raises(JobRejected):
        claim(probe_env, **change)
    assert rows(repo) == before


@pytest.mark.parametrize('operation', ['claim', 'cancel'])
@pytest.mark.parametrize('seam', ['event', 'receipt', 'commit', 'sql'])
@pytest.mark.parametrize('error_type', [ValueError, FileNotFoundError, sqlite3.OperationalError])
def test_internal_faults_remain_original_objects_and_roll_back(probe_env, monkeypatch, operation, seam, error_type):
    repo, task, job, adapter, store = probe_env
    record = claim(probe_env) if operation == 'cancel' else None
    before = rows(repo)
    error = error_type('synthetic internal ' + seam)
    def fail(*args, **kwargs):
        raise error
    if seam in ('event', 'receipt'):
        monkeypatch.setattr(repo, '_' + seam, fail)
    else:
        original = sqlite3.connect
        class FailingConnection(sqlite3.Connection):
            def execute(self, query, *args, **kwargs):
                if seam == 'sql' and query.startswith('UPDATE jobs SET probe'):
                    raise error
                return super().execute(query, *args, **kwargs)
            def commit(self):
                if seam == 'commit' and self.in_transaction:
                    raise error
                return super().commit()
        monkeypatch.setattr(sqlite3, 'connect', lambda *args, **kwargs: original(*args, **dict(kwargs, factory=FailingConnection)))
    with pytest.raises(error_type) as caught:
        if operation == 'claim':
            claim(probe_env)
        else:
            store.request_cancel(task, job['job_id'], record['version'], actor='bob', request_key='cancel')
    assert caught.value is error
    assert rows(repo) == before


@pytest.mark.parametrize('kind,helper_count,consumed', [
    ('probe.start_consumed', 0, False), ('probe.exited', 1, True)])
def test_other_real_owner_crash_windows_have_bounded_unknown_recovery(tmp_path, kind, helper_count, consumed):
    repo, task, job = environment(tmp_path)
    script = r'''
import json,sys,time
from pathlib import Path
from rag_eval.workbench.repository import WorkbenchRepository
from rag_eval.workbench.job_lifecycle_probe import JobLifecycleProbeStore
from rag_eval.workbench.probe_subprocess import ProbeSubprocess
state,task,job,barrier,kind=sys.argv[1:]
repo=WorkbenchRepository(state); adapter=ProbeSubprocess()
store=JobLifecycleProbeStore(repo,adapter=adapter)
original=repo._event
def event(conn, task_id, event_kind, object_id, content):
    if event_kind==kind:
        Path(barrier).write_text(json.dumps({'probe':content,'directory':str(adapter.directory)}))
        while True: time.sleep(.05)
    return original(conn,task_id,event_kind,object_id,content)
repo._event=event
store.claim(task,job,1,actor='alice',request_key='claim',mode='normal')
store.start_claimed(task,job,adapter=adapter)
while True:
    store.reconcile(task,job,adapter=adapter)
    time.sleep(.01)
'''
    barrier = tmp_path / 'barrier.json'
    owner = subprocess.Popen([sys.executable, '-B', '-c', script, str(repo.root), task,
        job['job_id'], str(barrier), kind], env={'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')})
    try:
        observed = wait_file(barrier, owner)
        owner.kill(); owner.wait(timeout=5)
        from rag_eval.workbench.job_lifecycle_probe import JobLifecycleProbeStore
        from rag_eval.workbench.probe_subprocess import ProbeSubprocess
        adapter = ProbeSubprocess()
        store = JobLifecycleProbeStore(WorkbenchRepository(repo.root), adapter=adapter)
        recovered = store.reconcile(task, job['job_id'], adapter=adapter)
        assert recovered['phase'] == 'unknown' and recovered['exit_observation'] is None
        assert recovered['start_consumed'] is consumed
        assert store.claim(task, job['job_id'], 1, actor='alice', request_key='claim', mode='normal')['phase'] == 'claimed'
        before = rows(repo)
        assert store.start_claimed(task, job['job_id'], adapter=adapter) == recovered
        assert store.reconcile(task, job['job_id'], adapter=adapter) == recovered
        assert rows(repo) == before
        assert len(list(Path(observed['directory']).glob('*/started.json'))) == helper_count
        assert list(adapter.directory.iterdir()) == []
        assert EvaluationJobStore(repo).receipt(task, actor='alice', request_key='prepare') == job
        if kind == 'probe.exited':
            assert observed['probe']['exit_observation']['returncode'] == 0
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.wait(timeout=5)


def test_cancel_winning_after_consumption_is_seen_by_the_starter_without_write_lock(probe_env, monkeypatch):
    repo, task, job, adapter, store = probe_env
    claim(probe_env, 'wait_cancel')
    original = adapter.start
    def cancel_then_start(*args):
        # This transaction would deadlock if start still held the DB write lock.
        EvaluationJobStore(repo).cancel(task, job['job_id'], 1, actor='bob', request_key='cancel')
        return original(*args)
    monkeypatch.setattr(adapter, 'start', cancel_then_start)
    result = store.start_claimed(task, job['job_id'], adapter=adapter)
    assert result['cancel_requested'] is True
    exited = finish(store, task, job, adapter)
    assert exited['exit_observation']['returncode'] < 0
    assert len(list(adapter.directory.glob('*/started.json'))) == 1
    assert EvaluationJobStore(repo).get(task, job['job_id'])['state'] == 'cancelled'


def test_creation_rejected_vs_launch_then_program_failure_do_not_retry(probe_env, monkeypatch):
    repo, task, job, adapter, store = probe_env
    claim(probe_env)
    from rag_eval.workbench.probe_subprocess import ProbeStartFailed
    original = subprocess.Popen
    def no_child(*args, **kwargs):
        raise FileNotFoundError('synthetic Popen rejection')
    monkeypatch.setattr(subprocess, 'Popen', no_child)
    result = store.start_claimed(task, job['job_id'], adapter=adapter)
    assert result['phase'] == 'start_failed' and result['process_identity'] is None
    monkeypatch.setattr(subprocess, 'Popen', original)
    before = rows(repo)
    assert store.start_claimed(task, job['job_id'], adapter=adapter) == result
    assert rows(repo) == before


@pytest.mark.parametrize('error_type', [ValueError, FileNotFoundError])
def test_internal_launch_exception_after_real_start_stays_unknown(probe_env, monkeypatch, error_type):
    repo, task, job, adapter, store = probe_env
    claim(probe_env)
    original = adapter.start
    error = error_type('synthetic exception after actual Popen handshake')
    def start_then_fail(*args):
        original(*args)
        raise error
    monkeypatch.setattr(adapter, 'start', start_then_fail)
    with pytest.raises(error_type) as caught:
        store.start_claimed(task, job['job_id'], adapter=adapter)
    assert caught.value is error
    assert store.get(task, job['job_id'])['phase'] == 'unknown'
    assert store.get(task, job['job_id'])['start_consumed'] is True
    before = rows(repo)
    store.start_claimed(task, job['job_id'], adapter=adapter)
    assert rows(repo) == before and len(list(adapter.directory.glob('*/started.json'))) == 1
    assert finish(store, task, job, adapter)['exit_observation']['returncode'] == 0


def test_handshake_uncertainty_never_grants_second_start(probe_env, monkeypatch):
    repo, task, job, adapter, store = probe_env
    claim(probe_env)
    from rag_eval.workbench import probe_subprocess
    original = probe_subprocess.select.select
    monkeypatch.setattr(probe_subprocess.select, 'select', lambda *args: ([], [], []))
    uncertain = store.start_claimed(task, job['job_id'], adapter=adapter)
    assert uncertain['phase'] == 'unknown' and uncertain['start_consumed'] is True
    monkeypatch.setattr(probe_subprocess.select, 'select', original)
    assert store.start_claimed(task, job['job_id'], adapter=adapter) == uncertain
    assert finish(store, task, job, adapter)['exit_observation']['returncode'] == 0


@pytest.mark.parametrize('change', [dict(format='future'), dict(helper_hash='0' * 64), dict(version=True),
    dict(job_version=True), dict(mode=[]), dict(phase='success'), dict(start_consumed=1),
    dict(cancel_requested=0), dict(extra='forged'), dict(owner_id=False), dict(exit_observation={}),
    dict(task_id='other'), dict(job_id='other'), dict(process_identity=dict(pid=1))])
def test_invalid_probe_schema_is_typed_before_any_process_action(probe_env, monkeypatch, change):
    repo, task, job, adapter, store = probe_env
    record = claim(probe_env)
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE jobs SET probe=?', (json.dumps(dict(record, **change)),))
    def forbidden(*args, **kwargs):
        raise AssertionError('bad schema cannot authorize process action')
    monkeypatch.setattr(adapter, 'start', forbidden)
    monkeypatch.setattr(adapter, 'observe', forbidden)
    before = rows(repo)
    from rag_eval.workbench.job_lifecycle_probe import ProbeRejected
    for action in (lambda: store.get(task, job['job_id']),
                   lambda: store.start_claimed(task, job['job_id'], adapter=adapter),
                   lambda: store.reconcile(task, job['job_id'], adapter=adapter)):
        with pytest.raises(ProbeRejected):
            action()
    assert rows(repo) == before


def test_returned_observations_are_detached_and_v1_bytes_unchanged(probe_env):
    repo, task, job, adapter, store = probe_env
    with repo.connect() as conn:
        v1 = conn.execute('SELECT record FROM jobs').fetchone()[0]
        receipts = [tuple(r) for r in conn.execute('SELECT * FROM requests')]
        history = [tuple(r) for r in conn.execute('SELECT * FROM history')]
    first = claim(probe_env)
    saved = copy.deepcopy(first)
    first['phase'] = 'success'
    store.get(task, job['job_id'])['version'] = 99
    assert store.get(task, job['job_id']) == saved
    store.start_claimed(task, job['job_id'], adapter=adapter)
    finish(store, task, job, adapter)
    with repo.connect() as conn:
        assert conn.execute('SELECT record FROM jobs').fetchone()[0] == v1
        current_receipts = [tuple(r) for r in conn.execute('SELECT * FROM requests')]
        current_history = [tuple(r) for r in conn.execute('SELECT * FROM history')]
    assert all(r in current_receipts for r in receipts)
    assert all(r in current_history for r in history)


def test_exit_and_cancel_race_keeps_observed_code_and_monotonic_cancel(probe_env):
    repo, task, job, adapter, store = probe_env
    claim(probe_env)
    running = store.start_claimed(task, job['job_id'], adapter=adapter)
    # OS wait proves exit before this race; its persistence still races cancel.
    assert adapter.observe(running['probe_id'], running['process_identity'], wait=True)['returncode'] == 0
    barrier = threading.Barrier(2)
    def cancel():
        barrier.wait(timeout=5)
        try:
            return store.request_cancel(task, job['job_id'], running['version'], actor='bob', request_key='cancel')
        except Conflict:
            return None
    def reconcile():
        barrier.wait(timeout=5)
        return store.reconcile(task, job['job_id'], adapter=adapter)
    with ThreadPoolExecutor(max_workers=2) as pool:
        f1, f2 = pool.submit(cancel), pool.submit(reconcile)
        cancel_result, exit_result = f1.result(timeout=5), f2.result(timeout=5)
    final = store.get(task, job['job_id'])
    assert final['phase'] == 'exited' and final['exit_observation']['returncode'] == 0
    if cancel_result is not None:
        assert final['cancel_requested'] is True


@pytest.mark.parametrize('field,value', [('pid', 1), ('token', 'fake')])
def test_matching_owner_hash_cannot_replace_the_bound_process_identity(probe_env, monkeypatch, field, value):
    repo, task, job, adapter, store = probe_env
    claim(probe_env)
    running = store.start_claimed(task, job['job_id'], adapter=adapter)
    actual = adapter.observe(running['probe_id'], running['process_identity'], wait=True)
    observed = dict(actual, identity=dict(actual['identity'], **{field: value}))
    monkeypatch.setattr(adapter, 'observe', lambda *args, **kwargs: observed)
    before = rows(repo)
    from rag_eval.workbench.job_lifecycle_probe import ProbeRejected
    with pytest.raises(ProbeRejected):
        store.reconcile(task, job['job_id'], adapter=adapter)
    assert rows(repo) == before


@pytest.mark.parametrize('phase_change', [dict(phase='exited', start_consumed=False),
    dict(phase='not_started', start_consumed=False, cancel_requested=True)])
def test_illegal_terminal_phase_cannot_retain_running_identity(probe_env, phase_change):
    repo, task, job, adapter, store = probe_env
    claim(probe_env)
    running = store.start_claimed(task, job['job_id'], adapter=adapter)
    observed = adapter.observe(running['probe_id'], running['process_identity'], wait=True)
    altered = dict(running, **phase_change)
    if altered['phase'] == 'exited':
        altered['exit_observation'] = dict(observed, observed_at='fixture')
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE jobs SET probe=?', (json.dumps(altered),))
    from rag_eval.workbench.job_lifecycle_probe import ProbeRejected
    with pytest.raises(ProbeRejected):
        store.get(task, job['job_id'])


@pytest.mark.parametrize('event', ['probe.start_consumed', 'probe.started', 'probe.exited'])
@pytest.mark.parametrize('error_type', [ValueError, FileNotFoundError, sqlite3.OperationalError])
def test_start_and_exit_persistence_failures_keep_prior_authority_and_original_error(probe_env, monkeypatch, event, error_type):
    repo, task, job, adapter, store = probe_env
    original_claim = claim(probe_env)
    original_event = repo._event
    error = error_type('synthetic ' + event + ' persistence failure')
    def fail(conn, task_id, kind, object_id, content):
        if kind == event:
            raise error
        return original_event(conn, task_id, kind, object_id, content)
    if event == 'probe.exited':
        running = store.start_claimed(task, job['job_id'], adapter=adapter)
        adapter.observe(running['probe_id'], running['process_identity'], wait=True)
    monkeypatch.setattr(repo, '_event', fail)
    before = rows(repo)
    with pytest.raises(error_type) as caught:
        if event == 'probe.exited':
            store.reconcile(task, job['job_id'], adapter=adapter)
        else:
            store.start_claimed(task, job['job_id'], adapter=adapter)
    assert caught.value is error
    current = store.get(task, job['job_id'])
    if event == 'probe.start_consumed':
        assert rows(repo) == before and current == original_claim
        assert list(adapter.directory.iterdir()) == []
    elif event == 'probe.started':
        assert current['phase'] == 'unknown' and current['start_consumed'] is True
        assert current['process_identity'] is None
    else:
        assert rows(repo) == before and current == running
    monkeypatch.setattr(repo, '_event', original_event)
    if event != 'probe.start_consumed':
        assert finish(store, task, job, adapter)['phase'] == 'exited'
        assert len(list(adapter.directory.glob('*/started.json'))) == 1


def test_foreign_adapter_cannot_start_a_claim_by_spoofing_an_owner_id(probe_env):
    repo, task, job, adapter, store = probe_env
    claim(probe_env)
    from rag_eval.workbench.probe_subprocess import ProbeSubprocess
    from rag_eval.workbench.job_lifecycle_probe import ProbeRejected
    other = ProbeSubprocess()
    before = rows(repo)
    with pytest.raises(ProbeRejected):
        store.start_claimed(task, job['job_id'], adapter=other)
    assert rows(repo) == before and list(other.directory.iterdir()) == []


def _process_claim(state, task, job_id, key, barrier, queue):
    from rag_eval.workbench.job_lifecycle_probe import JobLifecycleProbeStore
    from rag_eval.workbench.probe_subprocess import ProbeSubprocess
    try:
        adapter = ProbeSubprocess()
        store = JobLifecycleProbeStore(WorkbenchRepository(state), adapter=adapter)
        barrier.wait(timeout=15)
        try:
            record = store.claim(task, job_id, 1, actor='alice', request_key=key, mode='normal')
            queue.put(('ok', record))
        except Conflict:
            queue.put(('conflict', None))
    except BaseException as exc:
        queue.put(('error', type(exc).__name__ + ': ' + str(exc)))


@pytest.mark.parametrize('same_key', [True, False])
def test_real_process_claims_have_one_authoritative_probe(probe_env, same_key):
    repo, task, job, adapter, store = probe_env
    ctx = multiprocessing.get_context('spawn')
    barrier, queue = ctx.Barrier(3), ctx.Queue()
    children = [ctx.Process(target=_process_claim, args=(str(repo.root), task, job['job_id'],
                    'same' if same_key else str(i), barrier, queue)) for i in range(2)]
    try:
        for child in children:
            child.start()
        barrier.wait(timeout=20)
        results = [queue.get(timeout=20) for _ in children]
        for child in children:
            child.join(timeout=20)
            assert child.exitcode == 0
        assert sum(kind == 'ok' for kind, _ in results) == (2 if same_key else 1)
        values = [r for kind, r in results if kind == 'ok']
        assert all(v == values[0] for v in values)
        assert store.get(task, job['job_id']) == values[0]
        assert len([e for e in repo.task(task)['history'] if e['kind'] == 'probe.claimed']) == 1
    finally:
        for child in children:
            if child.is_alive():
                child.terminate(); child.join(timeout=5)
        queue.close()


def test_real_claim_and_job_cancel_race_preserves_cancellation_in_same_row(probe_env):
    repo, task, job, adapter, store = probe_env
    barrier = threading.Barrier(2)
    def claim_one():
        barrier.wait(timeout=5)
        try:
            return claim(probe_env)
        except Conflict:
            return None
    def cancel_one():
        barrier.wait(timeout=5)
        return EvaluationJobStore(repo).cancel(task, job['job_id'], 1, actor='bob', request_key='cancel')
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed, cancelled = pool.submit(claim_one), pool.submit(cancel_one)
        claim_result, cancel_result = claimed.result(timeout=15), cancelled.result(timeout=15)
    assert cancel_result['state'] == 'cancelled'
    current = store.get(task, job['job_id'])
    if claim_result is None:
        assert current is None
    else:
        assert current['cancel_requested'] is True
        assert store.start_claimed(task, job['job_id'], adapter=adapter)['phase'] == 'not_started'
    assert list(adapter.directory.iterdir()) == []


def test_concurrent_start_claimed_consumes_one_launch_authorization(probe_env):
    repo, task, job, adapter, store = probe_env
    claim(probe_env)
    barrier = threading.Barrier(2)
    def start():
        barrier.wait(timeout=5)
        return store.start_claimed(task, job['job_id'], adapter=adapter)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(start), pool.submit(start)
        one, two = first.result(timeout=5), second.result(timeout=5)
    assert one == two and len(list(adapter.directory.glob('*/started.json'))) == 1
    assert finish(store, task, job, adapter)['exit_observation']['returncode'] == 0


def test_cross_task_workspace_and_false_process_binding_rejected_without_signals(probe_env, tmp_path, monkeypatch):
    repo, task, job, adapter, store = probe_env
    record = claim(probe_env)
    ws = repo.register_workspace('Other', tmp_path / 'other')
    other = repo.create_task(ws['id'], 'Other', '', request_key='other')
    from rag_eval.workbench.evaluation_jobs import JobRejected
    def forbidden(*args, **kwargs):
        raise AssertionError('cross-scope data cannot authorize process I/O')
    monkeypatch.setattr(adapter, 'start', forbidden)
    monkeypatch.setattr(adapter, 'observe', forbidden)
    before = rows(repo)
    with pytest.raises(JobRejected):
        store.get(other['id'], job['job_id'])
    assert rows(repo) == before

    with repo.connect(write=True) as conn:
        conn.execute('UPDATE tasks SET workspace_id=? WHERE id=?', (ws['id'], task))
    before = rows(repo)
    for action in (lambda: store.start_claimed(task, job['job_id'], adapter=adapter),
                   lambda: store.reconcile(task, job['job_id'], adapter=adapter)):
        with pytest.raises(JobRejected):
            action()
    assert rows(repo) == before


@pytest.mark.parametrize('operation', ['claim', 'cancel'])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), object()])
def test_invalid_noncanonical_choices_are_typed_before_request_hashing(probe_env, operation, value):
    repo, task, job, adapter, store = probe_env
    from rag_eval.workbench.job_lifecycle_probe import ProbeRejected
    if operation == 'cancel':
        claim(probe_env)
        action = lambda key: store.request_cancel(task, job['job_id'], value, actor='alice', request_key=key)
        store.request_cancel(task, job['job_id'], 1, actor='alice', request_key='old')
    else:
        action = lambda key: claim(probe_env, expected_job_version=value, request_key=key)
        claim(probe_env, request_key='old')
    before = rows(repo)
    with pytest.raises(ProbeRejected):
        action('new')
    with pytest.raises(Conflict):
        action('old')
    assert rows(repo) == before


@pytest.mark.parametrize('operation', ['claim', 'cancel'])
def test_original_receipt_must_match_the_commands_initial_flags_and_version(probe_env, operation):
    repo, task, job, adapter, store = probe_env
    record = claim(probe_env)
    if operation == 'claim':
        altered = dict(record, cancel_requested=True)
        action = lambda: claim(probe_env)
    else:
        record = store.request_cancel(task, job['job_id'], record['version'], actor='alice', request_key='cancel')
        altered = dict(record, version=record['version'] + 3)
        action = lambda: store.request_cancel(task, job['job_id'], 1, actor='alice', request_key='cancel')
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE requests SET receipt=? WHERE scope LIKE ?',
                     (json.dumps(altered), 'probe.' + operation + ':%'))
    before = rows(repo)
    from rag_eval.workbench.job_lifecycle_probe import ProbeRejected
    with pytest.raises(ProbeRejected):
        action()
    assert rows(repo) == before


def test_original_owner_reconcile_keeps_an_unconsumed_claim_available_for_start(probe_env):
    repo, task, job, adapter, store = probe_env
    original = claim(probe_env)
    before = rows(repo)
    assert store.reconcile(task, job['job_id'], adapter=adapter) == original
    assert store.reconcile(task, job['job_id'], adapter=adapter) == original
    assert rows(repo) == before
    assert store.start_claimed(task, job['job_id'], adapter=adapter)['phase'] == 'running'
    assert finish(store, task, job, adapter)['exit_observation']['returncode'] == 0
