"""method-v4 §4.1 endpoint on SN: one test-set evaluation through v2's sealed lineage.

The node set is fixed before any claim: [seed, final] (default) or every
lineage node (``endpoint_nodes = "lineage"``). v2 ``SealedService.run_lineage``
claims the sealed study once (globally per bundle and sealed cases), runs each
node k_sealed times on the same cases, and freezes a curve paired with the
seed and clustered by paper. This module only builds the node set, refuses a
second node set, and turns the curve into the v4 report.
"""
from __future__ import annotations

import hashlib
import json
from ..adapter import Version

Z95 = 1.959963984540054
DEPLOY_RULE = ("deploy the final version; if the lower bound of the 95% interval of Δ_test is <= 0, report "
               "'the test set did not confirm an improvement' and still hand over the lineage, its diffs and "
               "the qualified candidates")


def node_set(lineage: list[dict], versions: dict[str, Version], mode: str) -> list[dict]:
    chosen = lineage if mode == "lineage" else ([lineage[0], lineage[-1]] if len(lineage) > 1 else lineage[:1])
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


def report(result: dict, nodes: list[dict], lineage: list[dict], *, k_val: int) -> dict:
    """Δ_test with a two-sided 95% interval (v2's paper-clustered SE), shrinkage, curve points."""
    rows = result.get("curve") or []
    if rows and [r["candidate_id"] for r in rows] != [n["candidate_id"] for n in nodes]:
        raise ValueError("v2 sealed curve nodes differ from the endpoint node set")
    by_version = {n["version"]: n for n in lineage}
    points, cumulative = [], 0.0
    cum_by_version = {}
    for node in lineage[1:]:
        cumulative += node.get("delta_val") or 0.0
        cum_by_version[node["version"]] = cumulative
    for node, row in zip(nodes, rows or [{}] * len(nodes)):
        d, se = row.get("delta_vs_seed"), row.get("se_delta")
        ci = [d - Z95 * se, d + Z95 * se] if d is not None and se is not None else None
        dval = cum_by_version.get(node["version"], 0.0 if node["index"] == 0 else None)
        points.append(dict(node, score=row.get("score"), status=row.get("status"), delta_test=d, se_test=se,
                           ci95=ci, delta_val_cumulative=dval,
                           shrinkage=(dval - d) if dval is not None and d is not None else None,
                           tier=by_version.get(node["version"], {}).get("tier")))
    final = points[-1] if points else {}
    confirmed = bool(final.get("ci95") and final["ci95"][0] > 0)
    out = dict(kind="method-v4-endpoint", status=result.get("status"), metric=result.get("metric"),
               n=result.get("n"), k=result.get("k"), n_families=result.get("n_families"),
               se_method="v2 lineage_curve paired SE, clustered by paper; interval Δ ± 1.96·SE (normal)",
               delta_test=final.get("delta_test"), ci95=final.get("ci95"), confirmed=confirmed,
               conclusion=("the test set confirms an improvement" if confirmed
                           else "the test set did not confirm an improvement"),
               deploy_rule=DEPLOY_RULE, deploy=final.get("candidate_id"), points=points,
               node_set_hash=result.get("node_set_hash"), claimed_at=result.get("claimed_at"),
               confirmed_by=result.get("confirmed_by"), warnings=[])
    if result.get("k") is not None and result["k"] != k_val:
        out["warnings"].append(f"test k = iteration k_sealed = {result['k']}, calibration's k_val = {k_val}")
    return out


def render(rep: dict) -> str:
    lines = [f"test set: n={rep['n']} k={rep['k']} metric={rep['metric']} status={rep['status']}"]
    for p in rep["points"]:
        ci = p["ci95"]
        lines.append(f"  {p['index']}. {p['candidate_id']} round {p['round']}: score {p['score']} "
                     f"Δ_test {p['delta_test']} 95% CI {ci} shrinkage {p['shrinkage']}")
    lines.append(f"conclusion: {rep['conclusion']}; deploy {rep['deploy']}")
    lines += [f"warning: {w}" for w in rep["warnings"]]
    return "\n".join(lines)
