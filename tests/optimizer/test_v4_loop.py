"""method-v4 loop on the deterministic mock (W5): cascade, adoption, merge, mechanism, resume, stops."""
import pytest

from rag_eval.optimizer.adapter import EvaluationUnavailable
from rag_eval.optimizer.v4 import decide
from rag_eval.optimizer.v4.config import V4Config
from rag_eval.optimizer.v4.loop import V4Run

from .v4_mock import V4Mock


def cfg(**kw):
    kw.setdefault("time_budget_hours", 100.0)
    kw.setdefault("b", 2)
    kw.setdefault("no_adoption_rounds", 1)
    return V4Config(**kw)


def run(mock, config, tmp_path=None, **kw):
    r = V4Run(mock, mock.seed, config, search=mock.search, validation=mock.validation,
              state_dir=tmp_path, **kw)
    return r, r.run()


def records(r, round_index=0):
    return {rec["title"]: rec for rec in r.rounds[round_index]["records"].values()}


def test_cascade_adoption_and_merge(tmp_path):
    mock = V4Mock([[dict(name="A", delta=0.10, effect=True), dict(name="B", delta=0.06),
                    dict(name="C", delta=-0.2), dict(name="D", implement_error=True)]])
    r, reason = run(mock, cfg(), tmp_path)
    assert reason == "no_adoption"          # round 2 has no cards
    rec = records(r)
    assert rec["C"]["stage"] == "minibatch_fail" and rec["C"]["label"] == "minibatch_fail"
    assert rec["D"]["stage"] == "error" and "label" not in rec["D"]
    assert rec["A"]["mechanism"]["tier"] == "confirmed" and rec["B"]["mechanism"]["tier"] == "unconfirmed"
    assert rec["A"]["qualification"]["qualified"] and rec["B"]["qualification"]["qualified"]
    merge = r.rounds[0]["decision"]["merge"]
    assert merge["adopt"] and merge["delta_val"] == pytest.approx(0.16, abs=0.01)
    node = r.lineage[-1]
    assert node["kind"] == "merge" and node["parent"] == "seed" and node["measured_against"] == "seed+r1-A"
    assert rec["A"]["label"] == rec["B"]["label"] == "adopted"
    # The calibration fixed k_val; the minibatch is a fixed quarter (>= 10) of validation.
    assert r.calibration["k_val"] == 2 and len(r.partitions["minibatch"]) == 10
    # Round 2 was analysed on the merged parent with the v4 labels of round 1 (none under it).
    assert mock.calls["analyse"] == 2 and mock.last_history == []
    # Control pairs exist and the reviewer never sees the candidate side or a task id.
    pairs = mock.reviews[0]["pairs"]
    assert len(pairs) == 8 and set(pairs[0]) == {"pair", "task", "A", "B"}
    ledger = r.ledger()
    assert ledger["stage_counts"]["measured"] == 2 and ledger["deviations"]


def test_merge_not_adopted_when_rebase_conflicts():
    mock = V4Mock([[dict(name="A", delta=0.10), dict(name="B", delta=0.06, conflict=True)]])
    r, _ = run(mock, cfg())
    assert r.rounds[0]["decision"]["merge"]["skipped"] == "rebase conflict"
    assert r.lineage[-1]["version"] == "seed+r1-A"
    assert records(r)["B"]["label"] == "qualified_not_adopted"


def test_two_tier_threshold_and_untrusted_reviewer():
    # Δ ≈ 0.75 SE: adopted only when the mechanism is confirmed (c_mech = 0.5 < 0.75 < c = 1).
    small = [[dict(name="S", delta=0.0042, effect=True)]]
    honest, _ = run(V4Mock(small), cfg())
    rec = records(honest)["S"]
    assert 0.5 < rec["delta_val"] / rec["se_val"] < 1
    assert rec["mechanism"]["tier"] == "confirmed" and honest.lineage[-1]["version"] == "seed+r1-S"
    sycophant, _ = run(V4Mock(small, reviewer="sycophant"), cfg())
    rec = records(sycophant)["S"]
    assert rec["mechanism"]["reviewer"]["trusted"] is False and rec["mechanism"]["tier"] == "unconfirmed"
    assert rec["label"] == "below_threshold" and len(sycophant.lineage) == 1


def test_mechanical_check_confirms_without_reviewer():
    mock = V4Mock([[dict(name="P", delta=0.0042, predicate=True)]], reviewer=None)
    r, _ = run(mock, cfg())
    rec = records(r)["P"]
    assert rec["mechanism"]["mechanical"]["status"] == "changed" and rec["mechanism"]["tier"] == "confirmed"
    assert rec["smoke"]["verdict"] == "pass" and len(r.lineage) == 2


def test_suspicious_reviewer_vetoes_a_large_gain():
    r, _ = run(V4Mock([[dict(name="G", delta=0.2)]], reviewer="gaming"), cfg())
    rec = records(r)["G"]
    assert rec["mechanism"]["tier"] == "suspicious" and "mechanism_suspicious" in rec["qualification"]["reasons"]
    assert len(r.lineage) == 1


def test_smoke_repair_then_pass_and_unrepairable_fail():
    mock = V4Mock([[dict(name="R", delta=0.1, predicate=True, smoke_behaviour_fails=True, repairable=True),
                    dict(name="U", delta=0.1, predicate=True, smoke_behaviour_fails=True)]])
    r, _ = run(mock, cfg(merge=False))
    rec = records(r)
    assert rec["R"]["repaired"] and rec["R"]["attempt"] == 2 and rec["R"]["stage"] == "measured"
    assert rec["R"]["prior_attempts"][0]["smoke"]["verdict"] == "fail"
    assert rec["U"]["stage"] == "smoke_fail" and rec["U"]["label"] == "smoke_fail"


def test_safeguard_breach_on_the_minibatch_eliminates():
    mock = V4Mock([[dict(name="H", delta=0.2)]])
    mock.safeguard_harm["H"] = 0.10       # +10 pp infrastructure failures > 5 pp tolerance
    r, _ = run(mock, cfg())
    rec = records(r)["H"]
    assert rec["stage"] == "minibatch_fail" and rec["label"] == "safeguard_breached"
    assert r.history[0]["safeguards_breached"] == ["infrastructure_failure_rate"]


def test_kill_and_resume_replays_only_unfinished_work(tmp_path):
    script = [[dict(name="A", delta=0.1), dict(name="B", delta=-0.3), dict(name="C", delta=0.05)]]
    mock = V4Mock(script)
    mock.fail_once["rest:A"] = EvaluationUnavailable("server went away")
    r, reason = run(mock, cfg(b=1, merge=False), tmp_path)
    assert reason == "process_failure" and r.current is not None
    implemented = mock.calls["implement"]
    r2, reason2 = run(mock, cfg(b=1, merge=False), tmp_path)
    assert reason2 == "no_adoption"
    assert r2.lineage[-1]["version"] == "seed+r1-A"
    # A's stage 2 was rerun from its persisted minibatch_pass record: nothing re-implemented except unstarted cards.
    assert mock.calls["implement"] - implemented <= len(script[0]) - 1
    assert len(r2.sessions) == 2 and r2.rounds[0]["decision"]["adopted"] == "seed+r1-A"
    with pytest.raises(ValueError):
        V4Run(mock, mock.seed, cfg(b=3), search=mock.search, validation=mock.validation, state_dir=tmp_path)


def test_time_budget_stops_before_a_round_that_cannot_fit(tmp_path):
    now = [0.0]

    def clock():
        now[0] += 60.0       # every clock read advances a minute
        return now[0]
    mock = V4Mock([[dict(name="A", delta=0.1)], [dict(name="B", delta=0.1)], [dict(name="C", delta=0.1)]] * 5)
    r, reason = run(mock, cfg(time_budget_hours=1.0, no_adoption_rounds=3), tmp_path, clock=clock)
    assert reason == "time_budget" and len(r.rounds) < 15


def test_no_adoption_rounds_stop():
    mock = V4Mock([[dict(name="X", delta=-0.01)]] * 5)
    r, reason = run(mock, cfg(no_adoption_rounds=2))
    assert reason == "no_adoption" and len(r.rounds) == 2 and len(r.lineage) == 1
    assert mock.last_history and mock.last_history[0]["label"] in ("below_threshold", "minibatch_fail")


def test_minibatch_split_is_fixed_and_proportional_over_families():
    ids = [f"v{i:02d}" for i in range(40)]
    fam = {t: f"paper{i % 4}" for i, t in enumerate(ids)}
    mb, rest = decide.minibatch_split(ids, 0.25, 10, seed="s", families=fam)
    assert len(mb) == 10 and not set(mb) & set(rest) and len(set(mb) | set(rest)) == 40
    counts = sorted(sum(fam[t] == f"paper{j}" for t in mb) for j in range(4))
    assert counts[-1] - counts[0] <= 1
    assert decide.minibatch_split(ids, 0.25, 10, seed="s", families=fam) == (mb, rest)


def test_reviewer_conclusion_rules():
    pairs = [dict(pair=f"p{i}", kind="candidate", candidate_side="A") for i in range(1, 7)] + \
            [dict(pair="p7", kind="control", candidate_side=None), dict(pair="p8", kind="control", candidate_side=None)]
    yes = lambda p, side="A": dict(pair=p, effect=True, changed_side=side, gaming=False)  # noqa: E731
    verdicts = [yes(f"p{i}") for i in range(1, 5)] + [dict(pair="p5", effect=False), dict(pair="p6", effect=False)]
    rules = dict(min_effect=4, max_control_effect=1)
    assert decide.reviewer_conclusion(verdicts + [yes("p7")], pairs, **rules)["conclusion"] == "effective"
    assert decide.reviewer_conclusion(verdicts + [yes("p7"), yes("p8")], pairs, **rules)["trusted"] is False
    wrong = [yes(f"p{i}", "B") for i in range(1, 7)]
    assert decide.reviewer_conclusion(wrong, pairs, **rules)["conclusion"] == "none"
    gaming = verdicts[:-1] + [dict(pair="p6", effect=False, gaming=True, note="shorter answers")]
    assert decide.reviewer_conclusion(gaming, pairs, **rules)["conclusion"] == "suspicious"
    assert decide.reviewer_conclusion(None, pairs, **rules)["conclusion"] == "none"
    assert decide.tier("changed", "none") == "confirmed" and decide.tier("changed", "suspicious") == "suspicious"
