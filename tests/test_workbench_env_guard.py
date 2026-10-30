"""Executions keep the server's start-time model credentials; changes are recorded by name."""
import json
import os
import sys
from pathlib import Path

import pytest

from rag_eval.workbench import env_guard
from rag_eval.workbench.job_execution import JobExecutionStore
from test_workbench_job_execution import fixture, spec, wait_terminal
from test_workbench_supervisor_bookkeeping import history


@pytest.fixture
def guard(tmp_path, monkeypatch):
    monkeypatch.setenv('SYSTEM_TEST_API_KEY', 'boot-value')
    monkeypatch.setattr(env_guard, '_boot', None)
    log = tmp_path / 'env-guard.jsonl'
    env_guard.install(log)
    yield log
    monkeypatch.setattr(env_guard, '_log_path', None)


def lines(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_execution_gets_lost_credential_back_and_records_its_name(tmp_path, monkeypatch, guard):
    repo, task, job = fixture(tmp_path)
    monkeypatch.delenv('SYSTEM_TEST_API_KEY')
    run = tmp_path / 'run'
    code = (f'import os; from pathlib import Path; p=Path({str(run)!r}); p.mkdir(); '
            '(p / "seen").write_text(os.environ.get("SYSTEM_TEST_API_KEY", "")); '
            '(p / "state.json").write_text("{\\"phase\\": \\"finished\\", \\"terminal\\": true}")')
    store = JobExecutionStore(repo)
    started = store.run(task['id'], job['job_id'], expected_state_version=1,
                        execution_spec=spec(tmp_path, code), actor='alice', request_key='run')
    assert wait_terminal(store, task['id'], started['execution_id'])['state'] == 'finished'
    assert (run / 'seen').read_text() == 'boot-value'
    process = dict(history(repo, started['execution_id']))['job.execution_process']
    assert process['restored_env_keys'] == ['SYSTEM_TEST_API_KEY']
    assert 'boot-value' not in json.dumps(process)


def test_removal_is_logged_with_stack_and_heartbeat_reports_the_change(monkeypatch, guard):
    monkeypatch.delenv('SYSTEM_TEST_API_KEY')
    env_guard.check()
    env_guard.check()  # An unchanged state is reported once.
    events = lines(guard)
    removed = [e for e in events if e['event'] == 'os.unsetenv']
    assert removed and removed[0]['key'] == 'SYSTEM_TEST_API_KEY'
    assert any('test_removal_is_logged' in frame for frame in removed[0]['stack'])
    changed = [e for e in events if e['event'] == 'changed']
    assert len(changed) == 1 and changed[0]['missing'] == ['SYSTEM_TEST_API_KEY'] and changed[0]['stacks']
    assert 'boot-value' not in guard.read_text()


def test_presence_names_declared_credentials_without_values(tmp_path, monkeypatch):
    toml = tmp_path / 'model-services.toml'
    toml.write_text('[services.general]\napi_key_env = "SYSTEM_A_KEY"\n'
                    '[services.answer]\napi_key_env = "SYSTEM_B_KEY"\n')
    monkeypatch.setenv('SYSTEM_A_KEY', 'secret')
    monkeypatch.delenv('SYSTEM_B_KEY', raising=False)
    found = env_guard.presence(env_guard.credential_names(toml))
    assert found['required'] == ['SYSTEM_A_KEY', 'SYSTEM_B_KEY'] and found['missing'] == ['SYSTEM_B_KEY']
    assert found['pid'] == os.getpid() and found['chain'][0]['pid'] == os.getpid()
    assert 'secret' not in json.dumps(found)


def test_non_transient_bookkeeping_failure_still_ends_the_execution(tmp_path, monkeypatch):
    repo, task, job = fixture(tmp_path)
    store = JobExecutionStore(repo)
    def broken(*args, **kwargs):
        raise ValueError('unreadable record')
    monkeypatch.setattr(store, '_finish_exited', broken)
    started = store.run(task['id'], job['job_id'], expected_state_version=1,
                        execution_spec=spec(tmp_path), actor='alice', request_key='run')
    finished = wait_terminal(store, task['id'], started['execution_id'])
    assert finished['state'] == 'failed' and finished['error_type'] == 'finish_failed'
    assert finished['run_state']['error_type'] == 'ValueError'
    kinds = [kind for kind, _ in history(repo, started['execution_id'])]
    assert kinds[-2:] == ['execution.finish_failed', 'job.execution_finished']
