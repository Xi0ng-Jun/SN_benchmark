"""Simulated restarts use durable records and small synthetic OS children only."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from rag_eval.workbench.job_execution import JobExecutionStore
from rag_eval.workbench.repository import WorkbenchRepository
from test_workbench_job_execution import fixture, spec, wait_terminal


def orphan(tmp_path, monkeypatch, *, timeout=30):
    repo, task, job = fixture(tmp_path)
    store = JobExecutionStore(repo)
    monkeypatch.setattr(store, '_start', lambda record: None)
    execution_spec = spec(tmp_path)
    execution_spec['budget']['timeout_seconds'] = timeout
    record = store.run(task['id'], job['job_id'], 1, execution_spec=execution_spec,
                       actor='alice', request_key='run')
    # A killed server loses its locks as well as its in-memory Popen objects.
    for lock in getattr(store, '_supervisors', {}).values():
        lock.close()
    getattr(store, '_supervisors', {}).clear()
    return repo, task, record


def save_identity(repo, record, pid, *, start_ticks=None):
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    identity = dict(pid=pid, start_ticks=start_ticks or fields[19],
                    boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                    pgrp=int(fields[2]), session=int(fields[3]))
    metadata = dict(execution_id=record['execution_id'], run_dir=record['run_dir'],
                    process_identity=identity)
    with repo.connect(write=True) as conn:
        repo._event(conn, record['task_id'], 'job.execution_process', record['execution_id'], metadata)
    return identity


@contextmanager
def child(code='import time; time.sleep(30)'):
    process = subprocess.Popen([sys.executable, '-c', code], start_new_session=True)
    try:
        yield process
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)


def events(repo, execution_id):
    with repo.connect() as conn:
        return [row['kind'] for row in conn.execute(
            'SELECT kind FROM history WHERE object_id=? ORDER BY seq', (execution_id,))]


def test_restart_without_identity_publishes_lost_and_preserves_charged_attempt(tmp_path, monkeypatch):
    from rag_eval.workbench.budgets import BudgetLedger
    repo, task, record = orphan(tmp_path, monkeypatch)
    ledger = BudgetLedger(repo)
    iteration = dict(id='iteration', task_id=task['id'], protocol=dict(
        budget=dict(limit_cny=10, max_runs_per_candidate=4)))
    ledger.reserve_many(iteration, [dict(candidate_id='candidate', request_key='one', case_count=2)],
                        group_key='group')
    ledger.attach('iteration', 'candidate', 'one', record['execution_id'])
    restarted = JobExecutionStore(WorkbenchRepository(repo.root))
    restarted.recover()
    finished = wait_terminal(restarted, task['id'], record['execution_id'])
    assert finished['state'] == 'failed'
    assert finished['error_type'] == 'execution_lost'
    assert finished['run_state']['failed_phase'] == 'supervising'
    summary = ledger.summary('iteration')
    assert summary['unknown_count'] == 1 and summary['charged_cny'] == 2
    assert summary['runs_by_candidate'] == {'candidate': 1}
    restarted.recover()
    assert events(repo, record['execution_id']).count('job.execution_finished') == 1
    assert json.loads((tmp_path / 'run/execution-record.json').read_text())['terminal'] is True


@pytest.mark.parametrize('proof', ['dead', 'mismatch'])
def test_dead_or_reused_pid_is_lost_without_signalling_unrelated_process(tmp_path, monkeypatch, proof):
    repo, task, record = orphan(tmp_path, monkeypatch)
    with child() as process:
        save_identity(repo, record, process.pid, start_ticks='1' if proof == 'mismatch' else None)
        if proof == 'dead':
            process.kill()
            process.wait()
        restarted = JobExecutionStore(WorkbenchRepository(repo.root))
        restarted.recover()
        finished = wait_terminal(restarted, task['id'], record['execution_id'])
        assert finished['error_type'] == 'execution_lost'
        if proof == 'mismatch':
            restarted.cancel(task['id'], record['execution_id'], actor='alice', request_key='stop')
            assert process.poll() is None


def test_proven_alive_process_is_reattached_and_cancelled_safely(tmp_path, monkeypatch):
    repo, task, record = orphan(tmp_path, monkeypatch)
    with child() as process:
        save_identity(repo, record, process.pid)
        restarted = JobExecutionStore(WorkbenchRepository(repo.root))
        restarted.recover()
        assert restarted.get(task['id'], record['execution_id'])['state'] == 'running'
        restarted.cancel(task['id'], record['execution_id'], actor='alice', request_key='stop')
        process.wait(timeout=3)
        assert restarted.get(task['id'], record['execution_id'])['state'] == 'cancelled'
        assert events(repo, record['execution_id']).count('job.execution_cancelled') == 1


def test_live_supervisor_lock_prevents_second_recovery_or_terminal_publication(tmp_path, monkeypatch):
    repo, task, record = orphan(tmp_path, monkeypatch)
    with child() as process:
        save_identity(repo, record, process.pid)
        first = JobExecutionStore(WorkbenchRepository(repo.root))
        second = JobExecutionStore(WorkbenchRepository(repo.root))
        first.recover()
        second.recover()
        assert record['execution_id'] in first._processes
        assert record['execution_id'] not in second._supervisors
        assert not second.get(task['id'], record['execution_id'])['terminal']
        first.cancel(task['id'], record['execution_id'], actor='alice', request_key='stop')
        process.wait(timeout=3)


@pytest.mark.parametrize('phase,state', [('finished', 'finished'), ('finished_with_errors', 'failed'),
                                        ('failed', 'failed')])
def test_completed_state_after_supervisor_death_is_recovered_and_settled(tmp_path, monkeypatch, phase, state):
    from rag_eval.workbench.budgets import BudgetLedger
    repo, task, record = orphan(tmp_path, monkeypatch)
    with child() as process:
        save_identity(repo, record, process.pid)
        process.kill()
        process.wait()
    run = Path(record['run_dir'])
    run.mkdir()
    (run / 'state.json').write_text(json.dumps(dict(phase=phase, terminal=True,
        cost_observation=dict(currency='CNY', complete=True, total_cny=0.25))))
    ledger = BudgetLedger(repo)
    ledger.reserve_many(dict(id='iteration', task_id=task['id'], protocol=dict(
        budget=dict(limit_cny=10, max_runs_per_candidate=4))),
        [dict(candidate_id='candidate', request_key='one', case_count=2)], group_key='group')
    ledger.attach('iteration', 'candidate', 'one', record['execution_id'])
    restarted = JobExecutionStore(WorkbenchRepository(repo.root))
    restarted.recover()
    finished = wait_terminal(restarted, task['id'], record['execution_id'])
    assert finished['state'] == state and finished['exit_code'] is None
    assert finished['run_state']['phase'] == phase
    assert ledger.summary('iteration')['actual_cny'] == 0.25


@pytest.mark.parametrize('untrusted', ['no_identity', 'symlink', 'nonterminal', 'stale'])
def test_untrusted_state_does_not_become_a_success(tmp_path, monkeypatch, untrusted):
    repo, task, record = orphan(tmp_path, monkeypatch)
    if untrusted != 'no_identity':
        with child() as process:
            save_identity(repo, record, process.pid)
            process.kill()
            process.wait()
    run = Path(record['run_dir'])
    run.mkdir()
    state = run / 'state.json'
    target = tmp_path / 'unrelated.json' if untrusted == 'symlink' else state
    target.write_text(json.dumps(dict(phase='finished', terminal=untrusted != 'nonterminal')))
    if untrusted == 'symlink':
        state.symlink_to(target)
    if untrusted == 'stale':
        os.utime(state, (1, 1))
    restarted = JobExecutionStore(WorkbenchRepository(repo.root))
    restarted.recover()
    assert wait_terminal(restarted, task['id'], record['execution_id'])['error_type'] == 'execution_lost'


def test_recovered_timeout_uses_original_deadline(tmp_path, monkeypatch):
    repo, task, record = orphan(tmp_path, monkeypatch, timeout=1)
    record['started_at'] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    with repo.connect(write=True) as conn:
        conn.execute("UPDATE history SET content=? WHERE object_id=? AND kind='job.execution_started'",
                     (json.dumps(record), record['execution_id']))
    with child() as process:
        save_identity(repo, record, process.pid)
        restarted = JobExecutionStore(WorkbenchRepository(repo.root))
        restarted.recover()
        assert wait_terminal(restarted, task['id'], record['execution_id'])['state'] == 'timeout'
        process.wait(timeout=3)


def test_normal_launch_persists_identity_and_keeps_its_supervision_lock(tmp_path):
    repo, task, job = fixture(tmp_path)
    store = JobExecutionStore(repo)
    record = store.run(task['id'], job['job_id'], 1,
        execution_spec=spec(tmp_path, 'import time; time.sleep(30)'), actor='alice', request_key='run')
    process = store._processes[record['execution_id']]
    try:
        with repo.connect() as conn:
            metadata = json.loads(conn.execute("SELECT content FROM history WHERE object_id=? "
                "AND kind='job.execution_process'", (record['execution_id'],)).fetchone()['content'])
        fields = Path(f'/proc/{process.pid}/stat').read_text().rsplit(')', 1)[1].split()
        assert metadata['execution_id'] == record['execution_id']
        assert metadata['run_dir'] == record['run_dir']
        assert metadata['process_identity'] == dict(pid=process.pid, start_ticks=fields[19],
            pgrp=process.pid, session=process.pid,
            boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        another = JobExecutionStore(WorkbenchRepository(repo.root))
        another.recover()
        assert record['execution_id'] not in another._supervisors
        assert not another.get(task['id'], record['execution_id'])['terminal']
    finally:
        store.cancel(task['id'], record['execution_id'], actor='alice', request_key='stop')
        process.wait(timeout=3)


def test_reattached_watcher_observes_later_completion_and_releases_claim(tmp_path, monkeypatch):
    repo, task, record = orphan(tmp_path, monkeypatch)
    with child() as process:
        save_identity(repo, record, process.pid)
        restarted = JobExecutionStore(WorkbenchRepository(repo.root))
        restarted.recover()
        run = Path(record['run_dir'])
        run.mkdir()
        (run / 'state.json').write_text(json.dumps(dict(phase='finished', terminal=True)))
        process.terminate()
        process.wait(timeout=3)
        assert wait_terminal(restarted, task['id'], record['execution_id'])['state'] == 'finished'
        deadline = time.monotonic() + 2
        while record['execution_id'] in restarted._supervisors and time.monotonic() < deadline:
            time.sleep(.01)
        assert record['execution_id'] not in restarted._supervisors
        assert record['execution_id'] not in restarted._processes
        assert events(repo, record['execution_id']).count('job.execution_finished') == 1


def test_recovered_cancel_stops_partition_child_after_session_leader_exits(tmp_path, monkeypatch):
    repo, task, record = orphan(tmp_path, monkeypatch)
    ready = tmp_path / 'ready'
    descendant = ('import os,signal,time; from pathlib import Path; '
        'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
        f'Path({str(ready)!r}).write_text(str(os.getpid())); time.sleep(30)')
    parent = f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{descendant!r}]); time.sleep(30)'
    with child(parent) as process:
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        descendant_pid = int(ready.read_text())
        save_identity(repo, record, process.pid)
        restarted = JobExecutionStore(WorkbenchRepository(repo.root))
        restarted.recover()
        restarted.cancel(task['id'], record['execution_id'], actor='alice', request_key='stop')
        process.wait(timeout=3)
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            path = Path(f'/proc/{descendant_pid}/stat')
            if not path.exists() or path.read_text().rsplit(')', 1)[1].split()[0] == 'Z':
                break
            time.sleep(.02)
        else:
            pytest.fail('recovered cancellation left the partition child running')
        assert restarted.get(task['id'], record['execution_id'])['state'] == 'cancelled'


def test_app_startup_recovers_orphan_without_starting_a_model_or_server(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from rag_eval.workbench.api import create_app
    repo, task, record = orphan(tmp_path, monkeypatch)
    app = create_app(repo.root, dict(workspace=dict(title='synthetic', location=str(tmp_path / 'project'))),
                     access_token='test')
    with TestClient(app):
        assert wait_terminal(JobExecutionStore(repo), task['id'], record['execution_id'])['error_type'] == 'execution_lost'


def test_other_process_supervisor_prevents_recovery_until_its_lock_is_released(tmp_path, monkeypatch):
    repo, task, record = orphan(tmp_path, monkeypatch)
    lock = repo.root / 'execution-supervisors' / (record['execution_id'] + '.lock')
    ready = tmp_path / 'supervisor-ready'
    code = ('import fcntl,time; from pathlib import Path; '
            f'handle=open({str(lock)!r},"r+"); fcntl.flock(handle,fcntl.LOCK_EX); '
            f'Path({str(ready)!r}).write_text("ready"); time.sleep(30)')
    restarted = JobExecutionStore(WorkbenchRepository(repo.root))
    with child(code):
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        restarted.recover()
        assert not restarted.get(task['id'], record['execution_id'])['terminal']
        assert record['execution_id'] not in restarted._supervisors
    restarted.recover()
    assert wait_terminal(restarted, task['id'], record['execution_id'])['error_type'] == 'execution_lost'
    assert events(repo, record['execution_id']).count('job.execution_finished') == 1


def test_recovery_uses_normal_terminal_bookkeeping_retry(tmp_path, monkeypatch):
    from test_workbench_supervisor_bookkeeping import flaky
    repo, task, record = orphan(tmp_path, monkeypatch)
    restarted = JobExecutionStore(WorkbenchRepository(repo.root))
    sleeps, calls = flaky(restarted, monkeypatch, failures=2)
    restarted.recover()
    assert wait_terminal(restarted, task['id'], record['execution_id'])['error_type'] == 'execution_lost'
    assert calls['finish'] == 3 and sleeps == [1.0, 2.0]
    assert events(repo, record['execution_id']) == ['job.execution_started', 'execution.finish_failed',
                                                 'execution.finish_failed', 'job.execution_finished']


def test_already_exited_unreaped_child_still_has_launch_provenance():
    from rag_eval.workbench.execution_recovery import process_identity, RecoveredProcess
    # waitid(WNOWAIT) observes exit without recycling the child's PID.
    with child('pass') as process:
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
        assert process_identity(process.pid) is None
        identity = process_identity(process.pid, allow_exited=True)
        assert identity['pid'] == process.pid
        assert identity['pgrp'] == process.pid and identity['session'] == process.pid
        assert RecoveredProcess.attach(identity) is None


def test_launch_transaction_rollback_releases_supervisor_without_watcher(tmp_path, monkeypatch):
    from rag_eval.workbench.execution_recovery import claim_supervisor
    repo, task, job = fixture(tmp_path)
    store = JobExecutionStore(repo)
    claimed = []
    original_claim = store._claim_supervisor
    def claim(execution_id):
        claimed.append(execution_id)
        return original_claim(execution_id)
    monkeypatch.setattr(store, '_claim_supervisor', claim)
    def fail_receipt(*args, **kwargs):
        raise sqlite3.OperationalError('synthetic receipt write failure')
    monkeypatch.setattr(repo, '_receipt', fail_receipt)
    with pytest.raises(sqlite3.OperationalError):
        store.run(task['id'], job['job_id'], 1, execution_spec=spec(tmp_path),
                  actor='alice', request_key='run')
    assert store._supervisors == {}
    assert len(claimed) == 1
    assert events(repo, claimed[0]) == []
    lock = claim_supervisor(repo.root, claimed[0])
    assert lock is not None
    lock.close()


def test_ordinary_log_finalization_failure_still_settles_and_releases_owner(tmp_path, monkeypatch):
    repo, task, job = fixture(tmp_path)
    store = JobExecutionStore(repo)
    def unavailable(*args):
        raise OSError('synthetic log move failure')
    monkeypatch.setattr(store, '_finalize_logs', unavailable)
    started = store.run(task['id'], job['job_id'], 1, execution_spec=spec(tmp_path),
                        actor='alice', request_key='run')
    assert wait_terminal(store, task['id'], started['execution_id'])['state'] == 'finished'
    deadline = time.monotonic() + 2
    while store._supervisors and time.monotonic() < deadline:
        time.sleep(.01)
    assert store._supervisors == {}
    assert started['execution_id'] not in store._processes


@pytest.mark.parametrize('recovery', ['startup', 'same_key_cancel'])
def test_cancel_commit_crash_still_stops_proven_live_child_and_preserves_reservation(tmp_path, monkeypatch, recovery):
    from rag_eval.workbench.budgets import BudgetLedger
    repo, task, record = orphan(tmp_path, monkeypatch)
    ledger = BudgetLedger(repo)
    ledger.reserve_many(dict(id='iteration', task_id=task['id'], protocol=dict(
        budget=dict(limit_cny=10, max_runs_per_candidate=4))),
        [dict(candidate_id='candidate', request_key='one', case_count=2)], group_key='group')
    ledger.attach('iteration', 'candidate', 'one', record['execution_id'])
    with child() as process:
        save_identity(repo, record, process.pid)
        crashed = JobExecutionStore(repo)
        def crash_after_commit(record):
            raise RuntimeError('synthetic crash before cancellation signaling')
        monkeypatch.setattr(crashed, '_settle_budget', crash_after_commit)
        with pytest.raises(RuntimeError, match='before cancellation signaling'):
            crashed.cancel(task['id'], record['execution_id'], actor='alice', request_key='stop')
        cancelled = crashed.get(task['id'], record['execution_id'])
        assert cancelled['state'] == 'cancelled' and process.poll() is None
        restarted = JobExecutionStore(WorkbenchRepository(repo.root))
        if recovery == 'startup':
            restarted.recover()
        else:
            assert restarted.cancel(task['id'], record['execution_id'], actor='alice', request_key='stop') == cancelled
        process.wait(timeout=3)
        deadline = time.monotonic() + 2
        while restarted._supervisors and time.monotonic() < deadline:
            time.sleep(.01)
        assert restarted.get(task['id'], record['execution_id']) == cancelled
        assert events(repo, record['execution_id']).count('job.execution_cancelled') == 1
        assert 'job.execution_finished' not in events(repo, record['execution_id'])
        assert restarted._supervisors == {}
        summary = ledger.summary('iteration')
        assert summary['unknown_count'] == 1 and summary['charged_cny'] == 2
        assert summary['runs_by_candidate'] == {'candidate': 1}
        assert json.loads((tmp_path / 'run/execution-record.json').read_text()) == cancelled


def test_cancelled_execution_with_reused_pid_does_not_signal_or_rewrite_history(tmp_path, monkeypatch):
    repo, task, record = orphan(tmp_path, monkeypatch)
    with child() as process:
        save_identity(repo, record, process.pid, start_ticks='1')
        store = JobExecutionStore(repo)
        cancelled = store.cancel(task['id'], record['execution_id'], actor='alice', request_key='stop')
        restarted = JobExecutionStore(WorkbenchRepository(repo.root))
        restarted.recover()
        deadline = time.monotonic() + 2
        while restarted._supervisors and time.monotonic() < deadline:
            time.sleep(.01)
        assert process.poll() is None
        assert restarted.get(task['id'], record['execution_id']) == cancelled
        assert 'job.execution_finished' not in events(repo, record['execution_id'])
