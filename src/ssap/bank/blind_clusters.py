"""Internal blind ``dev`` clusters and the proposer privacy boundary.

``BLIND_COUNT`` of the ``G_dev`` dev clusters are frozen as blind clusters by the same
SHA-256-seeded rule that produced the split (``ssap.targets.alw.splits.blind_clusters``); the
frozen set is checked against the split document. Proposers / implementers only ever see, for a
blind cluster, the aggregate ``{n_samples, n_pass, n_fp, n_tp, clusters_covered}``:

* ``n_samples``: labelled samples evaluated;
* ``n_pass``: samples the checker / probe did not flag;
* ``n_fp``: flagged samples whose label is positive (true solution flagged);
* ``n_tp``: flagged samples whose label is negative;
* ``clusters_covered``: number of distinct blind clusters with at least one sample.

No task id, cluster id, artifact path or log of a blind sample crosses the boundary.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..targets.alw.splits import BLIND_COUNT, blind_clusters as _derive_blind

AGGREGATE_KEYS = ("n_samples", "n_pass", "n_fp", "n_tp", "clusters_covered")


class PrivacyViolation(RuntimeError):
    """An export would reveal a blind-cluster sample."""


def dev_cluster_sizes(split_data: Mapping) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for n in split_data["splits"]["dev"]:
        cid = split_data["cluster_of"][str(n)]
        sizes[cid] = sizes.get(cid, 0) + 1
    return dict(sorted(sizes.items()))


@dataclass(frozen=True)
class BlindSet:
    blind: tuple[str, ...]
    dev_clusters: tuple[str, ...]
    blind_task_ids: frozenset[str]
    visible_task_ids: frozenset[str]

    @property
    def g_dev(self) -> int:
        return len(self.dev_clusters)

    def is_blind(self, cluster_id: str) -> bool:
        return cluster_id in self.blind

    def is_dev(self, cluster_id: str) -> bool:
        return cluster_id in self.dev_clusters


def freeze_blind_clusters(split_data: Mapping, seed: str | None = None) -> BlindSet:
    """Re-derive the blind clusters deterministically and require equality with the split."""
    seed = seed or split_data["seed"]
    sizes = dev_cluster_sizes(split_data)
    derived = _derive_blind(sizes, seed)
    recorded = list(split_data.get("blind_clusters") or [])
    if recorded != derived:
        raise ValueError("split.json blind_clusters differ from the deterministic derivation")
    if len(derived) != BLIND_COUNT:
        raise ValueError(f"expected {BLIND_COUNT} blind clusters")
    dev = split_data["splits"]["dev"]
    ids = split_data["task_ids"]["dev"]
    blind_ids = {ids[i] for i, n in enumerate(dev) if split_data["cluster_of"][str(n)] in derived}
    return BlindSet(tuple(derived), tuple(sizes), frozenset(blind_ids), frozenset(ids) - blind_ids)


def _get(rec: Any, key: str, default=None):
    return rec.get(key, default) if isinstance(rec, Mapping) else getattr(rec, key, default)


def aggregate(records: Iterable[Any]) -> dict[str, int]:
    """Aggregate of ``records`` with fields ``cluster_id``, ``flagged`` (bool) and ``label``
    (``1`` positive / ``0`` negative / anything else = unlabelled, counted only in ``n_samples``)."""
    n = n_pass = n_fp = n_tp = 0
    clusters: set[str] = set()
    for r in records:
        n += 1
        clusters.add(_get(r, "cluster_id"))
        flagged, label = bool(_get(r, "flagged")), _get(r, "label")
        if not flagged:
            n_pass += 1
        elif label == 1:
            n_fp += 1
        elif label == 0:
            n_tp += 1
    return {"n_samples": n, "n_pass": n_pass, "n_fp": n_fp, "n_tp": n_tp,
            "clusters_covered": len(clusters - {None})}


class BlindBoundary:
    """Privacy boundary between the referee (full bank) and proposing / implementing agents."""

    def __init__(self, blind: BlindSet):
        self.blind = blind

    def split(self, records: Iterable[Any]) -> tuple[list, list]:
        vis, bl = [], []
        for r in records:
            (bl if self.blind.is_blind(_get(r, "cluster_id")) else vis).append(r)
        return vis, bl

    def blind_aggregate(self, records: Iterable[Any]) -> dict[str, int]:
        return aggregate(self.split(records)[1])

    def export_for_proposer(self, records: Iterable[Any],
                            fields: Iterable[str] = ("sample_id", "task_id", "cluster_id",
                                                     "flagged", "label")) -> dict:
        """Visible samples in detail, blind samples as one aggregate; checked by ``guard``."""
        vis, bl = self.split(records)
        fields = tuple(fields)
        out = {"visible": [{f: _get(r, f) for f in fields} for r in vis],
               "blind_aggregate": aggregate(bl)}
        self.guard(out, blind_records=bl)
        return out

    def guard(self, payload: Any, blind_records: Iterable[Any] = ()) -> None:
        """Raise ``PrivacyViolation`` if *payload* names a blind task, cluster, sample or path."""
        text = json.dumps(payload, default=str, sort_keys=True)
        needles = set(self.blind.blind_task_ids) | set(self.blind.blind)
        for r in blind_records:
            for key in ("sample_id", "artifact_path", "task_id", "log_path"):
                v = _get(r, key)
                if isinstance(v, str) and v:
                    needles.add(v)
        hits = sorted(n for n in needles if n and n in text)
        if hits:
            raise PrivacyViolation(f"export reveals {len(hits)} blind-cluster identifier(s)")
