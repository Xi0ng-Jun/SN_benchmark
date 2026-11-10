"""Holdout curve: the seed and every adopted node on questions the optimisation never used.

The validation Δ of an adopted node is measured on the validation set the run selected
on, so a cumulative validation curve is optimistic. This measures the seed and the
lineage nodes (k=1) on the input's questions outside the iteration's pool, outside the
sealed selection and outside any other pool named by the caller, and reports, per node,
the paired Δ against the seed with a paper-level bootstrap 95% interval.

Measurements go through the iteration's ordinary subset path (scope ``subset:holdout``)
with deterministic request keys, so a rerun replays finished chunks instead of measuring
them again. It writes only new scope / execution rows; the run's state is only read.
It is an observation, not the run's pre-registered endpoint (``v4 test``).
"""
from __future__ import annotations

import random
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from .. import ledger_io
from ..adapter import Version
from . import decide, endpoint

OUT_DIR = "holdout"
CHUNK = 160
BOOTSTRAP = 2000


def _pool(protocol: dict) -> set:
    split = (protocol or {}).get("splits") or {}
    return set(split.get("pool") or ()) | set(split.get("search") or ()) | set(split.get("validation") or ())


def nodes(state: dict, mode: str = "lineage") -> list[dict]:
    """The seed and the adopted nodes of a finished or running run (``endpoint.node_set``)."""
    from .loop import final_version
    versions = {h: Version(content_hash=h, metadata=m) for h, m in state["versions"].items()}
    lineage = state["lineage"]
    return endpoint.node_set(lineage, versions, mode, final_version(lineage, state.get("ablation")))


def cases(service, iteration_id: str, exclude_iterations=()) -> tuple[list[str], dict]:
    """Holdout questions and case → paper; refuses an unfrozen split."""
    tree = service.iteration(iteration_id)
    protocol = tree.get("protocol") or {}
    split = protocol.get("splits") or {}
    if split.get("status") == "pending":
        raise ValueError("the iteration split is not frozen yet")
    excluded = _pool(protocol) | set(split.get("sealed") or ())
    for other in exclude_iterations:
        other_protocol = service.iteration(other).get("protocol") or {}
        if other_protocol.get("input") != protocol.get("input"):
            raise ValueError(f"iteration {other} uses a different input")
        excluded |= _pool(other_protocol)
    partitions = service.plans.inputs.describe(protocol["input"]["input_id"])["partitions"]
    paper = {c: p["partition_id"] for p in partitions for c in p["case_ids"]}
    # Whole papers only: a paper with any excluded question is excluded entirely.
    blocked = {paper[c] for c in excluded if c in paper}
    out = sorted(c for c, pid in paper.items() if pid not in blocked)
    return out, paper


def bootstrap(diffs: dict, paper: dict, *, resamples: int = BOOTSTRAP, seed: int = 0) -> Optional[list]:
    """Two-sided 95% percentile interval of the mean paired difference, resampling papers."""
    groups: dict = {}
    for case, d in diffs.items():
        groups.setdefault(paper.get(case, case), []).append(d)
    units = list(groups.values())
    if len(units) < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        total = count = 0
        for _ in units:
            unit = units[rng.randrange(len(units))]
            total += sum(unit)
            count += len(unit)
        means.append(total / count)
    means.sort()
    return [means[int(0.025 * resamples)], means[min(resamples - 1, int(0.975 * resamples))]]


def measure(adapter, node_list: list[dict], ids: list[str], *, chunk: int = CHUNK, workers: int = 3) -> dict:
    """candidate id → its k=1 result on *ids*, chunked; chunks of every node run concurrently."""
    chunks = [ids[i:i + chunk] for i in range(0, len(ids), chunk)]
    jobs = [(node["candidate_id"], part) for node in node_list for part in chunks]

    def one(job):
        cid, part = job
        return cid, adapter.evaluate_subset(adapter.version_for(cid), None, "holdout", part, 1)[0]

    out: dict = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(jobs))), thread_name_prefix="v4-holdout") as pool:
        for cid, result in pool.map(one, jobs):
            out.setdefault(cid, []).append(result)
    return {cid: decide.combine(*parts) for cid, parts in out.items()}


def report(node_list: list[dict], results: dict, ids: list[str], paper: dict, *, lineage: list[dict],
           ablation: Optional[dict], metric: Optional[str], safeguards=None, final: Optional[str] = None) -> dict:
    seed = results[node_list[0]["candidate_id"]]
    cumulative = endpoint._cumulative_val(lineage, ablation)
    families = {c: str(paper.get(c, c)) for c in ids}
    points = []
    for node in node_list:
        res = results[node["candidate_id"]]
        scored = [c for c in ids if c in res.per_task_scores]
        point = dict(node, score=statistics.mean(res.per_task_scores[c] for c in scored) if scored else None,
                     scored=len(scored), delta=None, se=None, ci95=None, n=None, dropped=None,
                     delta_val_cumulative=cumulative.get(node["version"]), final=node["version"] == final)
        if node["index"]:
            d = decide.paired_delta(res, seed, ids, rerun_var=0.0, families=families)
            both = [c for c in ids if c in res.per_task_scores and c in seed.per_task_scores]
            diffs = {c: res.per_task_scores[c] - seed.per_task_scores[c] for c in both}
            point.update(delta=d["delta"], se=d["se_own"], n=d["n"], dropped=d["dropped"],
                         ci95=bootstrap(diffs, paper))
            if safeguards is not None:
                try:
                    observed = safeguards(res, seed) or {}
                    point["safeguards"] = {k: v.get("value") for k, v in observed.items() if isinstance(v, dict)}
                except Exception as exc:  # noqa: BLE001  (context only)
                    point["safeguards"] = dict(unavailable=type(exc).__name__)
        points.append(point)
    # Per-question scores (private run directory): a later report can restrict to a shared question set.
    per_case = {node["candidate_id"]: {c: results[node["candidate_id"]].per_task_scores.get(c) for c in ids}
                for node in node_list}
    return dict(kind="method-v4.1-holdout", metric=metric, n_cases=len(ids), k=1,
                interval="paper-level percentile bootstrap, 2000 resamples, two-sided 95%",
                note="observation on questions outside the optimisation pool; not the pre-registered endpoint",
                points=points, per_case=per_case, papers={c: paper.get(c) for c in ids})


def render(rep: dict) -> str:
    lines = [f"# Holdout curve ({rep['metric']}, n={rep['n_cases']}, k=1)", "",
             rep["note"] + ".", "",
             "| node | round | kind | score | Δ vs seed | 95% CI | validation Δ (cumulative) | n |",
             "|---|---|---|---|---|---|---|---|"]

    def num(v, sign=False):
        return "-" if v is None else (f"{v:+.4f}" if sign else f"{v:.4f}")
    for p in rep["points"]:
        ci = p.get("ci95")
        mark = " (final)" if p.get("final") else ""
        lines.append(f"| {p['index']} {p['candidate_id'][:8]}{mark} | {p.get('round') or '-'} | {p.get('kind') or 'seed'} | "
                     f"{num(p['score'])} | {num(p['delta'], True)} | "
                     f"{'-' if not ci else f'[{ci[0]:+.4f}, {ci[1]:+.4f}]'} | "
                     f"{num(p.get('delta_val_cumulative'), True)} | {p.get('n') or p['scored']} |")
    return "\n".join(lines) + "\n"


def figure(rep: dict, out: Path) -> bool:
    """Holdout score per node with the seed line and each node's 95% interval; False without matplotlib."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    pts = [p for p in rep["points"] if p.get("score") is not None]
    if not pts:
        return False
    seed = pts[0]["score"]
    fig, ax = plt.subplots(figsize=(4.2, 2.8))
    xs = [p["index"] for p in pts]
    ax.axhline(seed, color="#999999", lw=0.8, zorder=0)
    ax.plot(xs, [p["score"] for p in pts], color="#222222", lw=1.0, zorder=1)
    for x, p in zip(xs, pts):
        if p.get("ci95"):
            ax.vlines(x, seed + p["ci95"][0], seed + p["ci95"][1], color="#888888", lw=0.8, zorder=1)
        hollow = p.get("kind") == "explore"
        color = "#888888" if p["index"] == 0 else "#2f6fde"
        ax.scatter([x], [p["score"]], s=18, zorder=2, facecolors="none" if hollow else color, edgecolors=color)
    ax.set_xlabel("Adopted node (0 = seed)", fontsize=8)
    ax.set_ylabel(f"Holdout {rep.get('metric') or ''} (n={rep['n_cases']})", fontsize=8)
    ax.set_xticks(xs)
    ax.tick_params(labelsize=7)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return True


def write(rep: dict, state_dir: Path) -> dict:
    out = Path(state_dir) / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    ledger_io.atomic_write_json(out / "holdout.json", rep)
    ledger_io.atomic_text(out / "holdout.md", render(rep))
    drawn = figure(rep, out / "holdout.png")
    return dict(json=str(out / "holdout.json"), markdown=str(out / "holdout.md"),
                figure=str(out / "holdout.png") if drawn else None)

