import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rag_eval.workbench.job_execution import ExecutionRejected, JobExecutionStore
from rag_eval.workbench.repository import WorkbenchRepository
from rag_eval.workbench.api import create_app
from test_workbench_evaluation_plans import setup


def fixture(tmp_path):
    repo, workspace, task, plans, bundle, command = setup(tmp_path)
    plan = plans.save(**command)
    from rag_eval.workbench.evaluation_jobs import EvaluationJobStore
    job = EvaluationJobStore(repo).prepare(task['id'], plan['plan_id'], plan['revision'], plan['content_hash'],
                                           actor='alice', request_key='job')
    with repo.connect(write=True) as conn:
        conn.execute('INSERT INTO history(task_id,kind,object_id,content,registered_at) VALUES(?,?,?,?,?)',
                     (task['id'], 'job.inputs_source_captured', job['job_id'],
                      json.dumps({'preparation': {'preparation_id': 'p' * 64}}), 'now'))
    return repo, task, job


def wait_terminal(store, task_id, execution_id):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        record = store.get(task_id, execution_id)
        if record['terminal']:
            return record
        time.sleep(0.01)
    raise AssertionError('execution did not reach a terminal state')


def spec(tmp_path, code='from pathlib import Path; Path("state.json").write_text("{\\"phase\\": \\"finished\\", \\"terminal\\": true}")'):
    run = tmp_path / 'run'
    if code.startswith('from pathlib import Path; Path("state.json")'):
        code = f'from pathlib import Path; p=Path({str(run)!r}); p.mkdir(); (p / "state.json").write_text("{{\\"phase\\": \\"finished\\", \\"terminal\\": true}}")'
    return dict(argv=[sys.executable, '-c', code], run_dir=str(run),
                budget=dict(timeout_seconds=30), preparation_id='p' * 64)


def test_run_without_cost_limit_uses_independent_run_dir_and_freezes_timeout(tmp_path):
    repo, task, job = fixture(tmp_path)
    executions = JobExecutionStore(repo)
    with pytest.raises(ExecutionRejected, match='budget'):
        executions.run(task['id'], job['job_id'], expected_state_version=1,
                       execution_spec={**spec(tmp_path), 'budget': {
                           'confirmed': False, 'limit_cny': 1, 'timeout_seconds': 30}},
                       actor='alice', request_key='run')
    assert list((tmp_path / 'run').parent.glob('run')) == []

    execution = spec(tmp_path)
    started = executions.run(task['id'], job['job_id'], expected_state_version=1,
                             execution_spec=execution, actor='alice', request_key='run')
    execution['budget']['timeout_seconds'] = 120
    assert started['budget'] == {'timeout_seconds': 30}
    assert started['state'] == 'running' and started['terminal'] is False
    assert started['argv'][0] == sys.executable
    finished = wait_terminal(executions, task['id'], started['execution_id'])
    assert finished['state'] == 'finished' and finished['exit_code'] == 0
    assert finished['budget'] == {'timeout_seconds': 30}
    assert Path(started['run_dir']).is_dir()
    assert json.loads((Path(started['run_dir']) / 'execution-record.json').read_text())['terminal'] is True
    assert json.loads((Path(started['run_dir']) / 'state.json').read_text())['phase'] == 'finished'
    assert executions.run(task['id'], job['job_id'], expected_state_version=1,
                          execution_spec=spec(tmp_path), actor='alice', request_key='run') == started


@pytest.mark.parametrize('budget', [
    dict(confirmed=True, limit_cny=None, timeout_seconds=30),
    dict(confirmed=True, limit_cny=0, timeout_seconds=30),
    dict(confirmed=True, limit_cny=True, timeout_seconds=30),
    dict(confirmed=True, limit_cny=float('inf'), timeout_seconds=30),
    dict(confirmed=True, limit_cny=1, timeout_seconds=True),
    dict(confirmed=True, timeout_seconds=0),
    dict(confirmed=True),
])
def test_invalid_budget_is_rejected_without_dispatch_or_receipt(tmp_path, budget):
    repo, task, job = fixture(tmp_path)
    executions = JobExecutionStore(repo)
    with pytest.raises(ExecutionRejected):
        executions.run(task['id'], job['job_id'], 1,
            execution_spec={**spec(tmp_path), 'budget': budget}, actor='alice', request_key='invalid')
    assert executions.latest_for_job(task['id'], job['job_id']) is None
    assert not (tmp_path / 'run').exists()


def test_cancel_preserves_terminal_execution_and_run_artifacts(tmp_path):
    repo, task, job = fixture(tmp_path)
    executions = JobExecutionStore(repo)
    code = 'import time; time.sleep(10)'
    started = executions.run(task['id'], job['job_id'], expected_state_version=1,
                             execution_spec=spec(tmp_path, code), actor='alice', request_key='run')
    cancelled = executions.cancel(task['id'], started['execution_id'], actor='alice', request_key='cancel')
    assert cancelled['state'] == 'cancelled' and cancelled['terminal'] is True
    assert Path(started['run_dir']).is_dir()
    assert executions.cancel(task['id'], started['execution_id'], actor='alice', request_key='cancel') == cancelled


def test_failed_child_keeps_state_and_stderr(tmp_path):
    repo, task, job = fixture(tmp_path)
    executions = JobExecutionStore(repo)
    started = executions.run(task['id'], job['job_id'], expected_state_version=1,
                             execution_spec=spec(tmp_path, 'raise SystemExit(7)'), actor='alice', request_key='run')
    finished = wait_terminal(executions, task['id'], started['execution_id'])
    assert finished['state'] == 'failed' and finished['exit_code'] == 7
    assert (Path(started['run_dir']) / 'stderr.log').is_file()


def test_timeout_is_a_terminal_fact_and_does_not_erase_run_dir(tmp_path):
    repo, task, job = fixture(tmp_path)
    executions = JobExecutionStore(repo)
    execution = spec(tmp_path, 'import time; time.sleep(10)')
    execution['budget']['timeout_seconds'] = 1
    started = executions.run(task['id'], job['job_id'], expected_state_version=1,
                             execution_spec=execution, actor='alice', request_key='timeout')
    finished = wait_terminal(executions, task['id'], started['execution_id'])
    assert finished['state'] == 'timeout' and finished['error_type'] == 'timeout'
    assert Path(started['run_dir']).is_dir()


def test_http_run_uses_trusted_template_and_retries_by_request_key(tmp_path, monkeypatch):
    from test_workbench_plans_comparison_api import setup as http_setup, command
    client, config, task, bundle = http_setup(tmp_path)
    config['job_execution'] = {
        'output_root': str(tmp_path / 'executions'),
        'argv': [sys.executable, '-c',
                 'from pathlib import Path; Path("state.json").write_text("{\\"phase\\": \\"finished\\"}")']}
    client = TestClient(create_app(tmp_path / 'state', config, actor='alice', access_token='test-access'))
    plan = client.post(f"/api/tasks/{task['id']}/plans", json=command(bundle), headers={'Authorization': 'Bearer test-access', 'X-Workbench': '1'}).json()['record']
    headers = {'Authorization': 'Bearer test-access', 'X-Workbench': '1'}
    job = client.post(f"/api/tasks/{task['id']}/jobs", json={
        'plan_id': plan['plan_id'], 'revision': plan['revision'],
        'expected_content_hash': plan['content_hash'], 'request_key': 'job'}, headers=headers).json()['record']
    repo = WorkbenchRepository(tmp_path / 'state')
    with repo.connect(write=True) as conn:
        conn.execute('INSERT INTO history(task_id,kind,object_id,content,registered_at) VALUES(?,?,?,?,?)',
                     (task['id'], 'job.inputs_source_captured', job['job_id'],
                      json.dumps({'preparation': {'preparation_id': 'a' * 64}}), 'now'))
    from rag_eval.workbench.evaluation_jobs import EvaluationJobStore
    monkeypatch.setattr(EvaluationJobStore, 'preparations',
                        lambda self, task_id, job_id: [{'preparation': {'preparation_id': 'a' * 64}}])
    path = f"/api/tasks/{task['id']}/jobs/{job['job_id']}/run"
    payload = {'expected_state_version': 1, 'preparation_id': 'a' * 64,
               'budget': {'timeout_seconds': 30}, 'request_key': 'run'}
    first = client.post(path, json=payload, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()['record']['budget'] == {'timeout_seconds': 30}
    second = client.post(path, json=payload, headers=headers)
    assert second.status_code == 200 and second.json() == first.json()


def test_running_status_reads_current_runner_state(tmp_path):
    repo, task, job = fixture(tmp_path)
    executions = JobExecutionStore(repo)
    started = executions.run(task['id'], job['job_id'], 1,
        execution_spec=spec(tmp_path, 'import time; time.sleep(10)'), actor='alice', request_key='live')
    root = Path(started['run_dir'])
    root.mkdir()
    (root / 'state.json').write_text(json.dumps(dict(phase='running', stage='scoring', planned=3)))
    try:
        assert executions.get(task['id'], started['execution_id'])['run_state']['stage'] == 'scoring'
    finally:
        executions.cancel(task['id'], started['execution_id'], actor='alice', request_key='stop-live')


def test_cancel_stops_child_that_ignores_sigterm_and_keeps_terminal_receipt(tmp_path):
    repo, task, job = fixture(tmp_path)
    executions = JobExecutionStore(repo)
    ready = tmp_path / 'ready'
    code = f'import signal,time; from pathlib import Path; signal.signal(signal.SIGTERM, signal.SIG_IGN); Path({str(ready)!r}).write_text("ready"); time.sleep(30)'
    started = executions.run(task['id'], job['job_id'], 1,
        execution_spec=spec(tmp_path, code), actor='alice', request_key='ignore-term')
    process = executions._processes[started['execution_id']]
    deadline = time.monotonic() + 2
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert ready.exists()
    try:
        executions.cancel(task['id'], started['execution_id'], actor='alice', request_key='stop')
        deadline = time.monotonic() + 7
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert process.poll() is not None
        assert executions.get(task['id'], started['execution_id'])['state'] == 'cancelled'
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


@pytest.mark.parametrize('operation', ['cancel', 'timeout'])
def test_stopping_execution_kills_partition_descendant_after_leader_exits(tmp_path, operation):
    import os
    repo, task, job = fixture(tmp_path)
    executions = JobExecutionStore(repo)
    ready = tmp_path / 'descendant-ready'
    child = f'import signal,time,os; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path({str(ready)!r}).write_text(str(os.getpid())); time.sleep(30)'
    parent = f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child!r}]); time.sleep(30)'
    execution_spec = spec(tmp_path, parent)
    execution_spec['budget']['timeout_seconds'] = 1 if operation == 'timeout' else 30
    started = executions.run(task['id'], job['job_id'], 1, execution_spec=execution_spec,
                             actor='alice', request_key='descendant-' + operation)
    process = executions._processes[started['execution_id']]
    deadline = time.monotonic() + 2
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert ready.exists()
    pid = int(ready.read_text())
    def alive():
        try:
            return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
        except FileNotFoundError:
            return False
    try:
        if operation == 'cancel':
            executions.cancel(task['id'], started['execution_id'], actor='alice', request_key='stop-descendant')
        deadline = time.monotonic() + 8
        while alive() and time.monotonic() < deadline:
            time.sleep(.02)
        assert not alive()
        deadline = time.monotonic() + 1
        while not executions.get(task['id'], started['execution_id'])['terminal'] and time.monotonic() < deadline:
            time.sleep(.01)
        assert executions.get(task['id'], started['execution_id'])['state'] == ('timeout' if operation == 'timeout' else 'cancelled')
    finally:
        if alive():
            os.killpg(process.pid, 9)
