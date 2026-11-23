"""Signoff event ledger (``event_ledger.jsonl``) and ``K_idle`` zero-rescue pruning.

Every terminal-gate / repair event ``e`` gets ``delta_Y = Y*(post) - Y*(baseline)`` and a verdict

* ``Rescued``  ``delta_Y > 0``
* ``Neutral``  ``delta_Y == 0`` (including a repair the ratchet rejected, so the archived best stayed)
* ``Damaged``  ``delta_Y < 0``; returned as full diagnostic entries for ``B_dev`` positive
  regression samples or ``eps_k`` widening.

``delta_Y`` is compared with zero exactly; ``FLOAT_REPR_TOL`` only absorbs binary floating-point
representation error of a mean of identical scores (e.g. ``(0.7 + 0.7 + 0.7) / 3 - 0.7``). It is
not a significance threshold.

Pruning: an adopted gate with ``0`` Rescued events in ``K_idle = 2`` consecutive rounds in which it
was active is removed from ``H_inc``.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable, Mapping

VERDICTS = ("Rescued", "Neutral", "Damaged")
K_IDLE = 2
FLOAT_REPR_TOL = 1e-12


def verdict(delta_y: float) -> str:
    if delta_y > FLOAT_REPR_TOL:
        return "Rescued"
    if delta_y < -FLOAT_REPR_TOL:
        return "Damaged"
    return "Neutral"


class EventLedger:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else None
        self.entries: list[dict] = []
        if self.path is not None and self.path.is_file():
            self.entries = [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]

    def append(self, *, round_id: int, candidate_id: str, task_id: str, gate_ids: Iterable[str],
               y_baseline: float, y_post: float, rep_seed: int | None = None,
               rep_results: list | None = None, promote_reason: str | None = None,
               baseline_kind: str = "w_inc_final", diagnostic: Mapping | None = None,
               seed: int | None = None) -> dict:
        d = float(y_post) - float(y_baseline)
        if abs(d) <= FLOAT_REPR_TOL:
            d = 0.0
        e = {"round": int(round_id), "candidate_id": str(candidate_id), "task_id": str(task_id),
             "seed": seed, "gate_ids": sorted(set(gate_ids)), "rep_seed": rep_seed,
             "rep_results": rep_results, "Y_pre": float(y_baseline), "Y_post": float(y_post),
             "baseline_kind": baseline_kind, "delta_Y": d, "verdict": verdict(d),
             "promote_reason": promote_reason, "diagnostic": dict(diagnostic or {})}
        self.entries.append(e)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a") as fh:
                fh.write(json.dumps(e, sort_keys=True) + "\n")
        return e

    def select(self, *, round_id: int | None = None, candidate_id: str | None = None,
               verdict_: str | None = None) -> list[dict]:
        return [e for e in self.entries
                if (round_id is None or e["round"] == round_id)
                and (candidate_id is None or e["candidate_id"] == candidate_id)
                and (verdict_ is None or e["verdict"] == verdict_)]

    def damaged(self, *, round_id: int | None = None, candidate_id: str | None = None) -> list[dict]:
        """Full diagnostic entries of every Damaged event (never aggregated away)."""
        return [dict(e) for e in self.select(round_id=round_id, candidate_id=candidate_id,
                                             verdict_="Damaged")]

    def counts(self, *, round_id: int | None = None, candidate_id: str | None = None) -> dict:
        out = {v: 0 for v in VERDICTS}
        for e in self.select(round_id=round_id, candidate_id=candidate_id):
            out[e["verdict"]] += 1
        return out

    def net_delta(self, *, round_id: int | None = None, candidate_id: str | None = None) -> float:
        return math.fsum(e["delta_Y"] for e in self.select(round_id=round_id, candidate_id=candidate_id))

    def rescues_by_gate(self, round_id: int) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.select(round_id=round_id, verdict_="Rescued"):
            for g in e["gate_ids"]:
                out[g] = out.get(g, 0) + 1
        return out


def update_idle_streaks(streaks: Mapping[str, int], *, round_id: int, active_gates: Iterable[str],
                        ledger: EventLedger, k_idle: int = K_IDLE) -> tuple[dict[str, int], list[str]]:
    """Advance the zero-rescue streak of every gate active in *round_id*; return the new streaks
    and the gates to prune (streak reached *k_idle*). A rescue resets the streak to 0."""
    if k_idle < 1:
        raise ValueError("k_idle must be >= 1")
    rescues = ledger.rescues_by_gate(round_id)
    new: dict[str, int] = {}
    pruned: list[str] = []
    for g in sorted(set(active_gates)):
        n = 0 if rescues.get(g, 0) > 0 else int(streaks.get(g, 0)) + 1
        if n >= k_idle:
            pruned.append(g)
        else:
            new[g] = n
    return new, pruned


def prune_h_inc(h_inc: Mapping, pruned: Iterable[str]) -> dict:
    """Copy of an ``H_inc`` record (``{"version_id", "gates": [...], ...}``) without *pruned*."""
    pruned = set(pruned)
    out = dict(h_inc)
    out["gates"] = [g for g in h_inc.get("gates", []) if g not in pruned]
    if pruned:
        out["pruned"] = sorted(set(h_inc.get("pruned", [])) | pruned)
    return out
