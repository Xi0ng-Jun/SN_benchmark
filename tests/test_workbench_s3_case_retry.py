"""S3: in-attempt re-asks of failed cases, frozen in the iteration protocol and counted in the ledger.

1. The rule is frozen at creation (default one re-ask per case) and drives every attempt;
   iterations without the key keep their old behaviour.
2. A re-ask that answers admits the attempt; a case still failed after the limit takes the infra path.
3. Every re-ask is a case run: reserved at its worst case, settled to what happened.
4. Each attempt records which cases were re-asked, how often and why they failed.
5. Comparisons and gate results show both sides' re-ask rates without changing the verdict.
"""
import json
from pathlib import Path

import pytest

from rag_eval.workbench.budgets import BudgetLedger
from test_workbench_budgets import setup, step
from test_workbench_iterations import BUDGET, env, iteration, payload  # noqa: F401


# ------------------------------------------------------------------ 1. protocol
def test_new_iterations_freeze_one_re_ask_and_attempts_use_the_frozen_rule(env):
    service = env['service']
    service.execution_config = dict(service.execution_config, retry_rounds=3)
    tree = iteration(env)
    assert tree['protocol']['case_retry'] == {'max_retries_per_case': 1}
    # A replay of the same creation (no explicit rule) is the same iteration.
    assert iteration(env)['id'] == tree['id']
    root = service.run(tree['root_candidate_id'], budget=BUDGET, actor='alice', request_key='r')
    assert payload(env, root)['retry_rounds'] == 1  # the protocol overrides the server setting
    assert payload(env, root)['case_retry_rule'] is True
    off = iteration(env, case_retry={'max_retries_per_case': 0}, request_key='off', title='off')
    assert off['protocol']['case_retry'] == {'max_retries_per_case': 0}
    root = service.run(off['root_candidate_id'], budget=BUDGET, actor='alice', request_key='r0')
    assert payload(env, root)['retry_rounds'] == 0
    for bad in ({'max_retries_per_case': -1}, {'max_retries_per_case': 11}, {'other': 1},
                {'max_retries_per_case': True}, 'one'):
        with pytest.raises(ValueError, match='case_retry'):
            iteration(env, case_retry=bad, request_key='bad', title='bad')


def _strip_rule(env, tree):
    """An iteration frozen before the rule existed."""
    protocol = dict(tree['protocol'])
    protocol.pop('case_retry')
    with env['repo'].connect(write=True) as conn:
        conn.execute('UPDATE iterations SET protocol_json=? WHERE id=?', (json.dumps(protocol), tree['id']))


def test_iterations_without_the_rule_keep_their_old_behaviour(env):
    service = env['service']
    service.execution_config = dict(service.execution_config, retry_rounds=3)
    plain = iteration(env)
    _strip_rule(env, plain)
    root = service.run(plain['root_candidate_id'], budget=BUDGET, actor='alice', request_key='p')
    assert payload(env, root)['retry_rounds'] == 3  # unbudgeted: the server setting
    assert 'case_retry_rule' not in payload(env, root)  # its retries never replace failed answers
    budgeted = iteration(env, budget=dict(max_case_runs=100, max_runs_per_candidate=10),
                         request_key='b', title='budgeted')
    _strip_rule(env, budgeted)
    root = service.run(budgeted['root_candidate_id'], budget=BUDGET, actor='alice', request_key='b')
    assert payload(env, root)['retry_rounds'] == 0  # budgeted: retries were new attempts
    with env['repo'].connect() as conn:
        assert conn.execute('SELECT retry_case_count FROM iteration_budget_reservations').fetchone()[0] == 0


# ------------------------------------------------------------------ 3. ledger
def counted(tmp_path, cap=100, retries=1):
    repo, tree = setup(tmp_path)
    tree['protocol']['budget'] = dict(max_case_runs=cap, max_runs_per_candidate=10)
    if retries is not None:
        tree['protocol']['case_retry'] = dict(max_retries_per_case=retries)
    return repo, tree, BudgetLedger(repo)


def settle(ledger, tree, tmp_path, key, execution_id, re_asked, *, state='finished', cny=None):
    run = tmp_path / execution_id
    run.mkdir()
    if re_asked is not None:  # None: the execution never wrote its retry record
        (run / 'retry-rounds.json').write_text(json.dumps(dict(format='sn-workbench-retry-rounds-v2', rounds=[],
            partitions={'0001': dict(run_dir=str(run), retries=[dict(round=1, case_ids=re_asked, complete=True)])})))
    ledger.attach(tree['id'], 'candidate', key, execution_id)
    run_state = dict(cost_observation=dict(currency='CNY', complete=True, total_cny=cny)) if cny is not None else None
    ledger.settle_execution(dict(execution_id=execution_id, terminal=True, state=state, run_dir=str(run),
                                 run_state=run_state))


def test_re_asks_are_reserved_at_worst_case_and_settled_to_what_happened(tmp_path):
    repo, tree, ledger = counted(tmp_path, cap=10)
    assert ledger.reserve_many(tree, [step('a', count=4)], group_key='a')['status'] == 'reserved'
    assert ledger.summary(tree['id'])['case_runs_used'] == 8  # 4 cases, each may be asked twice
    # A second attempt cannot slip under the cap by ignoring the first one's pending re-asks.
    denied = ledger.reserve_many(tree, [step('b', count=2)], group_key='b')
    assert denied['status'] == 'budget_exceeded' and denied['reasons'] == ['max_case_runs']
    settle(ledger, tree, tmp_path, 'a', 'exec-a', ['q1'], cny=2.0)
    assert ledger.summary(tree['id'])['case_runs_used'] == 5  # 4 first asks + 1 re-ask
    assert ledger.reserve_many(tree, [step('b', count=2)], group_key='b')['status'] == 'reserved'


@pytest.mark.parametrize('state', ['finished', 'failed', 'cancelled'])
def test_without_a_retry_record_the_worst_case_stays_charged(tmp_path, state):
    repo, tree, ledger = counted(tmp_path)
    ledger.reserve_many(tree, [step('a', count=3)], group_key='a')
    settle(ledger, tree, tmp_path, 'a', 'exec-a', None, state=state)
    assert ledger.summary(tree['id'])['case_runs_used'] == 6


@pytest.mark.parametrize('state', ['finished', 'failed'])
def test_re_asks_settle_without_any_cost_observation(tmp_path, state):
    # Count-only budgets (company models, no limit_cny) never observe cost.
    repo, tree, ledger = counted(tmp_path)
    ledger.reserve_many(tree, [step('a', count=3)], group_key='a')
    settle(ledger, tree, tmp_path, 'a', 'exec-a', ['q1'], state=state)
    summary = ledger.summary(tree['id'])
    assert summary['case_runs_used'] == 4 and summary['unknown_count'] == 1


def test_without_the_rule_nothing_extra_is_reserved_and_old_rows_count_as_none(tmp_path):
    repo, tree, ledger = counted(tmp_path, cap=4, retries=None)
    assert ledger.reserve_many(tree, [step('a', count=4)], group_key='a')['status'] == 'reserved'
    with repo.connect(write=True) as conn:  # a row written before the column existed
        conn.execute('UPDATE iteration_budget_reservations SET retry_case_count=NULL')
    assert ledger.summary(tree['id'])['case_runs_used'] == 4


def test_per_case_estimate_divides_by_asks_including_re_asks(tmp_path):
    repo, tree, ledger = counted(tmp_path)
    ledger.reserve_many(tree, [step('a', count=4)], group_key='a')
    settle(ledger, tree, tmp_path, 'a', 'exec-a', ['q1', 'q2'], cny=6.0)
    with repo.connect() as conn:
        assert ledger._estimate(conn, tree) == (1.0, 'historical_observed_per_case')


# ------------------------------------------------------------------ 2. admission and infra
def test_formal_admission_excuses_only_cases_a_retry_answered():
    from rag_eval.benchmark_comparison import require_complete
    submission = dict(coverage=dict(generation_complete=False), predictions=[
        dict(case_id='a', status='success'), dict(case_id='b', status='error'), dict(case_id='c', status='missing')])
    with pytest.raises(ValueError, match='complete generation'):
        require_complete(submission, ['b'])
    require_complete(submission, ['b', 'c'])
    require_complete(dict(coverage=dict(generation_complete=True), predictions=[]))


@pytest.mark.parametrize('rule', [True, False])
def test_run_admission_and_flips_pass_retried_cases_to_formal_admission(tmp_path, monkeypatch, rule):
    from rag_eval import flip_report
    from rag_eval.workbench.run_views import RunViews
    root = tmp_path / 'exec'
    first, r1, r2 = root / 'partitions/0001', root / 'retry-1/0001/r', root / 'retry-2/0001/r'
    for path in (first, r1, r2):
        path.mkdir(parents=True)
    (root / 'batch-plan.json').write_text(json.dumps([dict(
        partition_id='p', case_ids=['a', 'b', 'c'], run_dir=str(first), suite='alce', mode='chunk',
        official_name='0001', retries=[
            dict(round=1, run_dir=str(r1), case_ids=['b', 'c'], official_name='0001-r1', complete=True,
                 selected_case_ids=['b'], **({'replaces_failed': True} if rule else {})),
            dict(round=2, run_dir=str(r2), case_ids=['c'], official_name='0001-r2', complete=True,
                 selected_case_ids=['c'], **({'replaces_failed': True} if rule else {}))])]))
    seen = {}

    def spy(before, after, *, before_replaced=(), **kwargs):
        seen[Path(before).relative_to(root).parts[0]] = sorted(before_replaced)
        return dict(admission=dict(status='admitted', reason=None), before={}, cases=[])
    monkeypatch.setattr(flip_report, 'compare_runs_flips', spy)
    views = RunViews([], execution_records=lambda: [dict(execution_id='exec', task_id='t', run_dir=str(root),
                                                         state='finished', terminal=True)])
    assert views.admission('exec-1')['status'] == 'admitted'
    if not rule:  # server-level retries (older iterations, plain jobs): nothing is excused, as before
        assert seen == {'partitions': [], 'retry-1': [], 'retry-2': []}
        return
    # The partition run is excused for both re-asked cases; round 1 for the case round 2 answered.
    assert seen == {'partitions': ['b', 'c'], 'retry-1': ['c'], 'retry-2': []}
    seen.clear()
    views.flips('exec-1', 'exec-1', bundle=object())
    assert seen['partitions'] == ['b', 'c']


class _Views:
    def __init__(self, statuses, retries=(), replace=True):
        self.statuses, self.retries, self.replace = statuses, retries, replace

    def execution_runs(self, execution_id):
        from types import SimpleNamespace
        return [SimpleNamespace(id='run', retries=self.retries, retries_replace=self.replace)]

    def cases(self, run_id):
        return dict(cases=[dict(case_id=c, status=s) for c, s in self.statuses[run_id].items()])


@pytest.mark.parametrize('retry_status,infra', [('success', False), ('error', True), (None, True)])
def test_infra_retry_only_when_a_case_is_still_failed_after_its_re_asks(retry_status, infra):
    from rag_eval.workbench.loop_measurements import RoundMeasurements
    measurements = object.__new__(RoundMeasurements)
    statuses = dict(run=dict(a='success', b='error'), retry={} if retry_status is None else dict(b=retry_status))
    measurements.views = _Views(statuses, retries=(('retry', ('b',)),))
    assert measurements._infra(dict(execution_id='e', execution_state='finished')) is infra
    measurements.views = _Views(dict(run=dict(a='success', b='error')))  # no retries
    assert measurements._infra(dict(execution_id='e', execution_state='finished')) is True
    # Retries outside the frozen rule do not rescue the attempt (behaviour before S3).
    measurements.views = _Views(statuses, retries=(('retry', ('b',)),), replace=False)
    assert measurements._infra(dict(execution_id='e', execution_state='finished')) is True


# ------------------------------------------------------------------ 4. per-attempt record
@pytest.mark.parametrize('first,again,category', [
    (dict(response=dict(llm_mode='synthesis_failed')), 'success', 'synthesis_failed'),
    (dict(error_type='case_timeout', failed_phase='asking'), 'error', 'case_timeout'),
])
def test_execution_records_re_asked_cases_with_failure_categories(tmp_path, monkeypatch, first, again, category):
    from test_workbench_seeded_execution import fake_scoring, spec_for
    from rag_eval.workbench.notebook_job import execute
    _, payload_, prep = spec_for(tmp_path, retry_rounds=1, case_retry_rule=True)
    assert payload_['case_retry_rule'] is True
    case_ids = prep['input']['selection']['case_ids']

    def plan(**kwargs):
        ids = Path(kwargs['case_id_file']).read_text().split()
        return [dict(partition_id=payload_['selection']['partition_ids'][case_ids.index(cid)], case_ids=[cid],
                     run_dir=str(kwargs['output_root'] / cid), suite='alce', mode='chunk') for cid in ids]

    def batch(jobs, *, output_root, parallel, after_run=None, **kwargs):
        rows = []
        for job in jobs:
            cid, run = job['case_ids'][0], Path(job['run_dir'])
            status = again if 'retry-1' in str(output_root) else 'error'
            run.mkdir(parents=True)
            (run / 'product-artifacts').mkdir()
            (run / 'product-artifacts/state.json').write_text('{"prepared": true}')
            record = dict(first, status=status) if status == 'error' else dict(status=status)
            (run / 'outputs.jsonl').write_text(json.dumps(dict(case_id=cid, status=status,
                                                               product_record=record)) + '\n')
            (run / 'state.json').write_text(json.dumps({'phase': 'finished'}))
            (run / 'manifest.json').write_text('{}')
            rows.append(dict(run_dir=job['run_dir'], exit_code=0))
            if after_run is not None:
                after_run(job, rows[-1])
        return dict(exit_code=0, runs=rows)
    monkeypatch.setattr('rag_eval.notebook_batch.plan_batch', plan)
    monkeypatch.setattr('rag_eval.notebook_batch.execute_batch', batch)
    fake_scoring(monkeypatch, lambda run, cid: 1.0)
    execute(payload_)
    saved = json.loads((Path(payload_['run_dir']) / 'retry-rounds.json').read_text())
    n = len(case_ids)
    assert saved['case_retry'] == dict(max_retries_per_case=1, frozen_rule=True, cases=n, first_failed=n,
                                       retried_cases=n,
                                       re_asks=n, still_failed=n if again == 'error' else 0,
                                       categories={category: n})
    case = saved['partitions']['0001']['cases'][case_ids[0]]
    assert case['re_asks'] == 1 and case['final_status'] == again
    assert case['first_failure']['category'] == category
    assert case['scored_from'].endswith('retry-1/0001/' + case_ids[0])
    plan = json.loads((Path(payload_['run_dir']) / 'batch-plan.json').read_text())
    assert plan[0]['retries'][0]['replaces_failed'] is True


def test_failure_categories():
    from rag_eval.workbench.notebook_job import failure_category
    assert failure_category(None) == 'missing'
    assert failure_category(dict(product_record=dict(error_type='partition_deadline'))) == 'partition_deadline'
    assert failure_category(dict(product_record=dict(error_type='ReadTimeout'))) == 'model_infrastructure'
    assert failure_category(dict(product_record=dict(reason='native invocation/capture failed'))) == 'capture_failed'
    assert failure_category(dict(product_record=dict(error_type='ValueError'))) == 'other'


# ------------------------------------------------------------------ 5. side-by-side disclosure
def test_comparisons_and_gate_show_both_sides_re_ask_rates_without_changing_the_verdict(tmp_path):
    from rag_eval.workbench.comparisons import ComparisonService
    from rag_eval.workbench.gate import DEFAULT_RULE, judge
    service = object.__new__(ComparisonService)

    def side(name, record):
        run = tmp_path / name
        run.mkdir()
        if record is not None:
            (run / 'retry-rounds.json').write_text(json.dumps(dict(case_retry=record)))
        return dict(attempts=[dict(execution_id=name, execution_state='finished', run_dir=str(run))], links=[])
    child = side('child', dict(cases=10, first_failed=3, re_asks=3, still_failed=1,
                               categories=dict(synthesis_failed=3)))
    parent = side('parent', dict(cases=10, first_failed=1, re_asks=1, still_failed=0,
                                 categories=dict(case_timeout=1)))
    legacy = side('legacy', None)
    stats = service._retry_stats(child)
    assert (stats['re_asks'], stats['re_ask_rate'], stats['still_failed'], stats['unobserved']) == (3, .3, 1, 0)
    assert service._retry_stats(legacy)['unobserved'] == 1 and service._retry_stats(legacy)['re_ask_rate'] is None
    summary = dict(subject=stats, reference=service._retry_stats(parent))
    noise = dict(eta=.05, reference=dict(success_threshold=.5, k_base=2, cases={}),
                 subject=dict(success_threshold=.5, k_base=2, cases={}))
    body = dict(admission=dict(status='admitted'), mean=dict(delta=.1),
                rows=[dict(case_id='c1', left=.4, right=.6, delta=.2)])
    contract = dict(predicted_flips=['c1'], guards=[])
    plain = judge(body, noise, contract, DEFAULT_RULE)
    disclosed = judge(dict(body, retry_summary=summary), noise, contract, DEFAULT_RULE)
    assert disclosed['case_retry'] == summary and plain['case_retry'] is None
    assert {k: v for k, v in disclosed.items() if k != 'case_retry'} == {k: v for k, v in plain.items()
                                                                         if k != 'case_retry'}


# ------------------------------------------------------------------ deployment over an older ledger
def _drop_retry_column(repo):
    with repo.connect(write=True) as conn:
        conn.execute('ALTER TABLE iteration_budget_reservations DROP COLUMN retry_case_count')


def test_iterations_with_reservations_read_and_settle_on_a_ledger_without_the_new_column(env, tmp_path):
    from rag_eval.workbench.iterations import initialize_iterations
    repo = env['repo']
    tree = iteration(env, budget=dict(max_case_runs=100, max_runs_per_candidate=10))
    BudgetLedger(repo).reserve_many(tree, [step(candidate=tree['root_candidate_id'], count=2)], group_key='old')
    _drop_retry_column(repo)  # as written by the deployed version
    # Readers never migrate (they may run inside an open writer) and still work.
    assert env['service'].iteration(tree['id'])['budget_usage']['case_runs_used'] == 2
    # Settlement through the execution store's ledger migrates inside its own writer.
    ledger = BudgetLedger(repo, initialize=False)
    ledger.attach(tree['id'], tree['root_candidate_id'], 'one', 'exec-old')
    run = tmp_path / 'exec-old'
    run.mkdir()
    ledger.settle_execution(dict(execution_id='exec-old', terminal=True, state='finished', run_dir=str(run),
                                 run_state=None))
    with repo.connect() as conn:
        assert conn.execute('SELECT state FROM iteration_budget_reservations').fetchone()[0] == 'unknown'
        assert 'retry_case_count' in {r['name'] for r in conn.execute('PRAGMA table_info(iteration_budget_reservations)')}
    # Service start migrates too.
    _drop_retry_column(repo)
    initialize_iterations(repo)
    with repo.connect() as conn:
        assert 'retry_case_count' in {r['name'] for r in conn.execute('PRAGMA table_info(iteration_budget_reservations)')}


def test_an_interrupted_retry_round_still_counts_its_re_asks(tmp_path, monkeypatch):
    from test_workbench_seeded_execution import fake_scoring, spec_for
    from rag_eval.workbench.budgets import _count_retried_cases
    from rag_eval.workbench.notebook_job import execute
    from rag_eval.workbench.run_views import RunViews
    _, payload_, prep = spec_for(tmp_path, retry_rounds=1)
    case_ids = prep['input']['selection']['case_ids']

    def plan(**kwargs):
        ids = Path(kwargs['case_id_file']).read_text().split()
        return [dict(partition_id=payload_['selection']['partition_ids'][case_ids.index(cid)], case_ids=[cid],
                     run_dir=str(kwargs['output_root'] / cid), suite='alce', mode='chunk') for cid in ids]

    def batch(jobs, *, output_root, parallel, after_run=None, **kwargs):
        if 'retry-1' in str(output_root):
            raise RuntimeError('supervisor lost the retry child')
        rows = []
        for job in jobs:
            cid, run = job['case_ids'][0], Path(job['run_dir'])
            run.mkdir(parents=True)
            (run / 'product-artifacts').mkdir()
            (run / 'product-artifacts/state.json').write_text('{"prepared": true}')
            (run / 'outputs.jsonl').write_text(json.dumps(dict(case_id=cid, status='error', product_record={})) + '\n')
            (run / 'state.json').write_text('{"phase": "finished"}')
            (run / 'manifest.json').write_text('{}')
            rows.append(dict(run_dir=job['run_dir'], exit_code=0))
            if after_run is not None:
                after_run(job, rows[-1])
        return dict(exit_code=0, runs=rows)
    monkeypatch.setattr('rag_eval.notebook_batch.plan_batch', plan)
    monkeypatch.setattr('rag_eval.notebook_batch.execute_batch', batch)
    fake_scoring(monkeypatch, lambda run, cid: 0.0)
    execute(payload_)
    run = Path(payload_['run_dir'])
    assert _count_retried_cases(dict(run_dir=str(run))) == len(case_ids)
    saved = json.loads((run / 'retry-rounds.json').read_text())
    assert saved['case_retry']['re_asks'] == len(case_ids) and saved['case_retry']['still_failed'] == len(case_ids)
    # The interrupted round is never registered as an answering run.
    views = RunViews([], execution_records=lambda: [dict(execution_id='exec', task_id='t', run_dir=str(run),
                                                         state='finished', terminal=True)])
    assert [r.retries for r in views.execution_runs('exec')] == [()] * len(case_ids)
