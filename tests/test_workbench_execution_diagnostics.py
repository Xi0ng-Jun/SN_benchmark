"""Failure evidence survives wrapper, dispatcher and Agent summary boundaries."""
import json
from pathlib import Path

import pytest

from rag_eval.workbench.job_execution import JobExecutionStore
from rag_eval.workbench.notebook_job import execute
from rag_eval.workbench.jobs_api import build_execution_spec
from rag_eval.workbench.run_views import RunEntry, RunViews
from test_workbench_job_execution import fixture, spec, wait_terminal
from test_workbench_notebook_job import prepared, settings


def payload_for(tmp_path):
    jobs, job, prep, args = prepared(tmp_path)
    spec = build_execution_spec(jobs, job['task_id'], job['job_id'], prep['preparation_id'],
        settings(tmp_path, args), request_key='diagnostics', budget={'timeout_seconds': 10})
    return json.loads(spec['argv'][-1])


def test_wrapper_keeps_preparing_failure_message_stage_and_traceback(tmp_path, monkeypatch):
    payload = payload_for(tmp_path)
    def fail(**kwargs):
        raise ValueError('partition expansion failed at https://internal.invalid api_key=private-value')
    monkeypatch.setattr('rag_eval.notebook_batch.plan_batch', fail)
    with pytest.raises(ValueError):
        execute(payload)
    state = json.loads((Path(payload['run_dir']) / 'state.json').read_text())
    assert state['failed_phase'] == 'preparing'
    assert 'partition expansion failed' in state['error_message']
    diagnostic = Path(state['diagnostic_path']).read_text()
    assert 'ValueError' in diagnostic and 'fail' in diagnostic
    assert 'internal.invalid' not in diagnostic and 'private-value' not in diagnostic


def test_partition_failure_is_projected_before_secondary_missing_manifest(tmp_path, monkeypatch):
    payload = payload_for(tmp_path)
    def failed_batch(jobs, **kwargs):
        return {'exit_code': 1, 'runs': [{'run_dir': j['run_dir'], 'exit_code': 2,
            'state': {'phase': 'failed', 'error_type': 'ConnectionError',
                      'error_message': 'embedding service unavailable', 'failed_phase': 'importing',
                      'diagnostic_path': str(tmp_path / 'partition.traceback.log')},
            'stderr_tail': 'child failed'} for j in jobs]}
    monkeypatch.setattr('rag_eval.notebook_batch.execute_batch', failed_batch)
    assert execute(payload) == 1
    state = json.loads((Path(payload['run_dir']) / 'state.json').read_text())
    assert state['error_message'] == 'embedding service unavailable'
    assert state['failed_phase'] == 'importing'
    assert state['error_type'] == 'ConnectionError'
    assert state['generation_failures'][0]['run_dir']
    assert state['scoring_failures'][0]['error_type'] == 'missing_manifest'


def test_scoring_failure_has_message_and_saved_stack(tmp_path, monkeypatch):
    payload = payload_for(tmp_path)
    private_gold = 'UNPUBLISHED GOLD ANSWER'
    def completed_batch(jobs, **kwargs):
        for job in jobs:
            root = Path(job['run_dir'])
            root.mkdir(parents=True)
            (root / 'manifest.json').write_text('{}')
        return {'exit_code': 0, 'runs': [{'run_dir': j['run_dir'], 'exit_code': 0} for j in jobs]}
    def score_failure(*args, **kwargs):
        raise ValueError('official scorer inputs mismatch: ' + private_gold)
    monkeypatch.setattr('rag_eval.notebook_bundle.load_bundle',
                        lambda *args: {'cases': [{'gold': {'answer': private_gold}}]})
    monkeypatch.setattr('rag_eval.notebook_batch.execute_batch', completed_batch)
    monkeypatch.setattr('rag_eval.benchmark_submission.export_sn_runs', score_failure)
    assert execute(payload) == 1
    state = json.loads((Path(payload['run_dir']) / 'state.json').read_text())
    assert 'official scorer inputs mismatch' in state['error_message']
    assert private_gold not in json.dumps(state)
    assert state['failed_phase'] == 'scoring'
    assert Path(state['diagnostic_path']).is_file()
    assert private_gold not in Path(state['diagnostic_path']).read_text()
    assert state['scoring_failures'][0]['diagnostic_path'] == state['diagnostic_path']
    views = RunViews([], execution_records=lambda: [dict(run_dir=payload['run_dir'],
        execution_id='failed-scoring', task_id='task', state='failed', terminal=True, run_state=state)])
    _, text, _ = views.task_runs_context('task')
    failure = json.loads(text)['runs'][0]['failure']
    assert failure['failed_phase'] == 'scoring'
    assert failure['diagnostic_path'] == state['diagnostic_path']


def test_spawn_failure_keeps_actionable_details_without_existing_run(tmp_path):
    repo, task, job = fixture(tmp_path)
    store = JobExecutionStore(repo)
    execution = spec(tmp_path)
    execution['argv'] = [str(tmp_path / 'missing-python')]
    result = store.run(task['id'], job['job_id'], 1, execution_spec=execution,
                       actor='test', request_key='missing-python')
    assert result['state'] == 'failed'
    assert result['run_state']['failed_phase'] == 'launching'
    assert 'missing-python' in result['run_state']['error_message']
    assert Path(result['run_state']['diagnostic_path']).is_file()


def test_agent_summary_contains_failure_reason_and_log_location(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    detail = {'error_type': 'RuntimeError', 'error_message': 'embedding service unavailable',
              'failed_phase': 'importing', 'diagnostic_path': str(run / 'diagnostic.log')}
    (run / 'state.json').write_text(json.dumps(dict(phase='failed', **detail)))
    views = RunViews([RunEntry('failed', run, task_id='task')])
    _, text, _ = views.task_runs_context('task')
    assert json.loads(text)['runs'][0]['failure'] == detail


@pytest.mark.parametrize('saved', ['[1]', '"invalid"', '1', 'null'])
def test_invalid_child_state_cannot_hide_process_exit(tmp_path, saved):
    repo, task, job = fixture(tmp_path)
    store = JobExecutionStore(repo)
    run = tmp_path / 'run'
    code = (f'from pathlib import Path; p=Path({str(run)!r}); p.mkdir(); '
            f'(p / "state.json").write_text({saved!r}); raise SystemExit(7)')
    started = store.run(task['id'], job['job_id'], 1, execution_spec=spec(tmp_path, code),
                        actor='test', request_key='bad-state')
    result = wait_terminal(store, task['id'], started['execution_id'])
    assert result['state'] == 'failed' and result['exit_code'] == 7
    assert result['run_state']['error_message'] == 'Execution process exited with code 7'


def test_full_stderr_is_redacted_before_extracting_tail(tmp_path):
    repo, task, job = fixture(tmp_path)
    store = JobExecutionStore(repo)
    stderr = ('Traceback (most recent call last):\n' +
              ('  File "runner.py", line 2, in work\n' * 400) +
              '    send("PRIVATE-CORPUS-CONTENT")\nRuntimeError: request failed\n')
    code = f'import sys; sys.stderr.write({stderr!r}); raise SystemExit(7)'
    started = store.run(task['id'], job['job_id'], 1, execution_spec=spec(tmp_path, code),
                        actor='test', request_key='long-error')
    result = wait_terminal(store, task['id'], started['execution_id'])
    assert result['state'] == 'failed'
    assert 'request failed' in result['run_state']['error_message']
    assert 'PRIVATE-CORPUS-CONTENT' not in json.dumps(result['run_state'])


def test_real_pre_manifest_failure_reaches_agent_summary(tmp_path):
    payload = payload_for(tmp_path)
    assert execute(payload) == 1  # Invalid captured startup files; never reaches a model.
    root = Path(payload['run_dir'])
    state = json.loads((root / 'state.json').read_text())
    record = dict(run_dir=str(root), execution_id='failed-startup', task_id='task',
                  state='failed', terminal=True, run_state=state)
    views = RunViews([], execution_records=lambda: [record])
    _, text, _ = views.task_runs_context('task')
    summary = json.loads(text)['runs'][0]
    assert summary['phase'] == 'failed'
    assert summary['failure']['error_message']
    assert Path(summary['failure']['diagnostic_path']).is_file()
