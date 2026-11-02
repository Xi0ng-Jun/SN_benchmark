"""Read model of one method-v3 run, for the workbench's automatic-run page.

Projects the files of a run directory (``state.json``, ``ledger.json``,
``cli-run.json``, ``sealed-curve.json``) into what the page shows: the lineage
grouped into segments, the open round's cards, the finished rounds and the
evidence ledger. Pure apart from reading those files.

No model clients, no SN imports. The projection carries counts, never task ids:
partition membership stays in ``state.json``.

Liveness is a held file lock, not a heartbeat: a round can legitimately write
nothing for hours, while a dead process releases its lock at once.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from . import ledger_io

VIEW_SCHEMA_VERSION = 1
LOCK_FILE = "run.lock"
IDENTITY_FILE = "cli-run.json"
SEALED_FILE = "sealed-curve.json"

# Literal limits of loop.HaltTracker.halt_reason.
MAX_NO_SCREEN_PASS = 2
MAX_REJECTED_CONFIRMATIONS = 2

_CONFIG_SHOWN = ("mde", "sigma_d", "T", "ladder_c", "K_max", "n_fresh", "z_screen", "z_confirm", "b_cards",
                 "k_screen", "k_smoke", "safeguard_bounds", "max_confirmations", "predicate_coverage_floor",
                 "analyst_cards", "max_consecutive_run_failures")
_ATTEMPT_COPIED = ("round_id", "card_index", "card_ref", "verdict", "reason", "parent", "candidate", "rebased_from",
                   "smoke_verdict", "delta_val", "se_val", "score_val", "best_before", "ladder_m", "val_pool_size",
                   "n_unpaired", "screen_evaluations", "timings", "has_predicate", "resumed", "halted")


class RunLocked(Exception):
    """Another process holds this run directory."""


@contextmanager
def hold_run_lock(state_dir: str | Path, *, attempts: int = 5, wait: float = 0.2) -> Iterator[None]:
    """Hold the run directory for this process; a brief retry covers ``run_alive`` probes."""
    path = Path(state_dir) / LOCK_FILE
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        for attempt in range(attempts):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if attempt == attempts - 1:
                    raise RunLocked(f"another process is running {state_dir}") from None
                time.sleep(wait)
        yield
    finally:
        os.close(fd)


def run_alive(state_dir: str | Path) -> bool:
    """True while some process holds the run lock."""
    try:
        fd = os.open(Path(state_dir) / LOCK_FILE, os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        return False
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------

def _lower(delta: Optional[float], se: Optional[float], z: float) -> Optional[float]:
    return delta - z * se if delta is not None and se is not None else None


def _titles(state: dict) -> dict[str, str]:
    """Card reference → description, from everything that still names a card."""
    titles: dict[str, str] = {}
    cards = list(state.get("version_cards", {}).values())
    cards += ((state.get("current_round") or {}).get("cards") or [])
    for card in cards:
        if card.get("ref"):
            titles[card["ref"]] = card.get("failure_mode_desc") or ""
    for entry in state.get("history", []):
        if entry.get("card_ref"):
            titles.setdefault(entry["card_ref"], entry.get("title") or "")
    return titles


def _attempt(raw: dict, round_id: int, *, titles: dict, candidate_ids: dict, z_screen: float) -> dict:
    out = {k: raw.get(k) for k in _ATTEMPT_COPIED}
    out["round_id"] = round_id
    out["title"] = raw.get("title") or titles.get(raw.get("card_ref")) or raw.get("card_ref")
    out["candidate_id"] = raw.get("candidate_id") or candidate_ids.get(raw.get("candidate"))
    out["lower_val"] = _lower(raw.get("delta_val"), raw.get("se_val"), z_screen)
    best, m = raw.get("best_before"), raw.get("ladder_m")
    out["threshold"] = best + (m or 0.0) if best is not None else None
    out["key"] = f"{round_id}:{raw.get('card_index')}"
    return out


def _confirmation(raw: dict, round_id: int, z_confirm: float) -> dict:
    delta, se = raw.get("delta_fresh"), raw.get("se_fresh")
    cumulative = raw.get("cumulative_delta_val")
    cost_start, cost_end = raw.get("cost_start"), raw.get("cost_end")
    return dict(
        round_id=round_id, verdict=raw.get("verdict"), delta_fresh=delta, se_fresh=se,
        lower=_lower(delta, se, z_confirm), upper=(delta + z_confirm * se) if delta is not None and se is not None else None,
        n_fresh=raw.get("n_fresh"), n_unpaired=raw.get("n_unpaired"), k_fresh=raw.get("k_fresh"),
        fresh_batch_index=raw.get("fresh_batch_index"), segment_steps=raw.get("segment_steps"),
        cumulative_delta_val=cumulative,
        shrinkage=(cumulative - delta) if cumulative is not None and delta is not None else None,
        safeguards_end=raw.get("safeguards_end") or {},
        cost_start=cost_start, cost_end=cost_end,
        cost_change=(cost_end - cost_start) if cost_start is not None and cost_end is not None else None,
        attribution=raw.get("attribution"), val_pool_size=raw.get("val_pool_size"),
        ladder_m=raw.get("ladder_m"), best_score=raw.get("best_score"),
    )


def _segments(lineage: list[dict], confirmations: list[dict], attempts: list[dict],
              *, titles: dict, candidate_ids: dict) -> tuple[Optional[dict], list[dict]]:
    """(seed, segments in order): one per confirmation, then whatever is still provisional."""
    by_hash = {n["version_hash"]: n for n in lineage}
    adoption = {a["candidate"]: a for a in attempts if a.get("verdict") == "screen_pass" and a.get("candidate")}

    def node_view(node: dict) -> dict:
        attempt = adoption.get(node["version_hash"])
        return dict(
            version_hash=node["version_hash"], candidate_id=candidate_ids.get(node["version_hash"]),
            state=node.get("state"), card_ref=node.get("card_ref"), created_at=node.get("created_at"),
            agent_cost=node.get("agent_cost"), has_predicate=node.get("predicate_ref") is not None,
            title=(attempt or {}).get("title") or titles.get(node.get("card_ref")) or node.get("card_ref"),
            attempt=(attempt or {}).get("key"), delta_val=(attempt or {}).get("delta_val"),
        )

    seed = next((n for n in lineage if n.get("parent_hash") is None), None)
    placed = {seed["version_hash"]} if seed else set()
    segments: list[dict] = []
    for conf, raw in confirmations:
        chain, cursor = [], raw.get("segment_end")
        start = raw.get("segment_start")
        while cursor and cursor != start and cursor in by_hash and cursor not in placed:
            chain.append(by_hash[cursor])
            placed.add(cursor)
            cursor = by_hash[cursor].get("parent_hash")
        segments.append(dict(
            index=len(segments) + 1, status="certified" if conf["verdict"] == "confirmed" else "rolled_back",
            start=start, end=raw.get("segment_end"), nodes=[node_view(n) for n in reversed(chain)], confirmation=conf))
    rest = [n for n in lineage if n["version_hash"] not in placed]
    for state, status in (("rolled_back", "rolled_back"), ("provisional", "open")):
        nodes = [n for n in rest if n.get("state") == state]
        if nodes:
            segments.append(dict(index=len(segments) + 1, status=status, start=nodes[0].get("parent_hash"),
                                 end=nodes[-1]["version_hash"], nodes=[node_view(n) for n in nodes], confirmation=None))
    seed_view = dict(version_hash=seed["version_hash"], candidate_id=candidate_ids.get(seed["version_hash"]),
                     state=seed.get("state")) if seed else None
    return seed_view, segments


def _phase(state: dict, config: dict) -> str:
    if not state.get("baseline_done"):
        return "baseline"
    current = state.get("current_round")
    if current is None:
        return "between_rounds"
    if state.get("best_stale"):
        return "refreshing_best"
    if current.get("cards") is None:
        return "analysing"
    ms, halt = state.get("milestone") or {}, state.get("halt_tracker") or {}
    if (ms.get("current_provisional_id") != ms.get("last_certified_id")
            and (ms.get("cumulative_delta_val") or 0.0) >= config.get("T", float("inf"))
            and (halt.get("fresh_batches_remaining") or 0) > 0
            and (halt.get("confirmations_used") or 0) < config.get("max_confirmations", 0)):
        return "confirming"
    if current.get("inflight") or len(current.get("submitted") or []) < len(current.get("cards") or []):
        return "building"
    return "between_rounds"


def _open_round(state: dict, *, titles: dict, candidate_ids: dict, z_screen: float, phase: str) -> Optional[dict]:
    current = state.get("current_round")
    if current is None:
        return None
    raw = current["round"]
    round_id = raw["round_id"]
    decided = {c.get("card_index"): c for c in raw.get("candidates") or []}
    inflight = {int(i): parent for i, parent in (current.get("inflight") or {}).items()}
    submitted = set(current.get("submitted") or [])
    cards = []
    for index, card in enumerate(current.get("cards") or []):
        entry = dict(index=index, ref=card.get("ref"), title=card.get("failure_mode_desc"),
                     has_predicate=card.get("predicate") is not None, parent=inflight.get(index))
        if index in inflight:
            entry["status"] = "inflight"
        elif index in decided:
            entry["status"] = "decided"
        else:
            entry["status"] = "submitted" if index in submitted else "queued"
        if index in decided:
            entry["attempt"] = _attempt(decided[index], round_id, titles=titles, candidate_ids=candidate_ids,
                                        z_screen=z_screen)
        cards.append(entry)
    return dict(round_id=round_id, started_at=raw.get("started_at"), phase=phase,
                cards_generated=raw.get("cards_generated"), smoke_passed=raw.get("smoke_passed"),
                screen_passed=raw.get("screen_passed"), milestone_triggered=raw.get("milestone_triggered"),
                confirmation_verdict=raw.get("confirmation_verdict"), halt_reason=raw.get("halt_reason"), cards=cards)


def _ledger_view(ledger: Optional[dict]) -> Optional[dict]:
    if not ledger:
        return None
    utility = {k: v for k, v in (ledger.get("utility") or {}).items() if k != "lineage"}
    selection = {k: v for k, v in (ledger.get("selection") or {}).items() if k != "screened_candidates"}
    return dict(utility=utility, selection=selection, mechanism=ledger.get("mechanism") or {},
                integrity=ledger.get("integrity") or {})


def _next_action(status: str, halt_reason: Optional[str], sealed: Optional[dict]) -> Optional[str]:
    """What a person has to do now; None while the machine is working."""
    if status == "interrupted":
        return "resume"
    if status != "halted":
        return None
    if halt_reason == "process_failure":
        return "retry"
    if halt_reason == "budget_exhausted":
        return "budget"
    return "review" if sealed else "sealed"


def project(state: Optional[dict], *, identity: Optional[dict] = None, ledger: Optional[dict] = None,
            sealed: Optional[dict] = None, alive: bool = False) -> dict:
    """The page's view of one run. *state* None: registered, loop state not written yet."""
    identity = identity or {}
    run = dict(run_id=identity.get("run_id"), iteration_id=identity.get("iteration_id"),
               actor=identity.get("actor"), protocol_sha256=identity.get("protocol_sha256"),
               created_at=identity.get("created_at"))
    if state is None:
        return dict(schema_version=VIEW_SCHEMA_VERSION, run=dict(run, status="running" if alive else "not_started", alive=alive),
                    next_action=None, progress=None, config=None, seed=None, segments=[], open_round=None,
                    rounds=[], ledger=None, sealed=None)

    config = dict(state.get("config") or {})
    z_screen, z_confirm = config.get("z_screen", 0.84), config.get("z_confirm", 1.65)
    versions = state.get("versions") or {}
    candidate_ids = {h: (meta or {}).get("candidate_id") for h, meta in versions.items()}
    titles = _titles(state)

    current_raw = (state.get("current_round") or {}).get("round")
    rounds_raw = list(state.get("rounds") or []) + ([current_raw] if current_raw else [])
    attempts, confirmations = [], []
    for rnd in rounds_raw:
        for c in rnd.get("candidates") or []:
            attempts.append(_attempt(c, rnd["round_id"], titles=titles, candidate_ids=candidate_ids, z_screen=z_screen))
        for c in rnd.get("confirmations") or []:
            confirmations.append((_confirmation(c, rnd["round_id"], z_confirm), c))
    seed, segments = _segments(state.get("lineage") or [], confirmations, attempts, titles=titles, candidate_ids=candidate_ids)

    halt_reason = state.get("halt_reason")
    status = "running" if alive else "halted" if halt_reason else "interrupted"
    phase = _phase(state, config)
    ms, halt, partitions = state.get("milestone") or {}, state.get("halt_tracker") or {}, state.get("partitions") or {}
    fresh_total = len(partitions.get("fresh_batches") or [])
    progress = dict(
        phase=phase, round=state.get("round_counter", 0), rounds_finished=len(state.get("rounds") or []),
        milestones_certified=sum(s["status"] == "certified" for s in segments),
        fresh_used=state.get("fresh_batches_used", 0), fresh_total=fresh_total,
        confirmations_used=halt.get("confirmations_used", 0), max_confirmations=config.get("max_confirmations"),
        no_screen_streak=halt.get("consecutive_no_screen_pass", 0), no_screen_limit=MAX_NO_SCREEN_PASS,
        rejected_streak=halt.get("consecutive_rejected_confirmations", 0), rejected_limit=MAX_REJECTED_CONFIRMATIONS,
        run_failures=state.get("run_failures", 0), run_failures_limit=config.get("max_consecutive_run_failures"),
        cumulative_delta_val=ms.get("cumulative_delta_val"), T=config.get("T"), ladder_m=state.get("ladder_m"),
        best_score=ms.get("best_score"), key_generation=state.get("key_generation", 0),
        search_size=len(partitions.get("search") or []), validation_size=len(state.get("validation_pool") or []),
        validation_initial=len(partitions.get("initial_pool") or []),
    )
    finished = []
    for rnd in state.get("rounds") or []:
        round_attempts = [a for a in attempts if a["round_id"] == rnd["round_id"]]
        finished.append(dict(
            round_id=rnd["round_id"], started_at=rnd.get("started_at"), finished_at=rnd.get("finished_at"),
            cards_generated=rnd.get("cards_generated"), smoke_passed=rnd.get("smoke_passed"),
            screen_passed=rnd.get("screen_passed"), milestone_triggered=rnd.get("milestone_triggered"),
            confirmation_verdict=rnd.get("confirmation_verdict"), rolled_back=rnd.get("rolled_back"),
            attempts=round_attempts))
    return dict(
        schema_version=VIEW_SCHEMA_VERSION,
        run=dict(run, status=status, alive=alive, halt_reason=halt_reason, started_at=state.get("started_at"),
                 updated_at=state.get("updated_at"), sessions=state.get("sessions") or []),
        next_action=_next_action(status, halt_reason, sealed),
        progress=progress,
        config={k: config.get(k) for k in _CONFIG_SHOWN if k in config},
        seed=seed,
        segments=segments,
        open_round=_open_round(state, titles=titles, candidate_ids=candidate_ids, z_screen=z_screen, phase=phase),
        rounds=finished,
        ledger=_ledger_view(ledger),
        sealed=sealed,
    )


def _optional_json(path: Path) -> Any:
    """A file of a live run: absent, or torn only if written outside atomic_write_json."""
    try:
        return ledger_io.read_json(path)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def read_run(state_dir: str | Path) -> dict:
    """Project a run directory."""
    state_dir = Path(state_dir)
    return project(_optional_json(state_dir / ledger_io.STATE_FILE),
                   identity=_optional_json(state_dir / IDENTITY_FILE),
                   ledger=_optional_json(state_dir / ledger_io.LEDGER_FILE),
                   sealed=_optional_json(state_dir / SEALED_FILE),
                   alive=run_alive(state_dir))
