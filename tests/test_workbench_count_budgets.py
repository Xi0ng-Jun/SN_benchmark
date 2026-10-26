"""W8d count budgets and early price admission, using synthetic services only."""
import json
from pathlib import Path

import pytest

from rag_eval.workbench.budgets import BudgetLedger, validate_policy
from test_workbench_budgets import setup, step
from test_workbench_iterations import env, iteration  # noqa: F401
from test_workbench_clusters import frozen_cluster  # noqa: F401
from test_workbench_loop_services import adapter  # noqa: F401


def model_config(path, *, chat='company-chat', embedding='company-embedding', rerank='company-rerank'):
    path.write_text(f'''[services.chat]
kind = "chat"
model = "{chat}"
[services.embedding]
kind = "embedding"
model = "{embedding}"
[services.rerank]
kind = "rerank"
model = "{rerank}"
[services.unused]
kind = "chat"
model = "unbound-model"
[bindings]
ask_answer = "chat"
source_summary = "chat"
chunk_embedding = "embedding"
retrieval_rerank = "rerank"
''')
    return path


def test_count_only_and_mixed_policy_validation():
    for policy in (dict(max_case_runs=12, max_runs_per_candidate=4),
                   dict(max_case_runs=12, limit_cny=3, max_runs_per_candidate=4)):
        assert validate_policy(policy) == policy
    for value in (True, 0, -1, 1.5, None, '10'):
        with pytest.raises(ValueError):
            validate_policy(dict(max_case_runs=value, max_runs_per_candidate=4))
    with pytest.raises(ValueError):
        validate_policy(dict(max_runs_per_candidate=4))


@pytest.mark.parametrize('limits,reason', [
    ({'max_case_runs': 3}, 'max_case_runs'),
    ({'max_case_runs': 3, 'limit_cny': 100}, 'max_case_runs'),
    ({'max_case_runs': 100, 'limit_cny': 3}, 'limit_cny'),
])
def test_all_present_caps_apply_and_failed_retries_keep_case_counts(tmp_path, limits, reason):
    repo, tree = setup(tmp_path)
    tree['protocol']['budget'] = dict(limits, max_runs_per_candidate=10)
    ledger = BudgetLedger(repo)
    assert ledger.reserve_many(tree, [step(count=2), step('top-up', 'parent')], group_key='base')['status'] == 'reserved'
    ledger.attach(tree['id'], 'candidate', 'one', 'failed')
    ledger.settle_execution(dict(execution_id='failed', terminal=True, state='failed',
                                run_state=None, run_dir=str(tmp_path / 'absent')))
    denied = ledger.reserve_many(tree, [step('infra-retry', count=2)], group_key='retry')
    assert denied['status'] == 'budget_exceeded' and reason in denied['reasons']
    assert ledger.summary(tree['id'])['case_runs_used'] == 3
    admitted = ledger.reserve_many(tree, [step('infra-retry', count=2)], group_key='retry', override=True)
    assert admitted['summary']['case_runs_used'] == 5
    ledger.attach(tree['id'], 'candidate', 'infra-retry', 'finished')
    ledger.settle_execution(dict(execution_id='finished', terminal=True, state='finished',
        run_state=dict(cost_observation=dict(currency='CNY', complete=True, total_cny=0)),
        run_dir=str(tmp_path / 'absent')))
    assert BudgetLedger(repo).summary(tree['id'])['case_runs_used'] == 5
    assert ledger.reserve_many(tree, [step('infra-retry', count=2)], group_key='same-attempt')['status'] == 'reserved'
    assert ledger.summary(tree['id'])['case_runs_used'] == 5


@pytest.mark.parametrize('cap,expected', [(8, 'executed'), (7, 'pending')])
def test_cluster_auto_confirmation_obeys_case_run_cap(frozen_cluster, cap, expected):
    from rag_eval.workbench.action_requests import ActionRequestStore, auto_execute
    f = frozen_cluster
    service, repo, tree = f['service'], f['env']['repo'], f['tree']
    protocol = dict(tree['protocol'], budget=dict(max_case_runs=cap, max_runs_per_candidate=20))
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE iterations SET protocol_json=? WHERE id=?', (json.dumps(protocol), tree['id']))
    cid = f['candidate']['id']
    (Path(f['candidate']['worktree']['path']) / 'backend/app/__init__.py').write_text('VALUE = 3\n')
    service.freeze(cid, actor='test')
    store = ActionRequestStore(repo)
    request = store.request(tree['task_id'], 'node-cluster-run', cid, {'cluster_id': 'c01'},
                            reason='synthetic count admission', requested_by='agent:implementer')
    result = auto_execute(service, request, workspace_id=f['env']['workspace']['id'])
    assert result['status'] == expected
    summary = BudgetLedger(repo).summary(tree['id'])
    assert summary['case_runs_used'] == (8 if expected == 'executed' else 0)
    assert len(service.candidate(cid)['attempts']) == (2 if expected == 'executed' else 0)


def test_round_stops_at_case_run_exhaustion(adapter):
    from rag_eval.workbench.loop import RoundService
    f, ops = adapter['f'], adapter['ops']
    repo, tree = adapter['api'].repository, f['tree']
    tree['protocol']['budget'] = dict(max_case_runs=2, max_runs_per_candidate=10)
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE iterations SET protocol_json=? WHERE id=?', (json.dumps(tree['protocol']), tree['id']))
    BudgetLedger(repo).reserve_many(tree, [step(candidate=tree['root_candidate_id'], count=2)], group_key='used')
    assert ops.describe(tree['id'])['budget_exhausted'] is True
    engine = RoundService(repo, ops)
    state = engine.start(tree['id'], actor='alice', request_key='count-stop', rounds=2)
    state = engine.tick(state['id'])
    assert state['status'] == 'completed'
    assert state['stop_reason'] == 'budget_exhausted'
    assert not adapter['creation_bridge'].commands


def test_creation_rejects_unpriced_cny_but_allows_count_only(env):
    path = Path(env['service'].execution_config['model_config'])
    model_config(path)
    with pytest.raises(ValueError, match='company-chat.*company-embedding.*company-rerank.*max_case_runs'):
        iteration(env, budget=dict(limit_cny=100, max_runs_per_candidate=10))
    assert env['service'].iterations() == []
    tree = iteration(env, budget=dict(max_case_runs=100, max_runs_per_candidate=10))
    assert tree['protocol']['budget']['max_case_runs'] == 100


def test_priced_creation_still_works(env):
    model_config(Path(env['service'].execution_config['model_config']), chat='qwen-flash',
                 embedding='text-embedding-v4', rerank='qwen3-rerank')
    tree = iteration(env, budget=dict(limit_cny=100, max_runs_per_candidate=10))
    assert tree['protocol']['budget']['limit_cny'] == 100


@pytest.mark.parametrize('contents', [None, 'not toml!', '', '[bindings]\nask_answer="missing"'])
def test_cny_creation_refuses_unreadable_or_unresolvable_models(env, contents):
    path = Path(env['service'].execution_config['model_config'])
    if contents is None:
        path.unlink()
    else:
        path.write_text(contents)
    with pytest.raises(ValueError, match='model config'):
        iteration(env, budget=dict(limit_cny=100, max_runs_per_candidate=10))
    assert env['service'].iterations() == []


def test_direct_request_rejects_unpriced_before_reserving_or_preparing(env):
    tree = iteration(env, budget=dict(max_case_runs=100, max_runs_per_candidate=10))
    model_config(Path(env['service'].execution_config['model_config']))
    with pytest.raises(ValueError, match='company-chat.*max_case_runs'):
        env['service'].run(tree['root_candidate_id'], budget=dict(timeout_seconds=60, limit_cny=1, confirmed=True),
                           actor='alice', request_key='unpriced')
    assert not env['service'].candidate(tree['root_candidate_id'])['attempts']
    assert BudgetLedger(env['repo']).summary(tree['id'])['case_runs_used'] == 0


def test_job_request_rejects_unpriced_model_before_execution(tmp_path):
    from rag_eval.workbench.jobs_api import build_execution_spec
    from test_workbench_notebook_job import prepared, settings
    jobs, job, prep, args = prepared(tmp_path)
    config = settings(tmp_path, args)
    model_config(Path(config['model_config']))
    with pytest.raises(ValueError, match='company-chat.*max_case_runs'):
        build_execution_spec(jobs, job['task_id'], job['job_id'], prep['preparation_id'], config,
                             request_key='unpriced', budget=dict(timeout_seconds=60, limit_cny=1, confirmed=True))
    assert not (tmp_path / 'executions').exists()


@pytest.mark.parametrize('verb', ['node-run', 'node-cluster-run', 'node-fresh-run', 'iteration-sealed-run', 'iteration-create'])
def test_action_request_price_admission_precedes_persistence(env, verb):
    from rag_eval.workbench.action_requests import ActionRequestStore
    tree = iteration(env)
    config = env['service'].execution_config
    model_config(Path(config['model_config']))
    store = ActionRequestStore(env['repo'], execution_config=config)
    params = dict(timeout_seconds=60, limit_cny=1)
    target = tree['root_candidate_id']
    if verb == 'node-cluster-run':
        params['cluster_id'] = 'c01'
    if verb == 'iteration-create':
        target = None
        params = {k: v for k, v in env['create'].items() if k not in ('actor', 'request_key')}
        params['budget'] = dict(limit_cny=100, max_runs_per_candidate=10)
    with pytest.raises(ValueError, match='company-chat.*max_case_runs'):
        store.request(tree['task_id'], verb, target, params, reason='test price admission', requested_by='agent:analyst')
    assert store.list(task_id=tree['task_id']) == []


@pytest.mark.parametrize('kind', ['chat', 'embedding', 'rerank'])
def test_every_bound_model_kind_is_checked_without_guessing_from_display_labels(tmp_path, kind):
    from rag_eval.model_budget import check_priced_models
    names = dict(chat='qwen-flash', embedding='text-embedding-v4', rerank='qwen3-rerank')
    names[kind] = 'company-' + kind
    with pytest.raises(ValueError, match='company-' + kind):
        check_priced_models(model_config(tmp_path / 'models.toml', **names))


def test_pricing_errors_do_not_echo_private_file_contents(tmp_path):
    from rag_eval.model_budget import check_priced_models
    path = tmp_path / 'private-models.toml'
    path.write_text('api_key = "sk-private-key-not-for-output"\n[broken')
    with pytest.raises(ValueError) as error:
        check_priced_models(path)
    assert 'model config' in str(error.value)
    assert 'sk-private' not in str(error.value) and str(path) not in str(error.value)


def test_http_run_returns_actionable_price_error_without_dispatch(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from rag_eval.workbench.jobs_api import JobAPI
    from test_workbench_notebook_job import prepared, settings
    jobs, job, prep, args = prepared(tmp_path)
    config = settings(tmp_path, args)
    model_config(Path(config['model_config']))
    api = JobAPI(jobs.repository, job['workspace_id'], actor='alice', execution_config=config)
    monkeypatch.setattr(api.execution, '_start', lambda _: pytest.fail('must not dispatch'))
    app = FastAPI()
    app.include_router(api.router())
    with TestClient(app) as client:
        response = client.post(f"/api/tasks/{job['task_id']}/jobs/{job['job_id']}/run", json=dict(
            preparation_id=prep['preparation_id'], expected_state_version=job['state_version'],
            request_key='unpriced', budget=dict(timeout_seconds=60, limit_cny=1, confirmed=True)))
    assert response.status_code == 422
    assert response.json()['code'] == 'MODEL_PRICING_REJECTED'
    assert 'company-chat' in response.json()['error'] and 'max_case_runs' in response.json()['error']
    assert not (tmp_path / 'executions').exists()


def test_round_retry_charges_new_attempts_without_per_run_cny_guard(env):
    from test_workbench_loop_measurements import ports, finish
    tree = iteration(env, budget=dict(max_case_runs=4, max_runs_per_candidate=10))
    root = tree['root_candidate_id']
    model_config(Path(env['service'].execution_config['model_config']))
    assert ports(env).measure(root, key='retry-count', actor='alice')['status'] == 'running'
    ledger = BudgetLedger(env['repo'])
    assert ledger.summary(tree['id'])['case_runs_used'] == 2
    finish(env, env['service'].candidate(root))
    assert ports(env).measure(root, key='retry-count', actor='alice')['reason'] == 'Infrastructure retry planned'
    assert ports(env).measure(root, key='retry-count', actor='alice')['status'] == 'running'
    assert ledger.summary(tree['id'])['case_runs_used'] == 4
    attempts = env['service'].candidate(root)['attempts']
    for attempt in attempts:
        record = env['executions'].get(tree['task_id'], attempt['execution_id'])
        assert set(record['budget']) == {'timeout_seconds'}
    finish(env, env['service'].candidate(root))
    assert ports(env).measure(root, key='retry-count', actor='alice')['status'] == 'complete'
    assert ledger.summary(tree['id'])['case_runs_used'] == 4
    from test_workbench_loop_measurements import child
    candidate = child(env, tree, 'next-candidate')
    assert ports(env).measure(candidate, key='next-run', actor='alice')['status'] == 'budget_exhausted'


def test_count_budget_views_are_additive_and_read_only(env):
    from rag_eval.workbench.ledger import aggregate_ledger
    tree = iteration(env, budget=dict(max_case_runs=10, max_runs_per_candidate=4))
    assert tree['budget_usage']['case_runs_used'] == 0
    assert tree['budget_usage']['policy']['max_case_runs'] == 10
    ledger = BudgetLedger(env['repo'])
    ledger.reserve_many(tree, [step(candidate=tree['root_candidate_id'], count=2)], group_key='view')
    # Brief resolution calls this reader with an already-open writer.
    with env['repo'].connect(write=True):
        shown = env['service'].iteration(tree['id'])
    assert shown['budget_usage']['case_runs_used'] == 2
    report = aggregate_ledger(shown, [], [], [])
    assert set(report) == {'sampling_ledger', 'overfit_ledger', 'structure_ledger', 'sealed_score'}
    assert report['sampling_ledger']['budget_usage']['case_runs_used'] == 2


def test_count_only_concurrent_admission_and_candidate_cap(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    repo, tree = setup(tmp_path)
    tree['protocol']['budget'] = dict(max_case_runs=3, max_runs_per_candidate=1)
    ledger = BudgetLedger(repo)
    barrier = Barrier(2)
    def reserve(key):
        barrier.wait()
        return ledger.reserve_many(tree, [step(key, candidate=key, count=2)], group_key=key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, ['a', 'b']))
    assert sorted(r['status'] for r in results) == ['budget_exceeded', 'reserved']
    assert ledger.summary(tree['id'])['case_runs_used'] == 2
    winner = next(r['reservations'][0]['candidate_id'] for r in results if r['status'] == 'reserved')
    denied = ledger.reserve_many(tree, [step('extra', candidate=winner)], group_key='extra')
    assert denied['reasons'] == ['max_runs_per_candidate']


def test_summary_reports_frozen_count_cap_before_first_reservation(env):
    tree = iteration(env, budget=dict(max_case_runs=10, max_runs_per_candidate=4))
    summary = BudgetLedger(env['repo']).summary(tree['id'])
    assert summary['case_runs_used'] == 0
    assert summary['policy'] == tree['protocol']['budget']


@pytest.mark.parametrize('cap,expected', [(4, 'running'), (3, 'budget_exhausted')])
def test_fresh_parent_and_child_share_count_cap(env, cap, expected):
    from test_workbench_loop_measurements import ports, child
    tree = iteration(env, fresh_batch_count=1, fresh_papers_per_batch=1,
                     budget=dict(max_case_runs=cap, max_runs_per_candidate=10))
    candidate = child(env, tree, 'fresh-child')
    result = ports(env).fresh(candidate, key='fresh-count', actor='alice')
    assert result['status'] == expected
    summary = BudgetLedger(env['repo']).summary(tree['id'])
    assert summary['case_runs_used'] == (4 if expected == 'running' else 0)
    assert summary['runs_by_candidate'] == ({tree['root_candidate_id']: 2, candidate: 2} if expected == 'running' else {})


def test_job_cli_reports_unpriced_models_as_request_error(tmp_path, monkeypatch, capsys):
    from rag_eval.workbench import cli
    from test_workbench_notebook_job import prepared, settings
    jobs, job, prep, args = prepared(tmp_path)
    config = settings(tmp_path, args)
    model_config(Path(config['model_config']))
    monkeypatch.setattr(cli, 'load_config', lambda _: dict(job_execution=config))
    budget = tmp_path / 'budget.json'
    budget.write_text(json.dumps(dict(timeout_seconds=60, limit_cny=1, confirmed=True)))
    import sys
    monkeypatch.setattr(sys, 'argv', ['sn-workbench', '--state', str(jobs.repository.root), 'job-run', '--task', job['task_id'],
                  '--job', job['job_id'], '--preparation-id', prep['preparation_id'],
                  '--state-version', str(job['state_version']), '--request-key', 'unpriced',
                  '--budget', str(budget), '--config', str(tmp_path / 'unused.json'), '--actor', 'alice'])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    result = json.loads(capsys.readouterr().err)
    assert result['code'] == 'MODEL_PRICING_REJECTED'
    assert 'company-chat' in result['error'] and 'max_case_runs' in result['error']


def test_request_only_cli_checks_config_without_enabling_execution(env):
    from types import SimpleNamespace
    from rag_eval.workbench.agent_tools import IterationTools
    tree = iteration(env)
    views = SimpleNamespace(_worktree_service=env['service'].trees,
                            _command_config=dict(job_execution=env['service'].execution_config))
    tools = IterationTools(env['repo'], views, tree['task_id'], 'analyst')
    options = SimpleNamespace(attempts=None, timeout=60, limit_cny=1, reason='priced pending request')
    result = tools.request('node run', [tree['root_candidate_id']], options)
    assert result['request']['status'] == 'pending'
    assert tools.service.jobs is None and tools.service.build_spec is None
    model_config(Path(env['service'].execution_config['model_config']))
    with pytest.raises(ValueError, match='company-chat.*max_case_runs'):
        tools.request('node run', [tree['root_candidate_id']], options)
    assert len(tools.requests.list(task_id=tree['task_id'])) == 1
