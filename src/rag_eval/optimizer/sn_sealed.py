"""Method v3 end step on SN: one sealed evaluation of the whole lineage (§2.5).

The node set is fixed up front: the seed plus every lineage node in append
order, provisional, certified and rolled back alike. Execution and statistics
are the v2 workbench's ``SealedService`` (``rag_eval.workbench.sealed``):
``run_lineage(..., nodes=...)`` claims the sealed split once for exactly this
node set and dispatches each node ``k_sealed`` times on the same cases;
``lineage()`` reports progress and then the frozen curve, each node paired with
the seed, Holm-adjusted p-values and simultaneous Bonferroni intervals.

Once-only lives in v2, not here: one claim row per iteration and per sealed
study, one slot per node x repetition that can be filled once, and a curve
written once and replayed. This module only refuses to mix node sets: a
replay must present the same node-set hash the claim persisted.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from .adapter import EvaluationUnavailable, Version
from .ledger import Lineage, NodeState

# v2 lineage_curve's correction: Holm p-values, Bonferroni (z at alpha/m) intervals.
CORRECTION = "holm-p+bonferroni-interval"
# §2.5 / §6 curve marks: certified solid point, provisional hollow, rollback dashed segment.
STYLE = {NodeState.CERTIFIED: "solid", NodeState.PROVISIONAL: "hollow", NodeState.ROLLED_BACK: "dashed"}
_SETTLED = frozenset({"finished", "unmeasurable"})
_POINT_FIELDS = ("status", "score", "se", "n", "delta_vs_seed", "se_delta", "lower", "upper",
                 "lower_adj", "upper_adj", "p", "p_holm", "holds_holm")


def lineage_node_set(lineage: Lineage, versions: dict[str, Version]) -> list[dict]:
    """Seed + every lineage node in append order, mapped to v2 candidate ids."""
    nodes = lineage.all_nodes()
    if not nodes or nodes[0].parent_hash is not None:
        raise ValueError("sealed lineage needs a lineage whose first node is the seed")
    out = []
    for index, node in enumerate(nodes):
        version = versions.get(node.version_hash)
        if version is None:
            raise ValueError(f"lineage node {index} ({node.version_hash!r}) has no Version")
        cid = (version.metadata or {}).get("candidate_id")
        if not isinstance(cid, str) or not cid:
            raise ValueError(
                f"lineage node {index} ({node.version_hash!r}) carries no v2 candidate_id in "
                "Version.metadata; build SN versions with version_for() or materialize()"
            )
        out.append(dict(index=index, version_hash=node.version_hash, candidate_id=cid,
                        state=node.state.value, style=STYLE[node.state]))
    return out


def _sealed_service(adapter):
    from rag_eval.workbench.sealed import SealedService
    service = adapter._require_round_ops("evaluate_lineage_sealed").service
    views = adapter._run_views if adapter._run_views is not None else adapter._views()
    return SealedService(service.repository, service, views)


def _poll(adapter, sealed, iteration_id: str) -> dict:
    """Read v2 ``lineage()`` until its curve is frozen, on the adapter's eval cadence."""
    deadline = time.monotonic() + adapter._eval_timeout
    while True:
        result = sealed.lineage(iteration_id)
        if result is None:
            raise EvaluationUnavailable(f"sealed lineage of {iteration_id!r} has no claim after dispatch")
        if result.get("status") in _SETTLED:
            return result
        if time.monotonic() >= deadline:
            progress = result.get("progress") or {}
            raise EvaluationUnavailable(
                f"sealed lineage of {iteration_id!r} not settled within {adapter._eval_timeout}s "
                f"(status={result.get('status')!r}, terminal={progress.get('terminal')}/"
                f"{progress.get('slots')}); the claim is kept and a rerun resumes polling"
            )
        time.sleep(adapter._eval_poll_interval)


def _curve(result: dict, nodes: list[dict], digest: str, iteration_id: str) -> dict:
    rows = result.get("curve")
    if rows is not None:
        if [r["candidate_id"] for r in rows] != [n["candidate_id"] for n in nodes]:
            raise ValueError("v2 sealed curve nodes differ from the lineage node set")
        mismatched = [n["index"] for n, r in zip(nodes, rows) if r["frozen_commit"] != n["version_hash"]]
        if mismatched:
            raise ValueError(f"v2 frozen commits differ from lineage version hashes at nodes {mismatched}")
    points = []
    for i, node in enumerate(nodes):
        row = rows[i] if rows is not None else {}
        point = dict(node, k=result["k"])
        point.update({f: row.get(f) for f in _POINT_FIELDS})
        if rows is None:
            point["status"] = result["status"]
        points.append(point)
    return dict(
        kind="v3-sealed-lineage", status=result["status"], metric=result.get("metric"),
        n=result.get("n"), k=result["k"], alpha=result.get("alpha"), z=result.get("z"),
        z_adj=result.get("z_adj"), m=result.get("m"),
        correction=result.get("correction") or CORRECTION, points=points,
        metadata=dict(iteration_id=iteration_id, sealed_claim_id=iteration_id, node_set_hash=digest,
                      created_at=result.get("claimed_at"), confirmed_by=result.get("confirmed_by"),
                      correction=result.get("correction") or CORRECTION,
                      n_families=result.get("n_families")),
    )


def evaluate_lineage_sealed(
    adapter,
    lineage: Lineage,
    versions: dict[str, Version],
    *,
    confirmed: bool,
    budget: dict,
    k: Optional[int] = None,
    actor: Optional[str] = None,
    sealed: Any = None,
) -> dict:
    """Evaluate seed + every lineage node once on the sealed split; return curve data.

    ``confirmed`` must be True, set explicitly by a human operator; anything
    else raises before any claim. ``budget`` and ``k`` go to v2 unchanged
    (``k`` None takes the iteration's pre-registered ``k_sealed``; v2 refuses a
    different value). ``actor`` (default: the adapter's configured actor) is
    recorded by v2 as the confirmer. ``sealed`` injects a SealedService.

    First call claims and dispatches; any later call replays: a dispatch that
    was interrupted resumes only unfilled slots, a dispatched one is only
    polled, a settled one returns the frozen curve. Nothing is measured twice.
    """
    if confirmed is not True:
        raise ValueError("sealed lineage evaluation requires explicit human confirmation (confirmed=True)")
    nodes = lineage_node_set(lineage, versions)
    from rag_eval.workbench.sealed import node_set_hash
    candidate_ids = [n["candidate_id"] for n in nodes]
    digest = node_set_hash(candidate_ids)
    iteration_id, _ = adapter._require_ids()
    actor = actor or adapter._actor()
    sealed = sealed if sealed is not None else _sealed_service(adapter)

    state = sealed.lineage(iteration_id)
    if state is not None:
        if state.get("node_set_hash") != digest:
            raise ValueError(
                f"the sealed split of {iteration_id!r} was already claimed for another node set "
                f"(claimed {state.get('node_set_hash')!r}, lineage {digest!r}); it cannot be re-run"
            )
        if k is not None and k != state.get("k"):
            raise ValueError(f"k={k} differs from the claimed k_sealed={state.get('k')}")
    if state is None or state.get("status") == "dispatching":
        sealed.run_lineage(iteration_id, budget=budget, actor=actor, confirmed=confirmed, k=k, nodes=candidate_ids)
    return _curve(_poll(adapter, sealed, iteration_id), nodes, digest, iteration_id)
