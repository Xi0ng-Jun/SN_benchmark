"""Method-v4 coordinator: calibration, pipelined rounds, carry-over and ablation.

Workers in stages.py receive snapshots and return records. Only this coordinator
updates versions, persistent stage records and the adopted pointer. Implementation,
measurement and review pools are independent. Diagnostics and next-round analysis
are speculative and consumed only under the matching version/round identity.

A timeboxed round uses finished current-parent evidence and carries unfinished
cards forward. Carried candidates finish against their source parent; qualifying
ones must rebase and repeat the cascade against the new parent before adoption.
The adapter owns deadline checks and actual execution admission; shutdown wakes
pollers before joining and publishes completed materializations for recovery.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import logging
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .. import ledger_io
from ..adapter import BudgetExhausted, EvalResult, EvaluationUnavailable, SystemAdapter, Version
from ..loop import select_analysis_cases
from . import agent_context, calibrate, decide, stages
from .config import V4Config, digest

log = logging.getLogger(__name__)

SCHEMA = "v4-state-3"
STATE_FILE, LEDGER_FILE = "state.json", "ledger.json"
FAILURE_HALTS = ("process_failure", "budget_exhausted")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Snapshot:
    """Immutable inputs of one worker."""
    cfg: V4Config
    parent: Version
    parent_val: EvalResult          # the parent's cached results on the whole validation set
    smoke: tuple
    mb: tuple
    rest: tuple
    k_val: int
    rerun_var: float
    seed_aa: Optional[EvalResult]
    families: dict


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
        validation = sorted(set(validation))
        if families is None:
            families = adapter.task_families(validation)
        self.families = {t: f for t, f in (families or {}).items() if t in set(validation)}
        smoke = sorted(search, key=lambda c: (hashlib.sha256(c.encode()).hexdigest(), c))[:cfg.smoke_size]
        self.partitions = dict(search=sorted(search), validation=validation, smoke=smoke, test_size=test_size,
                               families=len(set(self.families.values())) or None)
        mb, rest = self.split_for(seed.content_hash)
        self.partitions.update(seed_minibatch=mb, seed_rest=rest)
        self.versions: dict[str, Version] = {seed.content_hash: seed}
        self.lineage: list[dict] = [dict(version=seed.content_hash, parent=None, round=0, kind="seed",
                                         components=[])]
        self.calibration: Optional[dict] = None
        self.feasibility: Optional[dict] = None
        self.rounds: list[dict] = []
        self.current: Optional[dict] = None
        self.history: list[dict] = []
        self.analyst_shown: dict[str, list[str]] = {}
        self.no_adoption = 0
        self.run_failures = 0
        self.stop_reason: Optional[str] = None
        self.halt_reason: Optional[str] = None
        self.ablation: Optional[dict] = None
        self.elapsed_before = 0.0
        self.sessions: list[dict] = []
        self.key_generation = 0
        self.key_generation_log: list[dict] = []
        self.started_at = _now()
        # In memory only (replayed on resume): calibration results, validation caches, prefetch.
        self.cal_results: Optional[dict] = None
        self._val_cache: dict[tuple, EvalResult] = {}
        self._diagnostics: dict[str, EvalResult] = {}
        self._prefetch: dict[str, Future] = {}
        self._prefetch_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="v4-prefetch")
        self._analysis_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="v4-analysis")
        self._analyses: dict[tuple, Future] = {}
        self._pools = {name: ThreadPoolExecutor(max_workers=cfg.b, thread_name_prefix=f"v4-{name}")
                       for name in ("implementation", "measurement", "review")}
        self._jobs: dict[Future, dict] = {}
        self._results: dict[str, EvalResult] = {}
        self.carry: list[dict] = []
        self._session_start = clock()
        resumed = self.state_dir is not None and (self.state_dir / STATE_FILE).exists()
        if resumed:
            self._restore(ledger_io.read_json(self.state_dir / STATE_FILE))
        self._apply_key_generation(key_generation, resumed)

    # -- partitions ----------------------------------------------------------

    def split_for(self, parent_hash: str) -> tuple[list[str], list[str]]:
        """(minibatch, rest) of one parent: fixed rule, seeded by the parent's hash (§3.2)."""
        return decide.minibatch_split(self.partitions["validation"], self.cfg.minibatch_fraction,
                                      self.cfg.minibatch_min, seed=f"v4-minibatch:{parent_hash}",
                                      families=self.families or None)

    # -- persistence --------------------------------------------------------

    def elapsed(self) -> float:
        return self.elapsed_before + (self.clock() - self._session_start)

    def _state(self) -> dict:
        return dict(schema_version=SCHEMA, config_sha256=digest(self.cfg), seed=self.seed.content_hash,
                    partitions=self.partitions, family_of=self.families, calibration=self.calibration,
                    feasibility=self.feasibility,
                    versions={h: ledger_io.version_metadata(v) for h, v in self.versions.items()},
                    lineage=self.lineage, rounds=self.rounds, current_round=self.current, history=self.history, carry=self.carry,
                    analyst_shown=self.analyst_shown, no_adoption=self.no_adoption, run_failures=self.run_failures,
                    stop_reason=self.stop_reason, ablation=self.ablation, halt_reason=self.halt_reason,
                    elapsed_s=self.elapsed(), sessions=self.sessions, key_generation=self.key_generation,
                    key_generation_log=self.key_generation_log, started_at=self.started_at, updated_at=_now())

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
        same = ("search", "validation", "smoke", "seed_minibatch", "seed_rest")
        if any(recorded.get(k) != self.partitions.get(k) for k in same) or st.get("family_of") != self.families:
            raise ValueError("search/validation/smoke ids or task families differ from the ones this run started with")
        self.partitions = recorded
        self.versions = {h: Version(content_hash=h, metadata=m) for h, m in st["versions"].items()}
        for name in ("calibration", "feasibility", "lineage", "rounds", "history", "analyst_shown", "sessions",
                     "key_generation_log", "started_at", "stop_reason", "ablation", "carry"):
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
                for k in ("agent_s", "smoke_s", "minibatch_s", "validation_s", "mechanism_s", "safeguards_s", "rebase_s")}
        stages: dict = {}
        for r in cand:
            stages[r.get("stage")] = stages.get(r.get("stage"), 0) + 1
        by_cache: dict = {}
        for r in cand:
            for stage, key in (("minibatch_s", "cache_mb"), ("validation_s", "cache_rest")):
                if stage not in (r.get("timings") or {}):
                    continue
                c = r.get(key) or {}
                cls = "cold" if c.get("cache_misses") else "warm" if c.get("cache_hits") else "unknown"
                row = by_cache.setdefault(stage, {}).setdefault(cls, dict(n=0, total_s=0.0))
                row["n"] += 1
                row["total_s"] += r["timings"][stage]
        for stage in by_cache.values():
            for row in stage.values():
                row["mean_s"] = row["total_s"] / row["n"]
        return dict(kind="method-v4-ledger", config=dataclasses.asdict(self.cfg), config_sha256=digest(self.cfg),
                    variants=self.cfg.variants(), seed=self.seed.content_hash,
                    partitions={k: (len(v) if isinstance(v, list) else v) for k, v in self.partitions.items()},
                    deviations=DEVIATIONS, calibration=self.calibration, feasibility=self.feasibility,
                    rounds=rounds, lineage=self.lineage, stop_reason=self.stop_reason, ablation=self.ablation,
                    halt_reason=self.halt_reason,
                    time_account=dict(total_wall_s=self.elapsed(),
                                      calibration_s=(self.calibration or {}).get("wall_s"),
                                      rounds=[dict(round=rd["round"], wall_s=rd.get("wall_s"),
                                                   analysis_s=rd.get("analysis_s"), diagnosis_s=rd.get("diagnosis_s"), decision_s=rd.get("decision_s"))
                                              for rd in rounds],
                                      candidate_sums_s=sums, stage_seconds_by_cache=by_cache,
                                      ablation_s=(self.ablation or {}).get("wall_s")),
                    stage_counts=stages, key_generation_log=self.key_generation_log, sessions=self.sessions)

    # -- helpers ------------------------------------------------------------

    def pointer(self) -> Version:
        return self.versions[self.lineage[-1]["version"]]

    def history_for(self, parent: Version) -> list[dict]:
        """History for the analyst: all hypotheses tried in this run (§5), including earlier parents."""
        out = []
        for e in self.history:
            row = {k: v for k, v in e.items() if k != "parent"}
            # Mark entries from earlier parents so the analyst knows this parent may already contain that change.
            if e["parent"] != parent.content_hash:
                row["earlier_parent"] = True
            out.append(row)
        for node in self.lineage:
            if node.get("parent") is not None:
                rd = next((r for r in self.rounds + ([self.current] if self.current else [])
                           if r["round"] == node["round"]), None)
                if rd is not None:
                    for index in node.get("card_indices", []):
                        card = rd["cards"][index]
                        out.append(dict(label="current_change", title=card["failure_mode_desc"],
                                        change=card["change_hypothesis"], version=node["version"]))
            if node["version"] == parent.content_hash:
                break
        return out

    def _node(self, h: str) -> Optional[dict]:
        return next((n for n in self.lineage if n["version"] == h), None)

    def measure_val(self, version: Version, split_of: str, k_val: Optional[int] = None) -> EvalResult:
        """*version* on the validation set split as *split_of*'s minibatch (k=1) + rest (k_val); replayable."""
        k_val = k_val or self.calibration["k_val"]
        key = (version.content_hash, split_of, k_val)
        if key not in self._val_cache:
            mb, rest = self.split_for(split_of)
            self._val_cache[key] = decide.combine(self.adapter.evaluate_subset(version, None, "mb", mb, 1)[0],
                                                  self.adapter.evaluate_subset(version, None, "rest", rest, k_val)[0])
        return self._val_cache[key]

    def parent_val(self, version: Version) -> EvalResult:
        """The cached validation results of a lineage node (the seed: its calibration A/A)."""
        h = version.content_hash
        if h == self.seed.content_hash:
            return decide.combine(self.cal_results["mb"], self.cal_results["rest"])
        node = self._node(h)
        if node is None:
            raise KeyError(f"{h!r} is not a lineage node")
        return self.measure_val(version, node["parent"], node.get("k_val"))

    def _snapshot(self, parent: Version) -> Snapshot:
        cal = self.calibration
        mb, rest = self.split_for(parent.content_hash)
        return Snapshot(cfg=self.cfg, parent=parent, parent_val=self.parent_val(parent),
                        smoke=tuple(self.partitions["smoke"]), mb=tuple(mb), rest=tuple(rest),
                        k_val=cal["k_val"], rerun_var=cal["rerun_var"],
                        seed_aa=decide.combine(self.cal_results["mb"], self.cal_results["rest"]),
                        families=dict(self.families))

    def diagnosis_sample(self, version: Version) -> list[str]:
        search = self.partitions["search"]
        ranked = sorted(search, key=lambda t: hashlib.sha256(f"diag:{version.content_hash}:{t}".encode()).hexdigest())
        return sorted(ranked[:self.cfg.diagnose_size] if self.cfg.diagnose_size else ranked)

    def _diagnose(self, version: Version) -> EvalResult:
        h = version.content_hash
        if h not in self._diagnostics:
            future = self._prefetch.pop(h, None)
            if future is not None:
                try:
                    self._diagnostics[h] = future.result()
                except EvaluationUnavailable:
                    # Speculation is optional, but an actual required diagnosis must succeed.
                    self.adapter.check_runtime()
            if h not in self._diagnostics:
                self._diagnostics[h] = self.adapter.evaluate_search(version, self.diagnosis_sample(version),
                                                                     self.cfg.diagnose_k)
        return self._diagnostics[h]

    def _start_prefetch(self, version: Version) -> bool:
        h = version.content_hash
        if self._out_of_time() or h in self._prefetch or h in self._diagnostics:
            return False
        self._prefetch[h] = self._prefetch_pool.submit(self.adapter.evaluate_search, version,
                                                      self.diagnosis_sample(version), self.cfg.diagnose_k)
        return True

    def _analysis(self, parent, round_id, history, shown, no_adoption):
        start = time.monotonic()
        search_eval = self._diagnostics[parent.content_hash]
        selected, roles = select_analysis_cases(search_eval, self.cfg, shown=shown)
        cards = self.adapter.analyse(selected, self.cfg.n_cards, case_roles=roles, parent=parent,
                                     round_id=round_id, history=history, no_adoption_rounds=no_adoption,
                                     case_scores={t: search_eval.per_task_scores[t] for t in selected
                                                  if t in search_eval.per_task_scores})
        return dict(cards=[ledger_io.card_to_dict(c) for c in cards], roles=roles,
                    wall_s=time.monotonic() - start)

    def _remaining(self) -> float:
        return self.cfg.time_budget_hours * 3600 - self.elapsed()

    def _out_of_time(self) -> bool:
        return self._remaining() <= 0

    # -- top level ----------------------------------------------------------

    def run(self) -> str:
        self.adapter.configure_runtime(remaining=self._remaining,
                                       max_concurrent_executions=self.cfg.max_concurrent_executions or 3)
        self.sessions.append(dict(started_at=_now(), key_generation=self.key_generation, rounds=len(self.rounds)))
        if self.halt_reason == "process_failure" and self.current:
            for rec in self.current["records"].values():
                if rec.get("halted"):
                    rec["stage"] = rec.pop("failed_stage", rec["stage"])
                    rec.pop("halted", None)
                    rec.pop("label", None)
            self.run_failures = 0
        self.halt_reason = None
        interrupted = False
        try:
            return self._run()
        except BaseException as exc:
            interrupted = not isinstance(exc, Exception)
            raise
        finally:
            # Wake pollers before joining. A resumable failure preserves durable remote work;
            # completed runs and exhausted budgets request cancellation of owned work.
            if interrupted or self.halt_reason == "process_failure":
                self.adapter.stop_polling()
            else:
                self.adapter.cancel_pending()
            for pool in [*self._pools.values(), self._prefetch_pool, self._analysis_pool]:
                pool.shutdown(wait=True, cancel_futures=True)
            for future, job in self._jobs.items():
                if future.done() and not future.cancelled() and future.exception() is None:
                    self._complete_job(job, future)
            self._jobs.clear()
            self.sessions[-1]["ended_at"] = _now()
            report = getattr(self.adapter, "cancellation_report", None)
            if report:
                self.sessions[-1]["cancellation"] = copy.deepcopy(report)
            self.save()
            self.write_ledger()

    def _run(self) -> str:
        try:
            self._calibrate()
        except EvaluationUnavailable as exc:
            log.error("calibration unavailable: %s", exc)
            return self._halt("time_budget" if self._out_of_time() else _failure(exc))
        while True:
            if self.current is None:
                reason = self.stop_reason or self._stop_reason()
                if reason:
                    self.stop_reason = reason
                    self.save()
                    try:
                        self._ablate()
                    except EvaluationUnavailable as exc:
                        log.error("ablation unavailable: %s", exc)
                        return self._halt("time_budget" if self._out_of_time() else _failure(exc))
                    return self._halt(reason)
                self.current = dict(round=len(self.rounds) + 1, parent=self.pointer().content_hash,
                                    started_at=_now(), cards=None, records={}, decision=None,
                                    started_elapsed=self.elapsed())
                self.save()
            try:
                failure = self._round(self.current)
            except EvaluationUnavailable as exc:
                log.error("evaluation unavailable in round %s: %s", self.current["round"], exc)
                failure = "time_budget" if self._out_of_time() else _failure(exc)
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
        if self._out_of_time():
            return "time_budget"
        if self.no_adoption >= self.cfg.no_adoption_rounds and not self.carry:
            return "no_adoption"
        if len(self.rounds) >= self.cfg.max_rounds:
            return "max_rounds"
        walls = [r["wall_s"] for r in self.rounds if r.get("wall_s")]
        if self.cfg.predictive_time_stop and walls and self._remaining() < sum(walls) / len(walls):
            return "time_budget"   # not enough left for a round: the rest is the endpoint's
        return None

    def _calibrate(self) -> None:
        p = self.partitions
        t0 = self.clock()
        self.cal_results, walls = calibrate.measure(self.adapter, self.seed, self.cfg, self.diagnosis_sample(self.seed),
                                                    p["seed_minibatch"], p["seed_rest"])
        self._diagnostics[self.seed.content_hash] = self.cal_results["search"]
        if self.calibration is not None:
            return   # resumed: derived values are part of the run record and never recomputed
        val = decide.combine(self.cal_results["mb"], self.cal_results["rest"])
        observed = self.adapter.observe_safeguards(val, val)
        sizes = dict(search=len(self.diagnosis_sample(self.seed)), mb=len(p["seed_minibatch"]), rest=len(p["seed_rest"]),
                     test=p.get("test_size"), smoke=len(p["smoke"]))
        profiles = {name: stages.cache(self.adapter, r) for name, r in self.cal_results.items()}
        cal = calibrate.derive(self.cal_results, walls, self.cfg, safeguards=observed, sizes=sizes, profiles=profiles)
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
            prefetched = parent.content_hash in self._prefetch or parent.content_hash in self._diagnostics
            self._diagnose(parent)
            rd.update(diagnosis_s=self.clock() - t0, diagnosis_prefetched=prefetched)
            key = (parent.content_hash, rd["round"])
            future = self._analyses.pop(key, None)
            if future is not None:
                try:
                    result = future.result()
                except EvaluationUnavailable:
                    self.adapter.check_runtime()
                    result = None
            else:
                result = None
            if result is None:
                result = self._analysis(parent, rd["round"], self.history_for(parent),
                                        self.analyst_shown.get(parent.content_hash, []), self.no_adoption)
            shown = self.analyst_shown.get(parent.content_hash, [])
            failures = [t for t, role in result["roles"].items() if role == "failure"]
            self.analyst_shown[parent.content_hash] = list(dict.fromkeys(shown + failures))
            mb, rest = self.split_for(parent.content_hash)
            rd.update(cards=result["cards"], analysis_s=result["wall_s"], analysis_prefetched=future is not None,
                      split=dict(minibatch=mb, rest=rest),
                      analysis=dict(failures_shown=len(failures), controls_shown=len(result["roles"]) - len(failures)))
            # Transfer unfinished records, preserving their source parent and deterministic Agent identity.
            for item in self.carry:
                idx = len(rd["cards"])
                rd["cards"].append(item["card"])
                rec = copy.deepcopy(item["record"])
                rec.update(card_index=idx)
                rec.pop("carried_to", None)
                rd["records"][str(idx)] = rec
                for job in self._jobs.values():
                    if job.get("uid") == rec["uid"]:
                        job["record"] = rec
            self.carry = []
            self.save()
        if rd.get("decision") is None:
            failure = self._pipeline(rd, parent)
            if failure:
                return failure
            self._decide(rd, parent)
        return None

    def _pipeline(self, rd: dict, parent: Version) -> Optional[str]:
        cards = [ledger_io.card_from_dict(c) for c in rd["cards"]]
        records = rd["records"]
        for i, card in enumerate(cards):
            records.setdefault(str(i), dict(card_index=i, uid=f"{rd['round']}:{i}", title=card.failure_mode_desc,
                                            card_ref=card.ref, parent=parent.content_hash, stage="pending", timings={}))
        snapshots = {}
        def snapshot(h):
            if h not in snapshots:
                snapshots[h] = self._snapshot(self.versions[h])
            return snapshots[h]
        failure = None
        prefetched = set(rd.get("prefetched", []))
        merges = rd.setdefault("merges", {})
        timebox = self.cfg.round_timebox_hours
        def expired():
            return timebox is not None and self.elapsed() - rd["started_elapsed"] >= timebox * 3600
        def active_uids():
            return {j.get("uid") for j in self._jobs.values()}
        def count(pool):
            return sum(j["pool"] == pool for j in self._jobs.values())
        def submit(rec, pool, fn, *args):
            rec.setdefault("started_at", _now())
            fut = self._pools[pool].submit(fn, self.adapter, *args)
            self._jobs[fut] = dict(uid=rec["uid"], record=rec, stage=rec["stage"], pool=pool)
        terminal = {"measured", "minibatch_fail", "smoke_fail", "run_failed", "error", "preflight_skipped"}
        while True:
            # Drain completions before checking the timebox so finished evidence is eligible.
            for fut in [f for f in self._jobs if f.done()]:
                job = self._jobs.pop(fut)
                rec = job.get('record')
                try:
                    completed = self._complete_job(job, fut)
                    if completed is not None and completed['stage'] in terminal:
                        self.run_failures = 0
                except Exception as exc:
                    unavailable = exc if isinstance(exc, EvaluationUnavailable) else EvaluationUnavailable(
                        f"{type(exc).__name__}: {exc}")
                    failure = self._record_failure(unavailable) or failure
                    if rec is not None:
                        rec['failed_stage'] = job['stage']
                        if failure:
                            rec['halted'] = f"{type(exc).__name__}: {exc}"
                        else:
                            stages.finish(rec, 'run_failed', str(exc), label='run_failed')
                    elif job.get('merge_key') and job['round'] == rd['round']:
                        merges[job['merge_key']] = dict(of=job["of"], adopt=False, skipped=f"measurement: {exc}")
                self.save()
            if self._out_of_time():
                self.adapter.cancel_pending()
                for rec in records.values():
                    if rec['stage'] not in terminal:
                        rec['unmeasured'] = 'time budget'
                rd['not_started'] = [r['card_index'] for r in records.values() if 'started_at' not in r]
                return None
            if failure:
                return failure
            if expired():
                rd['timeboxed'] = True
                for rec in records.values():
                    if rec['stage'] not in terminal or (rec['stage'] == 'measured' and rec['parent'] != parent.content_hash
                                                       and rec['qualification']['qualified']):
                        self.carry.append(dict(card=ledger_io.card_to_dict(cards[rec['card_index']]),
                                               record=copy.deepcopy(rec)))
                        rec['carried_to'] = rd['round'] + 1
                rd['not_started'] = [r['card_index'] for r in records.values() if 'started_at' not in r]
                self.save()
                return None
            active = active_uids()
            ordered = sorted(records.values(), key=lambda r: (-(r.get('delta_mb') or 0), r['card_index']))
            for rec in ordered:
                if rec['uid'] in active or rec.get('unmeasured'):
                    continue
                stage = rec['stage']
                if stage == 'measured' and rec['parent'] != parent.content_hash and rec['qualification']['qualified']:
                    rec['stage'] = stage = 'rebase'
                if stage in terminal:
                    continue
                card = cards[rec['card_index']]
                cand = self.versions.get(rec.get('candidate'))
                if stage in ('pending', 'repair', 'rebase'):
                    if count('implementation') >= self.cfg.b:
                        continue
                    if stage == 'rebase':
                        submit(rec, 'implementation', stages.rebase, copy.deepcopy(rec), cand, parent)
                    else:
                        submit(rec, 'implementation', stages.build, snapshot(rec['parent']), copy.deepcopy(rec), card, cand)
                elif stage in ('built', 'smoke_pass', 'minibatch_pass', 'validated'):
                    if stage == 'validated' and cand.content_hash in self._results:
                        if count('review') < self.cfg.b:
                            submit(rec, 'review', stages.review, snapshot(rec['parent']), copy.deepcopy(rec), card, cand,
                                   self._results[cand.content_hash])
                    elif count('measurement') < self.cfg.b:
                        fn = {'built': stages.smoke, 'smoke_pass': stages.minibatch,
                              'minibatch_pass': stages.validation, 'validated': stages.validation}[stage]
                        submit(rec, 'measurement', fn, snapshot(rec['parent']), copy.deepcopy(rec), card, cand)
            qualified = [r for r in records.values() if r['stage'] == 'measured'
                         and r['parent'] == parent.content_hash and r['qualification']['qualified']]
            best = decide.pick_best(qualified, self.cfg.tie_fraction)
            if best and len(prefetched) < self.cfg.speculative_prefetches:
                if self._start_prefetch(self.versions[best['candidate']]):
                    prefetched.add(best['candidate'])
                    rd['prefetched'] = sorted(prefetched)
            if self.cfg.merge and len(qualified) >= 2 and count('measurement') < self.cfg.b:
                second = max((r for r in qualified if r is not best), key=lambda r: (r['delta_val'], -r['card_index']))
                key = f"{best['candidate']}|{second['candidate']}"
                if key not in merges and not any(j.get('merge_key') == key for j in self._jobs.values()):
                    fut = self._pools['measurement'].submit(stages.merge, self.adapter, snapshot(parent.content_hash),
                            copy.deepcopy(best), copy.deepcopy(second), self.versions[best['candidate']],
                            self.versions[second['candidate']])
                    self._jobs[fut] = dict(merge_key=key, pool='measurement', parent=parent.content_hash, round=rd['round'],
                                           of=[best['card_index'], second['card_index']])
            # Next analysis is speculative: only the same parent and exact next round may consume it.
            key = (parent.content_hash, rd['round'] + 1)
            if (self.cfg.speculative_analysis and cards and key not in self._analyses
                    and rd['round'] < self.cfg.max_rounds
                    and all(r['stage'] != 'pending' or r['uid'] in active_uids() for r in records.values())):
                history = self.history_for(parent)
                for rec in records.values():
                    known = self._history_entry(rec, adopted=False)
                    if known is not None and known['label'] != 'qualified_not_adopted':
                        history.append({k: v for k, v in known.items() if k != 'parent'})
                    else:
                        history.append(dict(title=rec['title'], label='in_progress'))
                self._analyses[key] = self._analysis_pool.submit(self._analysis, parent, rd['round'] + 1,
                    history, list(self.analyst_shown.get(parent.content_hash, [])), self.no_adoption)
            unfinished = [r for r in records.values() if r['stage'] not in terminal]
            current_merges = any(j.get('merge_key') and j['round'] == rd['round'] for j in self._jobs.values())
            if not unfinished and not current_merges:
                return None
            self.save()
            if self._jobs:
                wait(list(self._jobs), timeout=.02, return_when=FIRST_COMPLETED)
            else:
                raise RuntimeError('unfinished v4 records have no runnable stage')

    def _complete_job(self, job, future):
        """Publish a worker result on the coordinator, including results received during shutdown."""
        result = future.result()
        if job.get('merge_key'):
            row, version, val = result
            if version is not None:
                self.versions[version.content_hash] = version
                if val is not None:
                    self._val_cache[(version.content_hash, job['parent'], self.calibration['k_val'])] = val
            for rd in self.rounds + ([self.current] if self.current else []):
                if rd['round'] == job['round']:
                    rd.setdefault('merges', {})[job['merge_key']] = row
            return None
        rec = next((item['record'] for item in self.carry if item['record']['uid'] == job['uid']), job['record'])
        if job['stage'] in ('pending', 'repair', 'rebase'):
            result, version = result
            if version is not None:
                self.versions[version.content_hash] = version
        elif job['stage'] in ('minibatch_pass', 'validated') and job['pool'] == 'measurement':
            result, val = result
            self._results[result['candidate']] = val
            self._val_cache[(result['candidate'], result['parent'], self.calibration['k_val'])] = val
        result['card_index'] = rec['card_index']
        if rec.get('unmeasured'):
            result['unmeasured'] = rec['unmeasured']
        rec.clear()
        rec.update(result)
        return rec

    def _record_failure(self, exc):
        from ..adapter import EvaluationCancelled
        if isinstance(exc, EvaluationCancelled):
            return 'time_budget' if self._out_of_time() else 'process_failure'
        if isinstance(exc, BudgetExhausted):
            return 'budget_exhausted'
        self.run_failures += 1
        return 'process_failure' if self.run_failures >= self.cfg.max_consecutive_run_failures else None

    # -- round-end decision (§3.3) ------------------------------------------

    def _decide(self, rd: dict, parent: Version) -> None:
        t0 = self.clock()
        records = list(rd["records"].values())
        qualified = [r for r in records if r["stage"] == "measured" and r["parent"] == parent.content_hash
                     and not r.get("carried_to") and r["qualification"]["qualified"]]
        best = decide.pick_best(qualified, self.cfg.tie_fraction)
        decision = dict(qualified=[r["card_index"] for r in qualified], best=best and best["card_index"],
                        merge=None, adopted=None)
        adopted, node = None, None
        if best is not None:
            adopted = self.versions[best["candidate"]]
            node = dict(kind="candidate", components=[best["candidate"]], delta_val=best["delta_val"],
                        se_val=best["se_val"], tier=best["mechanism"]["tier"], card_indices=[best["card_index"]])
            if self.cfg.merge and len(qualified) >= 2:
                second = max((r for r in qualified if r is not best), key=lambda r: (r["delta_val"], -r["card_index"]))
                key = f"{best['candidate']}|{second['candidate']}"
                decision["merge"] = m = rd.get("merges", {}).get(key) or dict(of=[best["card_index"], second["card_index"]], adopt=False, skipped="not complete before round deadline")
                if m.get("adopt"):
                    adopted = self.versions[m["candidate"]]
                    node = dict(kind="merge", components=[best["candidate"], m["candidate"]],
                                delta_val=m["delta_val"], se_val=m["se_val"], tier=None,
                                card_indices=sorted([best["card_index"], second["card_index"]]))
        in_lineage = set(node["card_indices"]) if node else set()
        for r in records:
            self._label(r, parent, adopted=r["card_index"] in in_lineage)
        if adopted is not None:
            self.lineage.append(dict(node, version=adopted.content_hash, parent=parent.content_hash,
                                     round=rd["round"], k_val=self.calibration["k_val"], elapsed_s=self.elapsed()))
            decision["adopted"] = adopted.content_hash
            self.no_adoption = 0
        else:
            self.no_adoption += 1
        rd.pop("merge_progress", None)
        rd["decision"] = decision
        rd["decision_s"] = self.clock() - t0
        self.save()

    def _label(self, rec: dict, parent: Version, *, adopted: bool) -> None:
        """Publish the outcome once the round's decision is final."""
        entry = self._history_entry(rec, adopted=adopted)
        if entry is not None:
            rec["label"] = entry["label"]
            self.history.append(entry)

    def _history_entry(self, rec: dict, *, adopted: bool) -> Optional[dict]:
        stage = rec["stage"]
        if rec.get("unmeasured") or rec.get("carried_to") or stage not in ("smoke_fail", "minibatch_fail", "measured", "run_failed", "error", "preflight_skipped"):
            return None
        if stage == "preflight_skipped":
            label = "preflight_skipped"
        elif stage == "error":
            label = "implementation_failed"
        elif stage == "run_failed":
            label = "run_failed"
        elif stage == "smoke_fail":
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
        entry = dict(parent=rec["parent"], title=rec["title"], label=label)
        entry.update(agent_context.feedback(rec, label))
        sg = rec.get("safeguards") or rec.get("safeguards_mb") or {}
        if label == "safeguard_breached" and sg.get("breached"):
            entry["safeguards_breached"] = list(sg["breached"])
        if rec.get("repaired"):
            entry["repaired"] = True
        tier_ = ((rec.get("mechanism") or {}).get("tier"))
        if stage == "measured" and tier_:
            entry["mechanism"] = tier_
        return entry

    # -- ablation pruning (§4.2) --------------------------------------------

    def _rebuild(self, skip: set[int]) -> Optional[Version]:
        """The final version without the lineage units in *skip* (0 = first adopted node); None on conflict."""
        units = self.lineage[1:]
        first = min(skip)
        base = self.versions[self.lineage[first]["version"]]      # everything before the first skipped unit
        for j in range(first + 1, len(units)):
            if j in skip:
                continue
            for component in units[j]["components"]:
                self.adapter.check_runtime()
                try:
                    base = self.adapter.rebase(self.versions[component], base)
                except EvaluationUnavailable:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log.warning("ablation rebase failed: %s", exc)
                    return None
                if base is None:
                    return None
                self.versions[base.content_hash] = base
        return base

    def _val_of(self, version: Version, split_of: str) -> EvalResult:
        if self._node(version.content_hash) is not None:
            return self.parent_val(version)
        return self.measure_val(version, split_of)

    def _validation_seconds(self) -> float:
        st = (self.feasibility or {}).get("stage_seconds") or {}
        return float(st.get("minibatch") or 0.0) + float(st.get("validation_rest") or 0.0)

    def _ablate(self) -> None:
        if self.ablation is not None and self.ablation.get("status") in ("done", "skipped"):
            return
        units = self.lineage[1:]
        if not self.cfg.ablation:
            self.ablation = dict(status="skipped", reason="disabled")
        elif not units:
            self.ablation = dict(status="skipped", reason="nothing adopted")
        if self.ablation is not None and self.ablation.get("status") == "skipped":
            self.save()
            return
        final = self.lineage[-1]
        ab = self.ablation or dict(status="running", units={}, split_of=final["parent"], final=final["version"],
                                   started_elapsed=self.elapsed())
        if "budget" not in ab:
            # One measurement per unit except the last (removing it leaves its parent, already cached),
            # plus the re-measurement of the pruned version.
            measured = len(units)
            needed = ((measured + self.cfg.ablation_parallel - 1) // self.cfg.ablation_parallel + 1) * self._validation_seconds()
            ab["budget"] = dict(validation_seconds=self._validation_seconds(), measurements=measured,
                                needed_s=needed, remaining_s=self._remaining())
            if needed > self._remaining():
                self.ablation = dict(status="skipped", reason="time budget", budget=ab["budget"])
                self.save()
                return
        self.ablation = ab
        self.save()
        final_v = self.versions[final["version"]]
        final_val = self.parent_val(final_v)
        ids = self.partitions["validation"]
        cal = self.calibration
        kill = self.cfg.ablation_kill
        # Build the independent versions on the coordinator; workers only measure immutable versions.
        jobs = []
        for i, node in enumerate(units):
            if str(i) in ab["units"]:
                continue
            if self._out_of_time():
                ab.update(status="skipped", reason="time budget")
                self.save()
                return
            row = dict(index=i + 1, version=node["version"], round=node["round"], kind=node["kind"],
                       card_indices=node.get("card_indices", []))
            try:
                ablated = self._rebuild({i})
            except Exception as exc:
                failure = self._record_failure(exc)
                if failure:
                    raise (exc if isinstance(exc, EvaluationUnavailable)
                           else EvaluationUnavailable(f"ablation: {type(exc).__name__}: {exc}"))
                ab["units"][str(i)] = dict(row, status="run_failed", reason=str(exc), prune=False)
                continue
            if ablated is None:
                ab["units"][str(i)] = dict(row, status="conflict", prune=False)
            else:
                jobs.append((i, row, ablated))
        mb, rest = self.split_for(ab["split_of"])
        def evaluate(version):
            self.adapter.check_runtime()
            # Known lineage results are captured on the coordinator, not read through a shared mutable cache.
            return decide.combine(self.adapter.evaluate_subset(version, None, "mb", mb, 1)[0],
                                  self.adapter.evaluate_subset(version, None, "rest", rest, cal["k_val"])[0])
        with ThreadPoolExecutor(max_workers=self.cfg.ablation_parallel, thread_name_prefix="v4-ablation") as pool:
            pending = {}
            try:
                for i, row, version in jobs:
                    if self._node(version.content_hash) is not None:
                        fut = Future()
                        fut.set_result(self.parent_val(version))
                    else:
                        fut = pool.submit(evaluate, version)
                    pending[fut] = (i, row, version)
                while pending:
                    done, _ = wait(pending, timeout=.05, return_when=FIRST_COMPLETED)
                    if self._out_of_time():
                        self.adapter.cancel_pending()
                        for fut in pending:
                            fut.cancel()
                        ab.update(status="skipped", reason="time budget")
                        self.save()
                        return
                    for fut in done:
                        i, row, version = pending.pop(fut)
                        try:
                            val = fut.result()
                            d = decide.paired_delta(val, final_val, ids, rerun_var=cal["rerun_var"], families=self.families)
                            row.update(status="measured", ablated=version.content_hash, delta=d["delta"], se=d["se"],
                                       n=d["n"], prune=d["delta"] is not None and d["se"] is not None
                                       and d["delta"] >= -kill * d["se"])
                            self.run_failures = 0
                        except Exception as exc:
                            failure = self._record_failure(exc)
                            row.update(status="run_failed", reason=str(exc), prune=False)
                            if failure:
                                # Leave this unit retryable; completed units remain in the ledger.
                                raise (exc if isinstance(exc, EvaluationUnavailable)
                                       else EvaluationUnavailable(f"ablation: {type(exc).__name__}: {exc}"))
                        ab["units"][str(i)] = row
                        self.save()
            except BaseException as exc:
                # Context-manager shutdown joins workers; wake them before leaving the block.
                if self._out_of_time() or isinstance(exc, BudgetExhausted):
                    self.adapter.cancel_pending()
                else:
                    self.adapter.stop_polling()
                for fut in pending:
                    fut.cancel()
                raise
        pruned = sorted(int(k) for k, r in ab["units"].items() if r.get("prune"))
        ab["pruned"] = [i + 1 for i in pruned]
        if not pruned:
            ab.update(status="done", accepted=False, reason="every adopted change contributes")
        else:
            if self._out_of_time():
                ab.update(status="done", accepted=False, reason="time budget before prune validation")
                self.save()
                return
            try:
                version = self._rebuild(set(pruned))
                if version is None:
                    ab.update(status="done", accepted=False, reason="rebase conflict while removing the pruned changes")
                else:
                    val = self._val_of(version, ab["split_of"])
                    d = decide.paired_delta(val, final_val, ids, rerun_var=cal["rerun_var"], families=self.families)
                    ok = d["delta"] is not None and d["se"] is not None and d["delta"] >= -kill * d["se"]
                    ab.update(status="done", candidate=version.content_hash,
                              check=dict(delta=d["delta"], se=d["se"], n=d["n"]), accepted=ok,
                              reason=None if ok else "the pruned version is worse on the validation set")
                    if ok:
                        ab["final"] = version.content_hash
                        for i in pruned:
                            self._mark_pruned(units[i])
            except Exception as exc:
                failure = self._record_failure(exc)
                if failure:
                    raise (exc if isinstance(exc, EvaluationUnavailable)
                           else EvaluationUnavailable(f"ablation: {type(exc).__name__}: {exc}"))
                ab.update(status="done", accepted=False, reason=f"pruned version unavailable: {exc}")
        ab["wall_s"] = self.elapsed() - ab.get("started_elapsed", self.elapsed())
        self.ablation = ab
        self.save()

    def _mark_pruned(self, unit: dict) -> None:
        for rd in self.rounds:
            if rd["round"] != unit["round"]:
                continue
            for r in (rd.get("records") or {}).values():
                if r["card_index"] in unit.get("card_indices", []):
                    r["pruned"] = True
                    self.history.append(dict(parent=unit["parent"], title=r["title"], label="pruned"))

    def final_version(self) -> str:
        """The endpoint's final version: the pruned version when ablation accepted one, else the pointer."""
        return final_version(self.lineage, self.ablation)


def final_version(lineage: list[dict], ablation: Optional[dict]) -> str:
    ab = ablation or {}
    return ab["final"] if ab.get("status") == "done" and ab.get("accepted") else lineage[-1]["version"]


def _failure(exc: EvaluationUnavailable) -> str:
    return "budget_exhausted" if isinstance(exc, BudgetExhausted) else "process_failure"


# Recorded in every ledger: where this implementation departs from method-v4.md.
DEVIATIONS = [
    "SN keeps v2's 60/40 search/validation split; the test set is the iteration's registered sealed selection",
    "the seed's calibration A/A stands in for the parent's A/A in the mechanical check and the reviewer's control pairs",
    "ablation removes one lineage node at a time; a merge node (two cards) is removed as one unit",
    "the analyst sees categorical validation outcomes and mechanism tiers, never validation scores/cases or reviewer prose",
]
