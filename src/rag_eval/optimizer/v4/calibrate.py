"""method-v4 §2.3 calibration: seed A/A runs, derived quantities and the feasibility card.

``measure`` runs (or, on resume, replays) the seed's A/A measurements:
search set (k = calibration_k, the diagnosis measurement), minibatch and rest
(subset measurements at calibration_k, unpaired). ``derive`` turns them into
the run's calibrated quantities; ``feasibility`` into the card printed before
the first round. The test set is never touched here (§4.1).
"""
from __future__ import annotations

import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from ..adapter import EvalResult, SystemAdapter, Version
from .config import V4Config
from .decide import attempts_by_task, combine


def measure(adapter: SystemAdapter, seed: Version, cfg: V4Config, search: list[str], mb: list[str],
            rest: list[str]) -> tuple[dict[str, EvalResult], dict[str, float]]:
    """Seed A/A on search, minibatch and rest, concurrently; (results, wall seconds per stage)."""
    k = cfg.calibration_k
    jobs = dict(
        search=lambda: adapter.evaluate(seed, search, k, purpose="diagnose"),
        mb=lambda: adapter.evaluate_subset(seed, None, "mb", mb, k)[0],
        rest=lambda: adapter.evaluate_subset(seed, None, "rest", rest, k)[0],
    )
    walls: dict[str, float] = {}

    def timed(name):
        t0 = time.monotonic()
        try:
            return jobs[name]()
        finally:
            walls[name] = time.monotonic() - t0
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="v4-cal") as pool:
        futures = {name: pool.submit(timed, name) for name in jobs}
        results = {name: f.result() for name, f in futures.items()}
    return results, walls


def _aa(result: EvalResult) -> dict:
    """Rerun noise and task variance from tasks with at least two attempts."""
    per_task = {t: v for t, v in attempts_by_task(result).items() if len(v) >= 2}
    diffs = [v[0] - v[1] for v in per_task.values()]
    means = [sum(v) / len(v) for v in per_task.values()]
    n = len(diffs)
    if n < 2:
        return dict(n=n, rerun_var=None, task_var=None, aa_sd=None, aa_se=None, constant_fraction=None,
                    mean=None)
    rerun_var = sum(d * d for d in diffs) / (2 * n)          # per-attempt variance, E[d] = 0
    k_mean = statistics.mean(len(v) for v in per_task.values())
    task_var = max(0.0, statistics.variance(means) - rerun_var / k_mean)
    constant = sum(1 for v in per_task.values() if len(set(v)) == 1 and v[0] in (0.0, 1.0))
    aa_sd = statistics.stdev(diffs)
    return dict(n=n, rerun_var=rerun_var, task_var=task_var, aa_sd=aa_sd, aa_se=aa_sd / math.sqrt(n),
                aa_delta=sum(diffs) / n, constant_fraction=constant / n, mean=statistics.mean(means))


def derive(results: dict[str, EvalResult], walls: dict[str, float], cfg: V4Config, *, safeguards: Optional[dict],
           sizes: dict[str, int]) -> dict:
    """Calibrated quantities (§2.3 step 3–4). Pure."""
    val = _aa(combine(results["mb"], results["rest"]))
    search = _aa(results["search"])
    if val["rerun_var"] is None:
        raise ValueError("calibration needs at least two validation tasks with two scored attempts")
    if cfg.k_val is not None:
        k_val, rule = cfg.k_val, "configured"
    else:
        k_val = 2 if val["rerun_var"] > val["task_var"] else 1
        rule = "rerun variance > task variance -> 2, else 1"
    tasks = dict(search=sizes["search"], mb=sizes["mb"], rest=sizes["rest"])
    per_task = {s: (walls.get(s) or 0.0) / max(1, tasks[s]) for s in tasks}
    return dict(validation=val, search=search, k_val=k_val, k_val_rule=rule,
                rerun_var=val["rerun_var"], se_floor_rule="sqrt(sum_i rerun_var*(1/k_c,i + 1/k_p,i)) / n",
                walls=dict(walls), seconds_per_task=per_task, sizes=dict(sizes), safeguards=safeguards,
                infrastructure_failure_rate=_infra(safeguards))


def _infra(safeguards: Optional[dict]) -> Optional[float]:
    row = (safeguards or {}).get("infrastructure_failure_rate") or {}
    return row.get("subject")


def feasibility(cal: dict, cfg: V4Config) -> dict:
    """§2.3 step 5 feasibility card (advice only, never blocks).

    Stage times are the calibration's observed walls (cache state as found:
    calibration of a new preparation-code version is cold; a candidate that
    changes preparation code is cold again), scaled to the stage's k when the
    calibration ran at a larger k. Cold/warm split needs the adapter's cost
    estimate (§6), which SN does not provide yet: both columns are the observed
    values and are labelled so.
    """
    sizes, walls, k_cal, k_val = cal["sizes"], cal["walls"], cfg.calibration_k, cal["k_val"]
    smoke_s = walls.get("search", 0.0) * min(1.0, cfg.smoke_size / max(1, sizes["search"]))
    stage = dict(agent=cfg.agent_estimate_minutes * 60, smoke=smoke_s,
                 minibatch=walls.get("mb", 0.0) * (1 / k_cal if walls.get("mb") else 0),
                 validation_rest=walls.get("rest", 0.0) * (k_val / k_cal))
    per_candidate = sum(stage.values())
    budget = cfg.time_budget_hours * 3600
    # Most candidates stop at stage 0/1; the card assumes every candidate runs through (an upper bound on time).
    slots = max(1, cfg.b)
    candidates = int(slots * budget // per_candidate) if per_candidate > 0 else None
    rounds = (candidates // cfg.n_cards) if candidates is not None else None
    aa_sd = cal["validation"]["aa_sd"]
    n_test = sizes.get("test")
    half = 1.96 * aa_sd / math.sqrt(n_test) if n_test and aa_sd is not None else None
    card = dict(stage_seconds=stage, per_candidate_seconds=per_candidate,
                cache_note="observed calibration walls; no cold/warm split (adapter gives no cost estimate)",
                candidates_in_budget=candidates, rounds_in_budget=rounds,
                test_half_width_95=half, test_n=n_test,
                k_val=k_val, rerun_var=cal["rerun_var"], task_var=cal["validation"]["task_var"],
                constant_fraction=cal["validation"]["constant_fraction"],
                infrastructure_failure_rate=cal.get("infrastructure_failure_rate"), warnings=[])
    if n_test is not None and n_test < 40:
        card["warnings"].append(f"test set has {n_test} tasks (method §2.3 asks for at least 40)")
    if sizes["mb"] + sizes["rest"] < 40:
        card["warnings"].append(f"validation set has {sizes['mb'] + sizes['rest']} tasks (method §2.3: at least 40)")
    mmi = cfg.min_meaningful_improvement
    if mmi is not None and half is not None and half > mmi:
        card["warnings"].append(
            f"test set too small to confirm a cumulative gain of {mmi:g}: 95% half-width ~{half:.3f}. "
            "Add test tasks, add time, or accept a coarser conclusion.")
    return card


def render(card: dict, cal: dict) -> str:
    st = card["stage_seconds"]
    lines = [
        "feasibility (advice only):",
        f"  validation: mb={cal['sizes']['mb']} rest={cal['sizes']['rest']} search={cal['sizes']['search']} "
        f"test={card['test_n'] if card['test_n'] is not None else '-'}",
        f"  k_val={card['k_val']} ({cal['k_val_rule']}); rerun var {card['rerun_var']:.4g}, "
        f"task var {card['task_var']:.4g}; constant tasks {card['constant_fraction']:.0%}",
        "  per candidate (s): " + ", ".join(f"{k} {v:.0f}" for k, v in st.items())
        + f" = {card['per_candidate_seconds']:.0f} ({card['cache_note']})",
        f"  in budget: ~{card['candidates_in_budget']} candidates, ~{card['rounds_in_budget']} rounds (upper bound)",
        f"  infrastructure failure rate (seed): {card['infrastructure_failure_rate']}",
        "  test 95% half-width ~" + (f"{card['test_half_width_95']:.3f}" if card["test_half_width_95"] else "-"),
    ]
    lines += [f"  warning: {w}" for w in card["warnings"]]
    return "\n".join(lines)
