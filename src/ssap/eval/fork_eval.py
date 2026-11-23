"""Pre-search dev diagnostics and fork-point evaluation (Paths 1-3).

Everything here consumes ``dev`` executions only. Model calls (stateless Phase B repair) and
verifier calls are injected callables, so the evaluation logic is deterministic and the same
code drives the server runner and the offline tests.

Path 1 -- terminal-only gate candidates (``Registry.evaluation_path == "path1"``). Fork point:
the archived ``pre_repair/`` snapshot at Phase A exit. A trial whose candidate gate decision
``(triggered, signature)`` equals the incumbent's costs 0 model and 0 verifier calls and has
``delta_Y = 0`` by construction. A divergent trial runs ``R_rep = 3`` independent stateless
repair seeds with the archived ``granted_repair_budget_sec``; each result is the post-ratchet
submission, and ``delta_Y_e = mean_r Y*(w_cand_post_r) - Y*(w_inc_final)``: the baseline is the
incumbent's *post-repair* final score, so the incumbent's own Phase B rescues are not counted
again. ``H_inc <- H_cand`` iff ``sum_e delta_Y_e > 0`` and the candidate is one atomic mutation.

Path 2 -- trajectory mirror / hook changes: paired dev runs from step 0 with the non-triggered
prefix leakage audit; a systematic offset on tasks where neither arm triggered reclassifies the
comparison to Path 3.

Path 3 -- pooled comparison of the candidate's dev runs with all archived ``H_inc`` seeds.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import fields
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..bank.referee_bank import BOTTOM, CHECKER_D_KEY, MIRROR_ORDER
from ..kernel.watermarks import compute_gate
from ..targets.alw.agent_wrapper import find_fork_meta
from .event_ledger import FLOAT_REPR_TOL, EventLedger
from .infra_ledger import THETA_GW_MAX, InfraLedger, classify_trial

R_REP = 3
K0_MIN = 2
GAP_TOL = 1e-6
LEAK_TOL = 0.05            # provisional (human judgement, not calibrated): Path 2 -> 3 offset
PUBLIC_MIRROR_KEYS = ("gds", "drc", "lvs", "shape", "fill", "morph")
HIDDEN_KEYS = ("pex", "postsim")
TAXONOMY = ("missing_or_invalid_gds", "public_mirror_execution_gap",
            "electrical_or_hidden_info_gap", "capability_bound")


def _mean(xs: Iterable[float]) -> float | None:
    xs = list(xs)
    return math.fsum(xs) / len(xs) if xs else None


def _positive(x: float | None) -> bool:
    return x is not None and x > FLOAT_REPR_TOL


# ---------------------------------------------------------------------------------------------
# dev diagnostics
# ---------------------------------------------------------------------------------------------

def _near_qualified(run: Mapping) -> bool | None:
    if "ever_near_qualified" in run:
        return bool(run["ever_near_qualified"])
    drafts = run.get("drafts_d_star")
    if not drafts:
        return None
    return any(d.get("gds") == 1 and d.get("lvs") == 1 for d in drafts)


def failure_class(run: Mapping) -> str | None:
    """Residual failure class of one scored run (``None`` = design passed, ``C* = 1``)."""
    if run.get("C_star") == 1:
        return None
    d = run.get("d_star") or {}
    if run.get("status") in ("missing_submission", "invalid_submission") or d.get("gds") == 0 \
            or run.get("submitted") is False:
        return "missing_or_invalid_gds"
    if _near_qualified(run) is False:
        return "capability_bound"
    if any(d.get(k) == 0 for k in PUBLIC_MIRROR_KEYS):
        return "public_mirror_execution_gap"
    return "electrical_or_hidden_info_gap"


def _concordance(runs: list[Mapping]) -> dict:
    out = {}
    for i, name in enumerate(MIRROR_ORDER):
        k = CHECKER_D_KEY[name]
        agree = fp = fr = excl = 0
        for r in runs:
            m, d = r.get("m"), (r.get("d_star") or {}).get(k, BOTTOM)
            if not isinstance(m, list) or i >= len(m) or d == BOTTOM:
                excl += 1
                continue
            if m[i] == 1 and d == 0:
                fp += 1
            elif m[i] == 0 and d == 1:
                fr += 1
            else:
                agree += 1
        out[name] = {"agree": agree, "m1_d0_false_certify": fp, "m0_d1_false_reject": fr,
                     "excluded_bottom_or_missing": excl}
    return out


def diagnose_dev_runs(runs: Iterable[Mapping], *, dev_task_ids: Iterable[str],
                      profile_of: Mapping[str, str] | None = None,
                      repeat_verifications: Iterable[Mapping] = (),
                      out_dir: str | Path | None = None, k0_min: int = K0_MIN,
                      theta_gw_max: float = THETA_GW_MAX) -> dict:
    """The four zero-LLM diagnostics on ``K_0 >= 2`` seeds of ``H_base`` on ``dev``.

    Run keys: ``task_id, seed, Y_star, C_star, d_star, status, m`` (mirror vector of the
    submission), ``draft_scores`` (``Y*`` of archived trajectory checkpoints), optional
    ``ever_near_qualified`` / ``drafts_d_star``. ``repeat_verifications``: ``{task_id, seed,
    Y_values}`` from repeated verifier calls on one unchanged artifact."""
    dev = set(dev_task_ids)
    runs = list(runs)
    foreign = sorted({str(r.get("task_id")) for r in runs} - dev)
    if foreign:
        raise ValueError(f"diagnostics read dev executions only; non-dev tasks: {foreign}")
    arms = {r.get("arm") for r in runs if r.get("arm") is not None}
    if arms - {"H_base"}:
        raise ValueError(f"diagnostics use H_base runs only, got arms {sorted(arms)}")
    seeds = sorted({int(r.get("seed", 0)) for r in runs})
    if len(seeds) < k0_min:
        raise ValueError(f"need K_0 >= {k0_min} H_base seeds on dev, got {len(seeds)}")
    profile_of = dict(profile_of or {})

    scored, infra = [], []
    for r in runs:
        v = classify_trial(r, theta_gw_max=theta_gw_max)
        (infra if v.infra_error else scored).append({**r, "Y_star": v.score} if not v.infra_error
                                                    else {**r, "infra_category": v.category})

    # (1) residual failure taxonomy
    counts = {c: 0 for c in TAXONOMY}
    hidden_split = {"binned": 0, "continuous": 0, "unknown": 0}
    per_run = []
    for r in scored:
        c = failure_class(r)
        if c is None:
            continue
        counts[c] += 1
        prof = profile_of.get(str(r["task_id"]), "unknown")
        if c == "electrical_or_hidden_info_gap":
            hidden_split[prof if prof in hidden_split else "unknown"] += 1
        per_run.append({"task_id": r["task_id"], "seed": int(r.get("seed", 0)), "class": c,
                        "runtime_profile": prof, "Y_star": r["Y_star"]})
    n_fail = sum(counts.values())
    shares = {c: (counts[c] / n_fail if n_fail else 0.0) for c in TAXONOMY}

    # (2) verifier repeatability and mirror concordance
    reps = []
    for rv in repeat_verifications:
        ys = [float(y) for y in rv.get("Y_values", [])]
        if len(ys) >= 2:
            reps.append({"task_id": rv.get("task_id"), "seed": rv.get("seed"),
                         "n": len(ys), "spread": max(ys) - min(ys)})
    repeat = {"measured": bool(reps), "artifacts": reps,
              "max_spread": max((x["spread"] for x in reps), default=None),
              "deterministic": all(x["spread"] == 0 for x in reps) if reps else None}

    # (3) seed noise floor (advisory reference only)
    by_task: dict[str, dict[int, float]] = {}
    for r in scored:
        by_task.setdefault(str(r["task_id"]), {})[int(r.get("seed", 0))] = float(r["Y_star"])
    sseeds = sorted({int(r.get("seed", 0)) for r in scored})
    common = [t for t, s in by_task.items() if all(x in s for x in sseeds)]
    seed_means = {s: _mean(by_task[t][s] for t in common) for s in sseeds} if common else {}
    task_sd = [statistics.stdev(v.values()) for v in by_task.values() if len(v) >= 2]
    sigma = {"advisory_reference_only": True, "seeds": sseeds, "n_common_tasks": len(common),
             "seed_means": {str(k): v for k, v in seed_means.items()},
             "sigma_seed": statistics.stdev(seed_means.values()) if len(seed_means) >= 2 else None,
             "mean_within_task_sd": _mean(task_sd),
             "note": "reference scale for reading gains; never a search or confirmation veto"}

    # (4) mid-trajectory checkpoint peak vs final
    gaps = []
    for r in scored:
        ds = [float(x) for x in (r.get("draft_scores") or []) if x is not None]
        if ds:
            gaps.append({"task_id": r["task_id"], "seed": int(r.get("seed", 0)),
                         "gap": max(ds) - float(r["Y_star"])})
    max_gap = max((g["gap"] for g in gaps), default=0.0)
    switches = {
        "enable_checkpoint_ranker": max_gap > GAP_TOL,
        "enable_mirror_gate_channel": counts["missing_or_invalid_gds"]
        + counts["public_mirror_execution_gap"] > 0,
        "enable_endogenous_probe_channel": counts["electrical_or_hidden_info_gap"] > 0,
        "capability_bound_tasks": sorted({x["task_id"] for x in per_run
                                          if x["class"] == "capability_bound"}),
    }
    rep = {"schema_version": 1, "split": "dev", "arm": "H_base", "k0": len(seeds),
           "n_runs": len(runs), "n_scored": len(scored),
           "n_infra_error": len(infra),
           "infra_errors": [{"task_id": r.get("task_id"), "seed": r.get("seed"),
                             "category": r["infra_category"]} for r in infra],
           "taxonomy": {"counts": counts, "shares": shares, "n_not_passed": n_fail,
                        "electrical_or_hidden_info_gap_by_profile": hidden_split, "runs": per_run},
           "verifier_repeatability": repeat, "mirror_concordance": _concordance(scored),
           "seed_noise_floor": sigma,
           "checkpoint_gap": {"max_gap": max_gap, "mean_gap": _mean(g["gap"] for g in gaps),
                              "n_runs_measured": len(gaps),
                              "n_runs_positive_gap": sum(g["gap"] > GAP_TOL for g in gaps),
                              "runs": gaps},
           "module_switches": switches}
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "dev_diagnostics.json").write_text(json.dumps(rep, indent=1, sort_keys=True))
        (out / "dev_diagnostics.md").write_text(render_diagnostics_md(rep))
    return rep


def render_diagnostics_md(rep: Mapping) -> str:
    t = rep["taxonomy"]
    L = ["# dev diagnostics (H_base)", "",
         f"K_0 = {rep['k0']} seeds, {rep['n_scored']} scored runs, {rep['n_infra_error']} infra_error.",
         "", "## 1. residual failure taxonomy", "", "| class | runs | share |", "|---|---|---|"]
    L += [f"| {c} | {t['counts'][c]} | {t['shares'][c]:.3f} |" for c in TAXONOMY]
    h = t["electrical_or_hidden_info_gap_by_profile"]
    L += ["", f"electrical / hidden-information gap: binned {h['binned']}, continuous "
          f"{h['continuous']}, unknown {h['unknown']}.", "",
          "## 2. verifier repeatability and mirror concordance", ""]
    r = rep["verifier_repeatability"]
    L.append(f"repeatability measured: {r['measured']}; max spread {r['max_spread']}; "
             f"deterministic {r['deterministic']}.")
    L += ["", "| checker | agree | m=1,d=0 | m=0,d=1 | excluded |", "|---|---|---|---|---|"]
    for k, v in rep["mirror_concordance"].items():
        L.append(f"| {k} | {v['agree']} | {v['m1_d0_false_certify']} | {v['m0_d1_false_reject']} "
                 f"| {v['excluded_bottom_or_missing']} |")
    s = rep["seed_noise_floor"]
    L += ["", "## 3. seed noise floor (advisory reference only)", "",
          f"sigma_seed = {s['sigma_seed']}; mean within-task sd = {s['mean_within_task_sd']}; "
          f"{s['n_common_tasks']} tasks in all seeds.", "",
          "## 4. checkpoint peak vs final", ""]
    g = rep["checkpoint_gap"]
    L.append(f"max gap {g['max_gap']}; mean gap {g['mean_gap']}; {g['n_runs_positive_gap']} of "
             f"{g['n_runs_measured']} runs with a positive gap.")
    L += ["", "## module switches", ""]
    L += [f"- {k}: {v}" for k, v in rep["module_switches"].items()]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------------------------
# mutation accounting
# ---------------------------------------------------------------------------------------------

def registry_mutations(inc_registry, cand_registry) -> list[str]:
    """Names of checker specs added, removed or changed between two registries."""
    a = {s.name: s for s in inc_registry.specs}
    b = {s.name: s for s in cand_registry.specs}
    out = []
    for n in sorted(set(a) | set(b)):
        if n not in a or n not in b:
            out.append(n)
        elif any(getattr(a[n], f.name) != getattr(b[n], f.name) for f in fields(a[n])):
            out.append(n)
    return out


def is_single_atomic_mutation(inc_registry, cand_registry, extra_mutations: Iterable[str] = ()) -> bool:
    return len(registry_mutations(inc_registry, cand_registry)) + len(list(extra_mutations)) == 1


# ---------------------------------------------------------------------------------------------
# Path 1
# ---------------------------------------------------------------------------------------------

def _signature(sig: Any) -> Any:
    if isinstance(sig, (list, tuple, set)):
        return tuple(sorted(str(x) for x in sig))
    return sig


def granted_budget(trial: Mapping) -> float:
    """``granted_repair_budget_sec`` of the archived ``fork_meta.json`` (never recomputed)."""
    fm = trial.get("fork_meta")
    if fm is None and trial.get("snapshot_dir"):
        snap = Path(trial["snapshot_dir"])
        p = find_fork_meta(snap / "pre_repair") or find_fork_meta(snap)
        fm = json.loads(p.read_text()) if p else None
    if not fm or fm.get("granted_repair_budget_sec") is None:
        raise ValueError(f"{trial.get('task_id')}: no archived fork_meta.json "
                         "granted_repair_budget_sec")
    return float(fm["granted_repair_budget_sec"])


def archived_gate_decision(trial: Mapping, registry) -> tuple[bool, tuple[str, ...]]:
    """Zero-LLM, zero-verifier terminal gate of *registry* on an archived Phase A exit state.

    Trial keys: ``fork_meta`` (or ``snapshot_dir``), ``m_best``, ``unevaluated_draft``,
    ``advisory_flags`` and ``terminal_m`` (``{checker: 0 | 1}`` for terminal-only Class-1
    checkers, measured once on ``pre_repair/``)."""
    fm = dict(trial.get("fork_meta") or {})
    if "repair_eligible" not in fm:
        fm["repair_eligible"] = granted_budget(trial) > 0
    flags = trial.get("advisory_flags") or {}
    adv = [n for n in registry.gate_triggers() if flags.get(n)]
    term = [s.name for s in registry.mirror("terminal_only")]
    tm = trial.get("terminal_m") or {}
    if any(n not in tm for n in term):
        raise ValueError(f"{trial.get('task_id')}: terminal-only checker result missing for "
                         f"{[n for n in term if n not in tm]}")
    g = compute_gate(fm, trial.get("m_best"), unevaluated_draft=bool(trial.get("unevaluated_draft")),
                     advisory_triggers=adv, terminal_m=[int(tm[n]) for n in term] if term else None)
    return bool(g["gate_triggered"]), tuple(sorted(g["trigger_reasons"]))


def evaluate_path1_fork(trials: Iterable[Mapping], *, inc_registry, cand_registry,
                        cand_gate: Callable[[Mapping], tuple[bool, Any]],
                        repair: Callable[[Mapping, int, float], Mapping],
                        candidate_id: str, round_id: int, ledger: EventLedger | None = None,
                        infra_ledger: InfraLedger | None = None, r_rep: int = R_REP,
                        extra_mutations: Iterable[str] = (), gate_ids: Iterable[str] | None = None,
                        infra_retries: int = 1) -> dict:
    """Path 1 replay of a terminal-only gate candidate over archived ``H_inc`` dev trials.

    ``trials``: ``{task_id, seed, snapshot_dir | fork_meta, inc_triggered, inc_gate_signature,
    Y_inc_final, Y_pre}`` (``Y_pre`` = verified score of the pre-repair best submission).
    ``cand_gate(trial) -> (triggered, signature)``: zero-LLM candidate gate on ``pre_repair/``.
    ``repair(trial, rep_seed, budget_sec) -> {Y_post, ratchet_promoted, promote_reason,
    infra_error?, llm_calls?, verifier_calls?}``: one stateless Phase B seed, post-ratchet."""
    path = inc_registry.evaluation_path(cand_registry)
    if path != "path1":
        raise ValueError("candidate changes a scope='trajectory' mirror checker: Phase A differs, "
                         "Path 1 replay is invalid -- evaluate it with Path 2 "
                         "(evaluate_path2_paired)")
    if r_rep < 1:
        raise ValueError("r_rep must be >= 1")
    extra_mutations = list(extra_mutations)
    mutations = registry_mutations(inc_registry, cand_registry) + extra_mutations
    atomic = len(mutations) == 1
    gate_ids = list(gate_ids) if gate_ids is not None else mutations
    ledger = ledger if ledger is not None else EventLedger()

    per_trial, deltas = [], []
    llm_calls = verifier_calls = 0
    unresolved = []
    for t in trials:
        inc_key = (bool(t.get("inc_triggered")), _signature(t.get("inc_gate_signature")))
        c_trig, c_sig = cand_gate(t)
        cand_key = (bool(c_trig), _signature(c_sig))
        y_inc = float(t["Y_inc_final"])
        row = {"task_id": t["task_id"], "seed": int(t.get("seed", 0)), "inc_triggered": inc_key[0],
               "cand_triggered": cand_key[0], "divergent": cand_key != inc_key,
               "Y_inc_final": y_inc, "llm_calls": 0, "verifier_calls": 0}
        if cand_key == inc_key:
            row.update(delta_Y=0.0, Y_cand=y_inc, verdict="Neutral(identical)")
            per_trial.append(row)
            continue
        if not cand_key[0]:
            if t.get("Y_pre") is None:
                raise ValueError(f"{t['task_id']}: candidate does not trigger; Y_pre of the "
                                 "archived pre-repair submission is required")
            reps = [{"rep_seed": None, "Y_post": float(t["Y_pre"]), "ratchet_promoted": False,
                     "promote_reason": "candidate_not_triggered"}]
        else:
            budget = granted_budget(t)
            reps = []
            for r in range(r_rep):
                res = None
                for attempt in range(infra_retries + 1):
                    res = dict(repair(t, r, budget))
                    llm_calls += int(res.get("llm_calls", 1))
                    verifier_calls += int(res.get("verifier_calls", 1))
                    row["llm_calls"] += int(res.get("llm_calls", 1))
                    row["verifier_calls"] += int(res.get("verifier_calls", 1))
                    if not res.get("infra_error"):
                        break
                    if infra_ledger is not None:
                        infra_ledger.observe({**res.get("infra_record", {}), "arm": "H_cand",
                                              "task_id": t["task_id"], "seed": int(t.get("seed", 0)),
                                              "run_id": f"{candidate_id}:rep{r}:try{attempt}",
                                              "status": res.get("status", "tool_error")})
                if res.get("infra_error"):
                    reps.append({"rep_seed": r, "infra_error": True})
                    continue
                y_post = res.get("Y_post")
                if y_post is None and not res.get("ratchet_promoted"):
                    if t.get("Y_pre") is None:
                        raise ValueError(f"{t['task_id']}: ratchet kept the archived best but "
                                         "Y_pre is not archived")
                    y_post = t["Y_pre"]
                if y_post is None:
                    raise ValueError(f"{t['task_id']}: promoted repair without a verified Y_post")
                reps.append({"rep_seed": r, "Y_post": float(y_post),
                             "ratchet_promoted": bool(res.get("ratchet_promoted")),
                             "promote_reason": res.get("promote_reason")})
        ok = [x for x in reps if not x.get("infra_error")]
        if not ok:
            unresolved.append({"task_id": t["task_id"], "seed": int(t.get("seed", 0))})
            row.update(delta_Y=None, verdict="unresolved_infra_error", reps=reps)
            per_trial.append(row)
            continue
        y_cand = _mean(x["Y_post"] for x in ok)
        e = ledger.append(round_id=round_id, candidate_id=candidate_id, task_id=t["task_id"],
                          seed=int(t.get("seed", 0)), gate_ids=gate_ids, y_baseline=y_inc,
                          y_post=y_cand, rep_results=reps,
                          promote_reason=";".join(sorted({str(x.get("promote_reason")) for x in ok})),
                          diagnostic={"inc_gate": list(inc_key[1]) if isinstance(inc_key[1], tuple)
                                      else inc_key[1],
                                      "cand_gate": list(cand_key[1]) if isinstance(cand_key[1], tuple)
                                      else cand_key[1],
                                      "Y_pre": t.get("Y_pre"),
                                      "n_infra_reps": len(reps) - len(ok)})
        deltas.append(e["delta_Y"])
        row.update(delta_Y=e["delta_Y"], Y_cand=y_cand, verdict=e["verdict"], reps=reps)
        per_trial.append(row)

    total = math.fsum(deltas)
    promoted = atomic and _positive(total) and not unresolved
    reason = ("promoted" if promoted else "not_single_atomic_mutation" if not atomic
              else "unresolved_infra_error" if unresolved and _positive(total)
              else "net_delta_not_positive")
    return {"path": "path1", "candidate_id": candidate_id, "round": round_id,
            "mutations": mutations, "single_atomic_mutation": atomic,
            "n_trials": len(per_trial), "n_divergent": sum(r["divergent"] for r in per_trial),
            "llm_calls": llm_calls, "verifier_calls": verifier_calls,
            "sum_delta_Y": total, "counts": ledger.counts(round_id=round_id, candidate_id=candidate_id),
            "damaged": ledger.damaged(round_id=round_id, candidate_id=candidate_id),
            "unresolved": unresolved, "promoted": promoted, "reason": reason, "trials": per_trial}


def record_incumbent_events(trials: Iterable[Mapping], *, round_id: int, version_id: str,
                            ledger: EventLedger) -> list[dict]:
    """Ledger entries for ``H_inc``'s own live Phase B events: ``Y*(w_inc_final) - Y*(w_pre)``,
    attributed to the gates that triggered (``inc_gate_ids``). Feeds ``K_idle`` pruning."""
    out = []
    for t in trials:
        if not t.get("inc_triggered") or t.get("Y_pre") is None:
            continue
        out.append(ledger.append(round_id=round_id, candidate_id=version_id, task_id=t["task_id"],
                                 seed=int(t.get("seed", 0)), gate_ids=t.get("inc_gate_ids") or [],
                                 y_baseline=float(t["Y_pre"]), y_post=float(t["Y_inc_final"]),
                                 baseline_kind="w_pre", promote_reason=t.get("promote_reason")))
    return out


# ---------------------------------------------------------------------------------------------
# Paths 2 and 3
# ---------------------------------------------------------------------------------------------

def _task_means(runs: Iterable[Mapping], theta: float) -> tuple[dict[str, float], dict[str, bool], list]:
    by: dict[str, list[float]] = {}
    trig: dict[str, bool] = {}
    infra = []
    for r in runs:
        t = str(r["task_id"])
        trig[t] = trig.get(t, False) or bool(r.get("hook_triggered"))
        v = classify_trial(r, theta_gw_max=theta)
        if v.infra_error:
            infra.append({"task_id": t, "seed": r.get("seed"), "category": v.category})
            continue
        by.setdefault(t, []).append(v.score)
    return {t: _mean(v) for t, v in by.items()}, trig, infra


def evaluate_path3_pooled(inc_pooled_runs: Iterable[Mapping], cand_runs: Iterable[Mapping], *,
                          dev_task_ids: Iterable[str], n_mutations: int = 1,
                          theta_gw_max: float = THETA_GW_MAX) -> dict:
    """Candidate dev runs against the pooled mean of all archived ``H_inc`` seeds, same task."""
    dev = sorted(set(dev_task_ids))
    inc, _, inc_inf = _task_means(inc_pooled_runs, theta_gw_max)
    cand, _, cand_inf = _task_means(cand_runs, theta_gw_max)
    missing = [t for t in dev if t not in inc or t not in cand]
    diffs = {t: cand[t] - inc[t] for t in dev if t in inc and t in cand}
    net = _mean(diffs.values()) if not missing else None
    atomic = n_mutations == 1
    promoted = atomic and _positive(net)
    return {"path": "path3", "per_task_delta": diffs, "net_delta": net, "missing_tasks": missing,
            "infra_errors": inc_inf + cand_inf, "single_atomic_mutation": atomic,
            "promoted": promoted,
            "reason": "promoted" if promoted else "incomplete_dev" if missing
            else "not_single_atomic_mutation" if not atomic else "net_delta_not_positive"}


def evaluate_path2_paired(inc_runs: Iterable[Mapping], cand_runs: Iterable[Mapping], *,
                          dev_task_ids: Iterable[str], leak_tol: float = LEAK_TOL,
                          inc_pooled_runs: Iterable[Mapping] | None = None, n_mutations: int = 1,
                          theta_gw_max: float = THETA_GW_MAX) -> dict:
    """Paired dev runs (``hook_triggered`` per run) with the non-triggered prefix leakage audit."""
    inc_runs, cand_runs = list(inc_runs), list(cand_runs)
    dev = sorted(set(dev_task_ids))
    inc, inc_trig, inc_inf = _task_means(inc_runs, theta_gw_max)
    cand, cand_trig, cand_inf = _task_means(cand_runs, theta_gw_max)
    untrig = [t for t in dev if t in inc and t in cand
              and not inc_trig.get(t, False) and not cand_trig.get(t, False)]
    leak = _mean(cand[t] - inc[t] for t in untrig)
    audit = {"untriggered_tasks": untrig, "mean_diff_untriggered": leak, "leak_tol": leak_tol,
             "leak_detected": leak is not None and abs(leak) > leak_tol}
    if audit["leak_detected"]:
        res = evaluate_path3_pooled(inc_pooled_runs if inc_pooled_runs is not None else inc_runs,
                                    cand_runs, dev_task_ids=dev, n_mutations=n_mutations,
                                    theta_gw_max=theta_gw_max)
        return {**res, "reclassified_from": "path2", "leakage_audit": audit}
    missing = [t for t in dev if t not in inc or t not in cand]
    diffs = {t: cand[t] - inc[t] for t in dev if t in inc and t in cand}
    net = _mean(diffs.values()) if not missing else None
    atomic = n_mutations == 1
    promoted = atomic and _positive(net)
    return {"path": "path2", "per_task_delta": diffs, "net_delta": net, "missing_tasks": missing,
            "infra_errors": inc_inf + cand_inf, "leakage_audit": audit,
            "single_atomic_mutation": atomic, "promoted": promoted,
            "reason": "promoted" if promoted else "incomplete_dev" if missing
            else "not_single_atomic_mutation" if not atomic else "net_delta_not_positive"}
