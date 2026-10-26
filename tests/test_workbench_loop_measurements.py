"""Round measurement integration with real durable jobs, synthetic executions."""
from types import SimpleNamespace

from rag_eval.workbench.action_requests import execute
from rag_eval.workbench.loop_measurements import RoundMeasurements
from test_workbench_iterations import env, iteration, BUDGET


def ports(env):
    return RoundMeasurements(SimpleNamespace(service=env['service'], repository=env['repo'],
        views=SimpleNamespace(execution_runs=lambda _: []), _scope=lambda **_: None))


def finish(env, candidate):
    for attempt in candidate['attempts']:
        if attempt['execution_state'] == 'running':
            env['executions']._finish(attempt['execution_id'], state='finished', exit_code=0, error_type=None,
                                      run_state=dict(phase='finished'))


def test_measure_freezes_missing_footprint_across_restart(env):
    tree = iteration(env)
    root = tree['root_candidate_id']
    assert ports(env).measure(root, key='measurement', actor='alice', infra_retry=0)['status'] == 'running'
    assert len(env['service'].candidate(root)['attempts']) == 2
    finish(env, env['service'].candidate(root))
    assert ports(env).measure(root, key='measurement', actor='alice', infra_retry=0)['status'] == 'complete'
    assert len(env['service'].candidate(root)['attempts']) == 2


def test_infra_retries_are_appended_once_and_bounded(env):
    tree = iteration(env)
    root = tree['root_candidate_id']
    measurements = ports(env)
    assert measurements.measure(root, key='infra', actor='alice')['status'] == 'running'
    finish(env, env['service'].candidate(root))
    assert measurements.measure(root, key='infra', actor='alice')['reason'] == 'Infrastructure retry planned'
    assert ports(env).measure(root, key='infra', actor='alice')['status'] == 'running'
    assert len(env['service'].candidate(root)['attempts']) == 4
    finish(env, env['service'].candidate(root))
    assert ports(env).measure(root, key='infra', actor='alice')['status'] == 'complete'
    assert len(env['service'].candidate(root)['attempts']) == 4


def test_pending_confirmation_dispatches_same_footprint(env):
    tree = iteration(env, budget=dict(limit_cny=.1, max_runs_per_candidate=10))
    root = tree['root_candidate_id']
    measurements = ports(env)
    result = measurements.measure(root, key='pending', actor='alice')
    assert result['status'] == 'budget_exhausted'
    assert env['service'].candidate(root)['attempts'] == []
    request = measurements.requests.get(result['action_request_id'])
    execute(env['service'], request, workspace_id=env['workspace']['id'], actor='other-operator',
            budget_override=True, human_confirmed=True)
    assert len(env['service'].candidate(root)['attempts']) == 2
    assert ports(env).measure(root, key='pending', actor='alice')['status'] == 'running'
    assert len(env['service'].candidate(root)['attempts']) == 2


def test_stop_during_planning_prevents_launch_but_preserves_reservations(env, monkeypatch):
    tree = iteration(env, budget=dict(limit_cny=10, max_runs_per_candidate=10))
    root = tree['root_candidate_id']
    with env['repo'].connect(write=True) as conn:
        conn.execute('CREATE TABLE iteration_round_invocations(id TEXT PRIMARY KEY, stop_actor TEXT)')
        conn.execute("INSERT INTO iteration_round_invocations VALUES('invocation',NULL)")
    measurements = ports(env)
    original = measurements._reserve
    def reserve(*args, **kwargs):
        value = original(*args, **kwargs)
        with env['repo'].connect(write=True) as conn:
            conn.execute("UPDATE iteration_round_invocations SET stop_actor='operator'")
        return value
    monkeypatch.setattr(measurements, '_reserve', reserve)
    assert measurements.measure(root, key='round/invocation/0/base', actor='alice')['status'] == 'stopped'
    assert env['service'].candidate(root)['attempts'] == []
    with env['repo'].connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM iteration_budget_reservations').fetchone()[0] == 2


def child(env, tree, note):
    from pathlib import Path
    candidate = env['service'].derive(tree['root_candidate_id'], actor='alice', request_key=note, note=note)
    (Path(candidate['worktree']['path']) / 'backend/app/__init__.py').write_text('VALUE = 3\n')
    return env['service'].freeze(candidate['id'], actor='alice')['id']


def test_fresh_claim_is_exact_across_restart_infra_retry_and_sibling(env):
    from rag_eval.workbench.splits import batches
    tree = iteration(env, fresh_batch_count=1, fresh_papers_per_batch=1)
    first, second = child(env, tree, 'first'), child(env, tree, 'second')
    measurements = ports(env)
    assert measurements.fresh(first, key='first-fresh', actor='alice')['status'] == 'running'
    assert ports(env).fresh(first, key='first-fresh', actor='alice')['status'] == 'running'
    claimed = batches(env['repo'], tree['id'])
    assert len(claimed) == 1
    initial_batch = claimed[0]['batch_id']
    assert len(env['service'].candidate(first)['attempts']) == 2
    finish(env, env['service'].candidate(first))
    finish(env, env['service'].candidate(tree['root_candidate_id']))
    assert measurements.fresh(first, key='first-fresh', actor='alice')['reason'] == 'Infrastructure retry planned'
    assert ports(env).fresh(first, key='first-fresh', actor='alice')['status'] == 'running'
    assert {a['batch_id'] for a in env['service'].candidate(first)['attempts']} == {initial_batch}
    assert len(env['service'].candidate(first)['attempts']) == 4
    assert measurements.fresh(second, key='second-fresh', actor='alice')['status'] == 'exhausted'
    claimed = batches(env['repo'], tree['id'])
    assert len(claimed) == 1 and claimed[0]['batch_id'] == initial_batch


def test_pending_fresh_manual_confirmation_does_not_allocate_next_batch(env):
    from rag_eval.workbench.splits import batches
    tree = iteration(env, fresh_batch_count=1, fresh_papers_per_batch=1,
                     budget=dict(limit_cny=.1, max_runs_per_candidate=20))
    candidate = child(env, tree, 'pending-fresh')
    measurements = ports(env)
    result = measurements.fresh(candidate, key='pending-fresh', actor='alice')
    assert result['status'] == 'budget_exhausted'
    assert batches(env['repo'], tree['id']) == []
    request = measurements.requests.get(result['action_request_id'])
    execute(env['service'], request, workspace_id=env['workspace']['id'], actor='operator',
            budget_override=True, human_confirmed=True)
    original = batches(env['repo'], tree['id'])
    assert len(original) == 1
    assert ports(env).fresh(candidate, key='pending-fresh', actor='alice')['status'] == 'running'
    assert batches(env['repo'], tree['id']) == original
    assert len(env['service'].candidate(candidate)['attempts']) == 2


def test_retry_budget_does_not_reset_with_new_round_key(env):
    tree = iteration(env)
    root = tree['root_candidate_id']
    measurements = ports(env)
    measurements.measure(root, key='one', actor='alice')
    finish(env, env['service'].candidate(root))
    measurements.measure(root, key='one', actor='alice')
    measurements.measure(root, key='one', actor='alice')
    finish(env, env['service'].candidate(root))
    measurements.measure(root, key='one', actor='alice')
    assert measurements.measure(root, key='two', actor='alice')['status'] == 'complete'
    assert len(env['service'].candidate(root)['attempts']) == 4


def test_cold_override_reserves_whole_group_before_parallel_launch(env, monkeypatch):
    from threading import Barrier
    tree = iteration(env, budget=dict(limit_cny=.1, max_runs_per_candidate=1))
    original = env['service'].run
    barrier = Barrier(2, timeout=5)
    def run(*args, **kwargs):
        with env['repo'].connect() as conn:
            assert conn.execute('SELECT COUNT(*) FROM iteration_budget_reservations').fetchone()[0] == 2
        barrier.wait()
        return original(*args, **kwargs)
    monkeypatch.setattr(env['service'], 'run', run)
    result = ports(env).measure(tree['root_candidate_id'], key='cold', actor='alice', override=True)
    assert result['status'] == 'running'
    assert len(env['service'].candidate(tree['root_candidate_id'])['attempts']) == 2


def test_per_case_infrastructure_is_detected_without_score_filter(env):
    measurements = ports(env)
    measurements.views = SimpleNamespace(execution_runs=lambda _: [SimpleNamespace(id='run')],
        cases=lambda _: dict(cases=[dict(status='success', score=0), dict(status='error'), dict(status='missing')]))
    assert measurements._infra(dict(execution_id='e', execution_state='finished'))
    measurements.views.cases = lambda _: dict(cases=[dict(status='success', score=0), dict(status='no_answer')])
    assert not measurements._infra(dict(execution_id='e', execution_state='finished'))


def test_completed_fresh_measure_tops_up_parent_and_child_without_duplicate(env):
    import json
    tree = iteration(env, fresh_batch_count=1, fresh_papers_per_batch=1)
    candidate = child(env, tree, 'validation-topup')
    scope = tree['protocol']['fresh_batches'][0]['batch_id']
    with env['repo'].connect(write=True) as conn:
        conn.execute('INSERT INTO iteration_fresh_batches VALUES(?,?,?,?,?,?,?)',
            (tree['id'], scope, candidate, 'prior-fresh', 'now', 'now', None))
    measurements = ports(env)
    assert measurements.measure(candidate, key='validation', actor='alice', infra_retry=0)['status'] == 'running'
    parent_attempts = env['service'].candidate(tree['root_candidate_id'])['attempts']
    child_attempts = env['service'].candidate(candidate)['attempts']
    assert [a['batch_id'] for a in parent_attempts] == [scope, scope]
    assert sum(a['batch_id'] is None for a in child_attempts) == 2
    assert sum(a['batch_id'] == scope for a in child_attempts) == 2
    assert measurements.measure(candidate, key='validation', actor='alice', infra_retry=0)['status'] == 'running'
    assert len(env['service'].candidate(candidate)['attempts']) == 4
    assert len(env['service'].candidate(tree['root_candidate_id'])['attempts']) == 2


def test_plan_crash_before_fresh_claim_does_not_claim_replacement(env):
    import hashlib
    from rag_eval.workbench.splits import claim, batches
    tree = iteration(env, fresh_batch_count=1, fresh_papers_per_batch=1)
    first, second = child(env, tree, 'crash-first'), child(env, tree, 'crash-second')
    measurements = ports(env)
    key = 'crashed-fresh'
    storage = 'round-measure:' + hashlib.sha256(key.encode()).hexdigest()
    state = measurements._plan(env['service'].candidate(first), tree, 'fresh', storage)
    state.update(original_key=key, actor='alice', infra_retry=1, timeout_seconds=7200, override=False)
    measurements._save(storage, state)
    claim(env['repo'], tree, second, 'other-owner')
    assert measurements.fresh(first, key=key, actor='alice')['status'] == 'failed'
    assert measurements.fresh(first, key=key, actor='alice')['status'] == 'failed'
    assert len(batches(env['repo'], tree['id'])) == 1
    assert env['service'].candidate(first)['attempts'] == []


def test_manual_confirmation_each_generation_preserves_prior_action(env):
    import json
    tree = iteration(env, budget=dict(limit_cny=.1, max_runs_per_candidate=10))
    root = tree['root_candidate_id']
    measurements = ports(env)
    first = measurements.measure(root, key='manual-generations', actor='alice')
    first_id = first['action_request_id']
    first_request = measurements.requests.get(first_id)
    first_result = execute(env['service'], first_request, workspace_id=env['workspace']['id'],
                           actor='operator', budget_override=True, human_confirmed=True)
    measurements.requests.finish(first_id, result=first_result)
    with env['repo'].connect() as conn:
        old_steps = conn.execute('SELECT steps_json FROM action_dispatches WHERE request_id=?', (first_id,)).fetchone()[0]
    finish(env, env['service'].candidate(root))
    assert measurements.measure(root, key='manual-generations', actor='alice')['reason'] == 'Infrastructure retry planned'
    retry = measurements.measure(root, key='manual-generations', actor='alice')
    second_id = retry['action_request_id']
    assert second_id != first_id
    execute(env['service'], measurements.requests.get(second_id), workspace_id=env['workspace']['id'],
            actor='operator', budget_override=True, human_confirmed=True)
    assert measurements.measure(root, key='manual-generations', actor='alice')['status'] == 'running'
    assert len(env['service'].candidate(root)['attempts']) == 4
    assert measurements.requests.get(first_id)['result'] == first_result
    assert measurements.requests.get(first_id)['status'] == 'executed'
    with env['repo'].connect() as conn:
        assert conn.execute('SELECT steps_json FROM action_dispatches WHERE request_id=?', (first_id,)).fetchone()[0] == old_steps


def test_fresh_reconciliation_waits_for_terminal_retry_generation(env, monkeypatch):
    from rag_eval.workbench.comparisons import ComparisonService
    from rag_eval.workbench.splits import batches
    tree = iteration(env, fresh_batch_count=1, fresh_papers_per_batch=1)
    candidate = child(env, tree, 'reconcile-after-retry')
    measurements = ports(env)
    measurements.fresh(candidate, key='reconcile', actor='alice')
    finish(env, env['service'].candidate(candidate))
    finish(env, env['service'].candidate(tree['root_candidate_id']))
    seen = []
    def stored(self, subject, reference, kind, actor, store=True):
        seen.append((len(subject['attempts']), len(reference['attempts'])))
        return dict(id='final-comparison')
    monkeypatch.setattr(ComparisonService, '_stored', stored)
    monkeypatch.setattr(ComparisonService, 'get', lambda self, _: dict(mean=dict(delta=.1), noise=dict(eta=.2)))
    comparisons = ComparisonService(env['repo'], env['service'], measurements.views)
    assert comparisons.reconcile_fresh(tree['id'], actor='reader') == 0
    assert batches(env['repo'], tree['id'])[0]['completed_at'] is None
    assert measurements.fresh(candidate, key='reconcile', actor='alice')['reason'] == 'Infrastructure retry planned'
    comparisons.reconcile_fresh(tree['id'], actor='reader')
    assert seen == []
    measurements.fresh(candidate, key='reconcile', actor='alice')
    finish(env, env['service'].candidate(candidate))
    finish(env, env['service'].candidate(tree['root_candidate_id']))
    assert comparisons.reconcile_fresh(tree['id'], actor='reader') == 1
    result = measurements.fresh(candidate, key='reconcile', actor='alice')
    assert result['status'] == 'complete'
    assert result['delta'] == .1
    assert seen == [(4, 4)]
    assert batches(env['repo'], tree['id'])[0]['comparison_id'] == 'final-comparison'


def test_cluster_infra_retries_only_existing_scope_and_cap_survives_new_key(env, monkeypatch):
    import json
    from rag_eval.workbench import clusters
    from rag_eval.workbench.execution_scopes import initialize
    tree = iteration(env)
    candidate = child(env, tree, 'cluster-retries')
    root = tree['root_candidate_id']
    scope = 'cluster:owned'
    initialize(env['repo'])
    with env['repo'].connect(write=True) as conn:
        conn.execute('INSERT INTO iteration_scopes VALUES(?,?,?,?,?)', (tree['id'], scope, 'cluster',
            json.dumps(dict(scope_id=scope, case_ids=['qasper:q'], plan_id=tree['plan_id'],
                plan_revision=tree['plan_revision'], plan_content_hash=tree['plan_content_hash'])), 'now'))
    monkeypatch.setattr(clusters, 'candidate_cluster', lambda *args: 'owned')
    measurements = ports(env)
    # Nothing requested yet: cluster port never manufactures initial attempts.
    assert measurements.cluster(candidate, 'owned', key='empty-cluster', actor='alice')['status'] == 'complete'
    assert env['service'].candidate(candidate)['attempts'] == []
    for cid in (root, candidate):
        env['service'].run(cid, budget=BUDGET, actor='alice', request_key='cluster-initial', batch_id=scope)
        finish(env, env['service'].candidate(cid))
    measurements.views = SimpleNamespace(execution_runs=lambda _: [SimpleNamespace(id='run')],
        cases=lambda _: dict(cases=[dict(status='error')]))
    planned = measurements.cluster(candidate, 'owned', key='cluster-retry', actor='alice')
    assert planned['reason'] == 'Infrastructure retry planned'
    assert planned['infra'] == 2
    import hashlib
    state = measurements._read('round-measure:' + hashlib.sha256(b'cluster-retry').hexdigest())
    watched = [(item['candidate_id'], item['attempt']) for item in state['watched']]
    assert len(watched) == len(set(watched)) == 2
    assert measurements.cluster(candidate, 'owned', key='cluster-retry', actor='alice')['status'] == 'running'
    for cid in (root, candidate):
        assert [a['batch_id'] for a in env['service'].candidate(cid)['attempts']] == [scope, scope]
        finish(env, env['service'].candidate(cid))
    measurements.cluster(candidate, 'owned', key='cluster-retry', actor='alice')
    measurements.cluster(candidate, 'owned', key='next-round-cluster', actor='alice')
    assert len(env['service'].candidate(candidate)['attempts']) == 2
    assert len(env['service'].candidate(root)['attempts']) == 2


def test_terminal_failed_fresh_releases_reconciliation_without_fabricated_quality(env, monkeypatch):
    from rag_eval.workbench.comparisons import ComparisonService
    from rag_eval.workbench.splits import batches
    tree = iteration(env, fresh_batch_count=1, fresh_papers_per_batch=1)
    candidate = child(env, tree, 'failed-fresh')
    measurements = ports(env)
    measurements.fresh(candidate, key='failed-fresh', actor='alice', infra_retry=0)
    for cid in (candidate, tree['root_candidate_id']):
        for attempt in env['service'].candidate(cid)['attempts']:
            env['executions']._finish(attempt['execution_id'], state='failed', exit_code=1,
                error_type='PreparationInfrastructureError', run_state=dict(phase='failed'))
    monkeypatch.setattr(ComparisonService, '_stored', lambda *args, **kwargs: dict(id='failed-comparison'))
    monkeypatch.setattr(ComparisonService, 'get', lambda *args: dict(admission=dict(status='not_admitted')))
    result = measurements.fresh(candidate, key='failed-fresh', actor='alice', infra_retry=0)
    assert result['status'] == 'failed'
    assert result['delta'] is None and result['eta'] is None
    assert batches(env['repo'], tree['id'])[0]['comparison_id'] == 'failed-comparison'


def test_transient_launch_failure_retries_same_reserved_slots(env, monkeypatch):
    import sqlite3
    tree = iteration(env)
    root = tree['root_candidate_id']
    original = env['service'].run
    monkeypatch.setattr(env['service'], 'run', lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError('locked')))
    measurements = ports(env)
    assert measurements.measure(root, key='transient-launch', actor='alice')['status'] == 'running'
    monkeypatch.setattr(env['service'], 'run', original)
    assert measurements.measure(root, key='transient-launch', actor='alice')['status'] == 'running'
    assert len(env['service'].candidate(root)['attempts']) == 2


def test_operator_cancel_is_not_automatically_retried(env):
    tree = iteration(env)
    root = tree['root_candidate_id']
    measurements = ports(env)
    measurements.measure(root, key='cancelled', actor='alice')
    for attempt in env['service'].candidate(root)['attempts']:
        env['executions']._finish(attempt['execution_id'], state='cancelled', exit_code=None,
                                 error_type=None, run_state=None)
    result = measurements.measure(root, key='cancelled', actor='alice')
    assert result['status'] == 'failed'
    assert len(env['service'].candidate(root)['attempts']) == 2


def test_later_invocation_recovers_undispatched_retry_generation(env):
    import json
    from rag_eval.workbench.loop import RoundService
    from test_workbench_loop import Ports
    rounds = RoundService(env['repo'], Ports())
    invocation = rounds.start('I', actor='alice', request_key='old')
    tree = iteration(env)
    root = tree['root_candidate_id']
    measurements = ports(env)
    key = f"round/{invocation['id']}/1/baseline"
    measurements.measure(root, key=key, actor='alice')
    finish(env, env['service'].candidate(root))
    assert measurements.measure(root, key=key, actor='alice')['reason'] == 'Infrastructure retry planned'
    rounds.stop(invocation['id'], actor='alice')
    assert measurements.measure(root, key='later', actor='bob')['reason'] == 'Infrastructure retry planned'
    assert measurements.measure(root, key='later', actor='bob')['status'] == 'running'
    assert len(env['service'].candidate(root)['attempts']) == 4
    assert {a['created_by'] for a in env['service'].candidate(root)['attempts']} == {'alice'}


def test_stopped_fresh_owner_allows_terminal_batch_reconciliation(env, monkeypatch):
    from rag_eval.workbench.loop import RoundService
    from test_workbench_loop import Ports
    from rag_eval.workbench.comparisons import ComparisonService
    from rag_eval.workbench.splits import batches
    rounds = RoundService(env['repo'], Ports())
    invocation = rounds.start('I', actor='alice', request_key='old-fresh')
    tree = iteration(env, fresh_batch_count=1, fresh_papers_per_batch=1)
    cid = child(env, tree, 'fresh-stop')
    measurements = ports(env)
    measurements.fresh(cid, key=f"round/{invocation['id']}/1/fresh", actor='alice')
    finish(env, env['service'].candidate(cid))
    finish(env, env['service'].candidate(tree['root_candidate_id']))
    rounds.stop(invocation['id'], actor='alice')
    monkeypatch.setattr(ComparisonService, '_stored', lambda *a, **kw: dict(id='stop-comparison'))
    comparisons = ComparisonService(env['repo'], env['service'], measurements.views)
    assert comparisons.reconcile_fresh(tree['id'], actor='reader') == 1
    assert batches(env['repo'], tree['id'])[0]['comparison_id'] == 'stop-comparison'


def test_retry_launch_crash_does_not_turn_retry_into_new_root(env, monkeypatch):
    tree = iteration(env)
    root = tree['root_candidate_id']
    measurements = ports(env)
    measurements.measure(root, key='crash-retry', actor='alice')
    finish(env, env['service'].candidate(root))
    measurements.measure(root, key='crash-retry', actor='alice')
    original = measurements._attempts
    class Crash(BaseException):
        pass
    monkeypatch.setattr(measurements, '_attempts', lambda *a: (_ for _ in ()).throw(Crash()))
    import pytest
    with pytest.raises(Crash):
        measurements.measure(root, key='crash-retry', actor='alice')
    finish(env, env['service'].candidate(root))
    result = ports(env).measure(root, key='new-invocation', actor='bob')
    assert result['status'] == 'complete'
    assert len(env['service'].candidate(root)['attempts']) == 4


def test_validation_refill_parent_does_not_consume_subject_cap(env):
    import json
    from rag_eval.workbench.budgets import BudgetLedger
    tree = iteration(env, fresh_batch_count=1, fresh_papers_per_batch=1,
                     budget=dict(max_case_runs=100, max_runs_per_candidate=2))
    cid = child(env, tree, 'reference-refill')
    root = tree['root_candidate_id']
    ledger = BudgetLedger(env['repo'])
    ledger.reserve_many(tree, [dict(candidate_id=root, request_key=f'root-own-{i}', case_count=1)
                              for i in range(2)], group_key='root-own')
    batch = tree['protocol']['fresh_batches'][0]['batch_id']
    with env['repo'].connect(write=True) as conn:
        conn.execute('INSERT INTO iteration_fresh_batches VALUES(?,?,?,?,?,?,?)',
                     (tree['id'], batch, cid, 'used-fresh', 'now', 'now', None))
    env['service'].fill_validation(cid, budget=BUDGET, actor='alice', request_key='refill')
    assert ledger.summary(tree['id'])['subject_runs_by_candidate'] == {root: 2, cid: 2}
    assert len(env['service'].candidate(root)['attempts']) == 2


def test_partial_fresh_dispatch_reconciles_after_owner_fails(env, monkeypatch):
    from rag_eval.workbench.loop import RoundService
    from test_workbench_loop import Ports
    from rag_eval.workbench.comparisons import ComparisonService
    rounds = RoundService(env['repo'], Ports())
    invocation = rounds.start('I', actor='alice', request_key='partial-fresh')
    tree = iteration(env, fresh_batch_count=1, fresh_papers_per_batch=1)
    cid = child(env, tree, 'partial-fresh')
    original = env['service'].run
    def partial(candidate, **kwargs):
        if candidate == cid:
            raise ValueError('synthetic logical failure')
        return original(candidate, **kwargs)
    monkeypatch.setattr(env['service'], 'run', partial)
    measurements = ports(env)
    measurements.fresh(cid, key=f"round/{invocation['id']}/1/fresh", actor='alice')
    finish(env, env['service'].candidate(tree['root_candidate_id']))
    rounds._finish(invocation, 'operation_failed', failed=True)
    comparisons = ComparisonService(env['repo'], env['service'], measurements.views)
    assert comparisons.reconcile_fresh(tree['id'], actor='alice') == 1
    from rag_eval.workbench.splits import batches
    record = comparisons.get(batches(env['repo'], tree['id'])[0]['comparison_id'])
    assert record['admission']['status'] == 'not_admitted'
    assert record['mean']['delta'] is None
    assert len(record['infrastructure']['reference']['attempts']) == 2
    assert record['infrastructure']['subject']['attempts'] == []


def test_restart_lost_execution_does_not_satisfy_baseline_admission(env):
    import time
    from rag_eval.workbench.job_execution import JobExecutionStore
    from rag_eval.workbench.budgets import BudgetLedger
    tree = iteration(env, budget=dict(max_case_runs=100, max_runs_per_candidate=8))
    root = tree['root_candidate_id']
    measurements = ports(env)
    measurements.measure(root, key='restart-baseline', actor='alice')
    initial = env['service'].candidate(root)['attempts']
    # Fixture launch creates no process; release only the simulated old owners.
    for attempt in initial:
        env['executions']._release_supervisor(attempt['execution_id'])
    restarted = JobExecutionStore(env['repo'])
    restarted.recover()
    deadline = time.monotonic() + 5
    while any(restarted.get(tree['task_id'], a['execution_id'])['state'] == 'running' for a in initial):
        assert time.monotonic() < deadline
        time.sleep(.01)
    assert all(a['admission']['status'] != 'admitted' for a in env['service'].candidate(root)['attempts'])
    assert measurements.measure(root, key='restart-baseline', actor='alice')['reason'] == 'Infrastructure retry planned'
    assert measurements.measure(root, key='restart-baseline', actor='alice')['status'] == 'running'
    assert len(env['service'].candidate(root)['attempts']) == 4
    assert BudgetLedger(env['repo']).summary(tree['id'])['runs_by_candidate'][root] == 4


def test_observe_only_does_not_dispatch_planned_retry(env):
    tree = iteration(env)
    root = tree['root_candidate_id']
    measurements = ports(env)
    measurements.measure(root, key='observe', actor='alice')
    finish(env, env['service'].candidate(root))
    measurements.measure(root, key='observe', actor='alice')
    assert measurements.measure(root, key='observe', actor='alice', observe_only=True)['status'] == 'running'
    assert len(env['service'].candidate(root)['attempts']) == 2


def test_prepared_only_fresh_failure_reconciles_without_fabricating_execution(env, monkeypatch):
    from rag_eval.workbench.comparisons import ComparisonService
    from rag_eval.workbench.splits import batches
    tree = iteration(env, fresh_batch_count=1, fresh_papers_per_batch=1)
    cid = child(env, tree, 'prepared-only')
    def invalid(*a, **k):
        raise ValueError('invalid local spec')
    monkeypatch.setattr(env['service'], 'build_spec', invalid)
    measurements = ports(env)
    assert measurements.fresh(cid, key='prepared-only', actor='alice')['status'] == 'failed'
    assert len(env['service'].candidate(cid)['attempts']) == 2
    comparisons = ComparisonService(env['repo'], env['service'], measurements.views)
    assert comparisons.reconcile_fresh(tree['id'], actor='alice') == 1
    record = comparisons.get(batches(env['repo'], tree['id'])[0]['comparison_id'])
    assert record['admission']['status'] == 'not_admitted'
    assert len(record['undispatched_attempts']['subject']) == 2


def test_observe_only_recognizes_terminal_final_retry_without_cached_result(env):
    tree = iteration(env)
    root = tree['root_candidate_id']
    measurements = ports(env)
    measurements.measure(root, key='late-observe', actor='alice')
    finish(env, env['service'].candidate(root))
    measurements.measure(root, key='late-observe', actor='alice')
    measurements.measure(root, key='late-observe', actor='alice')
    finish(env, env['service'].candidate(root))
    assert measurements.measure(root, key='late-observe', actor='alice', observe_only=True)['status'] == 'complete'
    assert len(env['service'].candidate(root)['attempts']) == 4


def test_stopped_prepared_retry_can_be_dispatched_by_later_invocation(env, monkeypatch):
    from rag_eval.workbench.loop import RoundService
    from test_workbench_loop import Ports
    rounds = RoundService(env['repo'], Ports())
    invocation = rounds.start('I', actor='alice', request_key='prepared-retry')
    tree = iteration(env)
    root = tree['root_candidate_id']
    key = f"round/{invocation['id']}/1/base"
    measurements = ports(env)
    measurements.measure(root, key=key, actor='alice')
    finish(env, env['service'].candidate(root))
    measurements.measure(root, key=key, actor='alice')
    original = env['service'].build_spec
    def unavailable(*a, **k):
        raise OSError('temporary spec storage')
    monkeypatch.setattr(env['service'], 'build_spec', unavailable)
    assert measurements.measure(root, key=key, actor='alice')['status'] == 'running'
    rounds.stop(invocation['id'], actor='alice')
    monkeypatch.setattr(env['service'], 'build_spec', original)
    for _ in range(3):
        measurements.measure(root, key='next-invocation', actor='bob')
    candidate = env['service'].candidate(root)
    assert len(candidate['attempts']) == 4
    assert all(a['execution_id'] for a in candidate['attempts'])


def test_observation_honors_retry_cap_across_invocations(env):
    import hashlib
    tree = iteration(env)
    root = tree['root_candidate_id']
    measurements = ports(env)
    measurements.measure(root, key='finished-retries', actor='alice')
    finish(env, env['service'].candidate(root))
    measurements.measure(root, key='finished-retries', actor='alice')
    measurements.measure(root, key='finished-retries', actor='alice')
    finish(env, env['service'].candidate(root))
    measurements.measure(root, key='finished-retries', actor='alice')
    storage = 'round-measure:' + hashlib.sha256(b'next-observation').hexdigest()
    state = measurements._plan(env['service'].candidate(root), tree, 'measure', storage)
    state.update(original_key='next-observation', actor='bob', infra_retry=1, timeout_seconds=7200, override=False)
    measurements._save(storage, state)
    assert measurements.measure(root, key='next-observation', actor='bob', observe_only=True)['status'] == 'complete'
