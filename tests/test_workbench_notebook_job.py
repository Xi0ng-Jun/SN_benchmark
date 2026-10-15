import json
from pathlib import Path
import sys
import pytest

from rag_eval.workbench.jobs_api import build_execution_spec
from rag_eval.workbench.job_execution import ExecutionRejected
from test_workbench_job_preparation_records import environment


def prepared(tmp_path):
    repo, jobs, job, args = environment(tmp_path)
    record = jobs.capture_inputs_source(**args)
    return jobs, job, record['preparation'], args


def settings(tmp_path, args):
    for name in ('model.toml', 'requirements.txt', 'expectation.json'):
        (tmp_path / name).write_text('{}')
    return dict(mode='notebook', output_root=str(tmp_path / 'executions'),
        artifact_root=str(args['artifact_store'].root), python=sys.executable,
        project_root=str(tmp_path / 'sn'), model_config=str(tmp_path / 'model.toml'),
        dependency_requirements=str(tmp_path / 'requirements.txt'),
        runtime_expectation=str(tmp_path / 'expectation.json'),
        scorer_sources=str(tmp_path / 'scorers'), max_limit_cny=10,
        model_names=['qwen-turbo', 'text-embedding-v4', 'qwen3-rerank'])


@pytest.mark.parametrize('budget', [dict(timeout_seconds=600),
    dict(confirmed=True, limit_cny=1, timeout_seconds=600)])
def test_notebook_spec_binds_fixed_selection_source_and_budget_without_writes(tmp_path, budget):
    jobs, job, prep, args = prepared(tmp_path)
    config = settings(tmp_path, args)
    spec = build_execution_spec(jobs, job['task_id'], job['job_id'], prep['preparation_id'],
                                config, request_key='run', budget=budget)
    payload = json.loads(spec['argv'][-1])
    assert payload['selection'] == prep['input']['selection']
    assert payload['sn_ref'] == prep['source']['artifact_ref']
    assert payload['bundle_ref'] == prep['input']['bundle_ref']
    assert payload['budget'] == budget
    budget['timeout_seconds'] = 1
    assert spec['budget']['timeout_seconds'] == 600
    assert not Path(spec['run_dir']).exists()
    assert '--payload' in spec['argv']


@pytest.mark.parametrize('budget', [dict(confirmed=False, limit_cny=1, timeout_seconds=600),
    dict(limit_cny=1, timeout_seconds=600),
    dict(confirmed=True, limit_cny=None, timeout_seconds=600),
    dict(confirmed=True, limit_cny=0, timeout_seconds=600),
    dict(confirmed=True, limit_cny=float('nan'), timeout_seconds=600),
    dict(confirmed=True, limit_cny=float('inf'), timeout_seconds=600),
    dict(confirmed=True, limit_cny=11, timeout_seconds=600),
    dict(confirmed=True, limit_cny=True, timeout_seconds=600),
    dict(confirmed=True, limit_cny=1, timeout_seconds=0),
    {}, dict(timeout_seconds=0), dict(timeout_seconds=True),
    dict(timeout_seconds='600'), dict(timeout_seconds=86401)])
def test_notebook_budget_rejects_before_creating_execution(tmp_path, budget):
    jobs, job, prep, args = prepared(tmp_path)
    with pytest.raises(ExecutionRejected):
        build_execution_spec(jobs, job['task_id'], job['job_id'], prep['preparation_id'],
                             settings(tmp_path, args), request_key='run', budget=budget)
    assert not (tmp_path / 'executions').exists()


def test_notebook_spec_rejects_unknown_preparation(tmp_path):
    jobs, job, prep, args = prepared(tmp_path)
    with pytest.raises(ExecutionRejected):
        build_execution_spec(jobs, job['task_id'], job['job_id'], '0' * 64,
                             settings(tmp_path, args), request_key='run',
                             budget=dict(confirmed=True, limit_cny=1, timeout_seconds=600))


@pytest.mark.parametrize('budget', [dict(timeout_seconds=10),
    dict(confirmed=True, limit_cny=1, timeout_seconds=10)])
def test_notebook_execution_retains_real_child_failure_without_model_calls(tmp_path, budget):
    from rag_eval.workbench.notebook_job import execute
    jobs, job, prep, args = prepared(tmp_path)
    config = settings(tmp_path, args)
    spec = build_execution_spec(jobs, job['task_id'], job['job_id'], prep['preparation_id'],
        config, request_key='failed-child', budget=budget)
    payload = json.loads(spec['argv'][-1])
    assert execute(payload) == 1
    root = Path(spec['run_dir'])
    assert json.loads((root / 'state.json').read_text())['phase'] == 'finished_with_errors'
    report = json.loads((root / 'batch-report.json').read_text())
    assert len(report['runs']) == len(prep['input']['selection']['partition_ids'])
    assert all(row['exit_code'] != 0 for row in report['runs'])
    plan = json.loads((root / 'batch-plan.json').read_text())
    ledger = root / 'invocation/budget.json'
    if 'limit_cny' in budget:
        assert json.loads(ledger.read_text())['requests'] == {}
        assert all(row['argv'][row['argv'].index('--budget-ledger') + 1] == str(ledger)
                   for row in plan)
    else:
        assert not ledger.exists()
        assert all('--budget-ledger' not in row['argv'] for row in plan)


def test_notebook_spec_rejects_output_inside_source_before_any_writes(tmp_path):
    jobs, job, prep, args = prepared(tmp_path)
    config = settings(tmp_path, args)
    config['output_root'] = str(Path(config['artifact_root']) / 'bad-output')
    with pytest.raises(ExecutionRejected):
        build_execution_spec(jobs, job['task_id'], job['job_id'], prep['preparation_id'],
            config, request_key='bad-output', budget=dict(confirmed=True, limit_cny=1, timeout_seconds=10))
    assert not Path(config['output_root']).exists()


def test_frozen_store_bundle_reaches_runner_initialization_without_model_calls(tmp_path, monkeypatch):
    from rag_eval.notebook_runner import execute
    from rag_eval import runtime_environment
    jobs, job, prep, args = prepared(tmp_path)
    config = settings(tmp_path, args)
    spec = build_execution_spec(jobs, job['task_id'], job['job_id'], prep['preparation_id'],
        config, request_key='initialization', budget=dict(confirmed=True, limit_cny=1, timeout_seconds=10))
    payload = json.loads(spec['argv'][-1])
    run = tmp_path / 'runner'
    class InitializationReached(Exception):
        pass
    def stop_before_runtime_or_models(*a, **kw):
        raise InitializationReached()
    monkeypatch.setattr(runtime_environment, 'configure_environment', stop_before_runtime_or_models)
    with pytest.raises(InitializationReached):
        execute(root=Path(__file__).resolve().parents[1], project=config['project_root'],
            bundle_dir=payload['bundle'], run=run, mode='chunk',
            partition_id=payload['selection']['partition_ids'][0],
            request_revision='notebook-request-v3', case_ids=payload['selection']['case_ids'],
            artifact_root=payload['artifact_root'], artifact_index_id=payload['index_ref']['id'],
            sn_source_ref=payload['sn_ref'])
    assert (run / 'artifact-refs.json').exists()
    assert json.loads((run / 'state.json').read_text())['phase'] == 'failed'


def test_store_overlap_with_uninstalled_input_is_still_rejected_before_run_creation(tmp_path):
    from rag_eval.notebook_runner import execute
    jobs, job, prep, args = prepared(tmp_path)
    root = tmp_path / 'runner'
    with pytest.raises(ValueError):
        execute(root=Path(__file__).resolve().parents[1], project=tmp_path / 'sn',
            bundle_dir=tmp_path / 'data/bundle', run=root, mode='chunk',
            partition_id=prep['input']['selection']['partition_ids'][0],
            artifact_root=tmp_path, artifact_index_id=prep['input']['index_ref']['id'])
    assert not root.exists()


def test_matching_bundle_copy_inside_store_is_not_treated_as_owned_object(tmp_path):
    import shutil
    from rag_eval.notebook_runner import execute
    jobs, job, prep, args = prepared(tmp_path)
    unowned = args['artifact_store'].root / 'unowned-input'
    shutil.copytree(tmp_path / 'data/bundle', unowned)
    run = tmp_path / 'runner'
    with pytest.raises(ValueError, match='verified installed bundle object'):
        execute(root=Path(__file__).resolve().parents[1], project=tmp_path / 'sn',
            bundle_dir=unowned, run=run, mode='chunk',
            partition_id=prep['input']['selection']['partition_ids'][0],
            artifact_root=args['artifact_store'].root, artifact_index_id=prep['input']['index_ref']['id'])
    assert not run.exists()
