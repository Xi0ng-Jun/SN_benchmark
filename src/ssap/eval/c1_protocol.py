"""Pure ``C1`` validation selection and the sealed multi-seed ``test`` report.

Validation: one paired comparison of the unique ``dev`` incumbent ``H_inc`` with ``H_base`` on
sealed ``val``; ``H* = H_inc`` iff ``delta_val = mean_x (Y*(x, H_inc) - Y*(x, H_base)) > 1e-6``,
else ``H_base`` (a tie keeps the smaller ``H_base``). The caller receives only
``{selected_version_id, promoted, delta_val, val_access_count}``; per-task ``val`` results never
flow back to proposers. There is no win-margin, McNemar, ``p``-value or loss-count veto.

Test: evaluated once (``test_unblinded.lock``) across ``R >= 3`` seeds with three-stage
attribution ``H_raw -> H_base -> H*`` (automated credit is claimed only for ``H_base -> H*``),
seed noise floor, per-task ``(n_plus, n_minus)``, a cluster bootstrap interval with the
small-cluster warning and every per-cluster raw net difference, the exact ``C*`` flip versus
non-flip decomposition, the raw cost ledger, the ``infra_error`` table with ``delta_worst`` and
the honest baselines.
"""
from __future__ import annotations

import itertools
import json
import math
import os
import random
import statistics
import time
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Mapping

from .infra_ledger import THETA_GW_MAX, classify_trial, delta_worst

DELTA_VAL_MIN = 1e-6
N_VAL = 14
R_MIN = 3
G_TEST_CONSERVATIVE = 10
G_TEST_HARD = 11
LOCK_NAME = "test_unblinded.lock"
REPORT_ARMS = ("H_raw", "H_base", "H_star")
BASELINE_ARMS = ("manual_instructions",)
COST_KEYS = ("input_tokens", "output_tokens", "cached_tokens", "usd", "tool_cpu_sec",
             "peak_ram_mb", "wall_sec")
C1_RETURN_KEYS = ("selected_version_id", "promoted", "delta_val", "val_access_count")


def _mean(xs: Iterable[float]) -> float | None:
    xs = list(xs)
    return math.fsum(xs) / len(xs) if xs else None


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------------------------
# holdout audit
# ---------------------------------------------------------------------------------------------

class HoldoutAudit:
    """Append-only access log of sealed splits (counts and version ids only, no scores)."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else None
        self.entries: list[dict] = []
        if self.path is not None and self.path.is_file():
            self.entries = [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]

    def access_count(self, split: str) -> int:
        return sum(1 for e in self.entries if e["split"] == split)

    def record(self, split: str, purpose: str, version_ids: Iterable[str]) -> dict:
        e = {"split": split, "purpose": purpose, "version_ids": list(version_ids), "at_utc": _utc()}
        self.entries.append(e)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a") as fh:
                fh.write(json.dumps(e, sort_keys=True) + "\n")
        return e


def _per_task(scores: Mapping[str, float | Iterable[float] | None]) -> dict[str, float | None]:
    out = {}
    for t, v in scores.items():
        if v is None or isinstance(v, (int, float)):
            out[str(t)] = None if v is None else float(v)
        else:
            vs = [float(x) for x in v]
            out[str(t)] = _mean(vs) if vs and all(x is not None for x in vs) else None
    return out


def run_c1_validation(inc_scores: Mapping, base_scores: Mapping, *, inc_version_id: str,
                      base_version_id: str, val_task_ids: Iterable[str], audit: HoldoutAudit,
                      n_val: int = N_VAL) -> dict:
    """Pure ``C1`` selection on ``val`` (see module docstring). Scores are per ``val`` task
    (a float, or a list of seed scores averaged per task) after infra reruns."""
    val = sorted(set(val_task_ids))
    if inc_version_id == base_version_id:
        return {"selected_version_id": base_version_id, "promoted": False, "delta_val": None,
                "val_access_count": audit.access_count("val")}
    if len(val) != n_val:
        raise ValueError(f"val must have N = {n_val} tasks, got {len(val)}")
    if audit.access_count("val") > 0:
        raise RuntimeError("val was already used for a C1 comparison; the selection is single-shot")
    inc, base = _per_task(inc_scores), _per_task(base_scores)
    for name, sc in (("H_inc", inc), ("H_base", base)):
        if sorted(sc) != val:
            raise ValueError(f"{name} scores must cover exactly the val tasks")
        if any(v is None for v in sc.values()):
            raise ValueError(f"{name}: unresolved infra_error on val; rerun the paired slot first")
    delta = math.fsum(inc[t] - base[t] for t in val) / len(val)
    promoted = delta > DELTA_VAL_MIN
    audit.record("val", "c1_selection", [inc_version_id, base_version_id])
    return {"selected_version_id": inc_version_id if promoted else base_version_id,
            "promoted": promoted, "delta_val": delta, "val_access_count": audit.access_count("val")}


# ---------------------------------------------------------------------------------------------
# sealed test report
# ---------------------------------------------------------------------------------------------

def _acquire_lock(lock_path: Path, meta: Mapping) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        raise RuntimeError(f"test split already unblinded ({lock_path}); it is evaluated once")
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps({**meta, "at_utc": _utc()}, sort_keys=True))


def _scored(runs: Iterable[Mapping], theta: float) -> dict[str, dict[tuple[str, int], dict]]:
    """arm -> slot -> {Y, C, infra, category, rec}; a later record of a slot replaces an earlier."""
    out: dict[str, dict[tuple[str, int], dict]] = {}
    for r in runs:
        v = classify_trial(r, theta_gw_max=theta)
        c = r.get("C_star")
        out.setdefault(r["arm"], {})[(str(r["task_id"]), int(r.get("seed", 0)))] = {
            "Y": v.score, "C": (1 if c == 1 else 0) if v.score is not None else None,
            "infra": v.infra_error, "category": v.category, "reasons": v.reasons, "rec": r}
    return out


def _arm_stats(slots: Mapping[tuple[str, int], dict]) -> dict:
    seeds = sorted({s for _, s in slots})
    tasks = sorted({t for t, _ in slots})
    seed_means = {}
    for s in seeds:
        ys = [v["Y"] for (t, ss), v in slots.items() if ss == s and v["Y"] is not None]
        seed_means[str(s)] = _mean(ys)
    task_means = {t: _mean(v["Y"] for (tt, _), v in slots.items() if tt == t and v["Y"] is not None)
                  for t in tasks}
    vals = [v for v in seed_means.values() if v is not None]
    return {"mean": _mean(y for y in task_means.values() if y is not None),
            "seed_means": seed_means,
            "sigma_seed": statistics.stdev(vals) if len(vals) >= 2 else None,
            "task_means": task_means, "n_slots": len(slots),
            "n_infra": sum(v["infra"] for v in slots.values())}


def c_star_decomposition(base: Mapping, star: Mapping) -> dict:
    """Exact split of the mean paired difference over resolved slots into ``C*``-flip slots and
    non-flip (partial-progress) slots, computed in exact rational arithmetic."""
    pairs = [k for k in sorted(set(base) & set(star))
             if base[k]["Y"] is not None and star[k]["Y"] is not None]
    n = len(pairs)
    if not n:
        return {"n_pairs": 0, "delta_Y_total": None, "delta_Y_c_star_flip": None,
                "delta_Y_partial_non_flip": None, "identity_exact": True}
    d = {k: Fraction(star[k]["Y"]) - Fraction(base[k]["Y"]) for k in pairs}
    up = [k for k in pairs if base[k]["C"] == 0 and star[k]["C"] == 1]
    down = [k for k in pairs if base[k]["C"] == 1 and star[k]["C"] == 0]
    flip = set(up) | set(down)
    total = sum(d.values(), Fraction(0)) / n
    f = sum((d[k] for k in flip), Fraction(0)) / n
    nf = sum((d[k] for k in pairs if k not in flip), Fraction(0)) / n
    exact = total == f + nf
    if not exact:                                   # cannot happen; guards future edits
        raise AssertionError("C* flip decomposition does not sum to the total")
    return {"n_pairs": n, "n_flip_gain": len(up), "n_flip_loss": len(down),
            "delta_Y_total": float(total), "delta_Y_c_star_flip": float(f),
            "delta_Y_c_star_flip_gain": float(sum((d[k] for k in up), Fraction(0)) / n),
            "delta_Y_c_star_flip_loss": float(sum((d[k] for k in down), Fraction(0)) / n),
            "delta_Y_partial_non_flip": float(nf), "identity_exact": exact,
            "exact_fractions": {"total": str(total), "flip": str(f), "non_flip": str(nf)}}


def win_loss(base: Mapping, star: Mapping) -> dict:
    per = {}
    for t in sorted({t for t, _ in base} | {t for t, _ in star}):
        np_ = nm = 0
        for (tt, s), b in base.items():
            if tt != t or (tt, s) not in star:
                continue
            ys, yb = star[(tt, s)]["Y"], b["Y"]
            if ys is None or yb is None:
                continue
            np_ += ys > yb
            nm += ys < yb
        per[t] = {"n_plus": np_, "n_minus": nm}
    return {"per_task": per, "n_plus": sum(v["n_plus"] for v in per.values()),
            "n_minus": sum(v["n_minus"] for v in per.values())}


def cluster_bootstrap(task_delta: Mapping[str, float], cluster_of: Mapping[str, str], *,
                      n_boot: int = 2000, seed: int = 0, alpha: float = 0.05,
                      g_expected: int = G_TEST_CONSERVATIVE) -> dict:
    """Percentile interval of the mean per-task difference resampling whole clusters."""
    clusters: dict[str, list[float]] = {}
    for t, d in task_delta.items():
        if d is not None:
            clusters.setdefault(cluster_of.get(t, f"unclustered:{t}"), []).append(d)
    names = sorted(clusters)
    per_cluster = {c: {"n_tasks": len(clusters[c]), "net_diff_sum": math.fsum(clusters[c]),
                       "net_diff_mean": math.fsum(clusters[c]) / len(clusters[c])} for c in names}
    g = len(names)
    rng = random.Random(seed)
    stats = []
    for _ in range(n_boot if g else 0):
        pick = [names[rng.randrange(g)] for _ in range(g)]
        s = math.fsum(math.fsum(clusters[c]) for c in pick)
        n = sum(len(clusters[c]) for c in pick)
        stats.append(s / n)
    stats.sort()

    def q(p: float) -> float | None:
        if not stats:
            return None
        i = min(len(stats) - 1, max(0, int(math.floor(p * (len(stats) - 1) + 0.5))))
        return stats[i]
    return {"G": g, "G_expected": g_expected, "n_boot": n_boot, "seed": seed,
            "ci": [q(alpha / 2), q(1 - alpha / 2)], "alpha": alpha,
            "point": _mean(d for v in clusters.values() for d in v),
            "per_cluster_raw_net_diff": per_cluster,
            "warning": (f"only G = {g} clusters: the number of clusters is close to the small-sample "
                        "boundary; the bootstrap interval is indicative and all per-cluster raw "
                        "net differences are listed")}


def cost_ledger(runs: Iterable[Mapping], offline_search: Mapping | None = None) -> dict:
    by: dict[str, dict] = {}
    for r in runs:
        c = r.get("cost") or {}
        a = by.setdefault(r["arm"], {k: 0.0 for k in COST_KEYS} | {"n_runs": 0})
        a["n_runs"] += 1
        for k in COST_KEYS:
            v = c.get(k)
            if isinstance(v, (int, float)) and math.isfinite(v):
                a[k] = max(a[k], float(v)) if k == "peak_ram_mb" else a[k] + float(v)
    out = {"arms": by}
    if offline_search is not None:
        off = {k: float(offline_search.get(k, 0.0) or 0.0) for k in COST_KEYS}
        out["offline_search"] = off
        if "H_star" in by:
            tot = {k: (max(by["H_star"][k], off[k]) if k == "peak_ram_mb" else by["H_star"][k] + off[k])
                   for k in COST_KEYS}
            out["H_star_including_offline_search"] = tot
    return out


def mirror_rank(rec: Mapping) -> tuple:
    """Public-mirror ranking key of one run (higher is better): certified tier prefix length,
    then the number of certified components. Uses only the run's own mirror vector ``m``."""
    m = rec.get("m") or []
    prefix = 0
    for v in m:
        if v != 1:
            break
        prefix += 1
    return prefix, sum(1 for v in m if v == 1)


def best_of_k(base_slots: Mapping[tuple[str, int], dict], k_max: int) -> dict:
    """Non-blind public-mirror ``Best-of-k(H_base)``: per task, the ``H_base`` attempt with the
    best public mirror rank among ``k`` seeds (ties: lowest seed) is submitted; averaged over
    all ``k``-subsets of the available seeds."""
    tasks: dict[str, list[tuple[int, dict]]] = {}
    for (t, s), v in base_slots.items():
        if v["Y"] is not None:
            tasks.setdefault(t, []).append((s, v))
    curve = {}
    for k in range(1, k_max + 1):
        means = []
        for t, att in sorted(tasks.items()):
            att.sort(key=lambda x: x[0])
            if len(att) < k:
                continue
            ys = []
            for sub in itertools.combinations(att, k):
                best = max(sub, key=lambda x: (mirror_rank(x[1]["rec"]), -x[0]))
                ys.append(best[1]["Y"])
            means.append(_mean(ys))
        curve[str(k)] = {"mean": _mean(means), "n_tasks": len(means)}
    return curve


def build_sealed_test_report(runs: Iterable[Mapping], *, selected_version_id: str,
                             h_star_is_base: bool, cluster_of: Mapping[str, str],
                             hard_cluster_of: Mapping[str, str] | None = None,
                             test_task_ids: Iterable[str] | None = None,
                             out_dir: str | Path, lock_path: str | Path | None = None,
                             r_min: int = R_MIN, offline_search_cost: Mapping | None = None,
                             n_boot: int = 2000, boot_seed: int = 0, k_max: int | None = None,
                             theta_gw_max: float = THETA_GW_MAX) -> dict:
    """Sealed ``test`` report (JSON + Markdown). ``runs`` carry ``arm`` in ``H_raw / H_base /
    H_star`` (plus optional ``manual_instructions``), ``task_id``, ``seed``, ``Y_star``,
    ``C_star``, ``m``, ``cost`` and the infra fields of ``infra_ledger``."""
    runs = [dict(r) for r in runs]
    if h_star_is_base:
        if any(r["arm"] == "H_star" for r in runs):
            raise ValueError("H* = H_base: H_star runs are the H_base runs, do not pass both")
        runs += [{**r, "arm": "H_star", "reused_from": "H_base"} for r in runs if r["arm"] == "H_base"]
    test_ids = set(test_task_ids) if test_task_ids is not None else None
    for arm in REPORT_ARMS:
        seeds = {int(r.get("seed", 0)) for r in runs if r["arm"] == arm}
        if len(seeds) < r_min:
            raise ValueError(f"{arm}: sealed test needs R >= {r_min} seeds, got {len(seeds)}")
    if test_ids is not None:
        foreign = sorted({str(r["task_id"]) for r in runs} - test_ids)
        if foreign:
            raise ValueError(f"non-test tasks in the sealed test report: {foreign}")
    out = Path(out_dir)
    lock = Path(lock_path) if lock_path is not None else out / LOCK_NAME
    _acquire_lock(lock, {"selected_version_id": selected_version_id})

    sc = _scored(runs, theta_gw_max)
    arms = {a: _arm_stats(sc.get(a, {})) for a in sc}
    base, star, raw = sc["H_base"], sc["H_star"], sc["H_raw"]
    task_delta = {t: (arms["H_star"]["task_means"].get(t) - arms["H_base"]["task_means"][t])
                  if arms["H_base"]["task_means"].get(t) is not None
                  and arms["H_star"]["task_means"].get(t) is not None else None
                  for t in arms["H_base"]["task_means"]}
    m_raw, m_base, m_star = arms["H_raw"]["mean"], arms["H_base"]["mean"], arms["H_star"]["mean"]
    attribution = {
        "H_raw": m_raw, "H_base": m_base, "H_star": m_star,
        "delta_raw_to_base": None if None in (m_raw, m_base) else m_base - m_raw,
        "delta_base_to_star": None if None in (m_base, m_star) else m_star - m_base,
        "claimed_automated_credit": "H_base -> H_star only",
        "note": "H_raw -> H_base is the fixed hand-designed kernel, not automated search credit"}

    infra_rows = [{"arm": a, "task_id": t, "seed": s, "category": v["category"],
                   "reasons": v["reasons"], "exit_code": v["rec"].get("exit_code")}
                  for a, slots in sorted(sc.items()) for (t, s), v in sorted(slots.items())
                  if v["infra"]]
    dw = delta_worst({k: v["Y"] for k, v in star.items()}, {k: v["Y"] for k, v in base.items()})

    baselines: dict = {}
    for b in BASELINE_ARMS:
        if b in arms:
            baselines[b] = {"mean": arms[b]["mean"],
                            "delta_vs_H_base": None if None in (arms[b]["mean"], m_base)
                            else arms[b]["mean"] - m_base}
        else:
            baselines[b] = {"status": "not_run"}
    n_seeds = len({s for _, s in base})
    kk = k_max or n_seeds
    tok = lambda a: sum(float((r.get("cost") or {}).get(k, 0) or 0)  # noqa: E731
                        for r in runs if r["arm"] == a for k in ("input_tokens", "output_tokens"))
    t_base, t_star = tok("H_base"), tok("H_star")
    k_eq = (t_star / t_base) if t_base > 0 else None
    baselines["best_of_k_H_base"] = {
        "selection": "non-blind public mirror rank (m vector) over H_base attempts",
        "curve": best_of_k(base, kk), "token_matched_k": k_eq,
        "k_available": n_seeds,
        "note": (None if k_eq is None or k_eq <= n_seeds else
                 f"token-matched k = {k_eq:.2f} exceeds the {n_seeds} archived H_base attempts; "
                 "extra H_base attempts are required for that point")}

    rep = {"schema_version": 1, "split": "test", "selected_version_id": selected_version_id,
           "h_star_is_base": h_star_is_base, "seeds": n_seeds,
           "three_stage_attribution": attribution,
           "multi_seed": {a: {k: v for k, v in s.items() if k != "task_means"} for a, s in arms.items()},
           "seed_noise_floor": {"sigma_seed": {a: s["sigma_seed"] for a, s in arms.items()},
                                "advisory_reference_only": True},
           "win_loss": win_loss(base, star),
           "per_task_delta": task_delta,
           "cluster_bootstrap": {
               "conservative": cluster_bootstrap(task_delta, cluster_of, n_boot=n_boot,
                                                 seed=boot_seed, g_expected=G_TEST_CONSERVATIVE),
               "hard": cluster_bootstrap(task_delta, hard_cluster_of, n_boot=n_boot, seed=boot_seed,
                                         g_expected=G_TEST_HARD) if hard_cluster_of else None},
           "c_star_decomposition": c_star_decomposition(base, star),
           "cost_ledger": cost_ledger(runs, offline_search_cost),
           "infra_error": {"table": infra_rows, "n": len(infra_rows), "delta_worst": dw},
           "honest_baselines": baselines,
           "raw_arm_present": bool(raw)}
    out.mkdir(parents=True, exist_ok=True)
    (out / "test_report.json").write_text(json.dumps(rep, indent=1, sort_keys=True, default=str))
    (out / "test_report.md").write_text(render_test_md(rep))
    return rep


def _f(v) -> str:
    return "n/a" if v is None else f"{v:.4f}" if isinstance(v, float) else str(v)


def render_test_md(rep: Mapping) -> str:
    a = rep["three_stage_attribution"]
    L = [f"# sealed test report ({rep['seeds']} seeds, selected {rep['selected_version_id']})", "",
         "## 1. three-stage attribution", "", "| arm | mean Y* |", "|---|---|"]
    L += [f"| {k} | {_f(a[k])} |" for k in ("H_raw", "H_base", "H_star")]
    L += ["", f"H_raw -> H_base: {_f(a['delta_raw_to_base'])} (fixed kernel, not claimed). "
          f"H_base -> H_star: {_f(a['delta_base_to_star'])} (claimed automated credit).", "",
          "## 2. multi-seed means and seed noise floor (advisory reference)", "",
          "| arm | seed means | sigma_seed |", "|---|---|---|"]
    for arm, s in rep["multi_seed"].items():
        sm = ", ".join(f"{k}: {_f(v)}" for k, v in s["seed_means"].items())
        L.append(f"| {arm} | {sm} | {_f(s['sigma_seed'])} |")
    w = rep["win_loss"]
    L += ["", f"## 3. per-task wins / losses (H_star vs H_base): n_plus {w['n_plus']}, "
          f"n_minus {w['n_minus']}", "", "| task | n_plus | n_minus |", "|---|---|---|"]
    L += [f"| {t} | {v['n_plus']} | {v['n_minus']} |" for t, v in w["per_task"].items()]
    cb = rep["cluster_bootstrap"]["conservative"]
    L += ["", "## 4. cluster bootstrap", "", f"> {cb['warning']}", "",
          f"G = {cb['G']} (expected {cb['G_expected']}), point {_f(cb['point'])}, "
          f"{int((1 - cb['alpha']) * 100)}% interval [{_f(cb['ci'][0])}, {_f(cb['ci'][1])}]", "",
          "| cluster | tasks | raw net diff (sum) | mean |", "|---|---|---|---|"]
    L += [f"| {c} | {v['n_tasks']} | {_f(v['net_diff_sum'])} | {_f(v['net_diff_mean'])} |"
          for c, v in cb["per_cluster_raw_net_diff"].items()]
    h = rep["cluster_bootstrap"].get("hard")
    if h:
        L += ["", f"hard clusters: G = {h['G']}, interval [{_f(h['ci'][0])}, {_f(h['ci'][1])}]"]
    d = rep["c_star_decomposition"]
    L += ["", "## 5. C* flip vs non-flip decomposition", "",
          f"total {_f(d['delta_Y_total'])} = C* flip {_f(d['delta_Y_c_star_flip'])} + partial "
          f"progress {_f(d['delta_Y_partial_non_flip'])} (exact: {d['identity_exact']}; "
          f"{d['n_pairs']} paired slots)", "", "## 6. raw cost ledger", "",
          "| arm | " + " | ".join(COST_KEYS) + " |", "|---" * (len(COST_KEYS) + 1) + "|"]
    cl = rep["cost_ledger"]
    rows = dict(cl["arms"])
    if "offline_search" in cl:
        rows["offline_search"] = cl["offline_search"]
        rows["H_star incl. offline search"] = cl["H_star_including_offline_search"]
    L += [f"| {k} | " + " | ".join(_f(v.get(c)) for c in COST_KEYS) + " |" for k, v in rows.items()]
    ie = rep["infra_error"]
    L += ["", f"## 7. infra_error ({ie['n']})", "", "| arm | task | seed | category |", "|---|---|---|---|"]
    L += [f"| {r['arm']} | {r['task_id']} | {r['seed']} | {r['category']} |" for r in ie["table"]]
    L += ["", f"delta_observed {_f(ie['delta_worst']['delta_observed'])}, delta_worst "
          f"{_f(ie['delta_worst']['delta_worst'])} ({ie['delta_worst']['n_unresolved']} unresolved)",
          "", "## 8. honest baselines", ""]
    for k, v in rep["honest_baselines"].items():
        L.append(f"- {k}: {json.dumps(v, sort_keys=True, default=str)}")
    return "\n".join(L) + "\n"
