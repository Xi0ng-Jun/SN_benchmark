"""Round x sealed-score curve of a method-v3 run: one projection for the page and the paper.

Joins the points of ``sealed-curve.json`` (one per lineage node, seed first, in
append order) to the round each node was adopted in and to the lineage tree,
so the workbench chart and ``cli curve`` draw the same thing (method-v3 §6):

- ``round``: the round whose ``screen_pass`` record names the node as its
  ``candidate`` (the round it was provisionally adopted in); the seed is round
  0. A node with no such record (a record lost with an unsynced round, or a
  hand-edited state) takes the latest round of the nodes appended before it,
  flagged ``round_inferred``; append order is adoption order, so that is a
  lower bound that keeps the x order.
- ``on_path``: on the main path, seed -> ... -> final pointer, following
  ``parent_hash`` back from the pointer (the last node not rolled back, as
  ``Lineage.current_parent``). Rolled-back nodes are off the path; each keeps
  ``parent_index`` so it is drawn joined to its parent.
- ``slot``/``slots``: position among the nodes of the same round, in append
  order, for the small horizontal offset.
- ``y_lower``/``y_upper``: the multiplicity-corrected interval on the score
  axis. ``lower_adj``/``upper_adj`` are on the paired difference to the seed
  (v2 ``lineage_curve``), so they are drawn as ``seed score + bound``: the
  whisker clears the seed line exactly when the corrected bound is above 0.

Presentation only: nothing here changes or re-derives a sealed statistic.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

from . import ledger_io

SEALED_FILE = "sealed-curve.json"
_COPIED = ("index", "version_hash", "candidate_id", "state", "style", "status", "score", "se", "delta_vs_seed",
           "lower_adj", "upper_adj", "p_holm", "holds_holm")


def adoption_rounds(rounds: Iterable[dict]) -> dict[str, int]:
    """version hash -> round of its ``screen_pass`` record; the first non-superseded one wins."""
    found: dict[str, tuple[bool, int]] = {}
    for rnd in sorted(rounds, key=lambda r: r["round_id"]):
        for c in rnd.get("candidates") or []:
            cand = c.get("candidate")
            if c.get("verdict") != "screen_pass" or not cand:
                continue
            superseded = bool(c.get("superseded"))
            if cand not in found or (found[cand][0] and not superseded):
                found[cand] = (superseded, int(rnd["round_id"]))
    return {h: r for h, (_, r) in found.items()}


def main_path(lineage: list[dict]) -> list[str]:
    """Version hashes seed -> final pointer, following ``parent_hash`` over nodes not rolled back."""
    by_hash = {n["version_hash"]: n for n in lineage}
    pointer = next((n for n in reversed(lineage) if n.get("state") != "rolled_back"), None)
    path, cursor = [], pointer
    while cursor is not None and cursor.get("state") != "rolled_back" and cursor["version_hash"] not in path:
        path.append(cursor["version_hash"])
        cursor = by_hash.get(cursor.get("parent_hash")) if cursor.get("parent_hash") else None
    return path[::-1]


def project_curve(sealed: Optional[dict], lineage: list[dict], rounds: Iterable[dict]) -> Optional[dict]:
    """The curve the chart and the figure draw; None without sealed points."""
    if not sealed or not sealed.get("points"):
        return None
    points_raw = sealed["points"]
    by_hash = {n["version_hash"]: n for n in lineage}
    adopted = adoption_rounds(rounds)
    on_path = set(main_path(lineage))
    position = {p.get("version_hash"): i for i, p in enumerate(points_raw)}
    seed_score = points_raw[0].get("score")

    points: list[dict] = []
    latest = 0
    for i, raw in enumerate(points_raw):
        point = {k: raw.get(k) for k in _COPIED}
        point["index"] = i if raw.get("index") is None else raw["index"]
        node = by_hash.get(raw.get("version_hash")) or {}
        parent = node.get("parent_hash")
        point["parent_hash"] = parent
        point["parent_index"] = position.get(parent) if parent else None
        point["state"] = point["state"] or node.get("state")
        if i == 0:
            point["round"], point["round_inferred"] = 0, False
        elif raw.get("version_hash") in adopted:
            point["round"], point["round_inferred"] = adopted[raw["version_hash"]], False
        else:
            point["round"], point["round_inferred"] = latest, True
        latest = max(latest, point["round"])
        point["on_path"] = raw.get("version_hash") in on_path
        lo, hi = raw.get("lower_adj"), raw.get("upper_adj")
        point["y_lower"] = seed_score + lo if seed_score is not None and lo is not None else None
        point["y_upper"] = seed_score + hi if seed_score is not None and hi is not None else None
        points.append(point)

    slots: dict[int, int] = {}
    for point in points:
        point["slot"] = slots.get(point["round"], 0)
        slots[point["round"]] = point["slot"] + 1
    for point in points:
        point["slots"] = slots[point["round"]]

    return dict(
        metric=sealed.get("metric"), n=sealed.get("n"), k=sealed.get("k"), m=sealed.get("m"),
        alpha=sealed.get("alpha"), z_adj=sealed.get("z_adj"), correction=sealed.get("correction"),
        status=sealed.get("status"), seed_score=seed_score,
        max_round=max(p["round"] for p in points),
        path=[p["index"] for p in points if p["on_path"]],
        points=points,
    )


def _rounds_jsonl(path: Path) -> list[dict]:
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                break   # torn tail, as ledger_io.sync_rounds_jsonl drops it
    return out


def run_rounds(state: dict, rounds_jsonl: Iterable[dict] = ()) -> list[dict]:
    """Every round the run recorded: rounds.jsonl, then state.json's rounds and open round by round_id."""
    current = (state.get("current_round") or {}).get("round")
    merged: dict[int, dict] = {}
    for rnd in list(rounds_jsonl) + list(state.get("rounds") or []) + ([current] if current else []):
        merged.setdefault(int(rnd["round_id"]), rnd)
    return [merged[k] for k in sorted(merged)]


def read_curve(state_dir: str | Path) -> Optional[dict]:
    """Project a run directory's sealed curve; None before ``cli sealed --confirm`` wrote it."""
    state_dir = Path(state_dir)
    sealed_path = state_dir / SEALED_FILE
    if not sealed_path.exists():
        return None
    sealed: Any = ledger_io.read_json(sealed_path)
    state = ledger_io.read_json(state_dir / ledger_io.STATE_FILE)
    rounds = run_rounds(state, _rounds_jsonl(state_dir / ledger_io.ROUNDS_FILE))
    return project_curve(sealed, state.get("lineage") or [], rounds)
