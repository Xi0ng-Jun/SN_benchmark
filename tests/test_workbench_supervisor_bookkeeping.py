"""Terminal bookkeeping survives a busy database; history lookups use indexes."""
import json
import sqlite3
import time
from pathlib import Path

from rag_eval.workbench.job_execution import JobExecutionStore
from rag_eval.workbench.repository import WorkbenchRepository
from test_workbench_job_execution import fixture, spec, wait_terminal


def history(repo, execution_id):
    with repo.connect() as conn:
        return [(row['kind'], json.loads(row['content'])) for row in conn.execute(
            'SELECT kind,content FROM history WHERE object_id=? ORDER BY seq', (execution_id,))]


def flaky(store, monkeypatch, *, failures=1, log_fails=False):
    sleeps = []
    monkeypatch.setattr(store, '_sleep', sleeps.append)
    original_finish = store._finish
    calls = {'finish': 0}
    def finish(*args, **kwargs):
        calls['finish'] += 1
        if calls['finish'] <= failures:
            raise sqlite3.OperationalError('database is locked')
        return original_finish(*args, **kwargs)
    monkeypatch.setattr(store, '_finish', finish)
    if log_fails:
        monkeypatch.setattr(store, '_log_finish_failure', lambda record, failure: False)
    return sleeps, calls


def test_busy_finish_is_retried_and_execution_reaches_terminal(tmp_path, monkeypatch):
    repo, task, job = fixture(tmp_path)
    store = JobExecutionStore(repo)
    sleeps, calls = flaky(store, monkeypatch, failures=3)
    started = store.run(task['id'], job['job_id'], expected_state_version=1,
                        execution_spec=spec(tmp_path), actor='alice', request_key='run')
    finished = wait_terminal(store, task['id'], started['execution_id'])
    assert finished['state'] == 'finished' and finished['exit_code'] == 0
    assert calls['finish'] == 4
    assert sleeps == [1.0, 2.0, 4.0]  # exponential backoff
    kinds = [kind for kind, _ in history(repo, started['execution_id'])]
    assert kinds == ['job.execution_started', 'execution.finish_failed', 'execution.finish_failed',
                     'execution.finish_failed', 'job.execution_finished']
    failure = history(repo, started['execution_id'])[1][1]
    assert failure['error_type'] == 'OperationalError' and failure['attempt'] == 1
    assert failure['process_exited_at'] <= finished['finished_at']
    record = json.loads((Path(started['run_dir']) / 'execution-record.json').read_text())
    assert record['state'] == 'finished'


def test_backoff_keeps_retrying_beyond_a_minute(tmp_path, monkeypatch):
    repo, task, job = fixture(tmp_path)
    store = JobExecutionStore(repo)
    sleeps, _ = flaky(store, monkeypatch, failures=9)
    started = store.run(task['id'], job['job_id'], expected_state_version=1,
                        execution_spec=spec(tmp_path), actor='alice', request_key='run')
    assert wait_terminal(store, task['id'], started['execution_id'])['state'] == 'finished'
    assert sum(sleeps) >= 60 and max(sleeps) == 60.0


def test_unlogged_failures_land_with_the_terminal_record(tmp_path, monkeypatch):
    repo, task, job = fixture(tmp_path)
    store = JobExecutionStore(repo)
    flaky(store, monkeypatch, failures=2, log_fails=True)
    started = store.run(task['id'], job['job_id'], expected_state_version=1,
                        execution_spec=spec(tmp_path), actor='alice', request_key='run')
    assert wait_terminal(store, task['id'], started['execution_id'])['terminal']
    events = history(repo, started['execution_id'])
    assert [kind for kind, _ in events] == ['job.execution_started', 'execution.finish_failed',
                                           'execution.finish_failed', 'job.execution_finished']
    assert [content['attempt'] for kind, content in events if kind == 'execution.finish_failed'] == [1, 2]


def test_timeout_finish_is_retried(tmp_path, monkeypatch):
    repo, task, job = fixture(tmp_path)
    store = JobExecutionStore(repo)
    flaky(store, monkeypatch, failures=1)
    execution = spec(tmp_path, 'import time; time.sleep(10)')
    execution['budget']['timeout_seconds'] = 1
    started = store.run(task['id'], job['job_id'], expected_state_version=1,
                        execution_spec=execution, actor='alice', request_key='run')
    finished = wait_terminal(store, task['id'], started['execution_id'])
    assert finished['state'] == 'timeout'
    assert 'execution.finish_failed' in [kind for kind, _ in history(repo, started['execution_id'])]


def test_real_writer_lock_delays_but_does_not_lose_terminal_state(tmp_path, monkeypatch):
    repo, task, job = fixture(tmp_path)
    store = JobExecutionStore(repo)
    monkeypatch.setattr(store, '_sleep', lambda seconds: time.sleep(0.05))
    run = tmp_path / 'run'
    code = (f'import time; from pathlib import Path; p=Path({str(run)!r}); p.mkdir(); time.sleep(0.3); '
            '(p / "state.json").write_text("{\\"phase\\": \\"finished\\"}")')
    started = store.run(task['id'], job['job_id'], expected_state_version=1,
                        execution_spec=spec(tmp_path, code), actor='alice', request_key='run')
    original_connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: original_connect(*a, **dict(k, timeout=0.05)))
    lock = original_connect(repo.database)
    lock.execute('BEGIN IMMEDIATE')
    try:
        time.sleep(1.5)  # the child exits and the supervisor keeps hitting the lock
        assert not store.get(task['id'], started['execution_id'])['terminal']
    finally:
        lock.rollback()
        lock.close()
    finished = wait_terminal(store, task['id'], started['execution_id'])
    assert finished['state'] == 'finished'
    assert 'execution.finish_failed' in [kind for kind, _ in history(repo, started['execution_id'])]


def plan(conn, sql, params):
    return ' | '.join(row[3] for row in conn.execute('EXPLAIN QUERY PLAN ' + sql, params))


def test_execution_discovery_queries_use_history_indexes(tmp_path):
    repo, task, job = fixture(tmp_path)
    with repo.connect() as conn:
        for sql, params in [
            # JobExecutionStore.records (feeds RunViews._discover_executions)
            ("SELECT object_id,task_id FROM history WHERE task_id IN "
             "(SELECT id FROM tasks WHERE workspace_id=?) "
             "AND kind='job.execution_started' ORDER BY seq", ('w',)),
            # JobExecutionStore._read, once per execution
            ("SELECT content FROM history WHERE task_id=? AND object_id=? "
             "AND kind IN ('job.execution_started','job.execution_finished',"
             "'job.execution_cancelled') ORDER BY seq", ('t', 'e')),
            # JobExecutionStore._finish
            ("SELECT content FROM history WHERE object_id=? AND kind IN "
             "('job.execution_started','job.execution_finished','job.execution_cancelled') ORDER BY seq", ('e',)),
        ]:
            detail = plan(conn, sql, params)
            assert 'INDEX history_' in detail, detail
            assert 'SCAN history' not in detail, detail


def test_v5_database_gains_indexes_and_wal_without_changing_rows(tmp_path):
    repo, task, job = fixture(tmp_path)
    with repo.connect() as conn:
        conn.execute('DROP INDEX history_object_kind')
        conn.execute('DROP INDEX history_task_kind_object')
        conn.execute('PRAGMA user_version=5')
        before = [tuple(row) for row in conn.execute('SELECT * FROM history ORDER BY seq')]
    conn = sqlite3.connect(repo.database)
    conn.execute('PRAGMA journal_mode=DELETE')
    conn.close()
    reopened = WorkbenchRepository(repo.root)
    with reopened.connect() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 6
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert conn.execute('PRAGMA synchronous').fetchone()[0] == 1  # NORMAL
        names = {row['name'] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {'history_object_kind', 'history_task_kind_object'} <= names
        assert [tuple(row) for row in conn.execute('SELECT * FROM history ORDER BY seq')] == before
