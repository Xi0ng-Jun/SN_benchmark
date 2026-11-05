"""Pure method-v4 decision rules: paired Δ with the SE floor, safeguards, tiers, adoption.

No adapter, no I/O. Everything the loop decides is a function of these.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

from ..adapter import EvalResult


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

def combine(*results: Optional[EvalResult]) -> EvalResult:
    """Union of results on disjoint task sets (mb ∪ rest = the validation set)."""
    parts = [r for r in results if r is not None]
    scores, trajectories, costs, errors, values, attempts = {}, {}, {}, {}, {}, []
    context: dict = {}
    for r in parts:
        scores.update(r.per_task_scores)
        trajectories.update(r.trajectories)
        costs.update(r.costs)
        errors.update(r.error_classes)
        values.update(r.safeguard_values)
        attempts.extend(r.attempt_scores)
        for k, v in (r.context or {}).items():
            if isinstance(v, dict) and isinstance(context.get(k), dict):
                context[k] = dict(context[k], **v)
            elif k not in context:
                context[k] = v
    return EvalResult(per_task_scores=scores, trajectories=trajectories, costs=costs, error_classes=errors,
                      safeguard_values=values, context=context, attempt_scores=attempts)


def attempts_by_task(result: EvalResult) -> dict[str, list[float]]:
    """task -> its per-attempt scores (from ``attempt_scores``)."""
    out: dict[str, list[float]] = {}
    for attempt in result.attempt_scores or []:
        for task, value in attempt.items():
            if value is not None:
                out.setdefault(task, []).append(float(value))
    return out


# ---------------------------------------------------------------------------
# Paired Δ and SE (§1, §2.3: SE = max(own SE, A/A floor))
# ---------------------------------------------------------------------------

def paired_delta(candidate: EvalResult, reference: EvalResult, task_ids: Iterable[str], *,
                 rerun_var: Optional[float] = None, k_candidate: int = 1, k_reference: int = 1) -> dict:
    """Δ = mean(s_c − s_p) over tasks scored on both sides; SE floored by the A/A estimate.

    The floor is the SE a zero effect would have from rerun noise alone:
    sqrt(Σ_i rerun_var·(1/k_c,i + 1/k_p,i)) / n, with k_·,i the attempts actually
    averaged for task i (nominal k when attempts are not reported).
    """
    ids = list(task_ids)
    ca, ra = attempts_by_task(candidate), attempts_by_task(reference)
    diffs, floor_terms = [], []
    for t in ids:
        x, y = candidate.per_task_scores.get(t), reference.per_task_scores.get(t)
        if x is None or y is None:
            continue
        diffs.append(x - y)
        if rerun_var is not None:
            kc, kr = len(ca.get(t, ())) or k_candidate, len(ra.get(t, ())) or k_reference
            floor_terms.append(rerun_var * (1 / kc + 1 / kr))
    n = len(diffs)
    out = dict(n=n, dropped=len(ids) - n, delta=None, se_own=None, se_floor=None, se=None)
    if n == 0:
        return out
    delta = sum(diffs) / n
    se_own = math.sqrt(sum((d - delta) ** 2 for d in diffs) / (n - 1)) / math.sqrt(n) if n > 1 else None
    se_floor = math.sqrt(sum(floor_terms)) / n if floor_terms else None
    known = [s for s in (se_own, se_floor) if s is not None]
    out.update(delta=delta, se_own=se_own, se_floor=se_floor, se=max(known) if known else None)
    return out


# ---------------------------------------------------------------------------
# Safeguards (§1 保护指标: point estimate vs tolerance, no test)
# ---------------------------------------------------------------------------

def safeguard_verdict(observed: dict, tolerances: dict) -> dict:
    """observed: key -> {direction, subject, reference[, margin]}; tolerance: config, else the margin.

    harm = (reference − subject) for higher_is_better, (subject − reference) otherwise;
    breached when harm > tolerance. A side that is None is "unobserved".
    """
    rows, breached, unobserved = [], [], []
    for key in sorted(observed):
        o = observed[key] or {}
        tol = tolerances.get(key, o.get("margin"))
        s, r = o.get("subject"), o.get("reference")
        known = all(type(v) in (int, float) and math.isfinite(v) for v in (s, r))
        harm = None
        if known:
            harm = (r - s) if o.get("direction") == "higher_is_better" else (s - r)
        row = dict(key=key, direction=o.get("direction"), subject=s, reference=r, tolerance=tol, harm=harm,
                   breached=None if not known or tol is None else harm > tol)
        rows.append(row)
        if not known or tol is None:
            unobserved.append(key)
        elif row["breached"]:
            breached.append(key)
    return dict(ok=not breached and not unobserved, breached=breached, unobserved=unobserved, rows=rows)


# ---------------------------------------------------------------------------
# Mechanism tier (§3.2 stage 3)
# ---------------------------------------------------------------------------

TIERS = ("confirmed", "unconfirmed", "suspicious")


def reviewer_conclusion(verdicts: Optional[list[dict]], pairs: list[dict], *, min_effect: int,
                        max_control_effect: int) -> dict:
    """'effective' / 'suspicious' / 'none' from per-pair verdicts and the accountant's pair key.

    *pairs* are the accountant's records (``pair``, ``kind`` candidate|control,
    ``candidate_side``). A candidate pair counts as "took effect" only when the
    reviewer names the candidate's side; a control pair counts when the reviewer
    claims any effect. More than ``max_control_effect`` such controls make the
    review untrusted (conclusion 'none'). Gaming with a note on any candidate pair
    makes it 'suspicious'.
    """
    if verdicts is None:
        return dict(conclusion="none", trusted=None, reason="no reviewer")
    by_pair = {v.get("pair"): v for v in verdicts if isinstance(v, dict)}
    missing = [p["pair"] for p in pairs if p["pair"] not in by_pair]
    controls = [p for p in pairs if p["kind"] == "control"]
    candidates = [p for p in pairs if p["kind"] == "candidate"]
    control_effects = sum(bool(by_pair.get(p["pair"], {}).get("effect")) for p in controls)
    effects = sum(bool(by_pair.get(p["pair"], {}).get("effect"))
                  and by_pair[p["pair"]].get("changed_side") == p["candidate_side"] for p in candidates)
    wrong_side = sum(bool(by_pair.get(p["pair"], {}).get("effect"))
                     and by_pair[p["pair"]].get("changed_side") not in (None, p["candidate_side"]) for p in candidates)
    gaming = [p["pair"] for p in candidates if by_pair.get(p["pair"], {}).get("gaming")
              and str(by_pair[p["pair"]].get("note") or "").strip()]
    side_effects = sum(bool(by_pair.get(p["pair"], {}).get("side_effect")) for p in candidates)
    base = dict(effects=effects, wrong_side=wrong_side, control_effects=control_effects, gaming=gaming,
                side_effects=side_effects, missing=missing)
    if missing and len(missing) == len(pairs):
        return dict(base, conclusion="none", trusted=None, reason="no verdicts")
    if control_effects > max_control_effect:
        return dict(base, conclusion="none", trusted=False, reason="control pairs judged changed")
    if gaming:
        return dict(base, conclusion="suspicious", trusted=True)
    if effects >= min_effect:
        return dict(base, conclusion="effective", trusted=True)
    return dict(base, conclusion="none", trusted=True)


def tier(mechanical: Optional[str], reviewer: str) -> str:
    """suspicious > confirmed (mechanical 'changed' or reviewer 'effective') > unconfirmed."""
    if reviewer == "suspicious":
        return "suspicious"
    if mechanical == "changed" or reviewer == "effective":
        return "confirmed"
    return "unconfirmed"


# ---------------------------------------------------------------------------
# Adoption (§3.3)
# ---------------------------------------------------------------------------

def qualification(delta: Optional[float], se: Optional[float], mechanism_tier: Optional[str],
                  safeguards: dict, *, c: float, c_mech: float, unobserved_blocks: bool) -> dict:
    coefficient = c_mech if mechanism_tier == "confirmed" else c
    threshold = coefficient * se if se is not None else None
    reasons = []
    if delta is None or se is None:
        reasons.append("not_measurable")
    elif delta <= 0:
        reasons.append("not_positive")
    elif delta < threshold:
        reasons.append("below_threshold")
    if mechanism_tier == "suspicious":
        reasons.append("mechanism_suspicious")
    if safeguards.get("breached"):
        reasons.append("safeguard_breached")
    if unobserved_blocks and safeguards.get("unobserved"):
        reasons.append("safeguard_unobserved")
    return dict(qualified=not reasons, coefficient=coefficient, threshold=threshold, reasons=reasons)


def pick_best(records: list[dict], tie_fraction: float) -> Optional[dict]:
    """Largest Δ_val; among those within tie_fraction·SE of it, the smallest diff (then card order)."""
    if not records:
        return None
    top = max(records, key=lambda r: r["delta_val"])
    close = [r for r in records if top["delta_val"] - r["delta_val"] < tie_fraction * (top["se_val"] or 0)
             or r is top]
    return min(close, key=lambda r: (r.get("diff_lines") if r.get("diff_lines") is not None else math.inf,
                                     -r["delta_val"], r["card_index"]))


def minibatch_split(validation: list[str], fraction: float, minimum: int, *, seed: str,
                    families: Optional[dict[str, str]] = None) -> tuple[list[str], list[str]]:
    """Fixed minibatch (§3.2; v4 first implementation: one per run) and the rest of the validation set.

    Proportional allocation over families when given: tasks ordered by
    (rank within their family / family size, hash), the first n taken; else by
    hash only. n = max(minimum, ceil(fraction·N)), at most N − 1.
    """
    import hashlib
    ids = sorted(set(validation))
    if len(ids) < 2:
        raise ValueError("the validation set needs at least 2 tasks for a minibatch and a rest")
    n = min(len(ids) - 1, max(minimum, math.ceil(fraction * len(ids))))
    h = lambda t: hashlib.sha256(f"{seed}:{t}".encode()).hexdigest()  # noqa: E731
    fam = families or {}
    groups: dict[str, list[str]] = {}
    for t in ids:
        groups.setdefault(fam.get(t, t if not fam else "_"), []).append(t)
    rank = {}
    for members in groups.values():
        members.sort(key=h)
        for i, t in enumerate(members):
            rank[t] = (i + 0.5) / len(members)
    ordered = sorted(ids, key=lambda t: (rank[t], h(t))) if fam else sorted(ids, key=h)
    mb = sorted(ordered[:n])
    return mb, sorted(set(ids) - set(mb))
