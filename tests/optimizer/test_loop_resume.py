"""Durable loop state and resume (binding plan §2.7), key generation, analyst history (§2.3)
and attribution at confirmation (§2.2), driven through MockAdapter."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from rag_eval.optimizer import (
    EvaluationUnavailable,
    Predicate,
    ProtocolConfig,
    attribution_result,
    run_optimization,
)
from rag_eval.optimizer import ledger_io
from tests.optimizer.mock_adapter import MockAdapter

POOL = [f"t{i}" for i in range(6)]
FRESH = [f"t{i}" for i in range(6, 12)]


class Crash(BaseException):
    """Stands in for a killed process (not an Exception: nothing may swallow it)."""


def _config(**overrides) -> ProtocolConfig:
    defaults = dict(mde=0.05, sigma_d=0.1, ladder_c=1.0, K_max=20, T=10.0, n_fresh=6,
                    b_cards=2, k_screen=1, k_smoke=1, n_smoke_sample=2,
                    predicate_coverage_floor=0.0)
    defaults.update(overrides)
    return ProtocolConfig(**defaults)


def _run(adapter, config, *, state_dir=None, fresh=None, max_rounds=1, **kw):
    return run_optimization(
        adapter, adapter.seed, config,
        search_set_ids=POOL, val_pool_ids=POOL,
        fresh_batch_ids=fresh if fresh is not None else [FRESH],
        smoke_sample_ids=POOL[:2], max_rounds=max_rounds, state_dir=state_dir, **kw,
    )


def _deltas(pool=0.0, fresh=0.0):
    return {**{t: pool for t in POOL}, **{t: fresh for t in FRESH}}


def _card(name, *, pool=0.0, fresh=0.0, sleep=0.0, **extra):
    return dict(name=name, deltas=_deltas(pool, fresh), sleep=sleep, **extra)


def _state(tmp_path) -> dict:
    return json.loads((tmp_path / "state.json").read_text())


class CrashingAdapter(MockAdapter):
    """Raises Crash from implement() of the named card while armed; counts implements per card."""

    def __init__(self, *a, crash_card=None, **kw):
        super().__init__(*a, **kw)
        self.crash_card = crash_card
        self.implemented: list[str] = []

    def implement(self, card, workspace):
        with self._lock:
            self.implemented.append(card.failure_mode_desc)
        if card.failure_mode_desc == self.crash_card:
            spec = self._card_specs[card.ref]
            if spec.get("sleep"):
                import time
                time.sleep(spec["sleep"])
            raise Crash("process killed")
        return super().implement(card, workspace)


# ---------------------------------------------------------------------------
# Atomic state writes
# ---------------------------------------------------------------------------

def test_state_written_atomically_after_transitions(tmp_path, monkeypatch):
    replaced = []
    real_replace = os.replace

    def spy(src, dst):
        replaced.append((Path(src).name, Path(dst).name))
        return real_replace(src, dst)

    monkeypatch.setattr(ledger_io.os, "replace", spy)
    adapter = MockAdapter([{"cards": [_card("a"), _card("b")]}], n_tasks=12)
    _run(adapter, _config(), state_dir=tmp_path)
    state_writes = [r for r in replaced if r[1] == "state.json"]
    # baseline (2) + round start + cards + 2 submissions/decisions + round end + halt, at least
    assert len(state_writes) >= 7
    assert all(src.startswith(".state.json.") and src.endswith(".tmp") for src, _ in state_writes)
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    st = _state(tmp_path)
    assert st["schema_version"] == ledger_io.STATE_SCHEMA_VERSION
    assert st["config_sha256"] == ledger_io.config_digest(_config())[1]
    assert st["round_counter"] == 1 and st["current_round"] is None
    assert st["halt_reason"] == "max_rounds"
    assert [r["round_id"] for r in st["rounds"]] == [1]
    assert {"versions", "lineage", "milestone", "validation_pool", "halt_tracker",
            "fresh_batches_used", "run_failures", "key_generation", "history"} <= set(st)


def test_failed_atomic_write_keeps_previous_file(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    ledger_io.atomic_write_json(target, {"v": 1})

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(ledger_io.os, "replace", boom)
    with pytest.raises(OSError):
        ledger_io.atomic_write_json(target, {"v": 2})
    assert json.loads(target.read_text()) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


# ---------------------------------------------------------------------------
# Resume after a crash mid-round
# ---------------------------------------------------------------------------

def test_resume_skips_decided_card_and_analyse_and_rebases_stale_inflight(tmp_path):
    # a is adopted quickly; b (built on the seed) crashes the process later.
    cards = [_card("a", pool=0.12), _card("b", pool=0.12, sleep=0.2)]
    adapter = CrashingAdapter([{"cards": cards}], n_tasks=12, crash_card="b")
    with pytest.raises(Crash):
        _run(adapter, _config(), state_dir=tmp_path)
    st = _state(tmp_path)
    cur = st["current_round"]
    assert [c["failure_mode_desc"] for c in cur["cards"]] == ["a", "b"]
    assert [(c["card_ref"], c["verdict"]) for c in cur["round"]["candidates"]] == [("R1:a", "screen_pass")]
    assert cur["inflight"] == {"1": adapter.seed.content_hash}
    a_hash = cur["round"]["candidates"][0]["candidate"]
    assert st["milestone"]["current_provisional_id"] == a_hash

    adapter.crash_card = None
    n_log = len(adapter.eval_log)
    result = _run(adapter, _config(), state_dir=tmp_path)
    assert adapter.analyse_calls == 1                      # cards came from state.json
    assert adapter.implemented == ["a", "b", "b"]          # a not rebuilt
    rr = result.rounds[0]
    assert [c["card_ref"] for c in rr.candidates] == ["R1:a", "R1:b"]   # a not re-decided
    a, b = rr.candidates
    assert not a["resumed"] and b["resumed"]
    # b's parent is stale: rebuilt by replay, not measured, then rebase (mock: refused).
    assert b["verdict"] == "stale_parent" and b["parent"] == adapter.seed.content_hash
    after = adapter.eval_log[n_log:]
    assert not [p for p, _, _ in after if p in ("diagnose", "smoke", "screen")]
    assert [n.version_hash for n in result.lineage.all_nodes()] == [adapter.seed.content_hash, a_hash]
    assert rr.screen_passed == 1
    assert result.halt_reason == "max_rounds"
    assert _state(tmp_path)["current_round"] is None


def test_resume_measures_inflight_card_built_on_current_pointer(tmp_path):
    # a fails screening (refill) → b is submitted on the seed → crash in b.
    cards = [_card("a"), _card("b", pool=0.12)]
    adapter = CrashingAdapter([{"cards": cards}], n_tasks=12, crash_card="b")
    with pytest.raises(Crash):
        _run(adapter, _config(b_cards=1, analyst_cards=2), state_dir=tmp_path)
    assert _state(tmp_path)["current_round"]["submitted"] == [0, 1]
    adapter.crash_card = None
    result = _run(adapter, _config(b_cards=1, analyst_cards=2), state_dir=tmp_path)
    assert adapter.analyse_calls == 1
    assert adapter.implemented == ["a", "b", "b"]
    got = {c["card_ref"]: c for c in result.rounds[0].candidates}
    assert got["R1:a"]["verdict"] == "screen_fail" and not got["R1:a"]["resumed"]
    assert got["R1:b"]["verdict"] == "screen_pass" and got["R1:b"]["resumed"]
    assert result.rounds[0].screen_passed == 1


def test_resume_does_not_redispatch_never_submitted_cards_early(tmp_path):
    # b=2: a adopted (no refill), b in flight and crashing, c never submitted.
    cards = [_card("a", pool=0.12), _card("b", sleep=0.2), _card("c")]
    adapter = CrashingAdapter([{"cards": cards}], n_tasks=12, crash_card="b")
    with pytest.raises(Crash):
        _run(adapter, _config(analyst_cards=3), state_dir=tmp_path)
    adapter.crash_card = None
    result = _run(adapter, _config(analyst_cards=3), state_dir=tmp_path)
    # b resumes (stale → rebase refused → refill) and only then c starts.
    refs = [c["card_ref"] for c in result.rounds[0].candidates]
    assert refs == ["R1:a", "R1:b", "R1:c"]
    assert adapter.implemented == ["a", "b", "b", "c"]


def test_crash_during_confirmation_resumes_the_same_batch(tmp_path):
    class ConfirmCrash(MockAdapter):
        armed = True

        def evaluate_pair(self, version, reference, task_ids, k, *, purpose):
            if purpose == "confirm" and self.armed:
                raise Crash("killed mid-confirmation")
            return super().evaluate_pair(version, reference, task_ids, k, purpose=purpose)

    fresh2 = [f"t{i}" for i in range(12, 18)]
    adapter = ConfirmCrash([{"cards": [_card("a", pool=0.12, fresh=0.06)]}], n_tasks=18)
    cfg = _config(T=0.10, b_cards=1)
    with pytest.raises(Crash):
        _run(adapter, cfg, state_dir=tmp_path, fresh=[FRESH, fresh2])
    st = _state(tmp_path)
    assert st["fresh_batches_used"] == 0 and st["milestone"]["cumulative_delta_val"] > 0
    adapter.armed = False
    result = _run(adapter, cfg, state_dir=tmp_path, fresh=[FRESH, fresh2])
    rr = result.rounds[0]
    assert len(rr.confirmations) == 1
    conf = rr.confirmations[0]
    assert conf["verdict"] == "confirmed" and conf["fresh_batch_index"] == 0
    assert conf["best_score"] is not None
    assert adapter.analyse_calls == 1
    assert _state(tmp_path)["fresh_batches_used"] == 1


def test_finished_run_resume_is_idempotent(tmp_path):
    adapter = MockAdapter([], n_tasks=12)
    first = _run(adapter, _config(), state_dir=tmp_path, max_rounds=5)
    assert first.halt_reason == "no_candidates"
    calls = adapter.analyse_calls
    again = _run(adapter, _config(), state_dir=tmp_path, max_rounds=5)
    assert again.halt_reason == "no_candidates"
    assert adapter.analyse_calls == calls
    lines = (tmp_path / "rounds.jsonl").read_text().splitlines()
    assert [json.loads(l)["round_id"] for l in lines] == [r.round_id for r in first.rounds]


# ---------------------------------------------------------------------------
# Read-only pre-registration
# ---------------------------------------------------------------------------

def test_config_hash_mismatch_refused(tmp_path):
    adapter = MockAdapter([], n_tasks=12)
    _run(adapter, _config(), state_dir=tmp_path)
    with pytest.raises(ValueError, match="pre-registered"):
        _run(adapter, _config(T=9.0), state_dir=tmp_path)
    with pytest.raises(ValueError, match="partitions"):
        run_optimization(adapter, adapter.seed, _config(), search_set_ids=POOL[:3], val_pool_ids=POOL,
                         fresh_batch_ids=[FRESH], smoke_sample_ids=POOL[:2], state_dir=tmp_path)


def test_callable_predicate_is_refused_for_persistence(tmp_path):
    pred = Predicate(lambda obs: True, "decrease")
    adapter = MockAdapter([{"cards": [_card("a")], "predicate": pred}], n_tasks=12)
    with pytest.raises(TypeError, match="callable"):
        _run(adapter, _config(), state_dir=tmp_path)
    # Without state_dir a callable predicate is fine.
    adapter = MockAdapter([{"cards": [_card("a")], "predicate": pred}], n_tasks=12)
    assert _run(adapter, _config()).halt_reason == "max_rounds"


# ---------------------------------------------------------------------------
# Failure halt, operator retry, key generation
# ---------------------------------------------------------------------------

def test_process_failure_retry_with_bumped_key_generation(tmp_path):
    adapter = MockAdapter([{"cards": [_card("a", pool=0.12)]}], n_tasks=12,
                          fail_on={"screen": EvaluationUnavailable("runner down")})
    first = _run(adapter, _config(), state_dir=tmp_path, max_rounds=1)
    assert first.halt_reason == "process_failure"
    st = _state(tmp_path)
    assert st["halt_reason"] == "process_failure" and st["key_generation"] == 0
    assert st["current_round"]["round"]["candidates"][0]["halted"] is True
    assert st["current_round"]["inflight"] == {"0": adapter.seed.content_hash}

    with pytest.raises(ValueError, match="lower"):
        state = tmp_path / "state.json"
        data = json.loads(state.read_text())
        data["key_generation"] = 3
        ledger_io.atomic_write_json(state, data)
        _run(adapter, _config(), state_dir=tmp_path, max_rounds=1, key_generation=1)
    data["key_generation"] = 0
    ledger_io.atomic_write_json(state, data)

    adapter.fail_on.clear()
    second = _run(adapter, _config(), state_dir=tmp_path, max_rounds=1, key_generation=1)
    assert adapter.key_generation == 1
    rr = second.rounds[0]
    assert [c["verdict"] for c in rr.candidates] == ["screen_pass"]   # retried, decided once
    assert adapter.analyse_calls == 1
    st = _state(tmp_path)
    assert st["key_generation"] == 1
    assert [e["generation"] for e in st["key_generation_log"]] == [0, 1]
    assert len(st["aborted_attempts"]) == 1 and st["aborted_attempts"][0]["halted"]
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    assert ledger["integrity"]["n_aborted_attempts_retried"] == 1


def test_key_generation_without_state_dir_is_passed_to_adapter():
    adapter = MockAdapter([], n_tasks=12)
    _run(adapter, _config(), key_generation=2)
    assert adapter.key_generation == 2


# ---------------------------------------------------------------------------
# §2.3 history labels to the analyst
# ---------------------------------------------------------------------------

def _no_numbers(entries):
    for e in entries:
        assert set(e) <= {"title", "label", "attribution", "attribution_possibly_biased"}
        for v in e.values():
            assert isinstance(v, (str, bool)), e


def test_history_has_labels_for_current_parent_only():
    script = [
        {"cards": [_card("a", smoke_pass=False), _card("b")]},      # under seed
        {"cards": [_card("c", pool=0.12)]},                          # under seed → adopted
        {"cards": [_card("d")]},                                     # under c
    ]
    adapter = MockAdapter(script, n_tasks=12)
    _run(adapter, _config(b_cards=1, analyst_cards=2), max_rounds=3)
    h = adapter.history_calls
    assert h[0] == []
    assert h[1] == [dict(title="a", label="smoke_fail"), dict(title="b", label="screen_fail")]
    assert h[2] == []                                               # new parent c
    for entries in h:
        _no_numbers(entries)


def test_history_after_rollback_carries_confirmation_outcome():
    base = {t: 0.3 for t in FRESH}
    script = [{"cards": [_card("a", pool=0.12, fresh=-0.12)]}, {"cards": [_card("b")]}]
    adapter = MockAdapter(script, n_tasks=12, base_scores=base)
    result = _run(adapter, _config(T=0.10, b_cards=1), fresh=[FRESH, FRESH], max_rounds=2)
    assert result.rounds[0].confirmation_verdict == "rejected"
    assert adapter.history_calls[1] == [dict(title="a", label="rejected", attribution="unattributed",
                                             attribution_possibly_biased=False)]
    _no_numbers(adapter.history_calls[1])


# ---------------------------------------------------------------------------
# §2.2 attribution at confirmation
# ---------------------------------------------------------------------------

class FeatureAdapter(MockAdapter):
    """Trajectories carry the version; the predicate fires on seed runs only (one task unobserved)."""

    def evaluate(self, version, task_ids, k, *, purpose="screen"):
        res = super().evaluate(version, task_ids, k, purpose=purpose)
        for tid, runs in res.trajectories.items():
            for run in runs:
                run["v"] = version.content_hash
        return res

    def features(self, trajectories, predicate):
        if predicate is None:
            return {}
        return {tid: (None if tid == FRESH[-1] else traj[0]["v"] == self._seed_hash)
                for tid, traj in trajectories.items() if traj}


def test_attribution_unattributed_without_predicate():
    adapter = MockAdapter([{"cards": [_card("a", pool=0.12, fresh=0.06)]}], n_tasks=12)
    result = _run(adapter, _config(T=0.10, b_cards=1), fresh=[FRESH, FRESH])
    attr = result.rounds[0].confirmations[0]["attribution"]
    assert attr["label"] == "unattributed" and attr["possibly_biased"] is False


def test_attribution_with_predicate_is_marked_possibly_biased():
    pred = Predicate({"field": "trace.n_empty_retrievals", "kind": "range", "min": 1}, "decrease")
    adapter = FeatureAdapter([{"cards": [_card("a", pool=0.12, fresh=0.06)], "predicate": pred}],
                             n_tasks=12)
    result = _run(adapter, _config(T=0.10, b_cards=1, attribution_n_min=3), fresh=[FRESH, FRESH])
    conf = result.rounds[0].confirmations[0]
    assert conf["verdict"] == "confirmed"
    attr = conf["attribution"]
    assert attr["label"] == "supported"
    assert attr["possibly_biased"] is True and "same segment-start run" in attr["reason"]
    assert attr["independent_parent_slice"] is False
    assert attr["r_p"] == 1.0 and attr["r_c"] == 0.0
    assert attr["slice_size"] == 5 and attr["n_not_computable"] == 1   # None dropped
    assert attr["delta_S"] == pytest.approx(0.06) and attr["delta_notS"] is None


def test_attribution_small_slice_is_insufficient():
    pred = Predicate({"field": "x"}, "decrease")
    adapter = FeatureAdapter([{"cards": [_card("a", pool=0.12, fresh=0.06)], "predicate": pred}],
                             n_tasks=12)
    result = _run(adapter, _config(T=0.10, b_cards=1, attribution_n_min=10), fresh=[FRESH, FRESH])
    assert result.rounds[0].confirmations[0]["attribution"]["label"] == "insufficient"


def test_attribution_result_requires_a_reason_when_not_independent():
    with pytest.raises(ValueError):
        attribution_result(1.0, 0.0, 0.1, 0.0, "decrease", 1, 5, independent_parent_slice=False)
    ok = attribution_result(1.0, 0.0, 0.1, 0.0, "decrease", 1, 5, independent_parent_slice=True)
    assert ok == dict(label="supported", possibly_biased=False, reason=None)
    none = attribution_result(1.0, None, 0.1, 0.0, "decrease", 1, 5,
                              independent_parent_slice=False, bias_reason="same run")
    assert none == dict(label="insufficient", possibly_biased=True, reason="same run")


def test_retry_after_run_failure_halt_continues_with_queued_cards(tmp_path):
    cards = [_card(n) for n in "abc"]
    adapter = MockAdapter([{"cards": cards}], n_tasks=12,
                          fail_on={"smoke": EvaluationUnavailable("runner down")})
    cfg = _config(b_cards=1, analyst_cards=3, max_consecutive_run_failures=2)
    first = _run(adapter, cfg, state_dir=tmp_path)
    assert first.halt_reason == "process_failure"
    assert [c["verdict"] for c in first.rounds[0].candidates] == ["smoke_fail", "smoke_fail"]
    assert _state(tmp_path)["current_round"]["owed_refills"] == 1
    adapter.fail_on.clear()
    second = _run(adapter, cfg, state_dir=tmp_path, key_generation=1)
    assert [c["card_ref"] for c in second.rounds[0].candidates] == ["R1:a", "R1:b", "R1:c"]
    assert second.rounds[0].candidates[2]["verdict"] == "screen_fail"
    assert second.halt_reason == "max_rounds"
