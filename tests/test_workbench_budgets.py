"""Budget accounting with synthetic observations and durable SQLite state."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
from threading import Barrier

import pytest

from rag_eval.workbench.repository import Conflict, WorkbenchRepository


def module():
    return importlib.import_module('rag_eval.workbench.budgets')


def setup(tmp_path, *, limit=5, maximum=4):
    repo = WorkbenchRepository(tmp_path / 'state')
    workspace = repo.register_workspace('synthetic', tmp_path / 'project')
    task = repo.create_task(workspace['id'], 'budget', '', request_key='task')
    return repo, dict(id='iteration', task_id=task['id'], protocol=dict(
        budget=dict(limit_cny=limit, max_runs_per_candidate=maximum)))


def step(key='one', candidate='candidate', count=1):
    return dict(candidate_id=candidate, request_key=key, case_count=count)


def test_frozen_policy_rejects_invalid_values_and_cannot_be_changed(tmp_path):
    m = module()
    for value in ({}, {'limit_cny': True, 'max_runs_per_candidate': 2},
                  {'limit_cny': 1, 'max_runs_per_candidate': 0},
                  {'limit_cny': float('nan'), 'max_runs_per_candidate': 2},
                  {'limit_cny': 1, 'max_runs_per_candidate': 2, 'extra': 3}):
        with pytest.raises(ValueError):
            m.validate_policy(value)
    assert m.validate_policy(None) is None
    repo, iteration = setup(tmp_path)
    ledger = m.BudgetLedger(repo)
    ledger.reserve_many(iteration, [step()], group_key='group')
    iteration['protocol']['budget']['limit_cny'] = 100
    with pytest.raises(Conflict, match='policy'):
        ledger.reserve_many(iteration, [step('two')], group_key='group2')


def test_group_denied_without_partial_reservations_and_receipts_are_idempotent(tmp_path):
    m = module()
    repo, iteration = setup(tmp_path, limit=2)
    ledger = m.BudgetLedger(repo)
    denied = ledger.reserve_many(iteration, [step('child', count=2), step('parent', 'parent')], group_key='too-big')
    assert denied['status'] == 'budget_exceeded'
    assert ledger.summary('iteration')['reserved_cny'] == 0
    first = ledger.reserve_many(iteration, [step('child'), step('parent', 'parent')], group_key='fits')
    assert first['status'] == 'reserved'
    assert ledger.reserve_many(iteration, [step('child'), step('parent', 'parent')], group_key='fits') == first
    # A direct run hook consumes the same reservation rather than charging again.
    assert ledger.reserve_many(iteration, [step('child')], group_key='direct')['status'] == 'reserved'
    summary = m.BudgetLedger(repo).summary('iteration')
    assert summary['charged_cny'] == 2
    assert summary['runs_by_candidate'] == {'candidate': 1, 'parent': 1}
    with pytest.raises(Conflict):
        ledger.reserve_many(iteration, [step('child', count=2)], group_key='different')


def test_concurrent_groups_cannot_oversubscribe(tmp_path):
    m = module()
    repo, iteration = setup(tmp_path, limit=1)
    m.BudgetLedger(repo)
    barrier = Barrier(2)
    def reserve(key):
        ledger = m.BudgetLedger(repo, initialize=False)
        barrier.wait()
        return ledger.reserve_many(iteration, [step(key)], group_key=key)['status']
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, ['a', 'b']))
    assert sorted(results) == ['budget_exceeded', 'reserved']
    assert m.BudgetLedger(repo).summary('iteration')['charged_cny'] == 1


def test_terminal_unknown_retains_reservation_and_actual_overrun_blocks_next(tmp_path):
    m = module()
    repo, iteration = setup(tmp_path, limit=3)
    ledger = m.BudgetLedger(repo)
    ledger.reserve_many(iteration, [step('one')], group_key='one')
    ledger.attach('iteration', 'candidate', 'one', 'execution1')
    record = dict(execution_id='execution1', terminal=True, state='failed', run_state=None,
                  run_dir=str(tmp_path / 'missing'))
    ledger.settle_execution(record)
    summary = ledger.summary('iteration')
    assert summary['charged_cny'] == 1
    assert summary['unknown_count'] == 1
    assert summary['actual_cny'] is None
    # A later complete receipt may reconcile an unknown cost, even for a failed run.
    record['run_state'] = dict(cost_observation=dict(currency='CNY', complete=True, total_cny=4))
    ledger.settle_execution(record)
    ledger.settle_execution(record)
    assert ledger.summary('iteration')['actual_cny'] == 4
    assert ledger.reserve_many(iteration, [step('two')], group_key='two')['status'] == 'budget_exceeded'


def test_complete_cost_settles_and_drives_weighted_case_estimate(tmp_path):
    m = module()
    repo, iteration = setup(tmp_path, limit=10)
    ledger = m.BudgetLedger(repo)
    ledger.reserve_many(iteration, [step('old', count=2)], group_key='old')
    ledger.attach('iteration', 'candidate', 'old', 'execution')
    ledger.settle_execution(dict(execution_id='execution', terminal=True, state='finished',
        run_state=dict(cost_observation=dict(currency='CNY', complete=True, total_cny=0.4)),
        run_dir=str(tmp_path / 'absent')))
    result = ledger.reserve_many(iteration, [step('new', count=3)], group_key='new')
    assert result['reservations'][0]['reserved_cny'] == pytest.approx(0.6)
    assert result['reservations'][0]['estimate_source'] == 'historical_observed_per_case'
    assert result['summary']['charged_cny'] == pytest.approx(1)


def test_run_count_includes_failed_unknown_parent_and_explicit_retry(tmp_path):
    m = module()
    repo, iteration = setup(tmp_path, limit=100, maximum=1)
    ledger = m.BudgetLedger(repo)
    ledger.reserve_many(iteration, [step('first'), step('parent', 'parent')], group_key='first')
    ledger.attach('iteration', 'candidate', 'first', 'failed')
    ledger.settle_execution(dict(execution_id='failed', terminal=True, state='failed',
                                 run_state=None, run_dir=str(tmp_path / 'none')))
    assert ledger.reserve_many(iteration, [step('retry')], group_key='retry')['status'] == 'budget_exceeded'
    # The override is an explicit trusted service argument, never an actor-name test.
    assert ledger.reserve_many(iteration, [step('retry')], group_key='retry', override=True)['status'] == 'reserved'
    assert ledger.summary('iteration')['runs_by_candidate'] == {'candidate': 2, 'parent': 1}


def test_model_request_ledger_partial_usage_never_becomes_complete_zero(tmp_path):
    m = module()
    run = tmp_path / 'run'
    (run / 'invocation').mkdir(parents=True)
    path = run / 'invocation/budget.json'
    path.write_text(json.dumps(dict(format='sn-model-budget-v1', requests={
        'a': dict(status='observed', cost_cny=0.2), 'b': dict(status='reserved', cost_cny=0.5)})))
    observation = m.execution_cost(dict(run_dir=str(run), run_state=None))
    assert observation == dict(complete=False, total_cny=0.2, source='model_request_ledger')
    path.write_text(json.dumps(dict(format='sn-model-budget-v1', requests={})))
    assert m.execution_cost(dict(run_dir=str(run), run_state=None)) is None


def test_empty_footprint_can_be_confirmed_without_creating_an_attempt(tmp_path):
    m = module()
    repo, iteration = setup(tmp_path)
    result = m.BudgetLedger(repo).reserve_many(iteration, [], group_key='freeze')
    assert result['status'] == 'reserved'
    assert result['reservations'] == []
    assert result['summary']['runs_by_candidate'] == {}


@pytest.mark.parametrize('operation', ['finish', 'cancel', 'terminal_before_attach'])
def test_execution_terminal_hook_and_attach_race_settle_durably(tmp_path, monkeypatch, operation):
    m = module()
    from test_workbench_job_execution import fixture, spec
    from rag_eval.workbench.job_execution import JobExecutionStore
    repo, task, job = fixture(tmp_path)
    iteration = dict(id='iteration', task_id=task['id'], protocol=dict(
        budget=dict(limit_cny=10, max_runs_per_candidate=4)))
    ledger = m.BudgetLedger(repo)
    ledger.reserve_many(iteration, [step()], group_key='group')
    executions = JobExecutionStore(repo)
    monkeypatch.setattr(executions, '_start', lambda record: None)
    record = executions.run(task['id'], job['job_id'], 1, execution_spec=spec(tmp_path),
                            actor='alice', request_key='launch')
    if operation != 'terminal_before_attach':
        ledger.attach('iteration', 'candidate', 'one', record['execution_id'])
    if operation == 'cancel':
        executions.cancel(task['id'], record['execution_id'], actor='alice', request_key='cancel')
        assert ledger.summary('iteration')['unknown_count'] == 1
    else:
        executions._finish(record['execution_id'], state='finished', exit_code=0, error_type=None,
            run_state=dict(cost_observation=dict(currency='CNY', complete=True, total_cny=0.3)))
        if operation == 'terminal_before_attach':
            ledger.attach('iteration', 'candidate', 'one', record['execution_id'])
        assert ledger.summary('iteration')['actual_cny'] == 0.3
    with pytest.raises(Conflict):
        ledger.attach('iteration', 'candidate', 'one', 'another-execution')


def test_trusted_group_override_survives_direct_run_reservation_hook(tmp_path):
    m = module()
    repo, iteration = setup(tmp_path, limit=1, maximum=1)
    ledger = m.BudgetLedger(repo)
    ledger.reserve_many(iteration, [step('one'), step('two')], group_key='human', override=True)
    direct = ledger.reserve_many(iteration, [step('two')], group_key='direct')
    assert direct['status'] == 'reserved'
    assert direct['summary']['charged_cny'] == 2


def test_unknown_reconciliation_does_not_erase_previously_observed_spend(tmp_path):
    m = module()
    repo, iteration = setup(tmp_path)
    ledger = m.BudgetLedger(repo)
    ledger.reserve_many(iteration, [step()], group_key='one')
    ledger.attach('iteration', 'candidate', 'one', 'execution')
    record = dict(execution_id='execution', terminal=True, state='failed', run_dir=str(tmp_path / 'absent'),
                  run_state=dict(cost_observation=dict(currency='CNY', complete=False, total_cny=3)))
    ledger.settle_execution(record)
    record['run_state'] = None
    ledger.settle_execution(record)
    assert ledger.summary('iteration')['charged_cny'] == 3
    assert ledger.summary('iteration')['observed_cny'] == 3


def test_restart_recovers_terminal_receipt_if_supervisor_died_before_settlement(tmp_path):
    m = module()
    repo, iteration = setup(tmp_path)
    ledger = m.BudgetLedger(repo)
    ledger.reserve_many(iteration, [step()], group_key='one')
    ledger.attach('iteration', 'candidate', 'one', 'execution')
    record = dict(execution_id='execution', terminal=True, state='failed', run_dir=str(tmp_path / 'none'),
                  run_state=dict(cost_observation=dict(currency='CNY', complete=True, total_cny=0.2)))
    with repo.connect(write=True) as conn:
        repo._event(conn, iteration['task_id'], 'job.execution_finished', 'execution', record)
    assert m.BudgetLedger(repo).summary('iteration')['charged_cny'] == pytest.approx(0.2)


def test_settlement_io_failure_does_not_interrupt_execution_finalization(tmp_path, monkeypatch):
    m = module()
    from test_workbench_job_execution import fixture, spec
    from rag_eval.workbench.job_execution import JobExecutionStore
    repo, task, job = fixture(tmp_path)
    executions = JobExecutionStore(repo)
    monkeypatch.setattr(executions, '_start', lambda record: None)
    record = executions.run(task['id'], job['job_id'], 1, execution_spec=spec(tmp_path),
                            actor='alice', request_key='launch')
    def unavailable(self, record):
        raise OSError('synthetic unavailable budget store')
    monkeypatch.setattr(m.BudgetLedger, 'settle_execution', unavailable)
    executions.cancel(task['id'], record['execution_id'], actor='alice', request_key='cancel')
    saved = json.loads((tmp_path / 'run/execution-record.json').read_text())
    assert saved['state'] == 'cancelled'


def fresh_service(tmp_path, *, limit=10):
    """Synthetic launch boundary; real dispatch plans, action state and SQLite claims."""
    from types import SimpleNamespace
    from rag_eval.workbench.execution_scopes import initialize
    from rag_eval.workbench.splits import initialize as initialize_splits
    repo, tree = setup(tmp_path, limit=limit, maximum=20)
    tree['protocol'].update(k_base=2, splits=dict(search=['search'], validation=[]),
        fresh_batches=[dict(batch_id='f1', case_ids=['fresh1']), dict(batch_id='f2', case_ids=['fresh2'])])
    initialize(repo)
    initialize_splits(repo)
    def candidate(cid):
        return dict(id=cid, iteration_id=tree['id'], parent_candidate_id='parent' if cid == 'child' else None,
                    status='frozen', attempts=[])
    service = SimpleNamespace(repository=repo, jobs=object(), build_spec=object(), candidate=candidate,
                              iteration=lambda _: tree, run=lambda *args, **kwargs: None)
    return service, tree, repo.task(tree['task_id'])['workspace_id']


def test_stale_fresh_plan_does_not_reserve_or_consume_the_next_batch(tmp_path):
    from rag_eval.workbench.action_requests import ActionRequestStore, auto_execute, execute
    from rag_eval.workbench.execution_scopes import dispatch_steps
    from rag_eval.workbench.splits import batches
    service, tree, workspace_id = fresh_service(tmp_path)
    requests = ActionRequestStore(service.repository)
    a, b = [requests.request(tree['task_id'], 'node-fresh-run', 'child', dict(timeout_seconds=t),
                             reason='synthetic fresh experiment', requested_by='analyst') for t in (60, 120)]
    assert {s['batch_id'] for s in dispatch_steps(service, a)} == {'f1'}
    assert {s['batch_id'] for s in dispatch_steps(service, b)} == {'f1'}
    assert auto_execute(service, a, workspace_id=workspace_id)['status'] == 'executed'
    ledger = module().BudgetLedger(service.repository)
    before = ledger.summary(tree['id'])
    assert auto_execute(service, b, workspace_id=workspace_id)['status'] == 'pending'
    with pytest.raises(Conflict, match='fresh batch'):
        execute(service, b, workspace_id=workspace_id, actor='human', budget_override=True)
    assert ledger.summary(tree['id']) == before
    used = batches(service.repository, tree['id'])
    assert [r['batch_id'] for r in used] == ['f1']
    assert used[0]['request_key'] == 'action:' + a['id']
    assert requests.get(b['id'])['status'] == 'pending'


def test_insufficient_fresh_budget_does_not_consume_a_batch(tmp_path):
    from rag_eval.workbench.action_requests import ActionRequestStore, auto_execute
    from rag_eval.workbench.splits import batches
    service, tree, workspace_id = fresh_service(tmp_path, limit=1)
    requests = ActionRequestStore(service.repository)
    request = requests.request(tree['task_id'], 'node-fresh-run', 'child', dict(timeout_seconds=60),
                               reason='synthetic fresh experiment', requested_by='analyst')
    assert auto_execute(service, request, workspace_id=workspace_id)['status'] == 'pending'
    assert batches(service.repository, tree['id']) == []
    assert module().BudgetLedger(service.repository).summary(tree['id'])['runs_by_candidate'] == {}
