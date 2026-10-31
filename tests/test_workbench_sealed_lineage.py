"""Method v2 end step: the sealed set measures a stopped lineage once, as a curve."""
import json
from pathlib import Path

import pytest

from test_workbench_iterations import env, iteration, BUDGET  # noqa: F401
from rag_eval.workbench.repository import Conflict


def registered_tree(env, **kwargs):
    env['service'].preparation_config['sealed_selections']['synthetic-holdout']['case_ids'] = ['qasper:q3']
    return iteration(env, **kwargs)


def chain(env, tree, length):
    """Seed + ``length`` confirmed children, each derived from the previous parent."""
    service, nodes = env['service'], [tree['root_candidate_id']]
    for i in range(length):
        child = service.derive(nodes[-1], note=f'confirmed change {i}', actor='loop', request_key=f'c{i}')
        Path(child['worktree']['path'], 'backend/app/__init__.py').write_text(f'VALUE = {10 + i}\n')
        nodes.append(service.freeze(child['id'], actor='loop')['id'])
    # A rejected sibling never joins the lineage.
    service.derive(nodes[0], note='rejected', actor='loop', request_key='rejected')
    return nodes


def stop(env, tree, baseline, status='completed'):
    from rag_eval.workbench.loop import RoundService
    RoundService(env['repo'], None)
    record = dict(id=tree['id'] + '-' + status, iteration_id=tree['id'], status=status, baseline_candidate_id=baseline)
    with env['repo'].connect(write=True) as conn:
        conn.execute('INSERT INTO iteration_round_invocations(id,iteration_id,record) VALUES(?,?,?)',
                     (record['id'], tree['id'], json.dumps(record)))


def sealed_attempts(env, cid):
    return [a for a in env['service'].candidate(cid)['attempts'] if a.get('batch_id') == 'sealed:lineage']


def test_lineage_needs_confirmation_and_a_stopped_optimisation(env):
    from rag_eval.workbench.sealed import SealedService
    tree = registered_tree(env)
    nodes = chain(env, tree, 1)
    sealed = SealedService(env['repo'], env['service'], None)
    with pytest.raises(ValueError, match='human confirmation'):
        sealed.run_lineage(tree['id'], budget=BUDGET, actor='human')
    with pytest.raises(ValueError, match='stopped optimisation'):
        sealed.run_lineage(tree['id'], budget=BUDGET, actor='human', confirmed=True)
    stop(env, tree, nodes[-1], status='running')
    with pytest.raises(ValueError, match='stopped optimisation'):
        sealed.run_lineage(tree['id'], budget=BUDGET, actor='human', confirmed=True)
    assert sealed.lineage(tree['id']) is None and sealed.score(tree['id']) is None


def test_one_claim_measures_every_lineage_node_k_times_and_is_never_repeated(env):
    from rag_eval.workbench.sealed import SealedService, _CAPABILITY
    tree = registered_tree(env)
    nodes = chain(env, tree, 2)
    stop(env, tree, nodes[-1])
    sealed = SealedService(env['repo'], env['service'], None)
    result = sealed.run_lineage(tree['id'], budget=BUDGET, actor='human', confirmed=True)
    k = tree['protocol']['k_base']
    assert result == dict(iteration_id=tree['id'], nodes=len(nodes), k=k, status='dispatched')
    for cid in nodes:
        attempts = sealed_attempts(env, cid)
        assert len(attempts) == k and all(a['execution_id'] for a in attempts)
        job = env['service'].jobs.get(tree['task_id'], attempts[0]['job_id'])
        assert job['plan_snapshot']['content']['selection']['case_ids'] == ['qasper:q3']
    with pytest.raises(Conflict, match='already claimed'):
        sealed.run_lineage(tree['id'], budget=BUDGET, actor='human', confirmed=True)
    with pytest.raises(Conflict, match='already claimed'):
        sealed.run(nodes[-1], budget=BUDGET, actor='human', confirmed=True)
    # The same sealed registration is spent for every other study too.
    other = registered_tree(env, request_key='other-study')
    stop(env, other, other['root_candidate_id'])
    with pytest.raises(Conflict, match='already claimed'):
        sealed.run_lineage(other['id'], budget=BUDGET, actor='human', confirmed=True)
    # No slot is left: an ordinary or repeated sealed run of a node is refused.
    with pytest.raises(Conflict, match='not available'):
        env['service'].run(nodes[0], budget=BUDGET, actor='human', request_key='again', batch_id='sealed:lineage',
                           _sealed_capability=_CAPABILITY)
    progress = sealed.score(tree['id'])
    assert progress['kind'] == 'lineage' and progress['nodes'] == len(nodes) and progress['curve'] is None


def test_a_v1_single_candidate_claim_blocks_the_lineage(env):
    from rag_eval.workbench.sealed import SealedService
    tree = registered_tree(env)
    stop(env, tree, tree['root_candidate_id'])
    sealed = SealedService(env['repo'], env['service'], None)
    sealed.run(tree['root_candidate_id'], budget=BUDGET, actor='human', confirmed=True)
    with pytest.raises(Conflict, match='already claimed'):
        sealed.run_lineage(tree['id'], budget=BUDGET, actor='human', confirmed=True)


def test_interrupted_dispatch_resumes_only_slots_without_an_execution(env, monkeypatch):
    from rag_eval.workbench.sealed import SealedService
    tree = registered_tree(env)
    nodes = chain(env, tree, 1)
    stop(env, tree, nodes[-1])
    service = env['service']
    sealed = SealedService(env['repo'], service, None)
    real, calls = service.run, []

    def flaky(*args, **kwargs):
        calls.append(kwargs['request_key'])
        if len(calls) == 3:
            raise OSError('synthetic dispatch failure')
        return real(*args, **kwargs)
    monkeypatch.setattr(service, 'run', flaky)
    with pytest.raises(OSError):
        sealed.run_lineage(tree['id'], budget=BUDGET, actor='human', confirmed=True)
    assert len(sealed_attempts(env, nodes[0])) == 2 and not sealed_attempts(env, nodes[1])
    # Resuming re-sends the failed slot under its own key, then the rest; nothing measured repeats.
    sealed.run_lineage(tree['id'], budget=BUDGET, actor='human', confirmed=True)
    assert calls[3:] == [calls[2], calls[2].rsplit(':', 1)[0] + ':1']
    assert [len(sealed_attempts(env, cid)) for cid in nodes] == [2, 2]


def test_curve_has_one_point_per_lineage_node_paired_with_the_seed(env, monkeypatch):
    from rag_eval.workbench.sealed import SealedService
    tree = registered_tree(env)
    nodes = chain(env, tree, 2)
    stop(env, tree, nodes[-1])
    sealed = SealedService(env['repo'], env['service'], None)
    sealed.run_lineage(tree['id'], budget=BUDGET, actor='human', confirmed=True)
    by_execution = {}
    for index, cid in enumerate(nodes):
        for rep, attempt in enumerate(sealed_attempts(env, cid)):
            by_execution[attempt['execution_id']] = .2 * index + .1 * rep
            assert sealed.lineage(tree['id'])['curve'] is None
            env['executions']._finish(attempt['execution_id'], state='finished', exit_code=0, error_type=None,
                                      run_state=dict(phase='finished'))
    monkeypatch.setattr(sealed, '_case_scores', lambda ids, execution: dict(
        metric='str_em', scores={c: by_execution[execution['execution_id']] for c in ids},
        families={c: 'paper' for c in ids}))
    result = sealed.score(tree['id'])
    assert result['status'] == 'finished' and result['metric'] == 'str_em'
    curve = result['curve']
    assert [p['node_index'] for p in curve] == [0, 1, 2] and [p['candidate_id'] for p in curve] == nodes
    assert [round(p['score'], 6) for p in curve] == [.05, .25, .45]
    assert [round(p['delta_vs_seed'], 6) for p in curve] == [0, .2, .4]
    assert result['mean'] == curve[-1]['score'] and result['m'] == 2
    assert 'qasper:' not in json.dumps(result)
    # Immutable once settled.
    monkeypatch.setattr(sealed, '_case_scores', lambda ids, execution: (_ for _ in ()).throw(ValueError('changed')))
    assert sealed.lineage(tree['id']) == result
    # lcd-curve reads the frozen curve read-only and renders one point per node.
    from rag_eval.workbench import lcd_curve
    out = Path(env['repo'].root).parent / 'report'
    lcd_curve.main(['--state', str(env['repo'].root), '--out', str(out) + '.html', '--csv', str(out) + '.csv',
                    '--json-out', str(out) + '.json'])
    html = Path(str(out) + '.html').read_text(encoding='utf-8')
    assert html.count('class="pt"') == len(nodes) * 2 and 'qasper:' not in html
    rows = Path(str(out) + '.csv').read_text(encoding='utf-8').splitlines()
    assert rows[0].startswith('node_index,candidate_id') and len(rows) == 1 + len(nodes)
    lcd_curve.main(['--from-json', str(out) + '.json', '--out', str(out) + '-copy.html', '--csv', str(out) + '-copy.csv'])
    assert Path(str(out) + '-copy.html').read_text(encoding='utf-8').count('class="pt"') == len(nodes) * 2


def test_lcd_curve_reports_progress_before_the_curve_exists(env):
    from rag_eval.workbench.sealed import SealedService
    from rag_eval.workbench import lcd_curve
    tree = registered_tree(env)
    stop(env, tree, chain(env, tree, 1)[-1])
    SealedService(env['repo'], env['service'], None).run_lineage(tree['id'], budget=BUDGET, actor='human', confirmed=True)
    data = lcd_curve.collect(env['repo'].root)
    assert data['iteration']['id'] == tree['id'] and data['lineage']['curve'] is None and data['warnings']
    assert '已派发 4 / 4' in lcd_curve.render(data)


def test_lineage_curve_pairs_cases_clusters_families_and_holm_adjusts():
    from rag_eval.workbench.sealed import lineage_curve, _holm
    seed = {f'c{i}': v for i, v in enumerate([.1, .4, .5, .2, .3, .6])}
    better = {c: v + d for (c, v), d in zip(seed.items(), [.3, .1, .2, .2, .25, .15])}
    flat = {c: v + d for (c, v), d in zip(seed.items(), [.1, -.1, .05, -.05, 0., 0.])}
    nodes = [dict(candidate_id='s', frozen_commit='a', scores=seed),
             dict(candidate_id='b', frozen_commit='b', scores=better),
             dict(candidate_id='u', frozen_commit='c', scores=None),
             dict(candidate_id='f', frozen_commit='d', scores=flat)]
    result = lineage_curve(nodes)
    seed_row, b, u, f = result['curve']
    assert result['m'] == 2 and seed_row['delta_vs_seed'] == 0 and seed_row['p'] is None
    assert u['status'] == 'unmeasurable' and u['delta_vs_seed'] is None
    assert b['lower'] > 0 and b['lower_adj'] < b['lower'] and b['upper_adj'] > b['upper']
    assert b['p_holm'] >= b['p'] and b['holds_holm'] is True and f['holds_holm'] is False
    assert f['p_holm'] >= b['p_holm']
    assert _holm([.01, .04, .03]) == pytest.approx([.03, .06, .06])
    # Family clustering widens the interval when changes move together within a paper.
    clustered = lineage_curve(nodes, families={c: c[1] in '012' and 'p1' or 'p2' for c in seed})
    assert clustered['curve'][1]['se_delta'] != b['se_delta'] and clustered['n_families'] == 2


def test_lineage_action_needs_a_person_targets_the_seed_and_carries_k(env):
    from rag_eval.workbench.action_requests import ActionRequestStore, auto_execute, execute
    tree = registered_tree(env, budget=dict(limit_cny=100, max_runs_per_candidate=10))
    nodes = chain(env, tree, 1)
    stop(env, tree, nodes[-1])
    service, store, workspace = env['service'], ActionRequestStore(env['repo']), env['workspace']['id']
    for bad in (dict(k=0), dict(k=True), dict(attempts=2), dict(cluster_id='x')):
        with pytest.raises(ValueError):
            store.request(tree['task_id'], 'iteration-sealed-lineage-run', nodes[0], bad, reason='r', requested_by='a')
    wrong = store.request(tree['task_id'], 'iteration-sealed-lineage-run', nodes[-1], {}, reason='r', requested_by='a')
    with pytest.raises(ValueError, match='seed'):
        execute(service, wrong, workspace_id=workspace, actor='human', human_confirmed=True)
    request = store.request(tree['task_id'], 'iteration-sealed-lineage-run', nodes[0], dict(k=1),
                            reason='R3 end step', requested_by='agent:analyst')
    assert request['params'] == dict(k=1, timeout_seconds=3600)
    # Never confirmed by the budget policy, never dispatched without a person.
    assert auto_execute(service, request, workspace_id=workspace)['status'] == 'pending'
    with pytest.raises(ValueError, match='human confirmation'):
        execute(service, request, workspace_id=workspace, actor='human')
    assert not any(sealed_attempts(env, cid) for cid in nodes)
    result = execute(service, request, workspace_id=workspace, actor='human', human_confirmed=True)
    assert result == dict(iteration_id=tree['id'], nodes=2, k=1, status='dispatched')
    assert [len(sealed_attempts(env, cid)) for cid in nodes] == [1, 1]


def test_lineage_action_through_the_http_confirmation(env):
    from rag_eval.workbench.iterations_api import IterationAPI
    from rag_eval.workbench.action_requests import ActionRequestStore
    tree = registered_tree(env)
    nodes = chain(env, tree, 1)
    stop(env, tree, nodes[-1])
    api = object.__new__(IterationAPI)
    api.workspace, api.actor, api.writable = env['workspace'], 'human', True
    api.service, api.repository, api.views = env['service'], env['repo'], None
    api.requests = ActionRequestStore(env['repo'])
    request = api.requests.request(tree['task_id'], 'iteration-sealed-lineage-run', nodes[0], {},
                                   reason='R3 end step', requested_by='agent:analyst')
    result = api.confirm(request['id'])
    assert result['status'] == 'executed' and result['result']['status'] == 'dispatched'
    assert api.confirm(request['id']) == result  # a receipt recovery never dispatches again
    k = tree['protocol']['k_base']
    assert [len(sealed_attempts(env, cid)) for cid in nodes] == [k, k]
