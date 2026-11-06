"""method-v4.1 deferred split: pool registration, calibration samples, one freeze."""
import pytest

from rag_eval.workbench import execution_scopes, splits
from rag_eval.workbench.repository import Conflict
from test_workbench_iterations import env, iteration, BUDGET  # noqa: F401

# Fixture bundle: paper A = qasper:q, qasper:q2; paper B = qasper:q3.
POOL = ['qasper:q', 'qasper:q2', 'qasper:q3']
P, D = ['qasper:q3'], ['qasper:q', 'qasper:q2']


def deferred(env, **changes):
    return iteration(env, **dict(dict(case_ids=POOL, split_mode='deferred', request_key='deferred'), **changes))


def parts(n=6):
    return [dict(partition_id=f'p{i}', case_ids=[f'q{i}a', f'q{i}b']) for i in range(n)]


def pending_split(pool, sealed=(), **extra):
    return dict(status='pending', pool=sorted(pool), sealed_ref='s', sealed=list(sealed), seed=0, **extra)


# ------------------------------------------------------------ pure checks
def test_check_samples_requires_disjoint_whole_papers_inside_the_pool():
    pool = [c for p in parts(5) for c in p['case_ids']]
    split = pending_split(pool, sealed=['q5a', 'q5b'])
    assert splits.check_samples(parts(), split, ['q0b', 'q0a'], ['q1a', 'q1b']) == (['q0a', 'q0b'], ['q1a', 'q1b'])
    with pytest.raises(ValueError, match='disjoint'):
        splits.check_samples(parts(), split, ['q0a', 'q0b'], ['q0a', 'q0b'])
    with pytest.raises(ValueError, match='whole papers'):
        splits.check_samples(parts(), split, ['q0a'], ['q1a', 'q1b'])
    with pytest.raises(ValueError, match='whole papers'):
        splits.check_samples(parts(), split, ['q0a', 'q0b'], ['q1a'])
    with pytest.raises(ValueError, match='pool'):
        splits.check_samples(parts(), split, ['q5a', 'q5b'], ['q1a', 'q1b'])
    with pytest.raises(ValueError, match='unique'):
        splits.check_samples(parts(), split, [], ['q1a', 'q1b'])


def test_check_freeze_enforces_probe_diag_pool_papers_and_sealed():
    pool = [c for p in parts(4) for c in p['case_ids']]
    split = pending_split(pool, probe=['q0a', 'q0b'], diagnosis=['q1a', 'q1b'])
    ok = splits.check_freeze(parts(), split, ['q1a', 'q1b', 'q2a', 'q2b'], ['q0a', 'q0b', 'q3a', 'q3b'])
    assert ok == dict(search=['q1a', 'q1b', 'q2a', 'q2b'], validation=['q0a', 'q0b', 'q3a', 'q3b'])
    with pytest.raises(ValueError, match='probe'):
        splits.check_freeze(parts(), split, ['q0a', 'q0b', 'q1a', 'q1b'], ['q2a', 'q2b', 'q3a', 'q3b'])
    with pytest.raises(ValueError, match='diagnosis'):
        splits.check_freeze(parts(), split, ['q2a', 'q2b', 'q3a', 'q3b'], ['q0a', 'q0b', 'q1a', 'q1b'])
    with pytest.raises(ValueError, match='cover exactly'):
        splits.check_freeze(parts(), split, ['q1a', 'q1b'], ['q0a', 'q0b', 'q3a', 'q3b'])
    with pytest.raises(ValueError, match='disjoint'):
        splits.check_freeze(parts(), split, ['q1a', 'q1b', 'q2a', 'q2b', 'q3a'], ['q0a', 'q0b', 'q3a', 'q3b'])
    with pytest.raises(ValueError, match='whole papers'):
        splits.check_freeze(parts(), split, ['q1a', 'q1b', 'q2a', 'q3a'], ['q0a', 'q0b', 'q2b', 'q3b'])
    sealed = dict(split, sealed=['q3a'])
    with pytest.raises(ValueError, match='sealed'):
        splits.check_freeze(parts(), sealed, ['q1a', 'q1b', 'q2a', 'q2b'], ['q0a', 'q0b', 'q3a', 'q3b'])


def test_register_pool_refuses_sealed_papers_and_unknown_cases():
    assert splits.register_pool(parts(), ['q1b', 'q0a'], sealed=['q5a']) == ['q0a', 'q1b']
    with pytest.raises(ValueError, match='sealed'):
        splits.register_pool(parts(), ['q5b'], sealed=['q5a'])
    with pytest.raises(ValueError, match='outside'):
        splits.register_pool(parts(), ['nope'])


def test_policy_and_roles_treat_a_pending_split_as_hidden(env):
    tree = deferred(env)
    assert splits.roles(tree['protocol']) is None
    policy = splits.Policy(env['repo'], tree['task_id'])
    for cid in POOL:  # nothing is visible before D is registered
        with pytest.raises(ValueError, match='search'):
            policy.require_search(cid)
    env['service'].register_calibration_samples(tree['id'], P, D, actor='alice')
    policy = splits.Policy(env['repo'], tree['task_id'])
    policy.require_search('qasper:q')
    with pytest.raises(ValueError, match='search'):
        policy.require_search('qasper:q3')
    projected = policy.project(dict(rows=[dict(case_id=c, value=1) for c in POOL], note='about qasper:q3'))
    assert [r['case_id'] for r in projected['rows']] == D and 'qasper:q3' not in projected['note']


# ------------------------------------------------------------ service
def test_eager_mode_is_unchanged(env):
    tree = iteration(env, case_ids=['qasper:q', 'qasper:q3'])
    assert set(tree['protocol']['splits']) == {'search', 'validation', 'sealed_ref', 'sealed', 'seed'}
    plan = env['service'].plans.get(tree['task_id'], tree['plan_id'])
    assert set(plan['content']['selection']['roles'].values()) == {'search', 'validation'}
    with pytest.raises(ValueError, match='split_mode'):
        iteration(env, request_key='bad', split_mode='lazy')


def test_deferred_create_registers_only_the_pool(env):
    tree = deferred(env)
    assert tree['protocol']['splits'] == dict(status='pending', pool=POOL, sealed_ref='synthetic-holdout',
                                              sealed=[], seed=0)
    assert tree['protocol']['fresh_batches'] == [] and tree['validation_pool'] == []
    plan = env['service'].plans.get(tree['task_id'], tree['plan_id'])
    assert 'roles' not in plan['content']['selection'] and plan['content']['selection']['case_ids'] == POOL
    assert deferred(env)['id'] == tree['id']  # replay
    with pytest.raises(ValueError, match='fresh'):
        deferred(env, request_key='fresh', fresh_batch_count=1, fresh_papers_per_batch=1)
    from rag_eval.workbench.iterations_api import CreateIteration
    command = CreateIteration(**{k: v for k, v in env['create'].items() if k != 'actor'}, split_mode='deferred')
    assert command.model_dump()['split_mode'] == 'deferred'
    assert CreateIteration(**{k: v for k, v in env['create'].items() if k != 'actor'}).split_mode == 'eager'


def test_samples_register_once_and_freeze_once(env):
    service = env['service']
    tree = deferred(env)
    with pytest.raises(Conflict, match='Register'):
        service.freeze_split(tree['id'], D, P, actor='alice')
    with pytest.raises(ValueError, match='whole papers'):
        service.register_calibration_samples(tree['id'], ['qasper:q'], ['qasper:q3'], actor='alice')
    after = service.register_calibration_samples(tree['id'], P, D, actor='alice')
    assert after['protocol']['splits']['probe'] == P and after['protocol']['splits']['diagnosis'] == D
    assert service.register_calibration_samples(tree['id'], P, D, actor='alice')['id'] == tree['id']  # identical replay
    with pytest.raises(Conflict, match='already registered'):
        service.register_calibration_samples(tree['id'], D, P, actor='alice')
    with pytest.raises(ValueError, match='probe'):
        service.freeze_split(tree['id'], ['qasper:q3'], ['qasper:q', 'qasper:q2'], actor='alice')
    frozen = service.freeze_split(tree['id'], D, P, actor='alice')
    split = frozen['protocol']['splits']
    assert {k: split[k] for k in ('search', 'validation', 'sealed_ref', 'sealed', 'seed', 'probe', 'diagnosis')} == \
        dict(search=D, validation=P, sealed_ref='synthetic-holdout', sealed=[], seed=0, probe=P, diagnosis=D)
    assert 'status' not in split and split['deferred']['pool_plan_revision'] == tree['plan_revision']
    assert frozen['plan_id'] == tree['plan_id'] and frozen['plan_revision'] != tree['plan_revision']
    plan = service.plans.get(tree['task_id'], tree['plan_id'])
    assert plan['revision'] == frozen['plan_revision'] and frozen['selection_hash'] == plan['content']['selection']['selection_hash']
    assert plan['content']['selection']['roles'] == {'qasper:q': 'search', 'qasper:q2': 'search', 'qasper:q3': 'validation'}
    assert service.freeze_split(tree['id'], D, P, actor='alice')['plan_revision'] == frozen['plan_revision']
    with pytest.raises(Conflict, match='already frozen'):
        service.freeze_split(tree['id'], ['qasper:q'], ['qasper:q2', 'qasper:q3'], actor='alice')
    with pytest.raises(Conflict):
        service.register_calibration_samples(tree['id'], ['qasper:q3'], ['qasper:q'], actor='alice')
    assert splits.roles(frozen['protocol']) == {'qasper:q': 'search', 'qasper:q2': 'search', 'qasper:q3': 'validation'}
    eager = iteration(env, request_key='eager', case_ids=['qasper:q', 'qasper:q3'])
    with pytest.raises(Conflict, match='pending deferred'):
        service.freeze_split(eager['id'], ['qasper:q'], ['qasper:q3'], actor='alice')


def test_subset_scope_ids_survive_the_freeze_and_follow_the_samples(env):
    service = env['service']
    tree = deferred(env)
    with pytest.raises(splits.SplitPending, match='Register'):
        execution_scopes.subset_scope_id(tree['protocol'], 'probe', P)
    tree = service.register_calibration_samples(tree['id'], P, D, actor='alice')
    pending = tree['protocol']
    probe_id = execution_scopes.subset_scope_id(pending, 'probe', P)
    diag_id = execution_scopes.subset_scope_id(pending, 'diag', ['qasper:q'])
    with pytest.raises(ValueError, match='probe sample'):
        execution_scopes.subset_scope_id(pending, 'probe', ['qasper:q'])
    with pytest.raises(ValueError, match='diagnosis sample'):
        execution_scopes.subset_scope_id(pending, 'diag', P)
    for kind in ('mb', 'rest'):
        with pytest.raises(splits.SplitPending, match='not frozen'):
            execution_scopes.subset_scope_id(pending, kind, P)
    scope = execution_scopes.subset_plan(service, tree, 'probe', P)
    plan = service.plans.get(tree['task_id'], scope['plan_id'])
    assert plan['content']['selection']['roles'] == {'qasper:q3': 'validation'}
    diag = execution_scopes.subset_plan(service, tree, 'diag', D)
    assert service.plans.get(tree['task_id'], diag['plan_id'])['content']['selection']['roles'] == {c: 'search' for c in D}
    # Calibration can measure the seed on a pending split through its subset scope.
    root = service.run(tree['root_candidate_id'], budget=BUDGET, actor='alice', request_key='cal-probe',
                       batch_id=scope['scope_id'])
    assert root['attempts'][-1]['batch_id'] == scope['scope_id'] and root['attempts'][-1]['execution_id']
    frozen = service.freeze_split(tree['id'], D, P, actor='alice')
    assert execution_scopes.subset_scope_id(frozen['protocol'], 'probe', P) == probe_id
    assert execution_scopes.subset_scope_id(frozen['protocol'], 'diag', ['qasper:q']) == diag_id
    assert execution_scopes.subset_plan(service, frozen, 'probe', P) == scope
    assert execution_scopes.subset_scope_id(frozen['protocol'], 'rest', P) == 'subset:rest:' + probe_id.split(':')[-1]
    with pytest.raises(ValueError, match='validation'):
        execution_scopes.subset_scope_id(frozen['protocol'], 'probe', ['qasper:q'])
    with pytest.raises(ValueError, match='search'):
        execution_scopes.subset_scope_id(frozen['protocol'], 'diag', P)


def test_consumers_refuse_a_pending_split_clearly(env):
    service = env['service']
    tree = service.register_calibration_samples(deferred(env)['id'], P, D, actor='alice')
    protocol = tree['protocol']
    for call in (lambda: execution_scopes.smoke_scope_id(protocol, D),
                 lambda: execution_scopes.split_scope_id(protocol, 'search'),
                 lambda: splits.require_frozen(protocol, 'Test')):
        with pytest.raises(splits.SplitPending, match='not frozen'):
            call()
    with pytest.raises(splits.SplitPending, match='not frozen'):
        service.run(tree['root_candidate_id'], budget=BUDGET, actor='alice', request_key='base')
    from rag_eval.optimizer.sn_adapter import SNAdapter
    adapter = SNAdapter.__new__(SNAdapter)
    adapter._protocol = lambda: protocol
    with pytest.raises(splits.SplitPending, match='not frozen'):
        adapter.partitions()
    with pytest.raises(splits.SplitPending, match='not frozen'):
        adapter._case_scopes(['qasper:q'], 'diagnose', protocol)
    assert splits.require_frozen(service.freeze_split(tree['id'], D, P, actor='alice')['protocol'])['search'] == D


def test_diagnosis_can_grow_monotonically_before_freeze(env, monkeypatch):
    service = env['service']
    pool = [c for paper in parts(4) for c in paper['case_ids']]
    state = dict(protocol=dict(splits=pending_split(pool)))
    monkeypatch.setattr(service, '_pending_split', lambda _: ({}, state['protocol'], parts(4)))
    monkeypatch.setattr(service, 'iteration', lambda _: dict(state))
    def replace(_, before, after, **kwargs):
        assert before == state['protocol']
        state['protocol'] = after
    monkeypatch.setattr(service, '_replace_protocol', replace)
    probe, diag = ['q0a', 'q0b'], ['q1a', 'q1b']
    service.register_calibration_samples('i', probe, diag, actor='alice')
    grown = service.register_calibration_samples('i', probe, diag + ['q2a', 'q2b'], actor='alice')
    assert grown['protocol']['splits']['diagnosis'] == diag + ['q2a', 'q2b']
    with pytest.raises(Conflict):
        service.register_calibration_samples('i', probe, diag, actor='alice')
    with pytest.raises(Conflict):
        service.register_calibration_samples('i', ['q3a', 'q3b'], diag + ['q2a', 'q2b'], actor='alice')
    with pytest.raises(ValueError, match='whole papers'):
        service.register_calibration_samples('i', probe, diag + ['q2a', 'q2b', 'q3a'], actor='alice')
