"""method-v4.1 fast variant: gate variants, independent re-measurement, carry-over, analyst history, dedup."""
import pytest

from rag_eval.optimizer.v4 import agent_context, proposal
from rag_eval.optimizer.v4.config import V4Config, digest

from .test_v4_loop import BAND, cfg, records, run
from .v4_mock import V4Mock

LOW_GATE = dict(c=0.0, c_mech=0.0)


# ---------------------------------------------------------------------------
# gate variants and re-measurement
# ---------------------------------------------------------------------------

def test_zero_threshold_adopts_a_small_positive_gain():
    # BAND: 0 < Δ/SE < 1 with an unconfirmed mechanism, so the default gate (c = 1) refuses it.
    strict, _ = run(V4Mock([[dict(name="A", **BAND)]]), cfg())
    assert len(strict.lineage) == 1
    loose, _ = run(V4Mock([[dict(name="A", **BAND)]]), cfg(**LOW_GATE))
    assert loose.lineage[-1]["version"] == "seed+r1-A"
    assert set(cfg(**LOW_GATE).variants()) == {"c", "c_mech"}


def test_independent_remeasure_rejects_a_lucky_first_measurement_the_merge_would_keep():
    spec = dict(name="A", delta=0.0, delta_by_attempt={0: 0.20, 1: -0.02})
    merged, _ = run(V4Mock([[dict(spec)]]), cfg(**LOW_GATE))
    assert merged.lineage[-1]["version"] == "seed+r1-A"
    indep, _ = run(V4Mock([[dict(spec)]]), cfg(remeasure_mode="independent", **LOW_GATE))
    rm = records(indep)["A"]["remeasure"]
    assert rm["qualification"]["qualified"] and not rm["independent"]["qualification"]["qualified"]
    assert rm["passed"] is False and len(indep.lineage) == 1


def test_parallel_remeasure_measures_every_qualified_candidate_once():
    mock = V4Mock([[dict(name="A", delta=0.10), dict(name="B", delta=0.06)]])
    r, _ = run(mock, cfg(remeasure_parallel=True))
    rec = records(r)
    assert rec["A"]["remeasure"] and rec["B"]["remeasure"]
    assert rec["A"]["label"] == "adopted" and rec["B"]["label"] == "qualified_not_adopted"


def test_carry_over_rebases_the_runner_up_onto_the_new_parent_without_an_agent():
    mock = V4Mock([[dict(name="A", delta=0.10), dict(name="B", delta=0.06)], [dict(name="C", delta=-0.2)]])
    r, _ = run(mock, cfg(carry_over=2))
    assert r.lineage[1]["version"] == "seed+r1-A"
    second = r.rounds[1]
    titles = [c["failure_mode_desc"] for c in second["cards"]]
    assert titles == ["B", "C"] and second["cards"][0]["carry_from"] == "seed+r1-B"
    rec = records(r, 1)["B"]
    assert rec["candidate"] == "seed+r1-A*r1-B" and mock.calls.get("implement") == 3   # A, B, C only
    assert r.lineage[-1]["version"] == "seed+r1-A*r1-B"


def test_no_carry_over_without_an_adoption_or_when_off():
    r, _ = run(V4Mock([[dict(name="A", delta=0.10), dict(name="B", delta=0.06)], [dict(name="C")]]), cfg())
    assert [c["failure_mode_desc"] for c in r.rounds[1]["cards"]] == ["C"]


def test_diagnosis_rotates_after_two_rounds_without_adoption():
    mock = V4Mock([[]], n_search=40)
    r, _ = run(mock, cfg(diagnose_size=10, rotate_diagnosis=True))
    first = r.diagnosis_sample(mock.seed)
    r.no_adoption = 2
    assert r.diagnosis_sample(mock.seed) != first
    r.no_adoption = 3
    assert r.diagnosis_sample(mock.seed) == r.diagnosis_sample(mock.seed)


def test_new_fields_at_default_keep_the_recorded_digest():
    base = V4Config(time_budget_hours=1.0)
    assert digest(base) != digest(V4Config(time_budget_hours=1.0, remeasure_mode="independent"))


def test_proposal_accepts_gate_variants_only_from_the_user():
    from .test_v4_proposal import INPUTS, cal
    CAL = cal()
    p = proposal.rule_defaults(CAL, INPUTS)
    p = proposal.apply_user_edit(p, "c", 0.0)
    p = proposal.apply_user_edit(p, "c_mech", 0.0)
    p = proposal.apply_user_edit(p, "remeasure_mode", "independent")
    frozen = proposal.freeze(p, CAL)
    assert frozen["config"]["c"] == 0.0 and frozen["config"]["remeasure_mode"] == "independent"
    with pytest.raises(ValueError):
        proposal.apply_user_edit(p, "remeasure_mode", "sometimes")
    bad = proposal.apply_user_edit(proposal.rule_defaults(CAL, INPUTS), "c", 0.2)   # c_mech 0.5 > c
    assert not proposal.validate(bad, CAL)["ok"]


# ---------------------------------------------------------------------------
# analyst history and duplicates
# ---------------------------------------------------------------------------

def test_effect_bands_never_carry_numbers():
    assert agent_context.effect_band(dict(stage="measured", delta_val=-0.05, se_val=0.03), "below_threshold") == "worse"
    assert agent_context.effect_band(dict(stage="measured", delta_val=-0.01, se_val=0.03), "below_threshold") == "flat"
    assert agent_context.effect_band(dict(stage="measured", delta_val=0.01, se_val=0.03), "below_threshold") == "positive"
    assert agent_context.effect_band(dict(stage="minibatch_fail"), "minibatch_fail") == "worse"
    line = agent_context._line(dict(title="T", label="below_threshold", effect="positive", outcome="mechanism_unknown"))
    assert "a positive gain, but not adopted" in line and "0.0" not in line


def test_history_reaches_the_analyst_with_effect_bands():
    mock = V4Mock([[dict(name="A", delta=-0.2)], [dict(name="B")]])
    r, _ = run(mock, cfg(minibatch=True))
    entry = next(e for e in r.history if e["title"] == "A")
    assert entry["effect"] == "worse" and "delta_val" not in entry


def test_duplicates_are_dropped_against_settled_cards_and_within_a_round():
    history = [dict(title="Answer every reading", label="below_threshold"),
               dict(title="Old idea", label="below_threshold", earlier_parent=True)]
    long = "Rewrite the synthesis prompt so each plausible reading of an ambiguous question gets its own answer."
    cards = [dict(failure_mode_desc="answer every reading!", change_hypothesis="x"),
             dict(failure_mode_desc="Old idea", change_hypothesis="y"),
             dict(failure_mode_desc="New", change_hypothesis=long),
             dict(failure_mode_desc="Newer", change_hypothesis=long.upper()),
             dict(failure_mode_desc="Short", change_hypothesis="x")]
    kept, dropped = agent_context.drop_duplicates(cards, history)
    assert [c["failure_mode_desc"] for c in kept] == ["Old idea", "New", "Short"]
    assert [d["reason"] for d in dropped] == ["repeats a card already settled under this parent",
                                              "repeats another card of this round"]


def test_repeated_implementation_failures_make_a_direction_fruitless():
    rows = agent_context.direction_summary([
        dict(title="a", label="implementation_failed", direction="retrieval-retry"),
        dict(title="b", label="implementation_failed", direction="retrieval-retry")])
    assert rows[0]["change_required"]
    text = agent_context.render_direction_summary([dict(title="a", label="implementation_failed", direction="d")] * 2)
    assert "REQUIRED" in text and "never finished implementing" in text


def test_task_note_is_selected_by_input_and_holds_no_case():
    from rag_eval.optimizer.v4.task_notes import task_note
    note = task_note(dict(input_id="alce-asqa-input", suite="alce"))
    assert "str_em" in note and "ambiguous" in note
    assert task_note(dict(input_id="qasper-input", suite="qasper")) == ""


# ---------------------------------------------------------------------------
# exploratory adoption
# ---------------------------------------------------------------------------

FLAT = dict(delta=-0.004)     # within the 0.01 margin, never qualifies by the ordinary gate


def test_exploration_moves_to_a_flat_candidate_and_keeps_the_best_node_final():
    from rag_eval.optimizer.v4.loop import final_version
    mock = V4Mock([[dict(name="A", delta=0.10)], [dict(name="B", **FLAT)]])
    r, _ = run(mock, cfg(explore_after=1, remeasure_mode="independent"))
    assert [n["kind"] for n in r.lineage] == ["seed", "candidate", "explore"]
    assert r.lineage[-1]["version"] == "seed+r1-A+r2-B"
    assert records(r, 1)["B"]["label"] == "explored"
    explore = r.rounds[1]["decision"]["explore"]
    assert explore["chosen"] == 0 and all(d >= -0.01 for d in explore["tried"][0]["deltas"])
    assert final_version(r.lineage, r.ablation) == "seed+r1-A"     # the exploratory step is not an improvement
    entry = next(e for e in r.history if e["title"] == "B")
    assert entry["outcome"] == "explored_without_gain" and "effect" not in entry


def test_exploration_waits_for_explore_after_rounds_without_adoption():
    mock = V4Mock([[dict(name="A", delta=0.10)], [dict(name="B", **FLAT)], [dict(name="D", **FLAT)]])
    r, _ = run(mock, cfg(explore_after=2))
    assert [(n["kind"], n["round"]) for n in r.lineage] == [("seed", 0), ("candidate", 1), ("explore", 3)]
    assert "explore" not in r.rounds[1]["decision"]


def test_no_exploration_for_a_clearly_worse_candidate_or_from_the_seed():
    worse, _ = run(V4Mock([[dict(name="A", delta=0.10)], [dict(name="C", delta=-0.2)]]), cfg(explore_after=1))
    assert [n["kind"] for n in worse.lineage] == ["seed", "candidate"]
    seed_only, _ = run(V4Mock([[dict(name="B", **FLAT)]]), cfg(explore_after=1))
    assert len(seed_only.lineage) == 1
    assert seed_only.rounds[0]["decision"]["explore"]["tried"][0]["ok"] is False   # cumulative Δ vs seed ≤ 0


def test_exploration_is_off_by_default():
    r, _ = run(V4Mock([[dict(name="A", delta=0.10)], [dict(name="B", **FLAT)]]), cfg())
    assert [n["kind"] for n in r.lineage] == ["seed", "candidate"] and "explore" not in r.rounds[1]["decision"]


def test_proposal_accepts_explore_and_agent_variants():
    from .test_v4_proposal import INPUTS, cal
    CAL = cal()
    p = proposal.rule_defaults(CAL, INPUTS)
    for key, value in (("explore_after", 2), ("explore_margin", 0.01), ("adapter.analyst_edits", True),
                       ("adapter.implementer_profile", "claude-kimi-k3")):
        p = proposal.apply_user_edit(p, key, value)
    frozen = proposal.freeze(p, CAL)["config"]
    assert frozen["explore_after"] == 2 and frozen["adapter"]["analyst_edits"] is True
    assert frozen["adapter"]["implementer_profile"] == "claude-kimi-k3"
    for key, bad in (("explore_margin", 0.5), ("adapter.implementer_profile", ""), ("adapter.analyst_edits", 1)):
        with pytest.raises(ValueError):
            proposal.apply_user_edit(p, key, bad)


# ---------------------------------------------------------------------------
# analyst edits: shape and application
# ---------------------------------------------------------------------------

def test_edits_shape_and_exactly_once_application():
    from rag_eval.workbench.hypotheses import apply_edits, validate_edits
    edits = validate_edits([dict(path='backend/app/a.py', find='X = 1', replace='X = 2'),
                            dict(path='backend/app/a.py', find='Y = 1', replace='Y = 3')])
    files = apply_edits(lambda p: 'X = 1\nY = 1\n', edits)
    assert files == {'backend/app/a.py': 'X = 2\nY = 3\n'}
    with pytest.raises(ValueError, match='occurs 2 times'):
        apply_edits(lambda p: 'X = 1\nX = 1\n', edits[:1])
    for bad in ([], [dict(path='backend/app/a.py', find='', replace='x')],
                [dict(path='src/rag_eval/a.py', find='a', replace='b')],
                [dict(path='backend/app/a.py', find='a', replace='a')],
                [dict(path='backend/app/a.py', find='a')]):
        with pytest.raises(ValueError):
            validate_edits(bad)
