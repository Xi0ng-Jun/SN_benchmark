import json

import pytest

from rag_eval.workbench import splits
from test_workbench_iterations import env, iteration, BUDGET, finish, approved  # noqa: F401


def test_split_is_reproducible_disjoint_and_fresh_uses_whole_papers():
    parts = [dict(partition_id=f'p{i}', case_ids=[f'q{i}a', f'q{i}b']) for i in range(12)]
    selected = [c for p in parts[:5] for c in p['case_ids']]
    result = splits.partition(parts, selected, sealed=['q11a', 'q11b'], seed=42, batches=2, papers_per_batch=2)
    assert result == splits.partition(parts[::-1], selected[::-1], sealed=['q11b', 'q11a'], seed=42, batches=2, papers_per_batch=2)
    assert len(result['search']) == 6 and len(result['validation']) == 4
    pools = [result['search'], result['validation'], ['q11a', 'q11b'], *result['batches']]
    assert sum(map(len, pools)) == len(set().union(*map(set, pools)))
    with pytest.raises(ValueError, match='sealed'):
        splits.partition(parts, ['q11a'], sealed=['q11b'])
    with pytest.raises(ValueError, match='fresh'):
        splits.partition(parts, selected, sealed=['q11a'], batches=10)


def test_visibility_filters_nested_gate_and_noise_and_refuses_hidden_case(env):
    tree = iteration(env, case_ids=['qasper:q', 'qasper:q3'])
    policy = splits.Policy(env['repo'], tree['task_id'])
    search = tree['protocol']['splits']['search'][0]
    hidden = tree['protocol']['splits']['validation'][0]
    body = dict(rows=[dict(case_id=search, value=1), dict(case_id=hidden, question='hidden question', value=0)],
                noise=dict(cases={search: dict(p=1), hidden: dict(p=0)}, pairs=[dict(deltas={hidden: -1})]),
                decision=dict(gate_verdict=dict(evidence=dict(rows=[dict(case_id=hidden, question='hidden question')]))))
    encoded = json.dumps(policy.project(body))
    assert search in encoded and hidden not in encoded and 'hidden question' not in encoded
    with pytest.raises(ValueError, match='search'):
        policy.require_search(hidden)


def test_iteration_freezes_roles_and_rejects_unknown_holdout(env):
    with pytest.raises(ValueError, match='sealed'):
        iteration(env, sealed_ref='unknown')
    tree = iteration(env, fresh_batch_count=1, fresh_papers_per_batch=1)
    plan = env['service'].plans.get(tree['task_id'], tree['plan_id'])
    assert plan['content']['selection']['roles'] == {'qasper:q': 'search'}
    assert tree['protocol']['fresh_batches'][0]['case_ids'] == ['qasper:q3']
    assert tree['protocol']['splits']['sealed_ref'] == 'synthetic-holdout'


def test_fresh_claim_recovery_completion_and_validation_growth(env):
    from rag_eval.workbench.comparisons import Admission, ComparisonService
    from test_workbench_comparisons import Views
    service = env['service']
    views = Views()
    service.admission = Admission(views)
    tree = iteration(env, fresh_batch_count=1, fresh_papers_per_batch=1)
    root = tree['root_candidate_id']
    root_view = service.run(root, budget=BUDGET, actor='test', request_key='base')
    eid = root_view['attempts'][-1]['execution_id']
    views.scores[eid] = {'qasper:q': 0}
    views.partitions[eid] = [('qasper:q',)]
    finish(env, root_view)
    proposal = approved(env, tree, root)
    candidate = service.derive(root, actor='test', request_key='child', proposal_id=proposal)
    from pathlib import Path
    Path(candidate['worktree']['path'], 'backend/app/__init__.py').write_text('VALUE = 10\n')
    service.freeze(candidate['id'], actor='test')
    result = service.fresh_run(candidate['id'], budget=BUDGET, actor='optimizer', request_key='fresh')
    assert result['batch']['batch_id'] == 'f1'
    assert service.fresh_run(candidate['id'], budget=BUDGET, actor='optimizer', request_key='fresh') == result
    with pytest.raises(Exception, match='unused'):
        service.fresh_run(candidate['id'], budget=BUDGET, actor='optimizer', request_key='another')
    assert not splits.batches(env['repo'], tree['id'], completed=True)
    for cid in (root, candidate['id']):
        for a in service.candidate(cid)['attempts']:
            if a['batch_id'] != 'f1':
                continue
            views.scores[a['execution_id']] = {'qasper:q3': 0 if cid == root else 1}
            views.partitions[a['execution_id']] = [('qasper:q3',)]
            env['executions']._finish(a['execution_id'], state='finished', exit_code=0, error_type=None,
                                      run_state=dict(phase='finished'))
    comparisons = ComparisonService(env['repo'], service, views)
    comparisons.reconcile_fresh(tree['id'], actor='optimizer')
    completed = splits.batches(env['repo'], tree['id'], completed=True)
    assert len(completed) == 1
    record = comparisons.get(completed[0]['comparison_id'])
    assert record['kind'] == 'fresh' and record['mean']['delta'] == 1
    assert record['pool'] == [dict(batch_id='f1', selection_hash=tree['protocol']['fresh_batches'][0]['selection_hash'], n=1)]
    assert record['frozen_commits'] == dict(subject=service.candidate(candidate['id'])['frozen_commit'], reference=service.candidate(root)['frozen_commit'])
    assert len(record['attempts_used']['reference']) == 2
    assert service.iteration(tree['id'])['validation_pool'] == ['qasper:q3']
    assert service.iteration(tree['id'])['protocol'] == tree['protocol']
    # A later candidate reports missing validation; only explicit execution fills it.
    for cid in (root, candidate['id']):
        result = service.run(cid, budget=BUDGET, actor='test', request_key='base-extra')
        a = result['attempts'][-1]
        views.scores[a['execution_id']] = {'qasper:q': .5}
        views.partitions[a['execution_id']] = [('qasper:q',)]
        finish(env, result)
    proposal = approved(env, tree, candidate['id'])
    later = service.derive(candidate['id'], actor='test', request_key='later', proposal_id=proposal)
    Path(later['worktree']['path'], 'backend/app/__init__.py').write_text('VALUE = 11\n')
    service.freeze(later['id'], actor='test')
    result = service.run(later['id'], budget=BUDGET, actor='test', request_key='base')
    a = result['attempts'][-1]
    views.scores[a['execution_id']] = {'qasper:q': 1}
    views.partitions[a['execution_id']] = [('qasper:q',)]
    finish(env, result)
    before = comparisons.for_candidate(later['id'], actor='reader')['parent']
    assert before['validation']['missing_batches'] == ['f1']
    service.fill_validation(later['id'], budget=BUDGET, actor='optimizer', request_key='supplement')
    for a in service.candidate(later['id'])['attempts']:
        if a['batch_id'] == 'f1':
            views.scores[a['execution_id']] = {'qasper:q3': .5}
            views.partitions[a['execution_id']] = [('qasper:q3',)]
            env['executions']._finish(a['execution_id'], state='finished', exit_code=0, error_type=None,
                                      run_state=dict(phase='finished'))
    after = comparisons.for_candidate(later['id'], actor='reader')['parent']
    assert after['id'] != before['id']
    assert after['validation']['delta'] == -.5 and not after['validation']['incomplete']
    assert after['validation']['pool'] == [dict(batch_id='base', selection_hash=tree['selection_hash'], n=0), *record['pool']]
    assert after['validation']['batches'][1]['delta'] == -.5
    base_sources = after['validation']['batches'][0]['attempts_used']
    fresh_sources = after['validation']['batches'][1]['attempts_used']
    for side in ('subject', 'reference'):
        assert {a['execution_id'] for a in base_sources[side]}.isdisjoint(
            {a['execution_id'] for a in fresh_sources[side]})
    assert before['validation']['batches'][1]['delta'] is None
    assert comparisons.get(before['id'])['validation']['missing_batches'] == ['f1']


def test_cli_and_context_refuse_validation_and_sealed_before_reading(env, monkeypatch, capsys):
    from rag_eval.workbench import agent_tools
    from rag_eval.workbench.context import resolve_context
    tree = iteration(env, case_ids=['qasper:q', 'qasper:q3'])
    search = tree['protocol']['splits']['search'][0]
    hidden = tree['protocol']['splits']['validation'][0]
    class Views:
        def cases(self, run_id, **kwargs):
            return dict(metric='answer_f1', cases=[dict(case_id=search, question='visible', value=1),
                dict(case_id=hidden, question='hidden question', value=.4), dict(case_id='foreign', question='foreign question')])
        def case(self, *args):
            raise AssertionError('Hidden evidence must not be read')
    monkeypatch.setattr(agent_tools, 'build', lambda *args: (env['repo'], Views()))
    agent_tools.main(['--task', tree['task_id'], 'cases', 'run'])
    out = json.loads(capsys.readouterr().out)
    assert [r['case_id'] for r in out['cases']] == [search]
    assert out['validation'] == dict(n=1, mean=.4, eta=None)
    with pytest.raises(ValueError, match='search'):
        agent_tools.main(['--task', tree['task_id'], 'case', 'run', hidden])
    with pytest.raises(ValueError, match='search'):
        resolve_context(env['repo'], tree['task_id'], dict(kind='run_case', run_id='run', case_id=hidden), run_views=Views())
    with env['repo'].connect(write=True) as conn:
        protocol = tree['protocol']
        protocol['splits']['sealed'] = ['sealed-secret']
        conn.execute('UPDATE iterations SET protocol_json=? WHERE id=?', (json.dumps(protocol), tree['id']))
    with pytest.raises(ValueError, match='sealed'):
        agent_tools.main(['--task', tree['task_id'], 'case', 'run', 'sealed-secret'])
    from rag_eval.workbench.action_requests import ActionRequestStore
    with pytest.raises(ValueError, match='sealed'):
        ActionRequestStore(env['repo']).request(tree['task_id'], 'iteration-create', None,
            dict(title='bad', start_commit=env['start'], input_id='registered', expected_bundle_id='a'*64,
                 case_ids=['sealed-secret'], mode='chunk'), reason='bad', requested_by='test')


def test_role_hash_survives_preparation_and_rejects_corruption(env):
    from rag_eval.workbench.evaluation_plans import validate_plan_snapshot
    from copy import deepcopy
    tree = iteration(env, case_ids=['qasper:q', 'qasper:q3'])
    root = env['service'].run(tree['root_candidate_id'], budget=BUDGET, actor='test', request_key='roles')
    assert root['attempts'][0]['execution_id']
    saved = env['service'].plans.get(tree['task_id'], tree['plan_id'])
    bad = deepcopy(saved)
    bad['content']['selection']['roles']['qasper:q'] = 'sealed'
    with pytest.raises(ValueError):
        validate_plan_snapshot(bad, task_id=tree['task_id'])


def test_registered_run_verbs_refuse_sealed_without_exposing_payload(env, tmp_path):
    from rag_eval.workbench.run_views import RunEntry, RunViews
    tree = iteration(env)
    with env['repo'].connect(write=True) as conn:
        protocol = tree['protocol']
        protocol['splits']['sealed'] = ['sealed-secret']
        conn.execute('UPDATE iterations SET protocol_json=? WHERE id=?', (json.dumps(protocol), tree['id']))
    views = RunViews([RunEntry('sealed-run', tmp_path / 'absent', case_ids=('sealed-secret',))],
                     worktree_service=env['service'].trees)
    for read in [lambda: views.run('sealed-run'), lambda: views.cases('sealed-run'),
                 lambda: views.case('sealed-run', 'sealed-secret'), lambda: views.flips('sealed-run', 'sealed-run')]:
        with pytest.raises(ValueError, match='sealed'):
            read()
    assert views.list_runs()['runs'][0]['status'] == 'unreadable'


def test_projected_comparison_counts_and_mean_describe_visible_search_rows(env):
    tree = iteration(env, case_ids=['qasper:q', 'qasper:q3'])
    search = tree['protocol']['splits']['search'][0]
    hidden = tree['protocol']['splits']['validation'][0]
    source = dict(rows=[dict(case_id=search, left=0, right=1, delta=1, flip='better'),
                       dict(case_id=hidden, left=1, right=0, delta=-1, flip='worse')],
                  counts=dict(better=1, worse=1, same=0, unpaired=0), mean=dict(reference=.5, subject=.5, delta=0),
                  validation=dict(n=1, reference=1, subject=0, delta=-1, eta=.1))
    out = splits.Policy(env['repo'], tree['task_id']).project(source)
    assert out['counts'] == dict(better=1, worse=0, same=0, unpaired=0)
    assert out['mean'] == dict(reference=0, subject=1, delta=1)
    assert out['validation'] == dict(n=1, mean=dict(reference=1, subject=0, delta=-1), eta=.1)
    assert source['mean']['delta'] == 0


def test_explicit_validation_fill_replaces_failed_measurements_without_deleting_them(monkeypatch):
    from types import SimpleNamespace
    from rag_eval.workbench.iterations import IterationService
    attempts = [dict(batch_id='f1', execution_id='failed', execution_state='failed', admission=dict(status='not_admitted')),
                dict(batch_id='f1', execution_id='good', execution_state='finished', admission=dict(status='admitted'))]
    dispatched = []
    service = SimpleNamespace(repository=object(), candidate=lambda cid: dict(id=cid, iteration_id='i',
        parent_candidate_id=None, attempts=attempts), iteration=lambda _: dict(id='i', protocol=dict(k_base=2)),
        run=lambda cid, **kwargs: dispatched.append((cid, kwargs)))
    monkeypatch.setattr(splits, 'batches', lambda *args, **kwargs: [dict(batch_id='f1')])
    IterationService.fill_validation(service, 'candidate', budget=BUDGET, actor='optimizer', request_key='repair')
    assert len(dispatched) == 1 and dispatched[0][1]['batch_id'] == 'f1'
    assert attempts[0]['execution_id'] == 'failed' and attempts[0]['execution_state'] == 'failed'
    IterationService.fill_validation(service, 'candidate', budget=BUDGET, actor='optimizer', request_key='repair')
    assert dispatched[1][1]['request_key'] == dispatched[0][1]['request_key']
    attempts.append(dict(batch_id='f1', execution_id='pending', execution_state='running', admission=None))
    IterationService.fill_validation(service, 'candidate', budget=BUDGET, actor='optimizer', request_key='new-request')
    assert len(dispatched) == 2
