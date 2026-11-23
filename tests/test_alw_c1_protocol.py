"""Milestone 3b: infra ledger, event ledger + K_idle pruning, fork evaluation (Paths 1-3), dev
diagnostics, pure C1 validation and the sealed test report. Deterministic; no containers."""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest

from ssap.checkers.alw import alw_registry
from ssap.eval import c1_protocol as c1
from ssap.eval.event_ledger import (EventLedger, prune_h_inc, update_idle_streaks, verdict)
from ssap.eval.fork_eval import (archived_gate_decision, diagnose_dev_runs, evaluate_path1_fork,
                                 evaluate_path2_paired, evaluate_path3_pooled, failure_class,
                                 is_single_atomic_mutation, record_incumbent_events,
                                 registry_mutations)
from ssap.eval.infra_ledger import (InfraLedger, classify_trial, delta_worst, paired_rerun_plan,
                                    scores_by_slot)

ROOT = Path(__file__).resolve().parents[1]
OK = {"proxy_requests": 40, "gateway_wait_seconds": 100.0, "wall_sec": 1000.0, "arm": "H_base"}


# ------------------------------------------------------------------------------------------
# infra ledger
# ------------------------------------------------------------------------------------------

def test_oom_exit_137_is_infra_not_zero():
    v = classify_trial({**OK, "exit_code": 137, "Y_star": 0.0, "status": "missing_submission"})
    assert v.infra_error and v.category == "oom" and v.score is None


def test_t_soft_sigterm_and_grace_kill_are_not_infra():
    v = classify_trial({**OK, "exit_code": 143, "signal": "SIGTERM", "stop_reason": "t_soft",
                        "Y_star": 0.42, "status": "ok"})
    assert not v.infra_error and v.score == 0.42 and v.intentional_stop
    v = classify_trial({**OK, "exit_code": 137, "stop_reason": "t_soft", "Y_star": 0.3})
    assert not v.infra_error and v.score == 0.3
    v = classify_trial({**OK, "exit_code": 137, "stop_reason": "t_soft", "oom_killed": True})
    assert v.infra_error and v.category == "oom"


def test_missing_and_invalid_submission_score_zero_never_dropped():
    for st in ("missing_submission", "invalid_submission"):
        v = classify_trial({**OK, "status": st, "evaluation_error": True, "Y_star": None})
        assert not v.infra_error and v.score == 0.0 and v.task_failure


def test_verifier_setup_gateway_faults():
    assert classify_trial({**OK, "evaluation_error": True, "Y_star": 0.5}).category == "verifier_fault"
    assert classify_trial({**OK, "status": "tool_error"}).category == "verifier_fault"
    assert classify_trial({**OK, "status": "interrupted"}).category == "verifier_fault"
    assert classify_trial({**OK, "no_trial_dir": True}).category == "setup_crash"
    assert classify_trial({**OK, "container_error": True}).category == "setup_crash"
    assert classify_trial({**OK, "unrecovered_gateway_error": True, "Y_star": 0.1}).category == "gateway_error"
    assert classify_trial({**OK, "proxy_requests": 0, "Y_star": 0.0}).category == "no_model_traffic"
    assert classify_trial({**OK, "Y_star": 0.5}).score == 0.5
    assert classify_trial({**OK}).category == "verifier_fault"       # no score at all


def test_theta_gw_max_boundary():
    at = {**OK, "gateway_wait_seconds": 350.0, "Y_star": 0.2}
    assert not classify_trial(at).infra_error                         # 0.35 is not > 0.35
    over = {**OK, "gateway_wait_seconds": 351.0, "Y_star": 0.2}
    v = classify_trial(over)
    assert v.infra_error and v.category == "gateway_stall" and v.gateway_wait_ratio == pytest.approx(0.351)
    assert not classify_trial(over, theta_gw_max=0.5).infra_error


def test_proxy_zero_only_counts_on_llm_arms():
    assert not classify_trial({"proxy_requests": 0, "llm_arm": False, "Y_star": 0.0}).infra_error
    assert not classify_trial({"arm": "nop", "proxy_requests": 0, "Y_star": 0.0}).infra_error


def test_infra_ledger_persistence_paired_rerun_and_delta_worst(tmp_path):
    led = InfraLedger(tmp_path / "infra_ledger.jsonl")
    led.observe({**OK, "task_id": "a", "seed": 0, "arm": "H_star", "exit_code": 137})
    led.observe({**OK, "task_id": "b", "seed": 0, "arm": "H_star", "Y_star": 0.4})   # scored
    led.observe({**OK, "task_id": "c", "seed": 1, "arm": "H_base", "no_trial_dir": True})
    assert len(led.table()) == 2 and led.unresolved_slots() == {("a", 0), ("c", 1)}
    led.mark_rerun("a", 0, "resolved")
    again = InfraLedger(tmp_path / "infra_ledger.jsonl")
    assert again.unresolved_slots() == {("c", 1)}
    assert again.table()[0]["paired_rerun_status"] == "pending"

    plan = paired_rerun_plan({"H_base": [{**OK, "task_id": "a", "seed": 0, "Y_star": 0.5}],
                              "H_star": [{**OK, "task_id": "a", "seed": 0, "exit_code": 137}]})
    assert plan == [{"task_id": "a", "seed": 0, "faulted_arms": ["H_star"],
                     "rerun_arms": ["H_base", "H_star"]}]

    cand = {("a", 0): 0.8, ("b", 0): None, ("c", 0): 0.6}
    base = {("a", 0): 0.5, ("b", 0): 0.7, ("c", 0): None}
    dw = delta_worst(cand, base)
    assert dw["n_unresolved"] == 2
    assert dw["delta_observed"] == pytest.approx(0.3)
    assert dw["delta_worst"] == pytest.approx((0.3 - 1.0 - 1.0) / 3)
    s = scores_by_slot([{**OK, "task_id": "a", "seed": 0, "exit_code": 137},
                        {**OK, "task_id": "a", "seed": 0, "Y_star": 0.9}])     # rerun replaces
    assert s == {("a", 0): 0.9}


# ------------------------------------------------------------------------------------------
# event ledger + K_idle
# ------------------------------------------------------------------------------------------

def test_event_verdicts_and_damaged_diagnostics(tmp_path):
    assert verdict(0.1) == "Rescued" and verdict(0.0) == "Neutral" and verdict(-0.1) == "Damaged"
    led = EventLedger(tmp_path / "event_ledger.jsonl")
    led.append(round_id=1, candidate_id="c1", task_id="t1", gate_ids=["g"], y_baseline=0.3, y_post=0.7)
    led.append(round_id=1, candidate_id="c1", task_id="t2", gate_ids=["g"], y_baseline=0.7,
               y_post=(0.7 + 0.7 + 0.7) / 3)
    led.append(round_id=1, candidate_id="c1", task_id="t3", gate_ids=["g"], y_baseline=0.9, y_post=0.4,
               diagnostic={"why": "repair broke LVS"})
    assert led.counts(round_id=1) == {"Rescued": 1, "Neutral": 1, "Damaged": 1}
    dmg = led.damaged(round_id=1)
    assert len(dmg) == 1 and dmg[0]["diagnostic"] == {"why": "repair broke LVS"}
    assert dmg[0]["delta_Y"] == pytest.approx(-0.5)
    assert EventLedger(tmp_path / "event_ledger.jsonl").counts() == led.counts()


def test_k_idle_two_consecutive_zero_rescue_rounds_prune():
    led = EventLedger()
    led.append(round_id=1, candidate_id="H", task_id="t", gate_ids=["g_a"], y_baseline=0.2, y_post=0.5)
    s, pr = update_idle_streaks({}, round_id=1, active_gates=["g_a", "g_b"], ledger=led)
    assert s == {"g_a": 0, "g_b": 1} and pr == []
    led.append(round_id=2, candidate_id="H", task_id="t", gate_ids=["g_a"], y_baseline=0.5, y_post=0.5)
    s, pr = update_idle_streaks(s, round_id=2, active_gates=["g_a", "g_b"], ledger=led)
    assert pr == ["g_b"] and s == {"g_a": 1}
    s, pr = update_idle_streaks(s, round_id=3, active_gates=["g_a"], ledger=led)
    assert pr == ["g_a"]
    h = prune_h_inc({"version_id": "v", "gates": ["g_a", "g_b"]}, ["g_b"])
    assert h["gates"] == ["g_a"] and h["pruned"] == ["g_b"]


# ------------------------------------------------------------------------------------------
# Path 1
# ------------------------------------------------------------------------------------------

FORK = {"granted_repair_budget_sec": 900.0, "phase_a_elapsed_sec": 9900.0, "repair_eligible": True}


def _trials():
    # t1: identical decisions (both untriggered); t2: identical (both triggered, same reasons);
    # t3: only the candidate triggers; t4: both trigger with different signatures.
    return [
        {"task_id": "t1", "seed": 0, "fork_meta": FORK, "inc_triggered": False,
         "inc_gate_signature": [], "Y_inc_final": 0.9, "Y_pre": 0.9},
        {"task_id": "t2", "seed": 0, "fork_meta": FORK, "inc_triggered": True,
         "inc_gate_signature": ["m_best_not_all_certified"], "Y_inc_final": 0.6, "Y_pre": 0.3},
        {"task_id": "t3", "seed": 0, "fork_meta": FORK, "inc_triggered": False,
         "inc_gate_signature": [], "Y_inc_final": 0.5, "Y_pre": 0.5},
        {"task_id": "t4", "seed": 0, "fork_meta": FORK, "inc_triggered": True,
         "inc_gate_signature": ["unevaluated_draft"], "Y_inc_final": 0.7, "Y_pre": 0.2},
    ]


CAND_GATE = {"t1": (False, []), "t2": (True, ["m_best_not_all_certified"]),
             "t3": (True, ["terminal_gate_not_certified"]),
             "t4": (True, ["terminal_gate_not_certified", "unevaluated_draft"])}


def _pex_on():
    inc = alw_registry()
    return inc, inc.with_change("m_pex", enabled_in_hbase=True)


def test_path1_identical_trials_cost_nothing_and_baseline_is_inc_final():
    inc, cand = _pex_on()
    calls = []
    post = {"t3": [0.8, 0.8, 0.5], "t4": [0.7, 0.4, 0.7]}

    def repair(t, r, budget):
        calls.append((t["task_id"], r, budget))
        return {"Y_post": post[t["task_id"]][r], "ratchet_promoted": True, "promote_reason": "tier3"}
    led = EventLedger()
    res = evaluate_path1_fork(_trials(), inc_registry=inc, cand_registry=cand,
                              cand_gate=lambda t: CAND_GATE[t["task_id"]], repair=repair,
                              candidate_id="pex_gate", round_id=1, ledger=led)
    rows = {r["task_id"]: r for r in res["trials"]}
    for t in ("t1", "t2"):
        assert rows[t]["delta_Y"] == 0.0 and rows[t]["llm_calls"] == 0 and rows[t]["verifier_calls"] == 0
    assert sorted({c[0] for c in calls}) == ["t3", "t4"] and len(calls) == 6
    assert all(c[2] == 900.0 for c in calls)                      # archived granted budget
    assert rows["t3"]["delta_Y"] == pytest.approx(0.7 - 0.5)
    # t4: H_inc already rescued 0.2 -> 0.7; the candidate's mean 0.6 is a loss vs w_inc_final
    assert rows["t4"]["delta_Y"] == pytest.approx(0.6 - 0.7) and rows["t4"]["verdict"] == "Damaged"
    assert res["sum_delta_Y"] == pytest.approx(0.1)
    assert res["promoted"] and res["single_atomic_mutation"] and res["mutations"] == ["m_pex"]
    assert res["llm_calls"] == 6 and res["n_divergent"] == 2
    assert led.counts() == {"Rescued": 1, "Neutral": 0, "Damaged": 1}
    assert res["damaged"][0]["task_id"] == "t4"


def test_path1_ratchet_rejection_keeps_pre_and_no_promotion_when_net_zero():
    inc, cand = _pex_on()
    trials = [_trials()[2]]
    res = evaluate_path1_fork(trials, inc_registry=inc, cand_registry=cand,
                              cand_gate=lambda t: CAND_GATE[t["task_id"]],
                              repair=lambda t, r, b: {"Y_post": None, "ratchet_promoted": False},
                              candidate_id="c", round_id=1)
    assert res["trials"][0]["delta_Y"] == 0.0 and res["trials"][0]["verdict"] == "Neutral"
    assert not res["promoted"] and res["reason"] == "net_delta_not_positive"


def test_path1_candidate_untriggered_uses_pre_repair_score_without_calls():
    inc, cand = _pex_on()
    t = _trials()[3]
    res = evaluate_path1_fork([t], inc_registry=inc, cand_registry=cand,
                              cand_gate=lambda t: (False, []),
                              repair=lambda *a: pytest.fail("no repair expected"),
                              candidate_id="c", round_id=1)
    assert res["llm_calls"] == 0 and res["sum_delta_Y"] == pytest.approx(0.2 - 0.7)


def test_path1_rejects_trajectory_mirror_change():
    inc = alw_registry()
    cand = inc.with_change("m_fill", eps=0.01)
    with pytest.raises(ValueError, match="Path 2"):
        evaluate_path1_fork(_trials(), inc_registry=inc, cand_registry=cand,
                            cand_gate=lambda t: (False, []), repair=lambda *a: {},
                            candidate_id="c", round_id=1)


def test_path1_single_atomic_mutation_required():
    inc = alw_registry()
    cand = inc.with_change("m_pex", enabled_in_hbase=True).with_change("advisory_missing_src_py",
                                                                         gate_trigger=True)
    assert registry_mutations(inc, cand) == ["advisory_missing_src_py", "m_pex"]
    assert not is_single_atomic_mutation(inc, cand)
    res = evaluate_path1_fork([_trials()[2]], inc_registry=inc, cand_registry=cand,
                              cand_gate=lambda t: CAND_GATE[t["task_id"]],
                              repair=lambda *a: {"Y_post": 1.0, "ratchet_promoted": True},
                              candidate_id="c", round_id=1)
    assert res["sum_delta_Y"] > 0 and not res["promoted"]
    assert res["reason"] == "not_single_atomic_mutation"


def test_path1_infra_repair_retried_then_unresolved_blocks_promotion():
    inc, cand = _pex_on()
    led = InfraLedger()
    res = evaluate_path1_fork([_trials()[2]], inc_registry=inc, cand_registry=cand,
                              cand_gate=lambda t: CAND_GATE[t["task_id"]],
                              repair=lambda *a: {"infra_error": True,
                                                 "infra_record": {"exit_code": 137}},
                              candidate_id="c", round_id=1, infra_ledger=led)
    assert res["unresolved"] == [{"task_id": "t3", "seed": 0}] and not res["promoted"]
    assert len(led.table()) == 6 and led.table()[0]["category"] == "oom"


def test_path1_budget_from_archived_fork_meta_file(tmp_path):
    inc, cand = _pex_on()
    snap = tmp_path / "snap"
    (snap / "pre_repair").mkdir(parents=True)
    (snap / "pre_repair" / "fork_meta.json").write_text(json.dumps({"granted_repair_budget_sec": 321.0}))
    t = {**_trials()[2], "snapshot_dir": str(snap)}
    t.pop("fork_meta")
    seen = []
    evaluate_path1_fork([t], inc_registry=inc, cand_registry=cand,
                        cand_gate=lambda t: CAND_GATE[t["task_id"]],
                        repair=lambda t, r, b: seen.append(b) or {"Y_post": 0.5, "ratchet_promoted": True},
                        candidate_id="c", round_id=1)
    assert seen == [321.0] * 3


def test_archived_gate_decision_with_terminal_pex():
    inc, cand = _pex_on()
    t = {"task_id": "t", "fork_meta": FORK, "m_best": [1] * 6, "unevaluated_draft": False,
         "advisory_flags": {"advisory_aspect_gt_3": False}, "terminal_m": {"m_pex": 0}}
    assert archived_gate_decision(t, inc) == (False, ())
    assert archived_gate_decision(t, cand) == (True, ("terminal_gate_not_certified",))
    with pytest.raises(ValueError):
        archived_gate_decision({**t, "terminal_m": {}}, cand)


def test_incumbent_events_feed_k_idle():
    led = EventLedger()
    trials = [{**_trials()[1], "inc_gate_ids": ["advisory_aspect_gt_3"]}]
    ev = record_incumbent_events(trials, round_id=1, version_id="H_inc", ledger=led)
    assert ev[0]["verdict"] == "Rescued" and ev[0]["baseline_kind"] == "w_pre"
    assert led.rescues_by_gate(1) == {"advisory_aspect_gt_3": 1}


# ------------------------------------------------------------------------------------------
# Paths 2 / 3
# ------------------------------------------------------------------------------------------

def _runs(arm, ys, trig=()):
    return [{**OK, "arm": arm, "task_id": t, "seed": s, "Y_star": y, "hook_triggered": t in trig}
            for t, seeds in ys.items() for s, y in enumerate(seeds)]


def test_path2_prefix_leakage_audit_passes_and_promotes():
    dev = ["a", "b", "c"]
    inc = _runs("H_inc", {"a": [0.5, 0.5], "b": [0.4, 0.4], "c": [0.2, 0.2]})
    cand = _runs("H_cand", {"a": [0.5, 0.5], "b": [0.4, 0.4], "c": [0.5, 0.5]}, trig={"c"})
    res = evaluate_path2_paired(inc, cand, dev_task_ids=dev)
    assert res["path"] == "path2" and not res["leakage_audit"]["leak_detected"]
    assert res["leakage_audit"]["untriggered_tasks"] == ["a", "b"]
    assert res["net_delta"] == pytest.approx(0.1) and res["promoted"]


def test_path2_leak_reclassifies_to_path3_pooled():
    dev = ["a", "b"]
    inc = _runs("H_inc", {"a": [0.5, 0.5], "b": [0.4, 0.4]})
    cand = _runs("H_cand", {"a": [0.3, 0.3], "b": [0.6, 0.6]}, trig={"b"})
    pooled = inc + _runs("H_inc", {"a": [0.1], "b": [0.1]})
    res = evaluate_path2_paired(inc, cand, dev_task_ids=dev, inc_pooled_runs=pooled)
    assert res["path"] == "path3" and res["reclassified_from"] == "path2"
    assert res["leakage_audit"]["mean_diff_untriggered"] == pytest.approx(-0.2)
    assert res["per_task_delta"]["a"] == pytest.approx(0.3 - (0.5 + 0.5 + 0.1) / 3)


def test_path3_requires_full_dev_and_strict_gain():
    inc = _runs("H_inc", {"a": [0.5], "b": [0.5]})
    assert evaluate_path3_pooled(inc, _runs("H_cand", {"a": [0.9]}), dev_task_ids=["a", "b"]
                                 )["reason"] == "incomplete_dev"
    tie = evaluate_path3_pooled(inc, _runs("H_cand", {"a": [0.5], "b": [0.5]}), dev_task_ids=["a", "b"])
    assert not tie["promoted"] and tie["net_delta"] == 0.0
    two = evaluate_path3_pooled(inc, _runs("H_cand", {"a": [0.9], "b": [0.5]}), dev_task_ids=["a", "b"],
                                n_mutations=2)
    assert not two["promoted"] and two["reason"] == "not_single_atomic_mutation"


# ------------------------------------------------------------------------------------------
# dev diagnostics
# ------------------------------------------------------------------------------------------

D_PASS = {k: 1 for k in ("gds", "drc", "lvs", "shape", "fill", "morph", "pex", "postsim")}


def _diag_runs():
    return [
        {**OK, "task_id": "a", "seed": 0, "Y_star": 1.0, "C_star": 1, "d_star": D_PASS,
         "m": [1] * 6, "draft_scores": [0.4, 1.0]},
        {**OK, "task_id": "a", "seed": 1, "Y_star": 0.0, "C_star": 0, "status": "missing_submission",
         "d_star": {**D_PASS, "gds": 0}, "m": [0] * 6},
        {**OK, "task_id": "b", "seed": 0, "Y_star": 0.3, "C_star": 0,
         "d_star": {**D_PASS, "shape": 0}, "m": [1, 1, 1, 1, 1, 1], "draft_scores": [0.6, 0.3],
         "ever_near_qualified": True},
        {**OK, "task_id": "b", "seed": 1, "Y_star": 0.6, "C_star": 0,
         "d_star": {**D_PASS, "postsim": 0}, "m": [1] * 6},
        {**OK, "task_id": "c", "seed": 0, "Y_star": 0.5, "C_star": 0,
         "d_star": {**D_PASS, "pex": 0}, "m": [1] * 6},
        {**OK, "task_id": "c", "seed": 1, "Y_star": 0.1, "C_star": 0,
         "d_star": {**D_PASS, "lvs": 0, "fill": "bottom"}, "m": [1, 0, 1, 0, 0, 1],
         "drafts_d_star": [{"gds": 1, "lvs": 0}]},
        {**OK, "task_id": "c", "seed": 2, "exit_code": 137},
    ]


def test_failure_taxonomy_four_way():
    r = _diag_runs()
    assert [failure_class(x) for x in r[:6]] == [
        None, "missing_or_invalid_gds", "public_mirror_execution_gap",
        "electrical_or_hidden_info_gap", "electrical_or_hidden_info_gap", "capability_bound"]


def test_diagnose_dev_runs_writes_reports_and_switches(tmp_path):
    rep = diagnose_dev_runs(_diag_runs(), dev_task_ids=["a", "b", "c"],
                            profile_of={"b": "binned", "c": "continuous"},
                            repeat_verifications=[{"task_id": "a", "seed": 0, "Y_values": [1.0, 1.0]},
                                                  {"task_id": "b", "seed": 0, "Y_values": [0.3, 0.31]}],
                            out_dir=tmp_path)
    t = rep["taxonomy"]
    assert t["counts"] == {"missing_or_invalid_gds": 1, "public_mirror_execution_gap": 1,
                           "electrical_or_hidden_info_gap": 2, "capability_bound": 1}
    assert t["electrical_or_hidden_info_gap_by_profile"] == {"binned": 1, "continuous": 1, "unknown": 0}
    assert rep["n_infra_error"] == 1 and rep["infra_errors"][0]["category"] == "oom"
    assert rep["verifier_repeatability"]["deterministic"] is False
    assert rep["verifier_repeatability"]["max_spread"] == pytest.approx(0.01)
    conc = rep["mirror_concordance"]
    assert conc["m_shape"]["m1_d0_false_certify"] == 1           # b seed 0: m_shape=1, d_shape=0
    assert conc["m_fill"]["excluded_bottom_or_missing"] == 1      # bottom excluded
    s = rep["seed_noise_floor"]
    assert s["advisory_reference_only"] is True and s["sigma_seed"] is not None
    assert rep["checkpoint_gap"]["max_gap"] == pytest.approx(0.3)
    sw = rep["module_switches"]
    assert sw["enable_checkpoint_ranker"] and sw["enable_mirror_gate_channel"]
    assert sw["enable_endogenous_probe_channel"] and sw["capability_bound_tasks"] == ["c"]
    assert json.loads((tmp_path / "dev_diagnostics.json").read_text())["k0"] == 3
    assert "residual failure taxonomy" in (tmp_path / "dev_diagnostics.md").read_text()


def test_diagnose_guards_dev_only_and_k0():
    with pytest.raises(ValueError, match="dev"):
        diagnose_dev_runs(_diag_runs(), dev_task_ids=["a", "b"])
    with pytest.raises(ValueError, match="K_0"):
        diagnose_dev_runs([r for r in _diag_runs() if r["seed"] == 0], dev_task_ids=["a", "b", "c"])
    one = [r for r in _diag_runs() if "draft_scores" not in r or r["task_id"] != "b"]
    rep = diagnose_dev_runs([{**r, "draft_scores": [r.get("Y_star") or 0.0]} for r in one],
                            dev_task_ids=["a", "b", "c"])
    assert rep["module_switches"]["enable_checkpoint_ranker"] is False


# ------------------------------------------------------------------------------------------
# pure C1
# ------------------------------------------------------------------------------------------

VAL = [f"v{i:02d}" for i in range(14)]


def test_c1_selects_inc_only_above_1e6_and_returns_four_keys(tmp_path):
    audit = c1.HoldoutAudit(tmp_path / "audit.jsonl")
    base = {t: 0.5 for t in VAL}
    inc = {**base, "v00": 0.5 + 14 * 2e-6}
    res = c1.run_c1_validation(inc, base, inc_version_id="inc1", base_version_id="H_base",
                               val_task_ids=VAL, audit=audit)
    assert set(res) == set(c1.C1_RETURN_KEYS)
    assert res["promoted"] and res["selected_version_id"] == "inc1" and res["val_access_count"] == 1
    assert res["delta_val"] == pytest.approx(2e-6)
    with pytest.raises(RuntimeError):
        c1.run_c1_validation(inc, base, inc_version_id="inc1", base_version_id="H_base",
                             val_task_ids=VAL, audit=c1.HoldoutAudit(tmp_path / "audit.jsonl"))
    logged = (tmp_path / "audit.jsonl").read_text()
    assert "0.5" not in logged and "v00" not in logged            # no per-task val leakage


def test_c1_tie_and_small_gain_keep_base():
    base = {t: 0.5 for t in VAL}
    for inc in (dict(base), {**base, "v01": 0.5 + 14 * 5e-7}, {**base, "v01": 0.4}):
        res = c1.run_c1_validation(inc, base, inc_version_id="inc", base_version_id="H_base",
                                   val_task_ids=VAL, audit=c1.HoldoutAudit())
        assert not res["promoted"] and res["selected_version_id"] == "H_base"


def test_c1_guards():
    base = {t: 0.5 for t in VAL}
    same = c1.run_c1_validation(base, base, inc_version_id="H_base", base_version_id="H_base",
                                val_task_ids=VAL, audit=c1.HoldoutAudit())
    assert same["val_access_count"] == 0 and same["selected_version_id"] == "H_base"
    with pytest.raises(ValueError, match="N = 14"):
        c1.run_c1_validation(base, base, inc_version_id="a", base_version_id="b",
                             val_task_ids=VAL[:13], audit=c1.HoldoutAudit())
    with pytest.raises(ValueError, match="infra"):
        c1.run_c1_validation({**base, "v03": None}, base, inc_version_id="a", base_version_id="b",
                             val_task_ids=VAL, audit=c1.HoldoutAudit())
    seeds = c1.run_c1_validation({t: [0.5, 0.7] for t in VAL}, base, inc_version_id="a",
                                 base_version_id="b", val_task_ids=VAL, audit=c1.HoldoutAudit())
    assert seeds["delta_val"] == pytest.approx(0.1) and seeds["promoted"]


def test_no_statistical_veto_vocabulary_in_eval():
    import re
    pat = re.compile(r"mcnemar|p_value|pvalue|W_i\b|0\.49|n_10|P-SHA", re.I)
    for f in (ROOT / "src" / "ssap" / "eval").glob("*.py"):
        code = "\n".join(l for l in f.read_text().splitlines() if not l.strip().startswith("#"))
        body = re.sub(r'"""[\s\S]*?"""', "", code)                    # docstrings may name them
        assert not pat.search(body), f


# ------------------------------------------------------------------------------------------
# sealed test report
# ------------------------------------------------------------------------------------------

TEST = [f"x{i:02d}" for i in range(14)]
CLUSTER = {t: f"cl{min(i, 9)}" for i, t in enumerate(TEST)}     # 10 conservative clusters
HARD = {t: f"h{min(i, 10)}" for i, t in enumerate(TEST)}        # 11 hard clusters


def _test_runs(seeds=3, star=True):
    runs = []
    for i, t in enumerate(TEST):
        for s in range(seeds):
            yb = 0.4 + 0.01 * i + 0.02 * s
            cb = 1 if i % 4 == 0 else 0
            runs.append({**OK, "arm": "H_raw", "task_id": t, "seed": s, "Y_star": yb - 0.2, "C_star": 0,
                         "cost": {"input_tokens": 100, "output_tokens": 10, "wall_sec": 5, "peak_ram_mb": 900}})
            runs.append({**OK, "arm": "H_base", "task_id": t, "seed": s, "Y_star": yb, "C_star": cb,
                         "m": [1, 1, 0 if s == 0 else 1, 1, 1, 1],
                         "cost": {"input_tokens": 200, "output_tokens": 20, "usd": 1.0,
                                  "tool_cpu_sec": 30, "peak_ram_mb": 1000, "wall_sec": 10}})
            if star:
                ys = yb + (0.3 if i % 3 == 0 else -0.05 if i % 5 == 1 else 0.0)
                cs = 1 if (i % 3 == 0 or cb) and not (i == 4 and s == 1) else 0
                runs.append({**OK, "arm": "H_star", "task_id": t, "seed": s, "Y_star": min(ys, 1.0),
                             "C_star": cs, "cost": {"input_tokens": 300, "output_tokens": 30,
                                                    "usd": 1.5, "peak_ram_mb": 1200, "wall_sec": 12}})
    if star:
        runs.append({**OK, "arm": "H_star", "task_id": "x13", "seed": 3, "exit_code": 137})
    return runs


def test_sealed_report_full(tmp_path):
    rep = c1.build_sealed_test_report(_test_runs(), selected_version_id="inc1", h_star_is_base=False,
                                      cluster_of=CLUSTER, hard_cluster_of=HARD, test_task_ids=TEST,
                                      out_dir=tmp_path, offline_search_cost={"usd": 7.0, "wall_sec": 100},
                                      n_boot=500)
    a = rep["three_stage_attribution"]
    assert a["delta_raw_to_base"] == pytest.approx(0.2)
    assert a["delta_base_to_star"] == pytest.approx(a["H_star"] - a["H_base"])
    assert a["claimed_automated_credit"] == "H_base -> H_star only"
    assert rep["seed_noise_floor"]["advisory_reference_only"]
    assert rep["multi_seed"]["H_base"]["sigma_seed"] == pytest.approx(0.02)
    wl = rep["win_loss"]
    assert wl["per_task"]["x00"] == {"n_plus": 3, "n_minus": 0}
    assert wl["per_task"]["x01"] == {"n_plus": 0, "n_minus": 3}
    cb = rep["cluster_bootstrap"]["conservative"]
    assert cb["G"] == 10 and len(cb["per_cluster_raw_net_diff"]) == 10
    assert "small-sample boundary" in cb["warning"]
    assert cb["ci"][0] <= cb["point"] <= cb["ci"][1]
    assert rep["cluster_bootstrap"]["hard"]["G"] == 11
    d = rep["c_star_decomposition"]
    assert d["identity_exact"] is True
    f = d["exact_fractions"]
    assert Fraction(f["total"]) == Fraction(f["flip"]) + Fraction(f["non_flip"])
    assert d["n_flip_loss"] == 1 and d["n_flip_gain"] > 0
    cl = rep["cost_ledger"]
    assert cl["arms"]["H_base"]["input_tokens"] == 200 * 42 and cl["arms"]["H_base"]["peak_ram_mb"] == 1000
    assert cl["H_star_including_offline_search"]["usd"] == pytest.approx(1.5 * 42 + 7.0)
    ie = rep["infra_error"]
    assert ie["n"] == 1 and ie["table"][0]["category"] == "oom"
    assert ie["delta_worst"]["n_unresolved"] == 1
    assert ie["delta_worst"]["delta_worst"] < ie["delta_worst"]["delta_observed"]
    hb = rep["honest_baselines"]
    assert hb["manual_instructions"] == {"status": "not_run"}
    bok = hb["best_of_k_H_base"]
    assert set(bok["curve"]) == {"1", "2", "3"} and bok["token_matched_k"] == pytest.approx(1.5)
    assert bok["curve"]["3"]["mean"] >= bok["curve"]["1"]["mean"] - 1e-12
    assert (tmp_path / "test_report.json").is_file()
    md = (tmp_path / "test_report.md").read_text()
    assert "three-stage attribution" in md and "C* flip" in md and "small-sample" in md
    # single unblind
    with pytest.raises(RuntimeError, match="already unblinded"):
        c1.build_sealed_test_report(_test_runs(), selected_version_id="inc1", h_star_is_base=False,
                                    cluster_of=CLUSTER, out_dir=tmp_path)


def test_sealed_report_guards_and_reuse(tmp_path):
    two = [r for r in _test_runs() if r["seed"] < 2]
    with pytest.raises(ValueError, match="R >= 3"):
        c1.build_sealed_test_report(two, selected_version_id="v", h_star_is_base=False,
                                    cluster_of=CLUSTER, out_dir=tmp_path / "a")
    assert not (tmp_path / "a" / c1.LOCK_NAME).exists()          # failed guard burns no unblind
    with pytest.raises(ValueError, match="non-test"):
        c1.build_sealed_test_report(_test_runs(), selected_version_id="v", h_star_is_base=False,
                                    cluster_of=CLUSTER, test_task_ids=TEST[:-1], out_dir=tmp_path / "b")
    rep = c1.build_sealed_test_report(_test_runs(star=False), selected_version_id="H_base",
                                      h_star_is_base=True, cluster_of=CLUSTER, out_dir=tmp_path / "c",
                                      n_boot=50)
    assert rep["three_stage_attribution"]["delta_base_to_star"] == 0.0
    assert rep["c_star_decomposition"]["delta_Y_total"] == 0.0


def test_c_star_decomposition_exact_on_awkward_floats():
    base = {("t", i): {"Y": y, "C": c} for i, (y, c) in enumerate([(0.1, 0), (0.2, 1), (0.7, 0)])}
    star = {("t", i): {"Y": y, "C": c} for i, (y, c) in enumerate([(0.3, 1), (0.1, 0), (0.7 + 1e-17, 0)])}
    d = c1.c_star_decomposition(base, star)
    assert d["identity_exact"] and d["n_flip_gain"] == 1 and d["n_flip_loss"] == 1
    assert d["delta_Y_c_star_flip"] == pytest.approx((0.2 - 0.1) / 3)
