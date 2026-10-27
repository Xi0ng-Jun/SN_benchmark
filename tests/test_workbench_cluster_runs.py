import json
from pathlib import Path
import pytest
from test_workbench_clusters import frozen_cluster, env  # noqa: F401
from test_workbench_iterations import BUDGET, finish, payload
from rag_eval.workbench.comparisons import ComparisonService


def test_cluster_run_has_own_plan_pairs_and_private_result(frozen_cluster):
    f = frozen_cluster
    service, candidate = f['service'], f['candidate']['id']
    (Path(f['candidate']['worktree']['path']) / 'backend/app/__init__.py').write_text('VALUE = 3\n')
    service.freeze(candidate, actor='test')
    result = service.cluster_run(candidate, 'c01', budget=BUDGET, actor='optimizer', request_key='cluster')
    assert result['status'] == 'dispatched'
    tree = f['tree']
    for cid, scores in [(tree['root_candidate_id'], {'qasper:q': 0, 'qasper:q2': 0}),
                        (candidate, {'qasper:q': 1, 'qasper:q2': 1})]:
        scoped = [a for a in service.candidate(cid)['attempts'] if a['batch_id'] == 'cluster:c01']
        assert len(scoped) == 2
        for a in scoped:
            job = service.jobs.get(tree['task_id'], a['job_id'])
            assert job['plan_snapshot']['plan_id'] != tree['plan_id']
            assert job['plan_snapshot']['content']['selection']['case_ids'] == ['qasper:q', 'qasper:q2']
            f['views'].scores[a['execution_id']] = scores
            f['views'].partitions[a['execution_id']] = [tuple(scores)]
            f['env']['executions']._finish(a['execution_id'], state='finished', exit_code=0,
                                           error_type=None, run_state={'phase': 'finished'})
    comparisons = ComparisonService(f['env']['repo'], service, f['views'])
    record = comparisons.for_cluster(candidate, 'c01', actor='test')
    assert record['admission']['status'] == 'admitted' and record['mean']['delta'] == 1
    assert record['cluster']['rho'] == 1
    private = comparisons.for_cluster(candidate, 'c01', actor='test', implementer=True)
    assert private['members'] == [{'member': 'm01', 'flip': 'better'}, {'member': 'm02', 'flip': 'better'}]
    assert not any(x in json.dumps(private) for x in ['qasper:', 'question', 'score', 'left', 'right'])
    service.cluster_run(candidate, 'c01', budget=BUDGET, actor='optimizer', request_key='cluster')
    assert len([a for a in service.candidate(candidate)['attempts'] if a['batch_id']]) == 2
    assert len(comparisons.scoped(service.candidate(tree['root_candidate_id']), None)['attempts']) == 2


def test_cluster_run_refuses_unrelated_cluster(frozen_cluster):
    f = frozen_cluster
    with pytest.raises(ValueError, match='own cluster'):
        f['service'].cluster_run(f['candidate']['id'], 'other', budget=BUDGET, actor='test', request_key='bad')


def set_budget(f, limit=100, runs=20):
    tree = f['tree']
    protocol = dict(tree['protocol'], budget=dict(limit_cny=limit, max_runs_per_candidate=runs))
    with f['env']['repo'].connect(write=True) as conn:
        conn.execute('UPDATE iterations SET protocol_json=? WHERE id=?', (json.dumps(protocol), tree['id']))


def test_budget_auto_dispatch_reserves_parent_and_child_and_reuses_receipts(frozen_cluster):
    from rag_eval.workbench.action_requests import ActionRequestStore, auto_execute, execute
    from rag_eval.workbench.budgets import BudgetLedger
    f = frozen_cluster
    set_budget(f)
    service, candidate = f['service'], f['candidate']['id']
    (Path(f['candidate']['worktree']['path']) / 'backend/app/__init__.py').write_text('VALUE = 3\n')
    service.freeze(candidate, actor='test')
    store = ActionRequestStore(f['env']['repo'])
    request = store.request(f['tree']['task_id'], 'node-cluster-run', candidate, {'cluster_id': 'c01'},
                            reason='check mechanism', requested_by='agent:implementer')
    outcome = auto_execute(service, request, workspace_id=f['env']['workspace']['id'])
    assert outcome['status'] == 'executed' and outcome['decided_by'] == 'budget'
    ledger = BudgetLedger(f['env']['repo'])
    summary = ledger.summary(f['tree']['id'])
    assert summary['runs_by_candidate'] == {candidate: 2, f['tree']['root_candidate_id']: 2}
    # Each case may be asked twice (the frozen default of one in-attempt re-ask).
    assert summary['charged_cny'] == 16
    execute(service, request, workspace_id=f['env']['workspace']['id'], actor='budget')
    assert ledger.summary(f['tree']['id']) == summary
    attempts = service.candidate(candidate)['attempts']
    assert len(attempts) == 2
    for a in attempts:
        f['env']['executions']._finish(a['execution_id'], state='failed', exit_code=1, error_type='synthetic',
            run_state=dict(phase='failed', cost_observation=dict(currency='CNY', complete=True, total_cny=.2)))
    settled = ledger.summary(f['tree']['id'])
    assert settled['charged_cny'] == pytest.approx(8.4)
    assert settled['runs_by_candidate'] == summary['runs_by_candidate']
    assert payload(f['env'], service.candidate(candidate))['retry_rounds'] == 1


def test_budget_over_limit_keeps_cluster_request_pending_without_partial_dispatch(frozen_cluster):
    from rag_eval.workbench.action_requests import ActionRequestStore, auto_execute
    f = frozen_cluster
    set_budget(f, limit=3)
    service, candidate = f['service'], f['candidate']['id']
    (Path(f['candidate']['worktree']['path']) / 'backend/app/__init__.py').write_text('VALUE = 3\n')
    service.freeze(candidate, actor='test')
    store = ActionRequestStore(f['env']['repo'])
    request = store.request(f['tree']['task_id'], 'node-cluster-run', candidate, {'cluster_id': 'c01'},
                            reason='check mechanism', requested_by='agent:implementer')
    assert auto_execute(service, request, workspace_id=f['env']['workspace']['id'])['status'] == 'pending'
    assert not service.candidate(candidate)['attempts']
    assert len(service.candidate(f['tree']['root_candidate_id'])['attempts']) == 2


def test_cluster_run_pairs_only_actual_subset(frozen_cluster):
    from test_workbench_roles_hypotheses import CARD
    from rag_eval.workbench.hypotheses import HypothesisStore
    f = frozen_cluster
    original = f['views'].observation
    f['views'].observation = lambda run, cid: dict(original(run, cid), prediction='absent' if cid.endswith('q2') else 'present')
    f['store'].propose(f['tree']['root_candidate_id'], dict(cluster_id='subset', description='Missing response',
        signature=dict(kind='regex', field='prediction', pattern='^absent$')), actor='test')
    card = dict(CARD, cluster_id='subset', predictions=[dict(case_id='qasper:q2', expect='improve')],
                contract=dict(cluster_id='subset', predicted_flips=['qasper:q2'], guards=[], tau=.5))
    service = f['service']
    p = service.draft_proposal(f['tree']['root_candidate_id'], card, author='test')
    HypothesisStore(f['env']['repo']).decide(f['tree']['task_id'], p['id'], 'approve', actor='human')
    c = service.derive(f['tree']['root_candidate_id'], proposal_id=p['id'], actor='test', request_key='subset')
    (Path(c['worktree']['path']) / 'backend/app/__init__.py').write_text('VALUE = 3\n')
    service.freeze(c['id'], actor='test')
    service.cluster_run(c['id'], 'subset', budget=BUDGET, actor='test', request_key='subset')
    a = service.candidate(c['id'])['attempts'][0]
    selection = service.jobs.get(f['tree']['task_id'], a['job_id'])['plan_snapshot']['content']['selection']
    assert selection['case_ids'] == ['qasper:q2']
    assert selection['selection_hash'] != f['tree']['selection_hash']
    # Cluster runs reuse prepared notebooks through the preparation cache, not root seeds.
    assert payload(f['env'], service.candidate(c['id']))['run_maps'] is None


def test_direct_budgeted_cluster_call_reserves_whole_footprint(frozen_cluster):
    from rag_eval.workbench.repository import Conflict
    f = frozen_cluster
    set_budget(f, limit=3)
    service, cid = f['service'], f['candidate']['id']
    (Path(f['candidate']['worktree']['path']) / 'backend/app/__init__.py').write_text('VALUE = 3\n')
    service.freeze(cid, actor='test')
    with pytest.raises(Conflict, match='budget'):
        service.cluster_run(cid, 'c01', budget=BUDGET, actor='test', request_key='direct')
    assert service.candidate(cid)['attempts'] == []
    assert len(service.candidate(f['tree']['root_candidate_id'])['attempts']) == 2


def test_budget_run_rejects_actor_change_before_second_dispatch(frozen_cluster):
    from rag_eval.workbench.repository import Conflict
    f = frozen_cluster
    set_budget(f)
    root = f['tree']['root_candidate_id']
    f['service'].run(root, budget=BUDGET, actor='alice', request_key='actor-bound')
    with pytest.raises(Conflict):
        f['service'].run(root, budget=BUDGET, actor='bob', request_key='actor-bound')
    assert len(f['service'].candidate(root)['attempts']) == 3
