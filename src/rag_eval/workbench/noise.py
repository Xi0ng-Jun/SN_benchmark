"""Offline rerun uncertainty. Arithmetic copied from harness-opt ledger/overfit.py
and optimizer/bookkeeper.py; no runtime dependency on that repository.

Success is a thresholded observed score; absent scores remain unknown. The
90% band uses Jeffreys smoothing and the normal half-width used by LCD.
"""
from __future__ import annotations

from itertools import combinations
import math

Z90 = 1.6448536269514722


def _finite(value: float, name: str) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _probability(value: float, name: str) -> float:
    value = _finite(value, name)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must lie in [0, 1]")
    return value


def score_se(p_list: list[float], n_eff: float) -> float:
    """Estimate score standard error as ``sqrt(mean(p*(1-p))/n_eff)``."""

    if not p_list:
        raise ValueError("p_list must not be empty")
    values = [_probability(p, "p") for p in p_list]
    n_eff = _finite(n_eff, "n_eff")
    if n_eff <= 0.0:
        raise ValueError("n_eff must be positive")
    return math.sqrt(sum(p * (1.0 - p) for p in values) / len(values) / n_eff)


def icc_from_families(scores_by_family: dict[str, list[float]]) -> float:
    """Estimate one-way random-effects ICC and clip it to ``[0, 1]``."""

    if not scores_by_family:
        raise ValueError("scores_by_family must not be empty")
    groups: list[list[float]] = []
    for family, scores in scores_by_family.items():
        if not scores:
            raise ValueError(f"family {family!r} must contain scores")
        groups.append([_probability(score, "score") for score in scores])
    n_groups = len(groups)
    n_total = sum(len(group) for group in groups)
    if n_groups < 2 or n_total <= n_groups:
        return 0.0
    grand = sum(sum(group) for group in groups) / n_total
    means = [sum(group) / len(group) for group in groups]
    ss_between = sum(len(group) * (mean - grand) ** 2 for group, mean in zip(groups, means))
    ss_within = sum(sum((value - mean) ** 2 for value in group) for group, mean in zip(groups, means))
    ms_between = ss_between / (n_groups - 1)
    ms_within = ss_within / (n_total - n_groups)
    mean_size = n_total / n_groups
    denominator = ms_between + (mean_size - 1.0) * ms_within
    if denominator <= 0.0:
        return 1.0 if ms_between > 0.0 else 0.0
    return min(1.0, max(0.0, (ms_between - ms_within) / denominator))


def n_eff(n: float, mean_family_size: float, icc: float) -> float:
    """Apply the family design effect ``1 + (m-1)*icc`` to ``n``."""

    n = _finite(n, "n")
    mean_family_size = _finite(mean_family_size, "mean_family_size")
    icc = _probability(icc, "icc")
    if n <= 0.0:
        raise ValueError("n must be positive")
    if mean_family_size < 1.0:
        raise ValueError("mean_family_size must be at least 1")
    return n / (1.0 + (mean_family_size - 1.0) * icc)


def half_width(successes, attempts):
    """Jeffreys-smoothed 90% normal half-width; None without observations."""
    if not attempts:
        return None
    p = (successes + .5) / (attempts + 1)
    return min(1., Z90 * math.sqrt(p * (1 - p) / attempts))


def difference_band(s0, n0, s1, n1):
    """LCD two-proportion half-width (Jeffreys-smoothed)."""
    p0, p1 = (s0 + .5) / (n0 + 1), (s1 + .5) / (n1 + 1)
    return min(1., Z90 * math.sqrt(p0 * (1 - p0) / max(n0, 1) + p1 * (1 - p1) / max(n1, 1)))


def estimate(candidate, *, success_threshold=.5, k_base=2):
    """Estimate from admitted attempts carrying rows(case_id, family, value).

    This pure projection deliberately takes no service/model clients. Its input
    is assembled only after execution and pair admission by ComparisonService.
    """
    attempts = candidate.get('attempts', [])
    cases, families, maps = {}, {}, []
    for attempt in attempts:
        rows = {r['case_id']: r for r in attempt['rows']}
        maps.append((attempt['execution_id'], rows))
        for cid, row in rows.items():
            case = cases.setdefault(cid, dict(values=[], family=row['family']))
            if row.get('value') is not None:
                case['values'].append(_probability(row['value'], 'score'))
    for cid, case in cases.items():
        values = case.pop('values')
        n = len(values)
        successes = sum(v >= success_threshold for v in values)
        p = successes / n if n else None
        case.update(n=n, successes=successes, p=p, mean=sum(values) / n if n else None,
                    half_width=half_width(successes, n))
        if p is not None:
            families.setdefault(case['family'], []).append(p)
    ps = [c['p'] for c in cases.values() if c['p'] is not None]
    icc = icc_from_families(families) if families else None
    effective = n_eff(len(ps), len(ps) / len(families), icc) if families else None
    pairs = [dict(left_execution=a, right_execution=b,
                  deltas={cid: rb[cid]['value'] - ra[cid]['value'] for cid in ra.keys() & rb.keys()
                          if ra[cid].get('value') is not None and rb[cid].get('value') is not None})
             for (a, ra), (b, rb) in combinations(maps, 2)]
    return dict(eta=score_se(ps, effective) if ps else None, n=len(ps), n_eff=effective, icc=icc,
                cases=cases, pairs=pairs, k=len(attempts), k_base=k_base,
                k_below_base=len(attempts) < k_base, success_threshold=success_threshold,
                band='jeffreys90-normal')


def summarize_cases(cases):
    """Recompute aggregate uncertainty for a role/paper subset of frozen case rates."""
    families = {}
    for c in cases.values():
        if c.get('p') is not None:
            families.setdefault(c['family'], []).append(c['p'])
    ps = [p for values in families.values() for p in values]
    icc = icc_from_families(families) if ps else None
    effective = n_eff(len(ps), len(ps) / len(families), icc) if ps else None
    return dict(n=len(ps), icc=icc, n_eff=effective, eta=score_se(ps, effective) if ps else None)
