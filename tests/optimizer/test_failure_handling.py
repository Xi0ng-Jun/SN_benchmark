"""method-v3 §2.1 "失败后的处理" (amendment 2026-11-03), driven through MockAdapter.

Smoke repair (one more attempt of a card whose behavioural smoke failed), the
richer §2.3 history labels, the full history of a rolled-back segment under the
certified node, and the escalation under one parent (case rotation, change of
direction, larger evidence per consecutive no-adoption round).
"""
from __future__ import annotations

import json

import pytest

from rag_eval.optimizer import EvaluationUnavailable, Predicate, ProtocolConfig, run_optimization
from rag_eval.optimizer import ledger_io
from rag_eval.optimizer.adapter import EvalResult
from rag_eval.optimizer.loop import select_analysis_cases
from tests.optimizer.mock_adapter import MockAdapter

POOL = [f"t{i}" for i in range(6)]
FRESH = [f"t{i}" for i in range(6, 12)]
PRED = Predicate({"kind": "range", "field": "trace.n_empty_retrievals", "min": 1}, "decrease")


class Crash(BaseException):
    """A killed process (not an Exception: nothing may swallow it)."""


def _config(**overrides) -> ProtocolConfig:
    defaults = dict(mde=0.05, sigma_d=0.1, ladder_c=1.0, K_max=20, T=10.0, n_fresh=6,
                    b_cards=1, k_screen=1, k_smoke=1, n_smoke_sample=2,
                    predicate_coverage_floor=0.0)
    defaults.update(overrides)
    return ProtocolConfig(**defaults)


def _run(adapter, config, *, state_dir=None, fresh=None, max_rounds=1):
    return run_optimization(
        adapter, adapter.seed, config,
        search_set_ids=POOL, val_pool_ids=POOL,
        fresh_batch_ids=fresh if fresh is not None else [FRESH],
        smoke_sample_ids=POOL[:2], max_rounds=max_rounds, state_dir=state_dir,
    )


def _deltas(pool=0.0, fresh=0.0):
    return {**{t: pool for t in POOL}, **{t: fresh for t in FRESH}}


def _card(name, *, pool=0.0, fresh=0.0, sleep=0.0, **extra):
    return dict(name=name, deltas=_deltas(pool, fresh), sleep=sleep, **extra)


class BehaviourAdapter(MockAdapter):
    """The predicate is true on a run unless the version's card spec says ``fixed``.

    A 'decrease' prediction therefore passes the behavioural smoke iff the
    candidate is fixed (the seed is not).
    """

    def evaluate(self, version, task_ids, k, *, purpose="screen"):
        res = super().evaluate(version, task_ids, k, purpose=purpose)
        fixed = bool(self._origin.get(version.content_hash, {}).get("fixed"))
        for runs in res.trajectories.values():
            for run in runs:
                run["fixed"] = fixed
        return res

    def features(self, trajectories, predicate):
        if predicate is None:
            return {}
        return {t: not traj[0]["fixed"] for t, traj in trajectories.items() if traj}


def _attempts(round_res):
    return [(c["attempt"], c["verdict"], c["superseded"]) for c in round_res.candidates]


# ---------------------------------------------------------------------------
# 1. Smoke (execution-only, no predicate check)
# ---------------------------------------------------------------------------

def test_generic_smoke_failures_and_smoke_run_failures_are_not_repaired():
    # No predicate: generic smoke (the candidate did not run) -> process failure, no repair.
    adapter = BehaviourAdapter([{"cards": [_card("a", smoke_pass=False, repair={"fixed": True})]}], n_tasks=12)
    result = _run(adapter, _config())
    assert _attempts(result.rounds[0]) == [(1, "smoke_fail", False)] and adapter.repair_calls == []
    # The smoke run itself failed.
    adapter = BehaviourAdapter([{"cards": [_card("a", repair={"fixed": True})], "predicate": PRED}], n_tasks=12,
                               fail_on={"smoke": EvaluationUnavailable("runner down")})
    result = _run(adapter, _config())
    rec = result.rounds[0].candidates[0]
    assert rec["verdict"] == "smoke_fail" and rec["reason"].startswith("smoke_run_failed")
    assert adapter.repair_calls == []


# ---------------------------------------------------------------------------
# 2. Richer history labels (§2.3: discrete details only)
# ---------------------------------------------------------------------------

def test_screen_fail_carries_the_smoke_conclusion_and_unmeasurable_the_breached_names():
    script = [{"cards": [_card("changed", fixed=True)], "predicate": PRED},
              {"cards": [_card("plain")]},
              {"cards": [_card("next")]}]
    adapter = BehaviourAdapter(script, n_tasks=12)
    _run(adapter, _config(max_rounds_without_screen_pass=5), max_rounds=3)
    assert adapter.history_calls[2] == [
        dict(title="changed", label="screen_fail", smoke="unchecked"),
        dict(title="plain", label="screen_fail", smoke="unchecked")]

    adapter = BehaviourAdapter([{"cards": [_card("costly", pool=0.12)]}, {"cards": [_card("x")]}], n_tasks=12)
    _run(adapter, _config(safeguard_bounds={"cost_per_task": -1.0, "latency": 5.0}), max_rounds=2)
    assert adapter.history_calls[1] == [dict(title="costly", label="unmeasurable",
                                             safeguards_breached=["cost_per_task"])]


def test_an_unmeasurable_confirmation_names_the_breached_safeguard_of_the_segment():
    class Guarded(MockAdapter):
        def safeguards(self, candidate, reference, bounds):
            ok = not set(candidate.per_task_scores) <= set(FRESH)          # breached on the fresh batch only
            return dict(source="test", ok=ok, reasons=[] if ok else ["safeguard_breached:cost_per_task"],
                        checks=[])
    adapter = Guarded([{"cards": [_card("a", pool=0.2, fresh=0.2)]}, {"cards": [_card("b")]}], n_tasks=12)
    result = _run(adapter, _config(T=0.01), fresh=[FRESH, FRESH], max_rounds=2)
    assert result.rounds[0].confirmation_verdict == "unmeasurable"
    assert adapter.history_calls[1] == [dict(title="a", label="unmeasurable", safeguards_breached=["cost_per_task"],
                                             attribution="unattributed", attribution_possibly_biased=False)]


# ---------------------------------------------------------------------------
# 3. Full segment history after a rollback
# ---------------------------------------------------------------------------

def test_every_hypothesis_of_a_rolled_back_segment_is_listed_under_the_certified_node():
    # a adopted on the seed; c (built on the seed) is rebased onto a and fails the screen there;
    # b is rebased onto a and adopted, the segment reaches T and is rejected on the fresh batch.
    cards = [_card("a", pool=0.12, fresh=-0.12, sleep=0.01),
             _card("c", sleep=0.1, rebase=True),
             _card("b", pool=0.12, fresh=-0.12, sleep=0.3, rebase=True)]
    script = [{"cards": cards}, {"cards": [_card("d")]}]
    adapter = MockAdapter(script, n_tasks=12, base_scores={t: 0.3 for t in FRESH})
    result = _run(adapter, _config(b_cards=3, T=0.2), fresh=[FRESH, FRESH], max_rounds=2)
    rr = result.rounds[0]
    by = {c["title"]: c for c in rr.candidates}
    a_hash = by["a"]["candidate"]
    assert by["c"]["parent"] == a_hash and by["c"]["verdict"] == "screen_fail"
    assert by["b"]["parent"] == a_hash and by["b"]["verdict"] == "screen_pass"
    assert rr.confirmation_verdict == "rejected" and rr.confirmations[0]["segment_steps"] == 2
    assert adapter.history_calls[1] == [
        dict(title="a", label="rejected"),
        dict(title="c", label="screen_fail", smoke="unchecked", rolled_back_segment=True),
        dict(title="b", label="rejected", rolled_back_segment=True, attribution="unattributed",
             attribution_possibly_biased=False)]
    # The rollback moved the pointer: the next diagnosis is back at the first tier.
    assert adapter.no_adoption_calls == [0, 0]


# ---------------------------------------------------------------------------
# 4. Escalation under one parent
# ---------------------------------------------------------------------------

FAILING = {t: 0.05 * i for i, t in enumerate(POOL)}       # t0 worst ... t5, all below 0.5


def test_select_analysis_cases_rotates_over_unshown_failures_then_restarts():
    ev = EvalResult(per_task_scores={**FAILING, "ok1": 0.9, "ok2": 0.8}, trajectories={}, costs={},
                    error_classes={})
    cfg = _config(analyst_max_failures=2, analyst_n_controls=1)
    fail = lambda roles: [t for t, r in roles.items() if r == "failure"]   # noqa: E731
    assert fail(select_analysis_cases(ev, cfg)[1]) == ["t0", "t1"]
    assert fail(select_analysis_cases(ev, cfg, shown=["t0", "t1"])[1]) == ["t2", "t3"]
    assert fail(select_analysis_cases(ev, cfg, shown=["t0", "t1", "t2", "t3", "t4"], max_failures=3)[1]) == [
        "t5", "t0", "t1"]                                                   # unshown first, then worst-first
    assert fail(select_analysis_cases(ev, cfg, shown=POOL, max_failures=3)[1]) == ["t0", "t1", "t2"]  # new cycle
    controls = [t for t, r in select_analysis_cases(ev, cfg, shown=["t0"])[1].items() if r == "control"]
    assert controls == [t for t, r in select_analysis_cases(ev, cfg)[1].items() if r == "control"]  # fixed rule


def _stuck_script(n):
    return [{"cards": [_card(f"idea{i}")]} for i in range(n)]


def test_consecutive_no_adoption_rounds_rotate_cases_and_step_up_the_case_cap():
    adapter = MockAdapter(_stuck_script(4), n_tasks=12, base_scores=FAILING)
    cfg = _config(analyst_max_failures=2, analyst_max_failures_step=1, max_rounds_without_screen_pass=10)
    result = _run(adapter, cfg, max_rounds=4)
    assert adapter.no_adoption_calls == [0, 1, 2, 3]
    assert [list(r) for r in adapter.roles_calls] == [
        ["t0", "t1"], ["t2", "t3", "t4"], ["t5", "t0", "t1", "t2"], ["t0", "t1", "t2", "t3", "t4"]]
    a = [r.analysis for r in result.rounds]
    assert [x["max_failures"] for x in a] == [2, 3, 4, 5]
    assert [x["failures_new"] for x in a] == [2, 3, 1, 5]
    assert [x["rotation_cycle_restarted"] for x in a] == [False, False, False, True]
    assert [x["no_adoption_rounds_after"] for x in a] == [1, 2, 3, 4] and not any(x["pointer_moved"] for x in a)
    # Capped by the search set (6 tasks).
    adapter = MockAdapter(_stuck_script(3), n_tasks=12, base_scores=FAILING)
    result = _run(adapter, _config(analyst_max_failures=2, analyst_max_failures_step=10,
                                   max_rounds_without_screen_pass=10), max_rounds=3)
    assert [r.analysis["max_failures"] for r in result.rounds] == [2, 6, 6]


def test_the_count_and_rotation_restart_when_the_pointer_advances():
    script = [{"cards": [_card("idle")]}, {"cards": [_card("good", pool=0.12)]}, {"cards": [_card("next")]}]
    adapter = MockAdapter(script, n_tasks=12, base_scores=FAILING)
    _run(adapter, _config(analyst_max_failures=2, analyst_max_failures_step=1), max_rounds=3)
    assert adapter.no_adoption_calls == [0, 1, 0]
    assert [list(r) for r in adapter.roles_calls] == [["t0", "t1"], ["t2", "t3", "t4"], ["t0", "t1"]]


def test_escalation_state_is_persisted_and_old_states_load_with_defaults(tmp_path):
    cfg = _config(analyst_max_failures=2, analyst_max_failures_step=1, max_rounds_without_screen_pass=10)
    _run(MockAdapter(_stuck_script(4), n_tasks=12, base_scores=FAILING), cfg, state_dir=tmp_path, max_rounds=2)
    st = json.loads((tmp_path / ledger_io.STATE_FILE).read_text())
    assert st["no_adoption"] == dict(parent="seed_v0", rounds=2)
    assert st["analyst_shown"] == {"seed_v0": ["t0", "t1", "t2", "t3", "t4"]}
    adapter = MockAdapter(_stuck_script(4), n_tasks=12, base_scores=FAILING)
    _run(adapter, cfg, state_dir=tmp_path, max_rounds=3)
    assert adapter.no_adoption_calls == [2] and list(adapter.roles_calls[0]) == ["t5", "t0", "t1", "t2"]
    # A state.json written before the amendment has neither key: first tier.
    st = json.loads((tmp_path / ledger_io.STATE_FILE).read_text())
    del st["no_adoption"], st["analyst_shown"]
    (tmp_path / ledger_io.STATE_FILE).write_text(json.dumps(st))
    adapter = MockAdapter(_stuck_script(4), n_tasks=12, base_scores=FAILING)
    _run(adapter, cfg, state_dir=tmp_path, max_rounds=4)
    assert adapter.no_adoption_calls == [0] and list(adapter.roles_calls[0]) == ["t0", "t1"]
