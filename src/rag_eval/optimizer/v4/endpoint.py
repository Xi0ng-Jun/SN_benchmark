"""method-v4 §4.1 endpoint on SN: one test-set evaluation through v2's sealed lineage.

The node set is fixed before any claim: [seed, final] (default) or the seed,
every adopted node and the final (``endpoint_nodes = "lineage"``). The final
version is the pruned version when ablation (§4.2) accepted one, else the
last adopted node. v2 ``SealedService.run_lineage`` claims the sealed study
once (globally per bundle and sealed cases), runs each node k_sealed times on
the same cases and freezes a curve paired with the seed: per node the paired
Δ with its paper-clustered SE and a paper-level bootstrap 95% interval
(``sealed._bootstrap``). This module builds the node set, refuses a second
node set, and turns the curve into the v4 report and figure.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from ..adapter import Version

Z95 = 1.959963984540054
DEPLOY_RULE = ("deploy the final version; if the lower bound of the 95% interval of Δ_test is <= 0, report "
               "'the test set did not confirm an improvement' and still hand over the lineage, its diffs and "
               "the qualified candidates")


def node_set(lineage: list[dict], versions: dict[str, Version], mode: str, final: Optional[str] = None) -> list[dict]:
    """Endpoint nodes in order, seed first; *final* (the pruned version) replaces / follows the last node."""
    final = final or lineage[-1]["version"]
    chosen = list(lineage) if mode == "lineage" else [lineage[0]]
    if final != lineage[0]["version"] and all(n["version"] != final for n in chosen):
        last = lineage[-1]
        chosen.append(dict(version=final, round=last.get("round"), elapsed_s=last.get("elapsed_s"),
                           kind="pruned" if final != last["version"] else last.get("kind")))
    out = []
    for index, node in enumerate(chosen):
        cid = (versions[node["version"]].metadata or {}).get("candidate_id")
        if not cid:
            raise ValueError(f"lineage node {node['version']!r} has no v2 candidate id")
        out.append(dict(index=index, version=node["version"], candidate_id=cid, round=node.get("round"),
                        kind=node.get("kind"), elapsed_s=node.get("elapsed_s")))
    return out


def sealed_service(adapter):
    from rag_eval.workbench.sealed import SealedService
    ops = adapter._require_round_ops("endpoint")
    return SealedService(ops.repository, ops.service, adapter._views())


def study_used(adapter, sealed=None) -> bool:
    """True when this iteration or its sealed study (bundle + sealed cases) was already claimed anywhere."""
    from rag_eval.workbench.sealed import _claimed
    protocol = adapter._protocol()
    ids = (protocol.get("splits") or {}).get("sealed") or []
    study = hashlib.sha256(json.dumps([protocol["input"]["bundle_id"], sorted(ids)]).encode()).hexdigest()
    sealed = sealed or sealed_service(adapter)
    iteration_id, _ = adapter._require_ids()
    with sealed.repository.connect() as conn:
        return _claimed(conn, iteration_id, study)


def evaluate(adapter, nodes: list[dict], *, budget: dict, actor: str, sealed=None) -> dict:
    """Claim (once) and run the sealed lineage of *nodes*; poll until the curve is frozen."""
    from rag_eval.workbench.sealed import node_set_hash
    from ..sn_sealed import _poll
    sealed = sealed or sealed_service(adapter)
    iteration_id, _ = adapter._require_ids()
    cids = [n["candidate_id"] for n in nodes]
    digest = node_set_hash(cids)
    state = sealed.lineage(iteration_id)
    if state is not None and state.get("node_set_hash") != digest:
        raise ValueError(f"the sealed split of {iteration_id!r} was already claimed for another node set")
    if state is None or state.get("status") == "dispatching":
        sealed.run_lineage(iteration_id, budget=budget, actor=actor, confirmed=True, nodes=cids)
    result = _poll(adapter, sealed, iteration_id)
    return dict(result, node_set_hash=digest)


def _cumulative_val(lineage: list[dict], ablation: Optional[dict]) -> dict[str, float]:
    """Validation Δ of each endpoint node against the seed, summed along the lineage (the pruned final adds its check)."""
    out, total = {lineage[0]["version"]: 0.0}, 0.0
    for node in lineage[1:]:
        total += node.get("delta_val") or 0.0
        out[node["version"]] = total
    ab = ablation or {}
    if ab.get("accepted") and ab.get("final"):
        out[ab["final"]] = total + ((ab.get("check") or {}).get("delta") or 0.0)
    return out


def report(result: dict, nodes: list[dict], lineage: list[dict], *, k_val: Optional[int],
           ablation: Optional[dict] = None) -> dict:
    """Δ_test with a two-sided 95% interval (paper bootstrap; z interval alongside), shrinkage, curve points."""
    rows = result.get("curve") or []
    if rows and [r["candidate_id"] for r in rows] != [n["candidate_id"] for n in nodes]:
        raise ValueError("v2 sealed curve nodes differ from the endpoint node set")
    by_version = {n["version"]: n for n in lineage}
    cumulative = _cumulative_val(lineage, ablation)
    points = []
    for node, row in zip(nodes, rows or [{}] * len(nodes)):
        d, se = row.get("delta_vs_seed"), row.get("se_delta")
        z_ci = [d - Z95 * se, d + Z95 * se] if d is not None and se is not None else None
        boot = [row["boot_lower"], row["boot_upper"]] if row.get("boot_lower") is not None else None
        dval = cumulative.get(node["version"])
        points.append(dict(node, score=row.get("score"), status=row.get("status"), delta_test=d, se_test=se,
                           ci95=boot or z_ci, ci95_method="bootstrap" if boot else "normal", ci95_normal=z_ci,
                           boot_units=row.get("boot_units"), boot_unit=row.get("boot_unit"),
                           delta_val_cumulative=dval,
                           shrinkage=(dval - d) if dval is not None and d is not None and node["index"] else None,
                           tier=by_version.get(node["version"], {}).get("tier")))
    final = points[-1] if points else {}
    confirmed = bool(final.get("ci95") and final["ci95"][0] > 0)
    out = dict(kind="method-v4-endpoint", status=result.get("status"), metric=result.get("metric"),
               n=result.get("n"), k=result.get("k"), n_families=result.get("n_families"),
               interval_method=("paper-level percentile bootstrap, 2000 resamples, two-sided 95%"
                                if final.get("ci95_method") == "bootstrap"
                                else "Δ ± 1.96·SE (v2 paper-clustered SE)"),
               delta_test=final.get("delta_test"), ci95=final.get("ci95"), ci95_normal=final.get("ci95_normal"),
               confirmed=confirmed,
               conclusion=("the test set confirms an improvement" if confirmed
                           else "the test set did not confirm an improvement"),
               deploy_rule=DEPLOY_RULE, deploy=final.get("candidate_id"), points=points,
               ablation=dict(status=(ablation or {}).get("status"), accepted=(ablation or {}).get("accepted"),
                             pruned=(ablation or {}).get("pruned")) if ablation else None,
               node_set_hash=result.get("node_set_hash"), claimed_at=result.get("claimed_at"),
               confirmed_by=result.get("confirmed_by"), warnings=[])
    if result.get("k") is not None and k_val is not None and result["k"] != k_val:
        out["warnings"].append(f"test k = iteration k_sealed = {result['k']}, validation k = {k_val}")
    if final and final.get("ci95_method") != "bootstrap":
        out["warnings"].append("no bootstrap interval (fewer than two papers): normal interval used")
    return out


def render(rep: dict) -> str:
    lines = [f"test set: n={rep['n']} k={rep['k']} metric={rep['metric']} status={rep['status']} "
             f"({rep['interval_method']})"]
    for p in rep["points"]:
        ci = p["ci95"]
        ci_text = f"[{ci[0]:+.4f}, {ci[1]:+.4f}]" if ci else "-"
        lines.append(f"  {p['index']}. {p['candidate_id']} {p.get('kind') or ''} round {p['round']}: "
                     f"score {p['score']} Δ_test {p['delta_test']} 95% CI {ci_text} shrinkage {p['shrinkage']}")
    lines.append(f"conclusion: {rep['conclusion']}; deploy {rep['deploy']}")
    lines += [f"warning: {w}" for w in rep["warnings"]]
    return "\n".join(lines)


LABELS = {"zh": dict(x="累计墙钟时间（小时）", x_index="节点", y="测试集 {metric}", seed="种子", interval="95% 区间"),
          "en": dict(x="Cumulative wall clock (h)", x_index="Node", y="Test {metric}", seed="Seed",
                     interval="95% interval")}


def figure(rep: dict, out: str | Path, *, lang: str = "zh", font: Optional[str] = None, width: float = 3.4,
           height: float = 2.5) -> str:
    """Node (or cumulative wall-clock hours) × test score, with the 95% interval of Δ around the seed score."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ..curve_figure import CERTIFIED, GRID, INK, MUTED, SEED_LINE, _cjk_font
    family = _cjk_font(font) if lang == "zh" else None
    if lang == "zh" and family is None:
        lang = "en"
    text = LABELS[lang]
    pts = [p for p in rep["points"] if p.get("score") is not None]
    if not pts:
        raise ValueError("the endpoint has no measured point")
    seed_score = pts[0]["score"] if pts[0]["index"] == 0 else None
    timed = all(p.get("elapsed_s") is not None or p["index"] == 0 for p in pts)
    xs = [((p.get("elapsed_s") or 0.0) / 3600 if timed else p["index"]) for p in pts]
    with matplotlib.rc_context({"font.family": family} if family else {}):
        fig, ax = plt.subplots(figsize=(width, height))
        if seed_score is not None:
            ax.axhline(seed_score, color=SEED_LINE, lw=0.8, zorder=0)
        ax.plot(xs, [p["score"] for p in pts], color=INK, lw=1.0, zorder=1)
        for x, p in zip(xs, pts):
            if p["index"] and p.get("ci95") and seed_score is not None:
                ax.vlines(x, seed_score + p["ci95"][0], seed_score + p["ci95"][1], color=MUTED, lw=0.8, zorder=1)
            ax.scatter([x], [p["score"]], s=18, zorder=2, color=MUTED if p["index"] == 0 else CERTIFIED)
        ax.set_xlabel(text["x"] if timed else text["x_index"], fontsize=8)
        ax.set_ylabel(text["y"].format(metric=rep.get("metric") or ""), fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(axis="y", color=GRID, lw=0.5)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        fig.tight_layout()
        fig.savefig(out)
        plt.close(fig)
    return lang
