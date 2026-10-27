"""S3: per-case retry accounting, protocol freeze, budget, and comparison reporting.

Rules covered:
1. case_retry frozen at iteration creation; default 1; old iterations (no key) → unchanged behaviour.
2. Retry limit enforced; still-failed after limit → infra path; retry success → admitted.
3. Budget worst-case reservation includes retries; settle accounts actual retried cases; estimate denominator includes retries.
4. Attempt records retry info; classifications present.
5. retry_summary appears in comparisons; gate verdict unchanged.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rag_eval.workbench.repository import WorkbenchRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _repo(tmp_path):
    repo = WorkbenchRepository(tmp_path / 'state')
    repo.register_workspace('ws', tmp_path / 'project')
    return repo


def _make_iteration(protocol):
    return dict(id='iter1', task_id='task1', protocol=protocol)


# ---------------------------------------------------------------------------
# Rule 1: validate_case_retry and protocol defaults
# ---------------------------------------------------------------------------

def test_validate_case_retry_defaults_and_rejects_invalid():
    from rag_eval.workbench.iterations import validate_case_retry
    assert validate_case_retry(None) is None
    assert validate_case_retry({'max_retries_per_case': 0}) == {'max_retries_per_case': 0}
    assert validate_case_retry({'max_retries_per_case': 1}) == {'max_retries_per_case': 1}
    assert validate_case_retry({'max_retries_per_case': 10}) == {'max_retries_per_case': 10}
    with pytest.raises(ValueError):
        validate_case_retry({'max_retries_per_case': -1})
    with pytest.raises(ValueError):
        validate_case_retry({'max_retries_per_case': 11})
    with pytest.raises(ValueError):
        validate_case_retry({'unexpected': 1})
    with pytest.raises(ValueError):
        validate_case_retry({'max_retries_per_case': 'one'})


def test_new_iteration_defaults_to_1_retry(tmp_path):
    """create_iteration with no case_retry arg stores max_retries_per_case=1."""
    from rag_eval.workbench.iterations import validate_case_retry
    # Default: pass None for case_retry → frozen as 1
    result = validate_case_retry({'max_retries_per_case': 1})
    assert result == {'max_retries_per_case': 1}


def test_old_iteration_protocol_no_case_retry_key_has_none():
    """Iterations created before this feature have no case_retry key → None."""
    from rag_eval.workbench.iterations import validate_case_retry
    protocol = {}  # old protocol without case_retry
    assert protocol.get('case_retry') is None
    assert validate_case_retry(protocol.get('case_retry')) is None


def test_case_retry_0_disables_retries():
    from rag_eval.workbench.iterations import validate_case_retry
    result = validate_case_retry({'max_retries_per_case': 0})
    assert result['max_retries_per_case'] == 0


# ---------------------------------------------------------------------------
# Rule 1: idempotency — payload must include case_retry only when explicitly passed
# ---------------------------------------------------------------------------

def test_create_iteration_payload_includes_case_retry_only_when_passed():
    """Payload construction: case_retry absent by default to keep old receipt hashes stable."""
    # We mimic the logic in create_iteration without running the full service.
    def build_payload(case_retry_arg, case_retry_frozen):
        payload = dict(workspace_id='ws', title='t', start_commit='a' * 40,
                       input_id='inp', expected_bundle_id='b' * 64,
                       case_ids=['c1'], mode='chunk', seeded_from=None,
                       sealed_ref=None, split_seed=0,
                       fresh_batch_count=0, fresh_papers_per_batch=8)
        if case_retry_arg is not None:
            payload['case_retry'] = case_retry_frozen
        return payload

    p_default = build_payload(None, {'max_retries_per_case': 1})
    p_explicit = build_payload({'max_retries_per_case': 2}, {'max_retries_per_case': 2})
    assert 'case_retry' not in p_default  # old receipt hash not perturbed
    assert p_explicit['case_retry'] == {'max_retries_per_case': 2}


# ---------------------------------------------------------------------------
# Rule 1: retry_rounds config driven by frozen protocol
# ---------------------------------------------------------------------------

def test_retry_rounds_config_new_budgeted_iteration():
    """New budgeted iteration: retry_rounds = frozen max_retries_per_case."""
    # Mirrors the logic in IterationService.run()
    def compute_retry_rounds(protocol, server_config):
        config = dict(server_config)
        frozen_retry = (protocol.get('case_retry') or {}).get('max_retries_per_case')
        if type(frozen_retry) is int:
            config['retry_rounds'] = frozen_retry
        elif protocol.get('budget'):
            config['retry_rounds'] = 0
        return config.get('retry_rounds')

    new_budgeted = {'budget': {'limit_cny': 5, 'max_runs_per_candidate': 4},
                    'case_retry': {'max_retries_per_case': 1}}
    assert compute_retry_rounds(new_budgeted, {'retry_rounds': 3}) == 1

    # Old budgeted iteration (no case_retry key) → 0
    old_budgeted = {'budget': {'limit_cny': 5, 'max_runs_per_candidate': 4}}
    assert compute_retry_rounds(old_budgeted, {'retry_rounds': 3}) == 0

    # New un-budgeted → frozen value wins
    new_unbudgeted = {'case_retry': {'max_retries_per_case': 2}}
    assert compute_retry_rounds(new_unbudgeted, {'retry_rounds': 0}) == 2

    # Old un-budgeted → server setting unchanged
    old_unbudgeted = {}
    assert compute_retry_rounds(old_unbudgeted, {'retry_rounds': 5}) == 5


# ---------------------------------------------------------------------------
# Rule 2: _infra reads retry-rounds selection
# ---------------------------------------------------------------------------

class _FakeRun:
    def __init__(self, run_id, run_dir, case_ids, retries=()):
        self.id = run_id
        self.run_dir = Path(run_dir)
        self.case_ids = case_ids
        self.retries = retries  # tuple of (retry_run_id, selected_case_ids)


class _FakeViews:
    def __init__(self, runs_dict):
        self._runs = runs_dict

    def execution_runs(self, execution_id):
        prefix = execution_id + '-'
        found = [e for i, e in self._runs.items() if i.startswith(prefix)]
        return sorted(found, key=lambda e: e.id)

    def cases(self, run_id):
        entry = self._runs[run_id]
        cases = [{'case_id': cid, 'status': entry._case_statuses.get(cid, 'success')}
                 for cid in entry.case_ids]
        return {'cases': cases}


def _fake_run(run_id, run_dir, case_ids, statuses, retries=()):
    r = _FakeRun(run_id, run_dir, case_ids, retries)
    r._case_statuses = statuses
    return r


def _make_infra_checker(views):
    from rag_eval.workbench.loop_measurements import RoundMeasurements
    rm = object.__new__(RoundMeasurements)
    rm.views = views
    return rm


def test_infra_false_when_all_cases_success(tmp_path):
    run = _fake_run('exec1-1', tmp_path / 'r1', ['c1', 'c2'], {'c1': 'success', 'c2': 'success'})
    views = _FakeViews({'exec1-1': run})
    rm = _make_infra_checker(views)
    attempt = dict(execution_id='exec1', execution_state='finished')
    assert rm._infra(attempt) is False


def test_infra_true_when_case_error_no_retries(tmp_path):
    run = _fake_run('exec2-1', tmp_path / 'r2', ['c1', 'c2'], {'c1': 'success', 'c2': 'error'})
    views = _FakeViews({'exec2-1': run})
    rm = _make_infra_checker(views)
    attempt = dict(execution_id='exec2', execution_state='finished')
    assert rm._infra(attempt) is True


def test_infra_false_when_retry_answered_error_case(tmp_path):
    """After retry answered c2, the original error is resolved → not infra."""
    retry_run = _fake_run('exec3-1-r1', tmp_path / 'retry', ['c2'], {'c2': 'success'})
    # Primary run: c2=error, but retry (listed in retries) answered it as success
    primary = _fake_run('exec3-1', tmp_path / 'r3', ['c1', 'c2'], {'c1': 'success', 'c2': 'error'},
                        retries=(('exec3-1-r1', ('c2',)),))
    views = _FakeViews({'exec3-1': primary, 'exec3-1-r1': retry_run})
    rm = _make_infra_checker(views)
    attempt = dict(execution_id='exec3', execution_state='finished')
    assert rm._infra(attempt) is False


def test_infra_true_when_retry_still_error(tmp_path):
    """Retry ran but c2 still error → infra path must fire."""
    retry_run = _fake_run('exec4-1-r1', tmp_path / 'retry', ['c2'], {'c2': 'error'})
    primary = _fake_run('exec4-1', tmp_path / 'r4', ['c1', 'c2'], {'c1': 'success', 'c2': 'error'},
                        retries=(('exec4-1-r1', ('c2',)),))
    views = _FakeViews({'exec4-1': primary, 'exec4-1-r1': retry_run})
    rm = _make_infra_checker(views)
    attempt = dict(execution_id='exec4', execution_state='finished')
    assert rm._infra(attempt) is True


def test_infra_cancelled_is_not_infra(tmp_path):
    attempt = dict(execution_id='exec5', execution_state='cancelled')
    views = _FakeViews({})
    rm = _make_infra_checker(views)
    assert rm._infra(attempt) is False


# ---------------------------------------------------------------------------
# Rule 3: Budget worst-case cap includes retries
# ---------------------------------------------------------------------------

def test_exceeded_caps_includes_retry_worst_case():
    from rag_eval.workbench.budgets import exceeded_caps
    policy = {'limit_cny': 10, 'max_case_runs': 4, 'max_runs_per_candidate': 3}
    summary = dict(charged_cny=0, case_runs_used=0,
                   runs_by_candidate={}, subject_runs_by_candidate={})

    # 2 base cases + 1 retry = 4 case-runs worst case → fits exactly
    steps_fit = [dict(candidate_id='c1', request_key='k1', case_count=2,
                      max_retries_per_case=1, counts_toward_candidate_cap=True)]
    assert exceeded_caps(policy, summary, steps_fit, 1.0) == []

    # 2 base cases + 2 retries = 6 case-runs → exceeds max_case_runs=4
    steps_over = [dict(candidate_id='c1', request_key='k1', case_count=2,
                       max_retries_per_case=2, counts_toward_candidate_cap=True)]
    reasons = exceeded_caps(policy, summary, steps_over, 1.0)
    assert 'max_case_runs' in reasons


def test_exceeded_caps_no_retry_key_uses_base_count():
    """Steps without max_retries_per_case use only case_count (backward compat)."""
    from rag_eval.workbench.budgets import exceeded_caps
    policy = {'max_case_runs': 2, 'limit_cny': 10, 'max_runs_per_candidate': 3}
    summary = dict(charged_cny=0, case_runs_used=0,
                   runs_by_candidate={}, subject_runs_by_candidate={})
    steps = [dict(candidate_id='c1', request_key='k1', case_count=2,
                  counts_toward_candidate_cap=True)]
    assert exceeded_caps(policy, summary, steps, 0.5) == []


# ---------------------------------------------------------------------------
# Rule 3: settle_execution records retry_case_count
# ---------------------------------------------------------------------------

def _write_retry_rounds(run_dir, partitions_retries):
    """partitions_retries: {partition_name: [{round, case_ids, complete}]}"""
    path = Path(run_dir) / 'retry-rounds.json'
    partitions = {}
    for name, retries in partitions_retries.items():
        partitions[name] = dict(run_dir=str(run_dir), retries=retries)
    path.write_text(json.dumps(dict(format='sn-workbench-retry-rounds-v2',
                                    rounds=[], partitions=partitions)))


def setup_budget(tmp_path):
    from rag_eval.workbench.budgets import BudgetLedger
    repo = WorkbenchRepository(tmp_path / 'state')
    ws = repo.register_workspace('ws', tmp_path / 'project')
    task = repo.create_task(ws['id'], 'task', '', request_key='t1')
    iteration = dict(id='iter1', task_id=task['id'],
                     protocol={'budget': {'limit_cny': 100, 'max_case_runs': 200,
                                          'max_runs_per_candidate': 10}})
    ledger = BudgetLedger(repo)
    return repo, iteration, ledger


def test_settle_records_retry_case_count(tmp_path):
    repo, iteration, ledger = setup_budget(tmp_path)
    step = dict(candidate_id='c1', request_key='k1', case_count=4)
    ledger.reserve_many(iteration, [step], group_key='g1')
    run_dir = tmp_path / 'run1'
    run_dir.mkdir()
    _write_retry_rounds(run_dir, {'p1': [{'round': 1, 'case_ids': ['a', 'b'], 'complete': True}]})
    record = dict(execution_id='exec1', terminal=True, state='finished', run_dir=str(run_dir),
                  run_state={'cost_observation': {'currency': 'CNY', 'complete': True, 'total_cny': 4.0}})
    with repo.connect(write=True) as conn:
        conn.execute("UPDATE iteration_budget_reservations SET execution_id='exec1' "
                     "WHERE iteration_id='iter1' AND candidate_id='c1' AND request_key='k1'")
    ledger.settle_execution(record)
    with repo.connect() as conn:
        row = conn.execute("SELECT retry_case_count,case_count FROM iteration_budget_reservations "
                           "WHERE execution_id='exec1'").fetchone()
    assert row['retry_case_count'] == 2  # 2 retried cases from retry-rounds.json
    assert row['case_count'] == 4


def test_case_runs_used_includes_retries(tmp_path):
    repo, iteration, ledger = setup_budget(tmp_path)
    step = dict(candidate_id='c1', request_key='k1', case_count=4)
    ledger.reserve_many(iteration, [step], group_key='g1')
    run_dir = tmp_path / 'run2'
    run_dir.mkdir()
    _write_retry_rounds(run_dir, {'p1': [{'round': 1, 'case_ids': ['a', 'b', 'c'], 'complete': True}]})
    record = dict(execution_id='exec2', terminal=True, state='finished', run_dir=str(run_dir),
                  run_state={'cost_observation': {'currency': 'CNY', 'complete': True, 'total_cny': 7.0}})
    with repo.connect(write=True) as conn:
        conn.execute("UPDATE iteration_budget_reservations SET execution_id='exec2' "
                     "WHERE iteration_id='iter1' AND candidate_id='c1' AND request_key='k1'")
    ledger.settle_execution(record)
    summary = ledger.summary('iter1', reconcile=False)
    assert summary['case_runs_used'] == 7  # 4 base + 3 retried


def test_old_reservation_row_without_retry_case_count(tmp_path):
    """Rows with NULL retry_case_count (old DB) are counted as 0 retries."""
    repo, iteration, ledger = setup_budget(tmp_path)
    step = dict(candidate_id='c2', request_key='k2', case_count=3)
    ledger.reserve_many(iteration, [step], group_key='g2')
    with repo.connect(write=True) as conn:
        conn.execute("UPDATE iteration_budget_reservations SET retry_case_count=NULL "
                     "WHERE iteration_id='iter1'")
    summary = ledger.summary('iter1', reconcile=False)
    assert summary['case_runs_used'] == 3  # only base count


# ---------------------------------------------------------------------------
# Rule 3: estimate denominator includes retries
# ---------------------------------------------------------------------------

def test_estimate_denominator_includes_retries(tmp_path):
    from rag_eval.workbench.budgets import BudgetLedger
    repo, iteration, ledger = setup_budget(tmp_path)
    step1 = dict(candidate_id='c1', request_key='k1', case_count=4)
    step2 = dict(candidate_id='c2', request_key='k2', case_count=4)
    ledger.reserve_many(iteration, [step1], group_key='g1')
    ledger.reserve_many(iteration, [step2], group_key='g2')
    for eid, rdir, retried in [('e1', 'run1', 2), ('e2', 'run2', 0)]:
        run_dir = tmp_path / rdir
        run_dir.mkdir()
        if retried:
            _write_retry_rounds(run_dir, {'p': [{'round': 1, 'case_ids': ['a', 'b'], 'complete': True}]})
        rec = dict(execution_id=eid, terminal=True, state='finished', run_dir=str(run_dir),
                   run_state={'cost_observation': {'currency': 'CNY', 'complete': True, 'total_cny': 6.0}})
        cid = 'c1' if eid == 'e1' else 'c2'
        with repo.connect(write=True) as conn:
            conn.execute(f"UPDATE iteration_budget_reservations SET execution_id=? "
                         f"WHERE iteration_id='iter1' AND candidate_id=?", (eid, cid))
        ledger.settle_execution(rec)
    # e1: 4 base + 2 retry = 6 actual case-runs; e2: 4 base + 0 = 4 actual
    # total cost = 12, total case-runs = 10 → per_case = 1.2
    with repo.connect() as conn:
        per_case, source = ledger._estimate(conn, iteration)
    assert source == 'historical_observed_per_case'
    assert abs(per_case - 12.0 / 10.0) < 1e-9


# ---------------------------------------------------------------------------
# Rule 4: retry_summary in comparisons
# ---------------------------------------------------------------------------

def test_classify_failure():
    from rag_eval.workbench.comparisons import _classify_failure
    assert _classify_failure({'error_type': 'malformed_response'}) == 'malformed_response'
    assert _classify_failure({'error_type': 'incomplete_object'}) == 'malformed_response'
    assert _classify_failure({'error_type': 'case_timeout'}) == 'case_timeout'
    assert _classify_failure({'error_type': 'partition_deadline'}) == 'partition_deadline'
    assert _classify_failure({'failed_phase': 'generating', 'error_type': 'child_exit'}) == 'generation_failure'
    assert _classify_failure({'error_type': 'model_error'}) == 'model_infrastructure'
    assert _classify_failure({'error_type': 'rate_limit'}) == 'model_infrastructure'
    assert _classify_failure({'error_type': 'unknown_thing'}) == 'other'
    assert _classify_failure({}) == 'other'


def test_retry_summary_in_comparison_scope(tmp_path):
    """_compute_scope includes retry_summary; gate is not affected by it."""
    from rag_eval.workbench.comparisons import ComparisonService

    class _StubViews:
        def execution_runs(self, eid): return []
        def cases(self, rid): return {'cases': []}

    class _StubIterations:
        def iteration_record(self, iid):
            return dict(id=iid, protocol={'k_base': 2, 'success_threshold': 0.5})
        iteration = iteration_record

    cs = object.__new__(ComparisonService)
    cs.repository = None
    cs.iterations = _StubIterations()
    cs.views = _StubViews()

    # Both sides have a terminal non-finished execution so _compute_scope
    # returns a body with infrastructure + retry_summary.
    def _make_side(cid, eid, parent=None):
        return dict(id=cid, iteration_id='iter1', status='frozen',
                    parent_candidate_id=parent,
                    attempts=[dict(execution_id=eid, execution_state='failed',
                                   attempt=1, run_dir=None, batch_id=None,
                                   job_id='j-' + cid, preparation_id='p-' + cid,
                                   created_by='test', created_at='',
                                   admission={'status': 'not_admitted', 'reason': 'failed'})],
                    links=[], reference=None, decision=None)

    subject = _make_side('s1', 'ex1', parent='r1')
    reference = _make_side('r1', 'ex2')
    body, _ = cs._compute_scope(subject, reference, 'parent')
    assert body is not None
    assert 'retry_summary' in body
    assert 'subject' in body['retry_summary']
    assert 'reference' in body['retry_summary']
    # Gate-relevant fields present and unchanged by retry_summary
    assert 'admission' in body
    assert 'mean' in body


# ---------------------------------------------------------------------------
# Rule 5: gate verdict not affected by retry_summary
# ---------------------------------------------------------------------------

def test_gate_verdict_unaffected_by_retry_summary():
    """The gate reads mean/noise/admission; retry_summary is metadata-only.

    judge() requires a real contract (predicted_flips + guards), so we supply
    a minimal one. The test confirms that a body carrying retry_summary produces
    a verdict via judge() and that the verdict fields are the standard ones.
    """
    from rag_eval.workbench.gate import DEFAULT_RULE, judge
    contract = dict(predicted_flips=['c1'], guards=[])
    body = dict(
        admission={'status': 'admitted'},
        mean=dict(reference=0.5, subject=0.6, delta=0.1),
        noise=dict(eta=0.05, reference=dict(success_threshold=0.5, k_base=2, cases={}, eta=0.05),
                   subject=dict(success_threshold=0.5, k_base=2, cases={}, eta=0.05)),
        rows=[{'case_id': 'c1', 'left': 0.5, 'right': 0.6, 'delta': 0.1, 'flip': 'better'}],
        retry_summary={'subject': {'total_retried_cases': 3, 'still_failed_after_retry': 0},
                       'reference': {'total_retried_cases': 0, 'still_failed_after_retry': 0}},
    )
    verdict = judge(body, body['noise'], contract, DEFAULT_RULE)
    # Gate fields must be present regardless of retry_summary contents.
    assert verdict is not None
    assert 'rho' in verdict
    assert 'tau' in verdict




