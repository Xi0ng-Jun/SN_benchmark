"""On-disk evidence ledger (method-v3 §2.4) and rounds.jsonl (binding plan §2.7)."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict

import pytest

from rag_eval.optimizer import ProtocolConfig, run_optimization
from rag_eval.optimizer import ledger_io
from tests.optimizer.mock_adapter import MockAdapter

POOL = [f"t{i}" for i in range(6)]
FRESH = [f"t{i}" for i in range(6, 12)]


def _config(**overrides) -> ProtocolConfig:
    defaults = dict(mde=0.05, sigma_d=0.1, ladder_c=1.0, K_max=20, T=0.10, n_fresh=6,
                    b_cards=1, k_screen=1, k_smoke=1, n_smoke_sample=2,
                    predicate_coverage_floor=0.6)
    defaults.update(overrides)
    return ProtocolConfig(**defaults)


def _card(name, *, pool=0.0, fresh=0.0, **extra):
    return dict(name=name, deltas={**{t: pool for t in POOL}, **{t: fresh for t in FRESH}}, **extra)


@pytest.fixture
def finished_run(tmp_path):
    # Round 1: smoke_fail, screen_fail, adoption → milestone confirmed.
    # Round 2: one screen_fail. Round 3: no cards → halt.
    script = [
        {"cards": [_card("a", smoke_pass=False), _card("b"), _card("c", pool=0.12, fresh=0.06)]},
        {"cards": [_card("d")]},
    ]
    adapter = MockAdapter(script, n_tasks=12)
    result = run_optimization(adapter, adapter.seed, _config(analyst_cards=3),
                              search_set_ids=POOL, val_pool_ids=POOL,
                              fresh_batch_ids=[FRESH, FRESH], smoke_sample_ids=POOL[:2],
                              max_rounds=5, state_dir=tmp_path)
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    return result, ledger, tmp_path


def test_ledger_has_separate_sections(finished_run):
    _, ledger, _ = finished_run
    # Utility, selection and mechanism are reported side by side, never summed.
    assert set(ledger) == {"schema_version", "utility", "selection", "mechanism", "context",
                           "integrity", "note"}
    assert "never summed" in ledger["note"]
    for section in ("utility", "selection", "mechanism"):
        assert not any("total" in k or "sum" in k for k in ledger[section]), section


def test_ledger_counts_match_round_results(finished_run):
    result, ledger, tmp_path = finished_run
    attempts = [c for r in result.rounds for c in r.candidates]
    integrity = ledger["integrity"]
    assert integrity["n_attempts_recorded"] == len(attempts) == 4
    assert integrity["attempts_by_verdict"] == dict(Counter(c["verdict"] for c in attempts))
    assert integrity["attempts_by_verdict"] == {"smoke_fail": 1, "screen_fail": 2, "screen_pass": 1}
    assert integrity["cards_generated"] == sum(r.cards_generated for r in result.rounds)
    assert "smoke_fail" in integrity["rule"] and "stale_parent" in integrity["rule"]
    sel = ledger["selection"]
    assert sel["verdict_counts"] == integrity["attempts_by_verdict"]
    assert sel["n_evaluated_on_validation_pool"] == 3          # smoke_fail never screened
    assert sel["confirmation_verdict_counts"] == {"confirmed": 1}
    # rounds.jsonl: one line per finished round, same content as RoundResults.
    lines = [json.loads(l) for l in (tmp_path / "rounds.jsonl").read_text().splitlines()]
    assert [l["round_id"] for l in lines] == [r.round_id for r in result.rounds]
    assert lines[0]["candidates"] == json.loads(ledger_io.dumps(asdict(result.rounds[0])))["candidates"]


def test_ledger_utility_selection_mechanism_content(finished_run):
    result, ledger, _ = finished_run
    conf = result.rounds[0].confirmations[0]
    [ms] = ledger["utility"]["certified_milestones"]
    assert ms["delta_fresh"] == pytest.approx(conf["delta_fresh"]) == pytest.approx(0.06)
    assert ms["se_fresh"] == pytest.approx(0.0)
    assert ms["k_fresh"] == 1 and ms["n_fresh"] == 6 and ms["provisional_steps_in_segment"] == 1
    assert ms["cost_change"] == 0.0
    screened = {s["card_ref"]: s for s in ledger["selection"]["screened_candidates"]}
    c = screened["R1:c"]
    assert c["delta_val"] == pytest.approx(0.12) and c["delta_fresh"] == pytest.approx(0.06)
    assert c["shrinkage"] == pytest.approx(0.06) and c["confirmation_verdict"] == "confirmed"
    assert screened["R1:b"]["delta_fresh"] is None
    [seg] = ledger["selection"]["segment_confirmations"]
    assert seg["segment_shrinkage"] == pytest.approx(0.06)
    mech = ledger["mechanism"]
    assert mech["attributions"][0]["label"] == "unattributed"
    assert mech["share_supported_among_certified"] == 0.0
    pa = mech["predicate_attachment"]
    assert pa == dict(cards_generated=4, cards_with_predicate=0, rate=0.0, floor=0.6, meets_floor=False)
    assert mech["counts_for_mechanism_comparison"] is False
    ctx = ledger["context"]
    assert ctx["halt_reason"] == result.halt_reason == "max_rounds"
    assert ctx["validation_pool_size"] == 12 and ctx["initial_validation_pool_size"] == 6
    assert ctx["config_sha256"] == ledger_io.config_digest(_config(analyst_cards=3))[1]
    assert "familywise_error" in ctx["alpha_note"]


def test_ladder_blocked_counts_lower_bound_pass_below_best_plus_m():
    rec = dict(card_ref="x", card_index=0, verdict="screen_fail", delta_val=0.05, se_val=0.01,
               score_val=0.55, best_before=0.52, ladder_m=0.05, candidate="h", parent="p")
    not_blocked = dict(rec, card_ref="y", delta_val=0.005)          # lower bound ≤ 0
    rounds = [dict(round_id=1, cards_generated=2, cards_with_predicate=1,
                   candidates=[rec, not_blocked], confirmations=[])]
    ledger = ledger_io.build_ledger(rounds=rounds, lineage=[], config=dict(z_screen=0.84),
                                    config_sha256="h", context={})
    assert ledger["selection"]["ladder_blocked"] == 1
    assert ledger["mechanism"]["predicate_attachment"]["rate"] == 0.5


def test_sync_rounds_jsonl_drops_torn_line_and_never_duplicates(tmp_path):
    path = tmp_path / "rounds.jsonl"
    path.write_text(json.dumps({"round_id": 1}) + "\n" + '{"round_id": 2, "cand')
    added = ledger_io.sync_rounds_jsonl(path, [{"round_id": 1}, {"round_id": 2}])
    assert added == 1
    assert ledger_io.sync_rounds_jsonl(path, [{"round_id": 1}, {"round_id": 2}]) == 0
    assert [json.loads(l)["round_id"] for l in path.read_text().splitlines()] == [1, 2]


def test_non_finite_floats_become_null():
    assert json.loads(ledger_io.dumps({"a": float("-inf"), "b": [float("nan"), 1.0]})) == \
        {"a": None, "b": [None, 1.0]}
