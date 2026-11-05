"""method-v4 loop (method-v4.md §2.3, §3): calibration, then rounds under one parent.

A round: (prefetched) search-set diagnosis of the parent → one analysis (2b
cards) → up to b implementations in parallel, each through stage 0 (smoke,
one repair) and stage 1 (fixed minibatch, paired with the cached parent) →
survivors queued by Δ_mb into stage 2 (rest of the validation set at k_val)
and stage 3 (mechanism: mechanical check + blinded reviewer) → one decision at
the end of the round: qualified candidates, the best adopted, the two best
merged (rebase) and the merge adopted if it beats the best.

Workers never touch loop state: they get an immutable snapshot and return a
JSON record. Every decision runs on the main thread. ``state.json`` is
rewritten atomically after every transition; a rerun resumes the open round
(adapter calls replay under deterministic keys; decided records are kept).
"""
from __future__ import annotations

import copy
import logging
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .. import ledger_io
from ..adapter import (BudgetExhausted, Card, Diff, EvalResult, EvaluationUnavailable, SmokeResult, SystemAdapter,
                       Version)
from ..loop import select_analysis_cases
from . import calibrate, decide, mechanism
from .config import V4Config, digest

log = logging.getLogger(__name__)

SCHEMA = "v4-state-1"
STATE_FILE, LEDGER_FILE = "state.json", "ledger.json"
RUN_FAILURE = "smoke_run_failed"
DIFF_EXCERPT_CHARS = 20000
TERMINAL_STAGES = ("error", "smoke_fail", "minibatch_fail", "measured")
FAILURE_HALTS = ("process_failure", "budget_exhausted")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Snapshot:
    """Immutable inputs of one worker."""
    cfg: V4Config
    parent: Version
    smoke: tuple
    mb: tuple
    rest: tuple
    k_val: int
    rerun_var: float
    seed_aa: Optional[EvalResult]


# ---------------------------------------------------------------------------
# Workers (thread pool; JSON records in, JSON records out)
# ---------------------------------------------------------------------------

def _finish(rec: dict, stage: str, reason: Optional[str] = None, **extra) -> dict:
    rec.update(stage=stage, reason=reason, finished_at=_now(), **extra)
    return rec


def _timed(rec: dict, name: str, t0: float) -> None:
    rec.setdefault("timings", {})[name] = rec.get("timings", {}).get(name, 0.0) + time.monotonic() - t0


def _smoke(adapter: SystemAdapter, snap: Snapshot, card: Card, cand: Version) -> dict:
    """Stage 0 on the smoke sample: run check, then the behaviour check."""
    try:
        ce, pe = adapter.evaluate_pair(cand, snap.parent, list(snap.smoke), 1, purpose="smoke")
    except BudgetExhausted:
        raise
    except EvaluationUnavailable as exc:
        return dict(verdict="run_failed", reason=f"{RUN_FAILURE}: {exc}")
    except Exception as exc:  # noqa: BLE001 - candidate-induced
        return dict(verdict="error", reason=f"smoke: {type(exc).__name__}: {exc}")
    if not all(ce.trajectories.get(t) for t in snap.smoke):
        return dict(verdict="run_failed", reason=f"{RUN_FAILURE}: smoke tasks without a trajectory")
    if not snap.cfg.smoke_behaviour_check:
        return dict(verdict="unchecked", reason="behaviour check disabled", _evals=(ce, pe))
    check = mechanism.smoke_behaviour(adapter, card.predicate, ce, pe)
    return dict(check, _evals=(ce, pe))


def stage01(adapter: SystemAdapter, snap: Snapshot, rec: dict, card: Card) -> tuple[dict, Optional[Version], Any]:
    """workspace → implement → materialize → stage 0 (smoke, ≤1 repair) → stage 1 (minibatch)."""
    cfg, parent = snap.cfg, snap.parent
    t0 = time.monotonic()
    try:
        ws = adapter.workspace(parent, card)
        diff = adapter.implement(card, ws)
        cand = adapter.materialize(diff, parent)
    except EvaluationUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - per-card failure
        _timed(rec, "agent_s", t0)
        return _finish(rec, "error", f"implement: {type(exc).__name__}: {exc}"), None, None
    _timed(rec, "agent_s", t0)
    attempt = 1
    while True:
        rec.update(attempt=attempt, candidate=cand.content_hash, **_diff_fields(diff))
        t0 = time.monotonic()
        smoke = _smoke(adapter, snap, card, cand)
        _timed(rec, "smoke_s", t0)
        evals = smoke.pop("_evals", None)
        rec["smoke"] = smoke
        if smoke["verdict"] == "run_failed":
            return _finish(rec, "smoke_fail", smoke["reason"]), cand, diff
        if smoke["verdict"] == "error":
            return _finish(rec, "error", smoke["reason"]), cand, diff
        if smoke["verdict"] != "fail":
            break
        if attempt > cfg.smoke_repair_attempts:
            return _finish(rec, "smoke_fail", "behaviour did not change as predicted"), cand, diff
        t0 = time.monotonic()
        try:
            result = SmokeResult(candidate_version=cand.content_hash, card_ref=card.ref or "",
                                 behavioral_check_ran=True, feature_present_before=None,
                                 feature_present_after=None, verdict="fail")
            repaired = adapter.repair(card, parent, cand, smoke=result, candidate_smoke=evals[0],
                                      parent_smoke=evals[1], attempt=attempt + 1)
            if repaired is not None:
                rec.setdefault("prior_attempts", []).append(dict(attempt=attempt, candidate=cand.content_hash,
                                                                 smoke=smoke))
                cand, diff = adapter.materialize(repaired, parent), repaired
        except EvaluationUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            _timed(rec, "agent_s", t0)
            return _finish(rec, "error", f"repair: {type(exc).__name__}: {exc}", repair_failed=True), cand, diff
        _timed(rec, "agent_s", t0)
        if repaired is None:
            return _finish(rec, "smoke_fail", "behaviour did not change as predicted (no repair)"), cand, diff
        rec["repaired"] = True
        attempt += 1
    # --- stage 1: minibatch, k=1, paired with the parent's cached results ---
    t0 = time.monotonic()
    cm, pm = adapter.evaluate_subset(cand, parent, "mb", list(snap.mb), 1)
    _timed(rec, "minibatch_s", t0)
    d = decide.paired_delta(cm, pm, snap.mb, rerun_var=snap.rerun_var)
    sg = decide.safeguard_verdict(adapter.observe_safeguards(cm, pm), cfg.safeguard_tolerances)
    rec.update(delta_mb=d["delta"], se_mb=d["se"], n_mb=d["n"], safeguards_mb=sg)
    if d["delta"] is None:
        return _finish(rec, "minibatch_fail", "no task scored on both sides"), cand, diff
    if sg["breached"]:
        return _finish(rec, "minibatch_fail", "safeguard_breached", label="safeguard_breached"), cand, diff
    if d["se"] is not None and d["delta"] < -cfg.minibatch_kill * d["se"]:
        return _finish(rec, "minibatch_fail", "delta_mb below -kill*SE_mb"), cand, diff
    rec.update(stage="minibatch_pass", reason=None)
    return rec, cand, diff


def _diff_fields(diff: Optional[Diff]) -> dict:
    text = (diff.content if diff is not None else "") or ""
    lines = sum(1 for ln in text.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---")))
    return dict(diff_lines=lines, diff_excerpt=text[:DIFF_EXCERPT_CHARS], diff_truncated=len(text) > DIFF_EXCERPT_CHARS)


def stage23(adapter: SystemAdapter, snap: Snapshot, rec: dict, card: Card, cand: Version) -> dict:
    """Stage 2 (rest at k_val; the minibatch result is reused) and stage 3 (mechanism, Δ_val > 0 only)."""
    cfg, parent = snap.cfg, snap.parent
    t0 = time.monotonic()
    cm, pm = adapter.evaluate_subset(cand, parent, "mb", list(snap.mb), 1)        # replay, nothing new runs
    cr, pr = adapter.evaluate_subset(cand, parent, "rest", list(snap.rest), snap.k_val)
    _timed(rec, "validation_s", t0)
    cv, pv = decide.combine(cm, cr), decide.combine(pm, pr)
    ids = list(snap.mb) + list(snap.rest)
    d = decide.paired_delta(cv, pv, ids, rerun_var=snap.rerun_var)
    sg = decide.safeguard_verdict(adapter.observe_safeguards(cv, pv), cfg.safeguard_tolerances)
    rec.update(delta_val=d["delta"], se_val=d["se"], se_own=d["se_own"], se_floor=d["se_floor"], n_val=d["n"],
               dropped_val=d["dropped"], safeguards=sg)
    mech = dict(mechanical=None, reviewer=dict(conclusion="none", reason="not reviewed"), tier=None)
    if d["delta"] is not None and d["delta"] > 0:
        t0 = time.monotonic()
        mech["mechanical"] = mechanism.mechanical(adapter, card.predicate, cv, pv, snap.seed_aa)
        if cfg.review:
            pairs = mechanism.sample_pairs(cv, pv, snap.seed_aa, ids, key=f"review/{cand.content_hash}",
                                           n_pairs=cfg.review_pairs, n_controls=cfg.review_controls)
            diff = Diff(content=rec.get("diff_excerpt") or "")
            verdicts, error = mechanism.review(adapter, card, diff, cand, pairs, key=f"review/{cand.content_hash}")
            conclusion = decide.reviewer_conclusion(verdicts, pairs, min_effect=cfg.review_min_effect,
                                                    max_control_effect=cfg.review_max_control_effect)
            if error:
                conclusion = dict(conclusion, conclusion="none", reason=f"review failed: {error}")
            mech["reviewer"] = dict(conclusion, pairs=mechanism.ledger_view(pairs), verdicts=verdicts)
        mech["tier"] = decide.tier(mech["mechanical"].get("status"), mech["reviewer"]["conclusion"])
        _timed(rec, "mechanism_s", t0)
    else:
        mech["tier"] = decide.tier(None, "none")
    q = decide.qualification(d["delta"], d["se"], mech["tier"], sg, c=cfg.c, c_mech=cfg.c_mech,
                             unobserved_blocks=cfg.unobserved_safeguard_blocks)
    return _finish(rec, "measured", None if q["qualified"] else ",".join(q["reasons"]), mechanism=mech,
                   qualification=q)


# ---------------------------------------------------------------------------
# Main-thread loop state
# ---------------------------------------------------------------------------

class V4Run:
    def __init__(self, adapter: SystemAdapter, seed: Version, cfg: V4Config, *, search: list[str],
                 validation: list[str], test_size: Optional[int] = None, families: Optional[dict] = None,
                 state_dir: Optional[str | Path] = None, key_generation: Optional[int] = None,
                 clock=time.monotonic) -> None:
        self.adapter, self.seed, self.cfg = adapter, seed, cfg
        self.clock = clock
        self.state_dir = Path(state_dir) if state_dir is not None else None
        mb, rest = decide.minibatch_split(validation, cfg.minibatch_fraction, cfg.minibatch_min,
                                          seed=f"v4-minibatch:{seed.content_hash}", families=families)
        import hashlib
        smoke = sorted(search, key=lambda c: (hashlib.sha256(c.encode()).hexdigest(), c))[:cfg.smoke_size]
        self.partitions = dict(search=sorted(search), validation=sorted(validation), minibatch=mb, rest=rest,
                               smoke=smoke, test_size=test_size)
        self.versions: dict[str, Version] = {seed.content_hash: seed}
        self.lineage: list[dict] = [dict(version=seed.content_hash, parent=None, round=0, kind="seed")]
        self.calibration: Optional[dict] = None
        self.feasibility: Optional[dict] = None
        self.rounds: list[dict] = []
        self.current: Optional[dict] = None
        self.history: list[dict] = []
        self.analyst_shown: dict[str, list[str]] = {}
        self.no_adoption = 0
        self.run_failures = 0
        self.halt_reason: Optional[str] = None
        self.elapsed_before = 0.0
        self.sessions: list[dict] = []
        self.key_generation = 0
        self.key_generation_log: list[dict] = []
        self.endpoint: Optional[dict] = None
        self.started_at = _now()
        # In memory only (replayed on resume): calibration results, parent caches, prefetch.
        self.cal_results: Optional[dict] = None
        self._parent_cache: dict[str, tuple[EvalResult, EvalResult]] = {}
        self._prefetch: Optional[tuple[str, Future]] = None
        self._prefetch_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="v4-prefetch")
        self._session_start = clock()
        resumed = self.state_dir is not None and (self.state_dir / STATE_FILE).exists()
        if resumed:
            self._restore(ledger_io.read_json(self.state_dir / STATE_FILE))
        self._apply_key_generation(key_generation, resumed)

    # -- persistence --------------------------------------------------------

    def elapsed(self) -> float:
        return self.elapsed_before + (self.clock() - self._session_start)

    def _state(self) -> dict:
        return dict(schema_version=SCHEMA, config_sha256=digest(self.cfg), seed=self.seed.content_hash,
                    partitions=self.partitions, calibration=self.calibration, feasibility=self.feasibility,
                    versions={h: ledger_io.version_metadata(v) for h, v in self.versions.items()},
                    lineage=self.lineage, rounds=self.rounds, current_round=self.current, history=self.history,
                    analyst_shown=self.analyst_shown, no_adoption=self.no_adoption, run_failures=self.run_failures,
                    halt_reason=self.halt_reason, elapsed_s=self.elapsed(), sessions=self.sessions,
                    key_generation=self.key_generation, key_generation_log=self.key_generation_log,
                    endpoint=self.endpoint, started_at=self.started_at, updated_at=_now())

    def save(self) -> None:
        if self.state_dir is not None:
            ledger_io.atomic_write_json(self.state_dir / STATE_FILE, self._state())

    def _restore(self, st: dict) -> None:
        if st.get("schema_version") != SCHEMA:
            raise ValueError(f"state.json schema {st.get('schema_version')!r} is not {SCHEMA}")
        if st.get("config_sha256") != digest(self.cfg):
            raise ValueError("the v4 config differs from the one this run started with (pre-registered, read-only)")
        if st.get("seed") != self.seed.content_hash:
            raise ValueError(f"seed {self.seed.content_hash!r} differs from the run's seed {st.get('seed')!r}")
        recorded = dict(st["partitions"])
        if {k: v for k, v in recorded.items() if k != "test_size"} != {
                k: v for k, v in self.partitions.items() if k != "test_size"}:
            raise ValueError("search/validation/minibatch/smoke ids differ from the ones this run started with")
        self.partitions = recorded
        self.versions = {h: Version(content_hash=h, metadata=m) for h, m in st["versions"].items()}
        for name in ("calibration", "feasibility", "lineage", "rounds", "history", "analyst_shown", "sessions",
                     "key_generation_log", "endpoint", "started_at"):
            setattr(self, name, st.get(name, getattr(self, name)))
        self.current = st.get("current_round")
        self.no_adoption, self.run_failures = st.get("no_adoption", 0), st.get("run_failures", 0)
        self.key_generation = st.get("key_generation", 0)
        self.elapsed_before = st.get("elapsed_s", 0.0)
        self.halt_reason = st.get("halt_reason")

    def _apply_key_generation(self, requested: Optional[int], resumed: bool) -> None:
        if requested is None:
            requested = self.key_generation if resumed else self.adapter.key_generation
        if requested < self.key_generation:
            raise ValueError("key_generation only grows")
        if requested != self.key_generation or not self.key_generation_log:
            if resumed and requested > self.key_generation:
                self.run_failures = 0
            self.key_generation_log.append(dict(generation=requested, at=_now(), rounds=len(self.rounds)))
        self.key_generation = requested
        self.adapter.set_key_generation(requested)

    def write_ledger(self) -> None:
        if self.state_dir is None:
            return
        ledger_io.atomic_write_json(self.state_dir / LEDGER_FILE, self.ledger())

    def ledger(self) -> dict:
        rounds = self.rounds + ([self.current] if self.current else [])
        cand = [r for rd in rounds for r in (rd.get("records") or {}).values()]
        sums = {k: sum((r.get("timings") or {}).get(k, 0.0) for r in cand)
                for k in ("agent_s", "smoke_s", "minibatch_s", "validation_s", "mechanism_s")}
        stages = {}
        for r in cand:
            stages[r.get("stage")] = stages.get(r.get("stage"), 0) + 1
        return dict(kind="method-v4-ledger", config=_asdict(self.cfg), config_sha256=digest(self.cfg),
                    variants=self.cfg.variants(), seed=self.seed.content_hash,
                    partitions={k: (len(v) if isinstance(v, list) else v) for k, v in self.partitions.items()},
                    deviations=DEVIATIONS, calibration=self.calibration, feasibility=self.feasibility,
                    rounds=rounds, lineage=self.lineage, halt_reason=self.halt_reason, endpoint=self.endpoint,
                    time_account=dict(total_wall_s=self.elapsed(),
                                      calibration_s=(self.calibration or {}).get("wall_s"),
                                      rounds=[dict(round=rd["round"], wall_s=rd.get("wall_s"),
                                                   analysis_s=rd.get("analysis_s"), decision_s=rd.get("decision_s"))
                                              for rd in rounds],
                                      candidate_sums_s=sums),
                    stage_counts=stages, key_generation_log=self.key_generation_log, sessions=self.sessions)

    # -- helpers ------------------------------------------------------------

    def pointer(self) -> Version:
        return self.versions[self.lineage[-1]["version"]]

    def history_for(self, parent: Version) -> list[dict]:
        return [{k: v for k, v in e.items() if k != "parent"} for e in self.history
                if e["parent"] == parent.content_hash]

    def _snapshot(self, parent: Version) -> Snapshot:
        cal = self.calibration
        return Snapshot(cfg=self.cfg, parent=parent, smoke=tuple(self.partitions["smoke"]),
                        mb=tuple(self.partitions["minibatch"]), rest=tuple(self.partitions["rest"]),
                        k_val=cal["k_val"], rerun_var=cal["rerun_var"],
                        seed_aa=decide.combine(self.cal_results["mb"], self.cal_results["rest"]))

    def parent_results(self, version: Version) -> tuple[EvalResult, EvalResult]:
        """(mb, rest) of a lineage node: calibration for the seed, its own paired measurement otherwise."""
        h = version.content_hash
        if h in self._parent_cache:
            return self._parent_cache[h]
        if h == self.seed.content_hash:
            out = (self.cal_results["mb"], self.cal_results["rest"])
        else:
            node = next(n for n in self.lineage if n["version"] == h)
            ref = self.versions[node["measured_against"]]
            mb, rest = self.partitions["minibatch"], self.partitions["rest"]
            out = (self.adapter.evaluate_subset(version, ref, "mb", mb, 1)[0],
                   self.adapter.evaluate_subset(version, ref, "rest", rest, self.calibration["k_val"])[0])
        self._parent_cache[h] = out
        return out

    def _diagnose(self, version: Version) -> EvalResult:
        if self._prefetch is not None and self._prefetch[0] == version.content_hash:
            return self._prefetch[1].result()
        return self.adapter.evaluate(version, self.partitions["search"], self.cfg.calibration_k, purpose="diagnose")

    def _start_prefetch(self, version: Version) -> None:
        """§3.4: the new parent's search set measured in the background for the next analysis."""
        if self._prefetch is not None and self._prefetch[0] == version.content_hash:
            return
        search, k = self.partitions["search"], self.cfg.calibration_k
        fut = self._prefetch_pool.submit(self.adapter.evaluate, version, search, k, purpose="diagnose")
        self._prefetch = (version.content_hash, fut)

    def _out_of_time(self) -> bool:
        return self.elapsed() >= self.cfg.time_budget_hours * 3600

    # -- top level ----------------------------------------------------------

    def run(self) -> str:
        self.sessions.append(dict(started_at=_now(), key_generation=self.key_generation, rounds=len(self.rounds)))
        self.halt_reason = None
        try:
            return self._run()
        finally:
            self.sessions[-1]["ended_at"] = _now()
            self.save()
            self.write_ledger()
            self._prefetch_pool.shutdown(wait=False, cancel_futures=True)

    def _run(self) -> str:
        try:
            self._calibrate()
        except EvaluationUnavailable as exc:
            log.error("calibration unavailable: %s", exc)
            return self._halt(_failure(exc))
        while True:
            if self.current is None:
                reason = self._stop_reason()
                if reason:
                    return self._halt(reason)
                self.current = dict(round=len(self.rounds) + 1, parent=self.pointer().content_hash,
                                    started_at=_now(), cards=None, records={}, decision=None,
                                    started_elapsed=self.elapsed())
                self.save()
            try:
                failure = self._round(self.current)
            except EvaluationUnavailable as exc:
                log.error("evaluation unavailable in round %s: %s", self.current["round"], exc)
                failure = _failure(exc)
            if failure is not None:
                return self._halt(failure)
            rd = self.current
            rd["finished_at"], rd["wall_s"] = _now(), self.elapsed() - rd["started_elapsed"]
            self.rounds.append(rd)
            self.current = None
            self.save()
            self.write_ledger()

    def _halt(self, reason: str) -> str:
        self.halt_reason = reason
        self.save()
        self.write_ledger()
        return reason

    def _stop_reason(self) -> Optional[str]:
        """§3.5 (process failures halt where they happen)."""
        if self.no_adoption >= self.cfg.no_adoption_rounds:
            return "no_adoption"
        if len(self.rounds) >= self.cfg.max_rounds:
            return "max_rounds"
        if self._out_of_time():
            return "time_budget"
        walls = [r["wall_s"] for r in self.rounds if r.get("wall_s")]
        if walls and self.cfg.time_budget_hours * 3600 - self.elapsed() < sum(walls) / len(walls):
            return "time_budget"   # not enough left for a round: the rest is the endpoint's
        return None

    def _calibrate(self) -> None:
        p = self.partitions
        t0 = self.clock()
        self.cal_results, walls = calibrate.measure(self.adapter, self.seed, self.cfg, p["search"],
                                                    p["minibatch"], p["rest"])
        if self.calibration is not None:
            return   # resumed: derived values are part of the run record and never recomputed
        val = decide.combine(self.cal_results["mb"], self.cal_results["rest"])
        observed = self.adapter.observe_safeguards(val, val)
        sizes = dict(search=len(p["search"]), mb=len(p["minibatch"]), rest=len(p["rest"]), test=p.get("test_size"))
        cal = calibrate.derive(self.cal_results, walls, self.cfg, safeguards=observed, sizes=sizes)
        cal["wall_s"] = self.clock() - t0
        self.calibration = cal
        self.feasibility = calibrate.feasibility(cal, self.cfg)
        log.info("%s", calibrate.render(self.feasibility, cal))
        self.save()

    # -- one round ----------------------------------------------------------

    def _round(self, rd: dict) -> Optional[str]:
        parent = self.versions[rd["parent"]]
        if rd["cards"] is None:
            t0 = self.clock()
            search_eval = self._diagnose(parent)
            shown = self.analyst_shown.get(parent.content_hash, [])
            selected, roles = select_analysis_cases(search_eval, self.cfg, shown=shown)
            cards = self.adapter.analyse(selected, self.cfg.n_cards, case_roles=roles, parent=parent,
                                         round_id=rd["round"], history=self.history_for(parent),
                                         no_adoption_rounds=self.no_adoption)
            failures = [t for t, r in roles.items() if r == "failure"]
            self.analyst_shown[parent.content_hash] = list(dict.fromkeys(shown + failures))
            stats = getattr(self.adapter, "last_analysis", None)
            rd.update(cards=[ledger_io.card_to_dict(c) for c in cards], analysis_s=self.clock() - t0,
                      analysis=dict(failures_shown=len(failures), controls_shown=len(roles) - len(failures),
                                    adapter=dict(stats) if isinstance(stats, dict) else None))
            self.save()
        if rd.get("decision") is None:
            failure = self._pipeline(rd, parent)
            if failure is not None:
                return failure
            self._decide(rd, parent)
        return None

    def _pipeline(self, rd: dict, parent: Version) -> Optional[str]:
        cards = [ledger_io.card_from_dict(c) for c in rd["cards"]]
        records: dict = rd["records"]
        snap = self._snapshot(parent)
        b = self.cfg.b
        timebox = self.cfg.round_timebox_hours
        queue = [i for i in range(len(cards)) if str(i) not in records]
        stage2 = [r for r in records.values() if r["stage"] == "minibatch_pass"]
        rerun = [r for r in records.values() if r["stage"] == "pending"]
        failure: Optional[str] = None
        p1: dict[Future, dict] = {}
        p2: dict[Future, dict] = {}

        def closed() -> bool:
            return (failure is not None or self._out_of_time()
                    or (timebox is not None and self.elapsed() - rd["started_elapsed"] >= timebox * 3600))

        with ThreadPoolExecutor(max_workers=b, thread_name_prefix="v4-impl") as pool1, \
                ThreadPoolExecutor(max_workers=b, thread_name_prefix="v4-eval") as pool2:
            def submit1(rec: dict) -> None:
                # Workers get a copy: the main thread serialises `records` while they run.
                p1[pool1.submit(stage01, self.adapter, snap, copy.deepcopy(rec), cards[rec["card_index"]])] = rec

            def submit2() -> None:
                stage2.sort(key=lambda r: (-(r.get("delta_mb") or 0.0), r["card_index"]))
                while stage2 and len(p2) < b and failure is None and not self._out_of_time():
                    rec = stage2.pop(0)
                    cand = self.versions[rec["candidate"]]
                    p2[pool2.submit(stage23, self.adapter, snap, copy.deepcopy(rec), cards[rec["card_index"]],
                                    cand)] = rec

            for rec in rerun:
                submit1(rec)
            while queue and len(p1) < b and not closed():
                idx = queue.pop(0)
                rec = records[str(idx)] = dict(card_index=idx, title=cards[idx].failure_mode_desc,
                                               card_ref=cards[idx].ref, parent=parent.content_hash,
                                               stage="pending", started_at=_now(), timings={})
                submit1(rec)
            self.save()
            submit2()
            while p1 or p2:
                done, _ = wait(list(p1) + list(p2), return_when=FIRST_COMPLETED)
                for fut in done:
                    first = fut in p1
                    rec = (p1 if first else p2).pop(fut)
                    exc = fut.exception()
                    if exc is not None:
                        if not isinstance(exc, EvaluationUnavailable):
                            raise exc
                        log.error("evaluation unavailable for card %s: %s", rec["card_index"], exc)
                        failure = failure or _failure(exc)
                        rec["halted"] = f"{type(exc).__name__}: {exc}"   # stage stays; a resume reruns it
                        continue
                    if not first:
                        records[str(rec["card_index"])] = fut.result()
                    else:
                        out, cand, _ = fut.result()
                        records[str(out["card_index"])] = out
                        if cand is not None:
                            self.versions[cand.content_hash] = cand
                        failure = failure or self._after_stage01(rd, out)
                        if out["stage"] == "minibatch_pass":
                            stage2.append(out)
                        if queue and not closed():
                            idx = queue.pop(0)
                            nrec = records[str(idx)] = dict(card_index=idx, title=cards[idx].failure_mode_desc,
                                                            card_ref=cards[idx].ref, parent=parent.content_hash,
                                                            stage="pending", started_at=_now(), timings={})
                            submit1(nrec)
                    submit2()
                self.save()
        if failure is None and stage2 and self._out_of_time():
            for rec in stage2:     # never measured on the rest: recorded, not decided
                rec["unmeasured"] = "time budget"
        return failure

    def _after_stage01(self, rd: dict, rec: dict) -> Optional[str]:
        if (rec.get("reason") or "").startswith(RUN_FAILURE):
            self.run_failures += 1
            if self.run_failures >= self.cfg.max_consecutive_run_failures:
                log.error("%d consecutive smoke run failures; halting", self.run_failures)
                return "process_failure"
        elif rec.get("smoke") is not None:
            self.run_failures = 0
        return None

    # -- round-end decision (§3.3) ------------------------------------------

    def _decide(self, rd: dict, parent: Version) -> None:
        t0 = self.clock()
        records = list(rd["records"].values())
        qualified = [r for r in records if r["stage"] == "measured" and r["qualification"]["qualified"]]
        best = decide.pick_best(qualified, self.cfg.tie_fraction)
        decision = dict(qualified=[r["card_index"] for r in qualified], best=best and best["card_index"],
                        merge=None, adopted=None)
        adopted, measured_against, delta, se, tier_ = None, parent.content_hash, None, None, None
        if best is not None:
            best_v = self.versions[best["candidate"]]
            self._start_prefetch(best_v)
            adopted, delta, se, tier_ = best_v, best["delta_val"], best["se_val"], best["mechanism"]["tier"]
            if self.cfg.merge and len(qualified) >= 2:
                second = max((r for r in qualified if r is not best), key=lambda r: (r["delta_val"], -r["card_index"]))
                decision["merge"] = m = self._merge(parent, best, second)
                if m.get("adopt"):
                    adopted = self.versions[m["candidate"]]
                    measured_against, delta, se, tier_ = best["candidate"], m["delta_val"], m["se_val"], None
                    self._start_prefetch(adopted)
        merged = bool(decision["merge"] and decision["merge"].get("adopt"))
        in_lineage = set(decision["merge"]["of"]) if merged else ({best["card_index"]} if best else set())
        for r in records:
            self._label(r, parent, adopted=r["card_index"] in in_lineage)
        if adopted is not None:
            self.lineage.append(dict(version=adopted.content_hash, parent=parent.content_hash, round=rd["round"],
                                     kind="merge" if measured_against != parent.content_hash else "candidate",
                                     measured_against=measured_against, delta_val=delta, se_val=se,
                                     tier=tier_, card_indices=sorted(in_lineage),
                                     elapsed_s=self.elapsed()))
            decision["adopted"] = adopted.content_hash
            self.no_adoption = 0
        else:
            self.no_adoption += 1
        rd["decision"] = decision
        rd["decision_s"] = self.clock() - t0
        self.save()

    def _merge(self, parent: Version, best: dict, second: dict) -> dict:
        """§3.3 step 3: second's change rebased onto best, measured on the whole validation set."""
        out = dict(of=[best["card_index"], second["card_index"]], adopt=False)
        try:
            merged = self.adapter.rebase(self.versions[second["candidate"]], self.versions[best["candidate"]])
        except EvaluationUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            return dict(out, skipped=f"rebase: {type(exc).__name__}: {exc}")
        if merged is None:
            return dict(out, skipped="rebase conflict")
        self.versions[merged.content_hash] = merged
        best_v = self.versions[best["candidate"]]
        mb, rest, k_val = self.partitions["minibatch"], self.partitions["rest"], self.calibration["k_val"]
        mm, _ = self.adapter.evaluate_subset(merged, best_v, "mb", mb, 1)
        mr, _ = self.adapter.evaluate_subset(merged, best_v, "rest", rest, k_val)
        pm, pr = self.parent_results(parent)
        mv, pv = decide.combine(mm, mr), decide.combine(pm, pr)
        d = decide.paired_delta(mv, pv, mb + rest, rerun_var=self.calibration["rerun_var"])
        sg = decide.safeguard_verdict(self.adapter.observe_safeguards(mv, pv), self.cfg.safeguard_tolerances)
        ok = sg["ok"] or (not sg["breached"] and not self.cfg.unobserved_safeguard_blocks)
        adopt = d["delta"] is not None and d["delta"] > best["delta_val"] and ok
        return dict(out, candidate=merged.content_hash, delta_val=d["delta"], se_val=d["se"], n_val=d["n"],
                    safeguards=sg, adopt=adopt)

    def _label(self, rec: dict, parent: Version, *, adopted: bool) -> None:
        """§5 history label (labels and discrete details only)."""
        stage = rec["stage"]
        if stage == "error" or rec.get("unmeasured"):
            return
        if stage == "smoke_fail":
            label = "smoke_fail"
        elif stage == "minibatch_fail":
            label = rec.get("label") or "minibatch_fail"
        elif adopted:
            label = "adopted"
        elif rec["qualification"]["qualified"]:
            label = "qualified_not_adopted"
        elif "safeguard_breached" in rec["qualification"]["reasons"]:
            label = "safeguard_breached"
        else:
            label = "below_threshold"
        rec["label"] = label
        entry = dict(parent=parent.content_hash, title=rec["title"], label=label)
        sg = rec.get("safeguards") or rec.get("safeguards_mb") or {}
        if label == "safeguard_breached" and sg.get("breached"):
            entry["safeguards_breached"] = list(sg["breached"])
        if rec.get("repaired"):
            entry["repaired"] = True
        tier_ = ((rec.get("mechanism") or {}).get("tier"))
        if stage == "measured" and tier_:
            entry["mechanism"] = tier_
        self.history.append(entry)


def _failure(exc: EvaluationUnavailable) -> str:
    return "budget_exhausted" if isinstance(exc, BudgetExhausted) else "process_failure"


def _asdict(cfg: V4Config) -> dict:
    import dataclasses
    return dataclasses.asdict(cfg)


# Recorded in every ledger: where this implementation departs from method-v4.md (binding plan §2).
DEVIATIONS = [
    "one fixed minibatch for the whole run, not one per parent (method §3.2; binding plan §2.3)",
    "SN keeps v2's 60/40 search/validation split; the test set is the iteration's sealed selection",
    "the seed's calibration A/A stands in for the parent's A/A in the mechanical check and the control pairs",
    "SE is iid with the A/A floor (no family clustering in the loop); the endpoint reports v2's family-clustered SE",
    "the analyst sees the mechanism tier only, never the reviewer's free-text reason",
    "no ablation pruning (method §4.2 is optional; binding plan W8)",
]
