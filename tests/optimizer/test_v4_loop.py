"""method-v4.1 loop on the deterministic mock: cascade, re-measurement, adoption, mechanism, safeguards, stops."""
import pytest

from rag_eval.optimizer.adapter import EvalResult, EvaluationUnavailable
from rag_eval.optimizer.v4 import agent_context, decide
from rag_eval.optimizer.v4.config import V4Config
from rag_eval.optimizer.v4.loop import STOPPED, RunStopRequested, V4Run

from .v4_mock import ScriptedRun, V4Mock


# Mock calibration: the candidate's first measurement uses attempt 0, whose hash noise sits above the seed's
# 3-attempt mean. BAND keeps Δ/SE in (c_mech, c) on the first measurement (~0.78) and on the merged one (~0.87).
BAND = dict(delta=-0.001, delta_by_attempt={1: 0.006})


def lucky(name, gain):
    """High on the first measurement, equally low on the re-measurement: the merged gain is ~0."""
    return dict(name=name, delta=0.0, delta_by_attempt={0: gain, 1: -gain})


def cfg(**kw):
    kw.setdefault("time_budget_hours", 100.0)
    kw.setdefault("b", 2)
    return V4Config(**kw)


def run(mock, config, tmp_path=None, **kw):
    r = ScriptedRun(mock, mock.seed, config, search=mock.search, validation=mock.validation,
                    state_dir=tmp_path, **kw)
    return r, r.run()


def records(r, round_index=0):
    return {rec["title"]: rec for rec in r.rounds[round_index]["records"].values()}


# ---------------------------------------------------------------------------
# cascade, re-measurement, adoption
# ---------------------------------------------------------------------------

def test_cascade_remeasures_the_best_and_adopts_the_merged_baseline(tmp_path):
    mock = V4Mock([[dict(name="A", delta=0.10), dict(name="B", delta=0.06),
                    dict(name="C", delta=-0.2), dict(name="D", implement_error=True)]])
    r, reason = run(mock, cfg(minibatch=True), tmp_path)
    assert reason == "script_end"
    rec = records(r)
    assert rec["C"]["stage"] == "minibatch_fail" and rec["C"]["label"] == "minibatch_fail"
    assert rec["D"]["stage"] == "error" and rec["D"]["label"] == "implementation_failed"
    assert rec["D"]["failure_kind"] == "workspace_error"
    failed = next(e for e in r.history if e["title"] == "D")
    assert failed["outcome"] == "implementation_workspace_error" and "reason" not in failed
    # All potentially qualifying candidates are reviewed, including Δ >= c·SE.
    assert mock.calls.get("review", 0) == 2
    assert rec["A"]["qualification"]["qualified"] and rec["B"]["qualification"]["qualified"]
    decision = r.rounds[0]["decision"]
    assert decision["order"] == [0, 1] and decision["remeasured"] == [0]
    assert rec["A"]["remeasure"]["passed"] and rec["A"]["label"] == "adopted"
    assert rec["B"]["label"] == "qualified_not_adopted" and "remeasure" not in rec["B"]
    node = r.lineage[-1]
    assert node["version"] == "seed+r1-A" and node["remeasured"] and node["delta_first"] == rec["A"]["delta_val"]
    # The re-measurement is a distinct request (k=2 on the same subsets) and the parent baseline merges both.
    mb, rest = r.split_for("seed")
    assert ("seed+r1-A", "rest", tuple(rest), 2) in mock.measured
    baseline = r.parent_val(r.versions["seed+r1-A"])
    assert {len(v) for v in decide.attempts_by_task(baseline).values()} == {2}
    assert rec["A"]["remeasure"]["se_floor"] < rec["A"]["se_floor"]
    assert r.calibration.get("k_val") is None and len(r.partitions["seed_minibatch"]) == 10
    ledger = r.ledger()
    assert ledger["remeasures"] == [dict(round=1, card_index=0, passed=True, status="measured")]
    assert ledger["implementation_failures"] == {"workspace_error": 1} and ledger["deviations"]


def test_remeasure_failure_falls_back_to_the_next_candidate():
    mock = V4Mock([[lucky("A", 0.12), dict(name="B", delta=0.06)]])
    r, _ = run(mock, cfg())
    rec = records(r)
    assert r.rounds[0]["decision"]["remeasured"] == [0, 1]
    assert rec["A"]["remeasure"]["passed"] is False and rec["A"]["label"] == "remeasure_failed"
    assert rec["B"]["label"] == "adopted" and r.lineage[-1]["version"] == "seed+r1-B"
    entry = next(e for e in r.history if e["title"] == "A")
    assert entry["outcome"] == "remeasure_not_confirmed" and "delta_val" not in entry


def test_no_adoption_when_every_remeasurement_fails():
    r, _ = run(V4Mock([[lucky("A", 0.12), lucky("B", 0.08)]]), cfg())
    decision = r.rounds[0]["decision"]
    assert decision["remeasured"] == [0, 1] and decision["adopted"] is None and len(r.lineage) == 1
    assert {rec["label"] for rec in records(r).values()} == {"remeasure_failed"}


def test_remeasure_off_adopts_the_single_measurement():
    mock = V4Mock([[dict(name="A", delta=0.0, delta_by_attempt={0: 0.12})]])
    r, _ = run(mock, cfg(remeasure=False))
    assert r.lineage[-1]["version"] == "seed+r1-A" and not r.lineage[-1]["remeasured"]
    assert not any(m[3] == 2 for m in mock.measured if m[0] != "seed")


def test_merge_when_enabled():
    mock = V4Mock([[dict(name="A", delta=0.10), dict(name="B", delta=0.06)]])
    r, _ = run(mock, cfg(merge=True))
    merge = r.rounds[0]["decision"]["merge"]
    assert merge["adopt"] and merge["delta_val"] == pytest.approx(0.16, abs=0.01)
    node = r.lineage[-1]
    assert node["kind"] == "merge" and node["components"][0] == "seed+r1-A"
    assert {rec["label"] for rec in records(r).values()} == {"adopted"}


def test_merge_not_adopted_when_rebase_conflicts():
    mock = V4Mock([[dict(name="A", delta=0.10), dict(name="B", delta=0.06, conflict=True)]])
    r, _ = run(mock, cfg(merge=True))
    assert r.rounds[0]["decision"]["merge"]["skipped"] == "rebase conflict"
    assert r.lineage[-1]["version"] == "seed+r1-A"
    assert records(r)["B"]["label"] == "qualified_not_adopted"


# ---------------------------------------------------------------------------
# mechanism: the reviewer lowers the threshold, never vetoes
# ---------------------------------------------------------------------------

def test_two_tier_threshold_and_untrusted_reviewer():
    # Δ ≈ 0.8 SE: adopted only when the mechanism is confirmed (c_mech = 0.5 < 0.8 < c = 1).
    small = [[dict(name="S", effect=True, **BAND)]]
    honest, _ = run(V4Mock(small), cfg())
    rec = records(honest)["S"]
    assert 0.5 < rec["delta_val"] / rec["se_val"] < 1
    assert rec["mechanism"]["tier"] == "confirmed" and honest.lineage[-1]["version"] == "seed+r1-S"
    assert rec["remeasure"]["tier"] == "confirmed" and 0.5 < rec["remeasure"]["delta_val"] / rec["remeasure"]["se_val"] < 1
    sycophant, _ = run(V4Mock(small, reviewer="sycophant"), cfg())
    rec = records(sycophant)["S"]
    assert rec["mechanism"]["reviewer"]["trusted"] is False and rec["mechanism"]["tier"] == "unconfirmed"
    assert rec["label"] == "below_threshold" and len(sycophant.lineage) == 1 and "remeasure" not in rec


def test_mechanical_check_confirms_without_reviewer():
    mock = V4Mock([[dict(name="P", predicate=True, **BAND)]], reviewer=None)
    r, _ = run(mock, cfg())
    rec = records(r)["P"]
    assert rec["mechanism"]["mechanical"]["status"] == "changed" and rec["mechanism"]["tier"] == "confirmed"
    assert "smoke" not in rec and len(r.lineage) == 2


def test_gaming_report_cancels_the_relaxation_and_flags_but_never_vetoes():
    # In the review band: the mechanical check changed, yet reported gaming keeps the full threshold.
    mock = V4Mock([[dict(name="G", predicate=True, **BAND)]], reviewer="gaming")
    band, _ = run(mock, cfg())
    assert mock.calls["review"] == 1
    rec = records(band)["G"]
    assert rec["mechanism"]["mechanical"]["status"] == "changed"
    assert rec["mechanism"]["tier"] == "unconfirmed" and rec["flags"] == [decide.NEEDS_HUMAN_REVIEW]
    assert "mechanism_suspicious" not in rec["qualification"]["reasons"]
    assert rec["label"] == "below_threshold" and len(band.lineage) == 1
    assert next(e for e in band.history if e["title"] == "G")["needs_human_review"] is True
    # A gain above c·SE is reviewed and flagged; gaming still cannot veto it.
    mock = V4Mock([[dict(name="L", delta=0.2)]], reviewer="gaming")
    large, _ = run(mock, cfg())
    assert mock.calls["review"] == 1 and records(large)["L"]["label"] == "adopted"
    assert records(large)["L"]["flags"] == [decide.NEEDS_HUMAN_REVIEW]


def test_smoke_repair_then_pass_and_unrepairable_fail():
    mock = V4Mock([[dict(name="R", delta=0.1, predicate=True, smoke_behaviour_fails=True, repairable=True),
                    dict(name="U", delta=0.1, predicate=True, smoke_behaviour_fails=True)]])
    r, _ = run(mock, cfg(smoke=True, smoke_behaviour_check=True))
    rec = records(r)
    assert rec["R"]["repaired"] and rec["R"]["attempt"] == 2 and rec["R"]["stage"] == "measured"
    assert rec["R"]["prior_attempts"][0]["smoke"]["verdict"] == "fail"
    assert rec["U"]["stage"] == "smoke_fail" and rec["U"]["label"] == "smoke_fail"


# ---------------------------------------------------------------------------
# safeguards and infrastructure
# ---------------------------------------------------------------------------

def test_minibatch_records_but_never_eliminates_on_safeguards():
    mock = V4Mock([[dict(name="H", delta=0.2)]])
    mock.safeguard_harm["H"] = 0.25       # a quarter of the tasks lose their answer
    r, _ = run(mock, cfg(minibatch=True))
    rec = records(r)["H"]
    assert rec["safeguards_mb"]["breached"] == ["answer_produced_rate"]
    assert rec["stage"] == "measured" and rec["delta_mb"] is not None           # passed the minibatch
    assert rec["safeguards"]["breached"] == ["answer_produced_rate"] and rec["label"] == "safeguard_breached"
    assert r.history[0]["safeguards_breached"] == ["answer_produced_rate"] and len(r.lineage) == 1


def test_token_safeguard_within_tolerance_is_not_breached():
    mock = V4Mock([[dict(name="T", delta=0.1)]])
    mock.token_harm["T"] = 0.3            # +30% tokens < +50% tolerance
    r, _ = run(mock, cfg())
    row = next(x for x in records(r)["T"]["safeguards"]["rows"] if x["key"] == "tokens_per_task_ratio")
    assert row["breached"] is False and row["se_source"] == "calibration" and row["kind"] == "ratio"
    assert len(r.lineage) == 2


@pytest.mark.parametrize("dropped,stage", [(3, "measured"), (6, "run_failed")])
def test_infrastructure_drops_tasks_until_ten_percent_then_run_failure(dropped, stage):
    class Mock(V4Mock):
        def evaluate_subset(self, version, reference, subset, task_ids, k):
            ce, pe = super().evaluate_subset(version, reference, subset, task_ids, k)
            if version.content_hash != "seed" and subset == "rest":
                gone = sorted(task_ids)[:dropped]
                ce = EvalResult({t: v for t, v in ce.per_task_scores.items() if t not in gone}, ce.trajectories,
                                ce.costs, {t: ("no_admitted_attempt" if t in gone else None) for t in task_ids},
                                attempt_scores=[{t: v for t, v in a.items() if t not in gone} for a in ce.attempt_scores],
                                context=ce.context)
            return ce, pe
    r, _ = run(Mock([[dict(name="X", delta=0.1)]]), cfg())
    rec = records(r)["X"]
    assert rec["stage"] == stage
    if stage == "measured":
        assert rec["dropped_val"] == dropped and rec["n_val"] == 40 - dropped
    else:
        assert rec["label"] == "run_failed" and "run failure" in rec["reason"]


def test_cumulative_check_warns_without_rollback(tmp_path):
    mock = V4Mock([[dict(name="A", delta=0.1)], [dict(name="B", delta=0.1)]])
    mock.token_harm.update(A=0.3, B=0.3)       # each step +30%, the lineage about +69%
    r, _ = run(mock, cfg(), tmp_path)
    assert len(r.lineage) == 3
    cum = r.cumulative
    assert cum["status"] == "checked" and cum["warning"] and cum["safeguards"]["breached"] == ["tokens_per_task_ratio"]
    assert r.final_version() == r.lineage[-1]["version"]
    assert r.ledger()["cumulative"]["warning"]


# ---------------------------------------------------------------------------
# implementation failures, directions
# ---------------------------------------------------------------------------

def test_implementation_failure_classes_reach_the_analyst():
    mock = V4Mock([[dict(name="E", lines=0), dict(name="T", implement_error=TimeoutError(
        "implementer agent did not finish within 60s")), dict(name="M", implement_error=ValueError(
        "preflight report missing or malformed: no file"))], []])
    r, _ = run(mock, cfg(b=3, speculative_analysis=False))
    rec = records(r)
    assert [rec[n]["failure_kind"] for n in "ETM"] == ["empty_diff", "timeout", "malformed_report"]
    outcomes = {e["title"]: e.get("outcome") for e in mock.last_history}
    assert outcomes == dict(E="implementation_empty_diff", T="implementation_timeout",
                            M="implementation_malformed_report")


def test_directions_are_recorded_inferred_and_summarised():
    mock = V4Mock([[dict(name="rewrite query A", delta=-0.1, direction="Query Rewrite"),
                    dict(name="rewrite query B", delta=-0.1, direction="query-rewrite"),
                    dict(name="Shorter answers please", delta=-0.1)], []])
    r, _ = run(mock, cfg(b=3, speculative_analysis=False))
    rec = records(r)
    assert rec["rewrite query A"]["direction"] == "query-rewrite" and not rec["rewrite query A"]["direction_inferred"]
    assert rec["Shorter answers please"]["direction"] == "shorter-answers-please"
    assert rec["Shorter answers please"]["direction_inferred"]
    rows = {row["direction"]: row for row in agent_context.direction_summary(mock.last_history)}
    assert rows["query-rewrite"]["change_required"] and not rows["shorter-answers-please"]["change_required"]
    text = agent_context.render_direction_summary(mock.last_history)
    assert "REQUIRED" in text and "query-rewrite" in text and "(inferred from titles)" in text


# ---------------------------------------------------------------------------
# stopping (§4.6) and signals
# ---------------------------------------------------------------------------

def test_kill_and_resume_replays_only_unfinished_work(tmp_path):
    script = [[dict(name="A", delta=0.1), dict(name="B", delta=-0.3), dict(name="C", delta=0.05)]]
    mock = V4Mock(script)
    mock.fail_once["rest:A"] = EvaluationUnavailable("server went away")
    r, reason = run(mock, cfg(b=1, max_consecutive_run_failures=1), tmp_path)
    assert reason == "process_failure" and r.current is not None and r.failure_detail
    implemented = mock.calls["implement"]
    r2, reason2 = run(mock, cfg(b=1, max_consecutive_run_failures=1), tmp_path)
    assert reason2 == "script_end" and r2.lineage[-1]["version"] == "seed+r1-A"
    assert mock.calls["implement"] - implemented <= len(script[0]) - 1
    assert len(r2.sessions) == 2 and r2.rounds[0]["decision"]["adopted"] == "seed+r1-A"
    with pytest.raises(ValueError):
        V4Run(mock, mock.seed, cfg(b=3), search=mock.search, validation=mock.validation, state_dir=tmp_path)


def test_only_the_budget_stops_many_rounds_without_adoption():
    mock = V4Mock([[dict(name=f"X{i}", delta=-0.01)] for i in range(7)])
    r, reason = run(mock, cfg())
    assert reason == "script_end" and len(r.rounds) == 7 and len(r.lineage) == 1


def test_time_budget_stops_on_the_clock(tmp_path):
    now = [0.0]

    def clock():
        now[0] += 60.0       # every clock read advances a minute
        return now[0]
    mock = V4Mock([[dict(name=f"A{i}", delta=0.1)] for i in range(15)])
    r, reason = run(mock, cfg(time_budget_hours=1.0, agent_estimate_minutes=0), tmp_path, clock=clock)
    assert reason == "time_budget" and len(r.rounds) < 15


def test_empty_analyses_are_run_failures():
    mock = V4Mock([])
    r = V4Run(mock, mock.seed, cfg(max_consecutive_run_failures=2), search=mock.search, validation=mock.validation)
    assert r.run() == "process_failure" and "no cards" in r.failure_detail
    assert len(r.rounds) == 1 and r.rounds[0]["analysis_empty"]


def test_failed_implementations_do_not_end_a_streak_of_empty_analyses():
    # Regression: a failed implementation (stage "error") used to reset the run-failure count, so empty
    # analyses alternating with failed implementations never halted and spun until the time budget.
    mock = V4Mock([[], [dict(name="E1", implement_error=True)], [], [dict(name="E2", implement_error=True)], []])
    r, reason = run(mock, cfg(b=1, max_consecutive_run_failures=2, speculative_analysis=False))
    # The round whose only card failed to build is itself the second run failure.
    assert reason == "process_failure" and "no candidate reached measurement" in r.failure_detail
    assert len(r.rounds) == 1 and r.current["round"] == 2
    assert [rec["stage"] for rec in r.current["records"].values()] == ["error"]
    # A measured candidate (adopted or not) still ends the streak.
    mock = V4Mock([[], [dict(name="A", delta=-0.1)], [], [dict(name="B", delta=-0.1)], []])
    r, reason = run(mock, cfg(b=1, max_consecutive_run_failures=2, speculative_analysis=False))
    assert reason == "script_end" and len(r.rounds) == 5


def test_rounds_without_a_measured_candidate_are_run_failures():
    # Regression: an analyst that keeps proposing cards the implementer cannot build never produced an
    # empty analysis, so the run repeated rounds until the time budget (seen with speculative_analysis off).
    failing = [[dict(name=f"E{i}", implement_error=True)] for i in range(1, 8)]
    r, reason = run(V4Mock(failing), cfg(b=1, max_consecutive_run_failures=3, speculative_analysis=False))
    assert reason == "process_failure" and "no candidate reached measurement" in r.failure_detail
    assert len(r.rounds) == 2 and r.current["round"] == 3 and r.current["nothing_measured"]
    assert r.current["decision"] is not None           # the round is closed: a resume starts the next one
    # A measured candidate in between ends the streak.
    mixed = [[dict(name="E1", implement_error=True)], [dict(name="E2", implement_error=True)],
             [dict(name="A", delta=-0.1)], [dict(name="E3", implement_error=True)],
             [dict(name="E4", implement_error=True)]]
    r, reason = run(V4Mock(mixed), cfg(b=1, max_consecutive_run_failures=3, speculative_analysis=False))
    assert reason == "script_end" and len(r.rounds) == 5
    # One failed card next to a measured one is an ordinary round.
    both = [[dict(name=f"E{i}", implement_error=True), dict(name=f"A{i}", delta=-0.1)] for i in range(1, 6)]
    r, reason = run(V4Mock(both), cfg(b=2, max_consecutive_run_failures=2, speculative_analysis=False))
    assert reason == "script_end" and not any(rd.get("nothing_measured") for rd in r.rounds)


def test_no_new_card_when_card_plus_remeasure_does_not_fit():
    # Before any card, the estimate falls back to agent_estimate_minutes + one validation measurement.
    mock = V4Mock([[dict(name="A", delta=0.1)]])
    r, reason = run(mock, cfg(time_budget_hours=0.5, agent_estimate_minutes=45))
    assert reason == "time_budget" and not r.rounds and mock.calls.get("implement", 0) == 0
    assert r.ledger()["card_estimate"]["needed_s"] > 1800

    class Run(V4Run):
        def _card_estimate(self):
            started = any("started_at" in rec for rec in self._all_records())
            return dict(card_s=0.0, remeasure_s=0.0, needed_s=1e9 if started else 0.0, source="test")
    mock = V4Mock([[dict(name="A", delta=0.1), dict(name="B", delta=0.2)]])
    r = Run(mock, mock.seed, cfg(b=1), search=mock.search, validation=mock.validation)
    assert r.run() == "time_budget"
    rd = r.rounds[0]
    assert rd["records"]["1"]["unmeasured"] == "time budget (not started)" and rd["not_started"] == [1]
    assert rd["decision"]["adopted"] == "seed+r1-A" and mock.calls["implement"] == 1


def test_sigterm_stop_cancels_remote_work_and_sigint_keeps_it(tmp_path):
    class Mock(V4Mock):
        def __init__(self, raise_):
            super().__init__([[dict(name="A", delta=0.1)], [dict(name="B", delta=0.1)]])
            self.raise_, self.events = raise_, []

        def analyse(self, trajectories, n_cards, **kwargs):
            if kwargs["round_id"] == 2 and kwargs["parent"].content_hash != "seed":
                raise self.raise_("signal")
            return super().analyse(trajectories, n_cards, **kwargs)

        def cancel_pending(self):
            self.events.append("cancel")

        def stop_polling(self):
            self.events.append("stop_polling")

    mock = Mock(RunStopRequested)
    r, reason = run(mock, cfg(speculative_analysis=False), tmp_path / "term")
    assert reason == STOPPED and r.halt_reason == STOPPED and r.sessions[-1]["stopped"]
    assert "cancel" in mock.events and "stop_polling" not in mock.events
    assert r.lineage[-1]["version"] == "seed+r1-A" and r.cumulative["status"] in ("checked", "unavailable")
    import json
    assert json.loads((tmp_path / "term" / "state.json").read_text())["halt_reason"] == STOPPED
    mock = Mock(KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        run(mock, cfg(speculative_analysis=False), tmp_path / "int")
    assert mock.events[-1] == "stop_polling" and "cancel" not in mock.events


def test_sigterm_handler_raises_run_stop_requested():
    import os
    import signal
    import time
    from rag_eval.optimizer.v4.cli import stop_on_sigterm
    before = signal.getsignal(signal.SIGTERM)
    with pytest.raises(RunStopRequested):
        with stop_on_sigterm():
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(2)
    assert signal.getsignal(signal.SIGTERM) == before


# ---------------------------------------------------------------------------
# pure parts
# ---------------------------------------------------------------------------

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
    assert decide.tier("changed", "none") == ("confirmed", [])
    assert decide.tier("changed", "suspicious") == ("unconfirmed", [decide.NEEDS_HUMAN_REVIEW])
    assert decide.tier(None, "effective") == ("confirmed", [])


def test_each_parent_has_its_own_minibatch_and_parents_are_never_remeasured():
    mock = V4Mock([[dict(name="A", delta=0.1)], [dict(name="B", delta=0.1)], []])
    r, _ = run(mock, cfg())
    assert [n["version"] for n in r.lineage] == ["seed", "seed+r1-A", "seed+r1-A+r2-B"]
    assert r.rounds[0]["split"]["minibatch"] != r.rounds[1]["split"]["minibatch"]
    # seed calibration (mb, rest) + per adopted candidate: first (k=1) and re-measurement (k=2) on mb and rest.
    # An adopted parent is never measured again (its parent-side reads replay the same requests).
    assert len(mock.measured) == 2 + 4 + 4
    assert {m[0] for m in mock.measured} == {"seed", "seed+r1-A", "seed+r1-A+r2-B"}


def test_clustered_se_uses_families():
    ids = [f"t{i}" for i in range(8)]
    a = EvalResult(per_task_scores={t: (1.0 if i < 4 else 0.0) for i, t in enumerate(ids)}, trajectories={},
                   costs={}, error_classes={})
    b = EvalResult(per_task_scores={t: 0.5 for t in ids}, trajectories={}, costs={}, error_classes={})
    iid = decide.paired_delta(a, b, ids)
    clustered = decide.paired_delta(a, b, ids, families={t: ("p0" if i < 4 else "p1") for i, t in enumerate(ids)})
    assert iid["se_method"] == "iid" and clustered["se_method"] == "cluster_cr1"
    assert clustered["se_own"] > iid["se_own"]


def test_ablation_prunes_a_change_that_does_not_contribute():
    # With all three calibration attempts, Z must remain inside the mechanism
    # band against an already remeasured parent (not just against the seed).
    mock = V4Mock([[dict(name="A", delta=0.1)],
                   [dict(name="Z", effect=True, delta=-.0005, delta_by_attempt={1: .0055})], []])
    r, reason = run(mock, cfg(ablation=True))
    assert reason == "script_end" and len(r.lineage) == 3          # Z adopted on mechanism evidence
    z = records(r, 1)['Z']
    assert .5 * z['se_val'] <= z['delta_val'] < z['se_val']
    assert z['remeasure']['passed']
    ab = r.ablation
    assert ab["status"] == "done" and ab["pruned"] == [2] and ab["accepted"]
    assert r.final_version() == ab["final"] and r.final_version() != r.lineage[-1]["version"]
    assert mock.chain[r.final_version()] == mock.chain["seed+r1-A"]
    assert any(h["label"] == "pruned" for h in r.history)
    assert ab["units"]["0"]["prune"] is False


def test_ablation_is_off_by_default_and_skipped_when_nothing_adopted():
    r, _ = run(V4Mock([[dict(name="X", delta=-0.2)]]), cfg(ablation=True))
    assert r.ablation == dict(status="skipped", reason="nothing adopted")
    r, _ = run(V4Mock([[dict(name="A", delta=0.1)], []]), cfg())
    assert r.ablation["reason"] == "disabled" and r.final_version() == r.lineage[-1]["version"]
