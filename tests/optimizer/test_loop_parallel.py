"""Parallel candidate pipeline, parent-hash precondition, validation-pool growth.

Binding plan §2.4 / §2.6 and method-v3 §1 (best, m), §2.1 (refill), §2.2 A
(rebase precondition) and B (state-transition table), driven through
MockAdapter.
"""
from __future__ import annotations

import threading
import time

import pytest

from rag_eval.optimizer import (
    BudgetExhausted,
    EvaluationUnavailable,
    NodeState,
    ProtocolConfig,
    ladder_m,
    run_optimization,
)
from tests.optimizer.mock_adapter import MockAdapter

POOL = [f"t{i}" for i in range(6)]
FRESH = [f"t{i}" for i in range(6, 12)]


def _config(**overrides) -> ProtocolConfig:
    defaults = dict(mde=0.05, sigma_d=0.1, ladder_c=1.0, K_max=20, T=10.0, n_fresh=6,
                    b_cards=2, k_screen=1, k_smoke=1, n_smoke_sample=2,
                    predicate_coverage_floor=0.0)
    defaults.update(overrides)
    return ProtocolConfig(**defaults)


def _run(adapter, config, *, fresh=None, max_rounds=1):
    return run_optimization(
        adapter, adapter.seed, config,
        search_set_ids=POOL, val_pool_ids=POOL,
        fresh_batch_ids=fresh if fresh is not None else [FRESH],
        smoke_sample_ids=POOL[:2], max_rounds=max_rounds,
    )


def _deltas(pool=0.0, fresh=0.0):
    return {**{t: pool for t in POOL}, **{t: fresh for t in FRESH}}


def _card(name, *, pool=0.0, fresh=0.0, sleep=0.0, **extra):
    return dict(name=name, deltas=_deltas(pool, fresh), sleep=sleep, **extra)


def _by_ref(round_res):
    return {c["card_ref"].split(":", 1)[1]: c for c in round_res.candidates}


# ---------------------------------------------------------------------------
# Concurrency and ordering
# ---------------------------------------------------------------------------

def test_candidates_run_concurrently():
    cards = [_card(n, sleep=0.2) for n in ("a", "b", "c")]   # Δ=0 → screen_fail
    adapter = MockAdapter([{"cards": cards}], n_tasks=12)
    t0 = time.monotonic()
    result = _run(adapter, _config(b_cards=3))
    elapsed = time.monotonic() - t0
    assert elapsed < 0.45, elapsed          # ≈ max(0.2), not sum(0.6)
    assert adapter.implement_done == 3
    assert [c["verdict"] for c in result.rounds[0].candidates] == ["screen_fail"] * 3
    assert all(c["timings"]["implement_s"] >= 0.19 for c in result.rounds[0].candidates)


def test_decisions_in_completion_order_and_stale_parent_without_rebase():
    # Both would pass; b finishes first, is adopted, and moves the pointer.
    cards = [_card("a", pool=0.12, sleep=0.3), _card("b", pool=0.12, sleep=0.02)]
    adapter = MockAdapter([{"cards": cards}], n_tasks=12)
    result = _run(adapter, _config())
    rr = result.rounds[0]
    assert [c["card_ref"] for c in rr.candidates] == ["R1:b", "R1:a"]
    b, a = rr.candidates
    assert b["verdict"] == "screen_pass"
    assert a["verdict"] == "stale_parent" and a["parent"] == adapter.seed.content_hash
    assert adapter.rebase_calls == 1
    assert rr.screen_passed == 1
    assert result.lineage.current_parent().version_hash == b["candidate"]


def test_rebased_candidate_is_resmoked_and_rescreened_against_new_parent():
    cards = [_card("a", pool=0.12, sleep=0.3, rebase=True), _card("b", pool=0.12, sleep=0.02)]
    adapter = MockAdapter([{"cards": cards}], n_tasks=12)
    result = _run(adapter, _config())
    rr = result.rounds[0]
    got = _by_ref(rr)
    b, a = got["b"], got["a"]
    assert b["verdict"] == "screen_pass" and a["verdict"] == "screen_pass"
    assert a["parent"] == b["candidate"]
    assert a["rebased_from"] and a["rebased_from"] != a["candidate"]
    # Stale measurement (never decided) + re-screen of the rebased version.
    assert a["screen_evaluations"] == 2
    # Rebased version went through smoke and screening against b.
    smoke = [h for p, h, _ in adapter.eval_log if p == "smoke"]
    screen = [h for p, h, _ in adapter.eval_log if p == "screen"]
    assert a["candidate"] in smoke and a["candidate"] in screen
    # Ladder: best climbed to b's score before a was judged.
    assert a["best_before"] == pytest.approx(0.62)
    nodes = [n.version_hash for n in result.lineage.all_nodes()]
    assert nodes == [adapter.seed.content_hash, b["candidate"], a["candidate"]]
    assert rr.screen_passed == 2


def test_refill_after_smoke_fail():
    cards = [_card("a", smoke_pass=False), _card("b"), _card("c")]
    adapter = MockAdapter([{"cards": cards}], n_tasks=12)
    result = _run(adapter, _config(b_cards=2, analyst_cards=3))
    rr = result.rounds[0]
    got = _by_ref(rr)
    assert got["a"]["verdict"] == "smoke_fail"
    assert got["b"]["verdict"] == "screen_fail" and got["c"]["verdict"] == "screen_fail"
    assert adapter.implement_calls == 3
    assert rr.smoke_passed == 2 and rr.cards_generated == 3


def test_no_refill_without_failure_and_analyst_asked_for_b_cards_by_default():
    cards = [_card(n) for n in ("a", "b", "c")]
    adapter = MockAdapter([{"cards": cards}], n_tasks=12)
    result = _run(adapter, _config(b_cards=2))
    assert result.rounds[0].cards_generated == 2       # analyst_cards defaults to b
    assert adapter.implement_calls == 2


def test_implementer_exception_is_per_card_error_and_refilled():
    class Flaky(MockAdapter):
        def implement(self, card, workspace):
            if card.failure_mode_desc == "a":
                raise RuntimeError("agent timeout")
            return super().implement(card, workspace)

    adapter = Flaky([{"cards": [_card("a"), _card("b"), _card("c")]}], n_tasks=12)
    result = _run(adapter, _config(b_cards=2, analyst_cards=3))
    got = _by_ref(result.rounds[0])
    assert got["a"]["verdict"] == "error" and "implement" in got["a"]["reason"]
    assert got["c"]["verdict"] == "screen_fail"
    assert result.halt_reason == "max_rounds"


# ---------------------------------------------------------------------------
# Validation-pool growth, m and best
# ---------------------------------------------------------------------------

def test_pool_grows_m_shrinks_and_best_remeasured_after_confirm():
    # +0.12 on the pool, +0.06 on the fresh tasks → confirmed (SE=0).
    adapter = MockAdapter([{"cards": [_card("a", pool=0.12, fresh=0.06)]}], n_tasks=12)
    result = _run(adapter, _config(T=0.10, b_cards=1), fresh=[FRESH, FRESH])
    rr = result.rounds[0]
    assert rr.milestone_triggered and rr.confirmation_verdict == "confirmed"
    a = rr.candidates[0]["candidate"]
    m6, m12 = ladder_m(0.1, 20, 6, 1.0), ladder_m(0.1, 20, 12, 1.0)
    assert m12 < m6
    assert rr.val_pool_size == 12 and rr.ladder_m == pytest.approx(m12)
    assert rr.candidates[0]["ladder_m"] == pytest.approx(m6)
    conf = rr.confirmations[0]
    assert conf["val_pool_size"] == 12 and conf["ladder_m"] == pytest.approx(m12)
    # best = certified node on the 12-task pool (0.5 + (6·0.12 + 6·0.06)/12),
    # not its 6-task screening score 0.62.
    assert rr.best_score == pytest.approx(0.59) and conf["best_score"] == pytest.approx(0.59)
    assert ("screen", a, tuple(POOL + FRESH)) in adapter.eval_log
    assert result.lineage.current_parent().state == NodeState.CERTIFIED


def test_best_after_rollback_is_last_certified_on_enlarged_pool():
    # Seed scores 0.5 on the pool and 0.3 on the fresh tasks; the candidate
    # helps on the pool and hurts on fresh → rejected → rollback to seed.
    base = {t: 0.3 for t in FRESH}
    adapter = MockAdapter([{"cards": [_card("a", pool=0.12, fresh=-0.12)]}], n_tasks=12,
                          base_scores=base)
    result = _run(adapter, _config(T=0.10, b_cards=1), fresh=[FRESH, FRESH])
    rr = result.rounds[0]
    assert rr.confirmation_verdict == "rejected" and rr.rolled_back
    seed = adapter.seed.content_hash
    assert result.lineage.current_parent().version_hash == seed
    # Not the pre-segment best on the 6-task pool (0.5): seed on 12 tasks.
    assert rr.best_score == pytest.approx(0.4)
    assert ("screen", seed, tuple(POOL + FRESH)) in adapter.eval_log
    assert rr.val_pool_size == 12


def test_milestone_stops_new_work_and_inflight_same_parent_is_rescreened():
    # a adopted fast → milestone rejected → pointer back to seed and pool grows.
    # b was built on seed: parent still matches, but its measurement used the
    # 6-task pool, so it is re-screened on 12 tasks. c is never started.
    base = {t: 0.3 for t in FRESH}
    cards = [_card("a", pool=0.12, fresh=-0.12, sleep=0.0), _card("b", sleep=0.3), _card("c")]
    adapter = MockAdapter([{"cards": cards}], n_tasks=12, base_scores=base)
    result = _run(adapter, _config(T=0.10, b_cards=2, analyst_cards=3), fresh=[FRESH, FRESH])
    rr = result.rounds[0]
    got = _by_ref(rr)
    assert set(got) == {"a", "b"}
    assert adapter.implement_calls == 2
    assert got["b"]["screen_evaluations"] == 2 and got["b"]["val_pool_size"] == 12
    assert got["b"]["verdict"] == "screen_fail"


def test_parent_val_results_cached_per_pool_size():
    class Tagged(MockAdapter):
        """Logs pair calls separately so direct loop evaluations can be counted."""
        def evaluate_pair(self, version, reference, task_ids, k, *, purpose):
            pair = super().evaluate_pair(version, reference, task_ids, k, purpose=purpose)
            with self._lock:
                self.pairs = getattr(self, "pairs", 0) + 1
                del self.eval_log[-2:]
            return pair

    base = {t: 0.3 for t in FRESH}
    script = [{"cards": [_card("a", pool=0.12, fresh=-0.12)]},
              {"cards": [_card("b")]}, {"cards": [_card("c")]}]
    adapter = Tagged(script, n_tasks=12, base_scores=base)
    _run(adapter, _config(T=0.10, b_cards=1), fresh=[FRESH, FRESH], max_rounds=3)
    seed = adapter.seed.content_hash
    direct = [(p, len(ids)) for p, h, ids in adapter.eval_log
              if h == seed and p in ("baseline", "screen")]
    # Seed measured directly once per pool size: baseline on 6, after the
    # rollback on 12 — never again in later rounds.
    assert direct == [("baseline", 6), ("screen", 12)]


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------

def test_budget_exhausted_halts():
    cards = [_card("a", sleep=0.0), _card("b", sleep=0.2)]
    adapter = MockAdapter([{"cards": cards}] * 3, n_tasks=12,
                          fail_on={"screen": BudgetExhausted("budget gone")})
    threads_before = threading.active_count()
    result = _run(adapter, _config(), max_rounds=3)
    assert result.halt_reason == "budget_exhausted"
    assert result.rounds[-1].halt_reason == "budget_exhausted"
    assert len(result.rounds) == 1
    # In-flight worker finished before returning; no thread leaked.
    assert adapter.implement_done == 2
    assert threading.active_count() == threads_before
    assert {c["verdict"] for c in result.rounds[0].candidates} == {"error"}


def test_one_smoke_run_failure_is_that_cards_smoke_fail():
    # Generic smoke exists to catch candidates that crash the run: one such
    # failure discards the card, it does not halt the loop.
    adapter = MockAdapter([{"cards": [_card("a")]}], n_tasks=12,
                          fail_on={"smoke": EvaluationUnavailable("candidate crashed")})
    result = _run(adapter, _config(), max_rounds=1)
    assert result.halt_reason != "process_failure"
    rec = result.rounds[0].candidates[0]
    assert rec["verdict"] == "smoke_fail" and rec["reason"].startswith("smoke_run_failed")


def test_consecutive_smoke_run_failures_halt_as_process_failure():
    cards = [_card(n) for n in "abc"]
    adapter = MockAdapter([{"cards": cards}], n_tasks=12,
                          fail_on={"smoke": EvaluationUnavailable("runner down")})
    result = _run(adapter, _config(b_cards=1, analyst_cards=3, max_consecutive_run_failures=3),
                  max_rounds=3)
    assert result.halt_reason == "process_failure"
    assert [c["verdict"] for c in result.rounds[0].candidates] == ["smoke_fail"] * 3


def test_budget_exhausted_in_smoke_still_halts():
    adapter = MockAdapter([{"cards": [_card("a")]}], n_tasks=12,
                          fail_on={"smoke": BudgetExhausted("budget")})
    assert _run(adapter, _config(), max_rounds=3).halt_reason == "budget_exhausted"


def test_screen_run_failure_in_worker_is_process_failure():
    adapter = MockAdapter([{"cards": [_card("a")]}], n_tasks=12)
    adapter.fail_on["screen"] = EvaluationUnavailable("runner down")
    result = _run(adapter, _config(), max_rounds=3)
    assert result.halt_reason == "process_failure"
    assert result.rounds[0].candidates[0]["verdict"] == "error"


def test_unscored_tasks_leave_the_paired_difference():
    from rag_eval.optimizer.loop import _paired
    from rag_eval.optimizer.adapter import EvalResult
    a = EvalResult({"t1": 0.5, "t2": 1.0, "t3": 0.0}, {}, {}, {})
    b = EvalResult({"t1": 0.5, "t3": 0.5}, {}, {}, {})
    xs, ys, dropped = _paired(a, b, ["t1", "t2", "t3", "t4"])
    assert (xs, ys, dropped) == ([0.5, 0.0], [0.5, 0.5], 2)  # t2/t4 not zeros


def test_evaluation_unavailable_in_confirmation_is_process_failure():
    cards = [_card("a", pool=0.12, fresh=0.12), _card("b", sleep=0.2)]
    adapter = MockAdapter([{"cards": cards}], n_tasks=12,
                          fail_on={"confirm": EvaluationUnavailable("timeout")})
    threads_before = threading.active_count()
    result = _run(adapter, _config(T=0.10), max_rounds=3)
    assert result.halt_reason == "process_failure"
    assert adapter.implement_done == 2
    assert threading.active_count() == threads_before
    got = _by_ref(result.rounds[0])
    assert got["a"]["verdict"] == "screen_pass"
    assert got["b"]["verdict"] == "error" and got["b"]["reason"].startswith("halted")
    # Halted-but-measured candidates keep Δ for selection accounting.
    assert got["b"]["delta_val"] == pytest.approx(0.0)


def test_baseline_unavailable_halts_before_any_round():
    adapter = MockAdapter([{"cards": [_card("a")]}], n_tasks=12,
                          fail_on={"baseline": BudgetExhausted("no budget")})
    result = _run(adapter, _config())
    assert result.halt_reason == "budget_exhausted" and result.rounds == []


# ---------------------------------------------------------------------------
# Safeguards: every paired decision asks the adapter (SystemAdapter.safeguards)
# ---------------------------------------------------------------------------

class _Guarded(MockAdapter):
    """Blocks the safeguards of the pairs listed by purpose, records what it was asked."""

    def __init__(self, *a, block=(), **kw):
        super().__init__(*a, **kw)
        self.block, self.asked = set(block), []

    def safeguards(self, candidate, reference, bounds):
        purpose = "confirm" if set(candidate.per_task_scores) <= set(FRESH) else "screen"
        self.asked.append(purpose)
        ok = purpose not in self.block
        return dict(source="test", ok=ok, reasons=[] if ok else ["safeguard_breached:cost_per_task"],
                    checks=[dict(key="cost_per_task", margin=0.5, reference=1.0, subject=1.0 if ok else 2.0,
                                 breached=not ok)])


def test_a_blocked_screen_safeguard_is_unmeasurable_and_recorded():
    adapter = _Guarded([{"cards": [_card("a", pool=0.2, fresh=0.2)]}], block={"screen"})
    result = _run(adapter, _config(T=0.01))
    rec = _by_ref(result.rounds[0])["a"]
    assert rec["verdict"] == "unmeasurable" and rec["safeguards"]["reasons"] == ["safeguard_breached:cost_per_task"]
    assert adapter.asked == ["screen"] and result.rounds[0].confirmations == []


def test_a_blocked_confirmation_safeguard_rolls_back_and_fills_safeguards_end():
    adapter = _Guarded([{"cards": [_card("a", pool=0.2, fresh=0.2)]}], block={"confirm"})
    result = _run(adapter, _config(T=0.01))
    [conf] = result.rounds[0].confirmations
    assert conf["verdict"] == "unmeasurable" and result.rounds[0].rolled_back
    assert conf["safeguards_end"]["ok"] is False and conf["safeguards_end"]["checks"][0]["subject"] == 2.0
    assert _by_ref(result.rounds[0])["a"]["safeguards"]["ok"]
    assert [n.state for n in result.lineage.all_nodes()] == [NodeState.CERTIFIED, NodeState.ROLLED_BACK]
