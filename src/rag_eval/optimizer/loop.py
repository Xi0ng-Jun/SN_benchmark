"""Main v3 optimization loop orchestration.

Implements the §2.2 A→B state machine, §1 Ladder-gated screening,
milestone confirmation, rollback, validation-pool growth, and §2.5 halt
conditions from method-v3.md.  All SN-specific logic is behind SystemAdapter.

Concurrency (binding plan §2.6): per-card work — workspace, implement,
materialize, smoke, screening measurement, and any rebase + re-measurement —
runs in a thread pool of ``b_cards`` workers.  Every decision (Ladder gate,
provisional adoption, milestone confirmation, state transitions) is taken on
the main thread, one finished candidate at a time, in completion order.
Workers never touch loop state; they get immutable snapshots (parent version,
validation-pool ids) and return a CandidateRecord.
"""
from __future__ import annotations

import hashlib
import logging
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Optional

from .adapter import (
    BudgetExhausted,
    Card,
    Diff,
    EvalResult,
    EvaluationUnavailable,
    SmokeResult,
    SystemAdapter,
    Version,
)
from .ledger import Lineage, MilestoneState, NodeState
from .stats import (
    alpha_warning,
    attribution_label,
    confirm_decision,
    ladder_m,
    mean_delta,
    paired_differences,
    paired_se,
    screen_decision,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol configuration (preregistered before any data is seen)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProtocolConfig:
    """All parameters preregistered before the experiment starts.

    Notation matches method-v3.md §1 and §2.2.
    """
    mde: float                     # minimum detectable effect δ
    sigma_d: float                 # planned per-task paired-diff SD
    ladder_c: float                # Ladder scale constant (default 1.0)
    K_max: int                     # pre-registered max screening submissions
    T: float                       # milestone trigger threshold (2–4×MDE recommended)
    n_fresh: int                   # tasks per fresh batch
    z_screen: float = 0.84         # one-sided z for screening
    z_confirm: float = 1.65        # one-sided z for confirmation
    b_cards: int = 4               # parallel implementers (candidates in flight) per round
    k_screen: int = 3              # eval repetitions per task at screening
    k_smoke: int = 1               # eval repetitions for smoke sample
    n_smoke_sample: int = 5        # search-set tasks sampled for smoke check
    safeguard_bounds: dict = field(default_factory=dict)  # metric → max_delta
    max_confirmations: int = 10    # §7 α accumulation upper bound
    predicate_coverage_floor: float = 0.60  # §6 min fraction of cards with predicate
    # §2.3 analyst case selection (accountant-owned, fixed rule): tasks scoring
    # below the threshold on the parent's search runs, worst first, plus a few
    # deterministically chosen passing tasks for contrast.
    analyst_failure_threshold: float = 0.5
    analyst_max_failures: int = 12
    analyst_n_controls: int = 3
    # K_c: cards requested from the analyst per round (§2.1 step 1). Cards
    # beyond b_cards are the refill queue (§2.1 补位). None → b_cards.
    analyst_cards: Optional[int] = None


# ---------------------------------------------------------------------------
# Per-round book-keeping
# ---------------------------------------------------------------------------

# Candidate verdicts recorded in RoundResult.candidates (stable vocabulary).
CANDIDATE_VERDICTS = ("smoke_fail", "screen_fail", "screen_pass", "unmeasurable",
                      "stale_parent", "error")


@dataclass
class CandidateRecord:
    """One card tracked through implement → smoke → screen (→ rebase → ...).

    Filled by a worker thread; read by the main thread only after the
    worker's future has completed.
    """
    card: Card
    card_index: int
    parent: Version                     # version this candidate is built against
    candidate: Optional[Version] = None
    diff: Optional[Diff] = None
    smoke: Optional[SmokeResult] = None
    eval_result: Optional[EvalResult] = None   # candidate on the validation pool
    ref_result: Optional[EvalResult] = None    # parent on the same pool (paired)
    pool_size: int = 0                  # validation-pool size the measurement used
    stage: str = "measured"             # 'measured' | 'error' | 'smoke_fail' | 'stale_parent'
    reason: Optional[str] = None
    rebased_from: Optional[str] = None
    screen_evaluations: int = 0
    delta_val: Optional[float] = None
    se_val: Optional[float] = None
    screen_verdict: Optional[str] = None
    timings: dict = field(default_factory=dict)
    started_at: float = 0.0
    finished_at: float = 0.0


@dataclass
class RoundResult:
    """Summary of one optimization round.

    Field names are stable (§2.4 integrity rule): W5 persists them.
    """
    round_id: int
    cards_generated: int
    smoke_passed: int
    screen_passed: int
    milestone_triggered: bool
    confirmation_verdict: Optional[str]   # last of `confirmations`, or None
    rolled_back: bool
    halt_reason: Optional[str]
    # One dict per card that entered the pipeline, in decision order. Keys:
    # card_ref, card_index, verdict (CANDIDATE_VERDICTS), reason, parent,
    # candidate, rebased_from, smoke_verdict, delta_val, se_val, score_val,
    # best_before, ladder_m, val_pool_size, screen_evaluations, timings.
    candidates: list = field(default_factory=list)
    # One dict per milestone confirmation this round. Keys: verdict,
    # delta_fresh, se_fresh, n_fresh, fresh_batch_index, segment_start,
    # segment_end, val_pool_size, ladder_m, best_score.
    confirmations: list = field(default_factory=list)
    val_pool_size: int = 0                # at end of round
    ladder_m: float = 0.0                 # m in force at end of round
    best_score: float = 0.0               # best at end of round


@dataclass
class LoopResult:
    """Full result returned after the loop terminates."""
    # 'fresh_exhausted' | 'no_improvement' | 'no_candidates' | 'max_confirmations'
    # | 'max_rounds' | 'budget_exhausted' | 'process_failure'
    halt_reason: str
    lineage: Lineage
    rounds: list[RoundResult]
    alpha_note: dict      # from alpha_warning()


# ---------------------------------------------------------------------------
# Halt-condition tracker
# ---------------------------------------------------------------------------

class HaltTracker:
    def __init__(self) -> None:
        self.consecutive_no_screen_pass: int = 0
        self.consecutive_rejected_confirmations: int = 0
        self.fresh_batches_remaining: int = 0
        self.confirmations_used: int = 0

    def record_round(self, screen_passed: int, confirmation_verdict: Optional[str] = None) -> None:
        if screen_passed == 0:
            self.consecutive_no_screen_pass += 1
        else:
            self.consecutive_no_screen_pass = 0
        if confirmation_verdict is not None:
            self.record_confirmation(confirmation_verdict)

    def record_confirmation(self, verdict: str) -> None:
        if verdict == 'rejected':
            self.consecutive_rejected_confirmations += 1
        elif verdict in ('confirmed', 'insufficient'):
            self.consecutive_rejected_confirmations = 0
        if verdict in ('confirmed', 'rejected', 'insufficient'):
            self.confirmations_used += 1

    def can_confirm(self, max_confirmations: int) -> bool:
        return self.fresh_batches_remaining > 0 and self.confirmations_used < max_confirmations

    def halt_reason(self, max_confirmations: int) -> Optional[str]:
        if self.fresh_batches_remaining <= 0:
            return 'fresh_exhausted'
        if self.consecutive_rejected_confirmations >= 2:
            return 'no_improvement'
        if self.consecutive_no_screen_pass >= 2:
            return 'no_candidates'
        if self.confirmations_used >= max_confirmations:
            return 'max_confirmations'
        return None


def _failure_halt_reason(exc: EvaluationUnavailable) -> str:
    return 'budget_exhausted' if isinstance(exc, BudgetExhausted) else 'process_failure'


# ---------------------------------------------------------------------------
# Smoke check helper
# ---------------------------------------------------------------------------

def _run_smoke(
    adapter: SystemAdapter,
    candidate: Version,
    parent: Version,
    card: Card,
    smoke_task_ids: list[str],
) -> SmokeResult:
    """Run the cheap pre-screen behavioral check on n_smoke_sample tasks.

    With a predicate (§2.1 step 3a): check the feature rate moved in the
    predicted direction; fail the candidate if it did not.

    Without a predicate (§2.1 step 3b): run a generic smoke — the candidate
    must produce non-empty trajectories on every smoke task (i.e. it runs
    without infrastructure failure). Verdict is 'unchecked' for the
    behavioral dimension but the candidate is still discarded on infra fail.

    EvaluationUnavailable (incl. BudgetExhausted) propagates: a missing
    measurement is a process failure, not evidence against the candidate.
    """
    if card.predicate is None:
        # Generic smoke: candidate must run on the smoke sample.
        try:
            cand_eval, _ = adapter.evaluate_pair(candidate, parent, smoke_task_ids, 1, purpose='smoke')
        except EvaluationUnavailable:
            raise
        except Exception as exc:  # candidate-induced failure during smoke
            log.warning("generic smoke infra failure: %s", exc)
            return SmokeResult(
                candidate_version=candidate.content_hash,
                card_ref=card.failure_mode_desc[:40],
                behavioral_check_ran=False,
                feature_present_before=None,
                feature_present_after=None,
                verdict='fail',
            )
        ran = all(bool(cand_eval.trajectories.get(tid)) for tid in smoke_task_ids)
        return SmokeResult(
            candidate_version=candidate.content_hash,
            card_ref=card.failure_mode_desc[:40],
            behavioral_check_ran=False,
            feature_present_before=None,
            feature_present_after=None,
            verdict='pass' if ran else 'fail',
        )
    # Predicate present: behavioral smoke.
    cand_eval, parent_eval = adapter.evaluate_pair(candidate, parent, smoke_task_ids, 1, purpose='smoke')
    before = [v for v in adapter.features(parent_eval.trajectories, card.predicate).values() if v is not None]
    after = [v for v in adapter.features(cand_eval.trajectories, card.predicate).values() if v is not None]
    if not before or not after:
        # Predicate not computable on the smoke sample: behaviour unchecked, not failed.
        return SmokeResult(
            candidate_version=candidate.content_hash,
            card_ref=card.failure_mode_desc[:40],
            behavioral_check_ran=False,
            feature_present_before=None,
            feature_present_after=None,
            verdict='unchecked',
        )
    r_before = sum(before) / len(before)
    r_after = sum(after) / len(after)
    passed = (card.predicate.predicted_direction == 'decrease' and r_after < r_before) or \
             (card.predicate.predicted_direction == 'increase' and r_after > r_before)
    return SmokeResult(
        candidate_version=candidate.content_hash,
        card_ref=card.failure_mode_desc[:40],
        behavioral_check_ran=True,
        feature_present_before=r_before > 0.5,
        feature_present_after=r_after > 0.5,
        verdict='pass' if passed else 'fail',
    )


# ---------------------------------------------------------------------------
# Safeguard checker
# ---------------------------------------------------------------------------

def _safeguards_ok(eval_result: EvalResult, bounds: dict) -> bool:
    """Return True iff no safeguard metric breaches its registered bound."""
    for metric, max_delta in bounds.items():
        val = eval_result.safeguard_values.get(metric, 0.0)
        if val > max_delta:
            log.warning("safeguard breach: %s=%.4f > %.4f", metric, val, max_delta)
            return False
    return True


# ---------------------------------------------------------------------------
# Worker-side pipeline (runs in the thread pool; touches no loop state)
# ---------------------------------------------------------------------------

def _stop(rec: CandidateRecord, stage: str, reason: Optional[str] = None) -> CandidateRecord:
    rec.stage = stage
    rec.reason = reason
    rec.finished_at = time.monotonic()
    rec.timings['total_s'] = rec.finished_at - rec.started_at
    return rec


def _build_and_measure(adapter: SystemAdapter, config: ProtocolConfig, rec: CandidateRecord,
                       smoke_ids: list[str], pool: list[str]) -> CandidateRecord:
    """workspace → implement → materialize → smoke → screening measurement."""
    rec.started_at = time.monotonic()
    t0 = rec.started_at
    for step in ('workspace', 'implement', 'materialize'):
        try:
            if step == 'workspace':
                ws = adapter.workspace(rec.parent, rec.card)
            elif step == 'implement':
                rec.diff = adapter.implement(rec.card, ws)
            else:
                rec.candidate = adapter.materialize(rec.diff, rec.parent)
        except EvaluationUnavailable:
            raise
        except Exception as exc:  # per-card failure: skip card, refill
            log.warning("%s failed on card %r: %s", step, rec.card.failure_mode_desc[:40], exc)
            rec.timings['implement_s'] = time.monotonic() - t0
            return _stop(rec, 'error', f"{step}: {type(exc).__name__}: {exc}")
    rec.timings['implement_s'] = time.monotonic() - t0
    return _smoke_and_screen(adapter, config, rec, smoke_ids, pool)


def _smoke_and_screen(adapter: SystemAdapter, config: ProtocolConfig, rec: CandidateRecord,
                      smoke_ids: list[str], pool: list[str]) -> CandidateRecord:
    t0 = time.monotonic()
    try:
        rec.smoke = _run_smoke(adapter, rec.candidate, rec.parent, rec.card, smoke_ids)
    except EvaluationUnavailable:
        raise
    except Exception as exc:
        rec.timings['smoke_s'] = rec.timings.get('smoke_s', 0.0) + time.monotonic() - t0
        return _stop(rec, 'error', f"smoke: {type(exc).__name__}: {exc}")
    rec.timings['smoke_s'] = rec.timings.get('smoke_s', 0.0) + time.monotonic() - t0
    if rec.smoke.verdict == 'fail':
        return _stop(rec, 'smoke_fail')
    return _screen(adapter, config, rec, pool)


def _screen(adapter: SystemAdapter, config: ProtocolConfig, rec: CandidateRecord,
            pool: list[str]) -> CandidateRecord:
    """Paired candidate-vs-parent measurement on the validation pool (§2.2 A)."""
    if not rec.started_at:
        rec.started_at = time.monotonic()
    t0 = time.monotonic()
    try:
        rec.eval_result, rec.ref_result = adapter.evaluate_pair(
            rec.candidate, rec.parent, pool, config.k_screen, purpose='screen')
    except EvaluationUnavailable:
        raise
    except Exception as exc:
        rec.timings['screen_s'] = rec.timings.get('screen_s', 0.0) + time.monotonic() - t0
        return _stop(rec, 'error', f"screen: {type(exc).__name__}: {exc}")
    rec.timings['screen_s'] = rec.timings.get('screen_s', 0.0) + time.monotonic() - t0
    rec.pool_size = len(pool)
    rec.screen_evaluations += 1
    return _stop(rec, 'measured')


def _rebase_and_measure(adapter: SystemAdapter, config: ProtocolConfig, rec: CandidateRecord,
                        new_parent: Version, smoke_ids: list[str],
                        pool: list[str]) -> CandidateRecord:
    """§2.2 A precondition: re-apply onto the current parent, re-smoke, re-screen."""
    t0 = time.monotonic()
    reason = None
    try:
        rebased = adapter.rebase(rec.candidate, new_parent)
    except EvaluationUnavailable:
        raise
    except Exception as exc:
        rebased, reason = None, f"rebase: {type(exc).__name__}: {exc}"
    rec.timings['rebase_s'] = rec.timings.get('rebase_s', 0.0) + time.monotonic() - t0
    if rebased is None:
        return _stop(rec, 'stale_parent', reason or f"parent moved to {new_parent.content_hash}")
    rec.rebased_from = rec.candidate.content_hash
    rec.candidate = rebased
    rec.parent = new_parent
    rec.eval_result = rec.ref_result = None
    return _smoke_and_screen(adapter, config, rec, smoke_ids, pool)


# ---------------------------------------------------------------------------
# Main optimization loop
# ---------------------------------------------------------------------------

def run_optimization(
    adapter: SystemAdapter,
    seed: Version,
    config: ProtocolConfig,
    search_set_ids: list[str],
    val_pool_ids: list[str],
    fresh_batch_ids: list[list[str]],
    smoke_sample_ids: list[str],
    max_rounds: int = 50,
) -> LoopResult:
    """Run the full v3 optimization loop.

    Parameters
    ----------
    adapter : SystemAdapter
        SN-specific four-capability adapter. Must be thread-safe: per-card
        work for up to ``config.b_cards`` candidates runs concurrently.
    seed : Version
        Starting parent version (the harness baseline).
    config : ProtocolConfig
        All pre-registered parameters.
    search_set_ids : list[str]
        Search-set task ids (analyst reads failures here).
    val_pool_ids : list[str]
        Initial validation pool task ids (used for screening). The loop works
        on a copy that grows as fresh batches are folded in; the caller's
        list is not mutated.
    fresh_batch_ids : list[list[str]]
        Ordered list of fresh batches; each consumed once on confirmation.
    smoke_sample_ids : list[str]
        Search-set tasks used for the cheap smoke check (n_smoke_sample of them).
    max_rounds : int
        Hard outer bound on rounds.

    Returns
    -------
    LoopResult
    """
    return _Run(adapter, seed, config, search_set_ids, val_pool_ids, fresh_batch_ids,
                smoke_sample_ids).run(max_rounds)


class _Run:
    """Loop state; every method runs on the main thread."""

    def __init__(self, adapter: SystemAdapter, seed: Version, config: ProtocolConfig,
                 search_set_ids: list[str], val_pool_ids: list[str],
                 fresh_batch_ids: list[list[str]], smoke_sample_ids: list[str]) -> None:
        self.adapter = adapter
        self.seed = seed
        self.config = config
        self.search_set_ids = list(search_set_ids)
        self.pool: list[str] = list(val_pool_ids)
        self.fresh_batch_ids = fresh_batch_ids
        self.smoke_ids = list(smoke_sample_ids)
        self.lineage = Lineage()
        # Real Version objects by content hash: adapters need their metadata (SN:
        # candidate_id), which the lineage's hash-only nodes do not carry.
        self.versions: dict[str, Version] = {seed.content_hash: seed}
        # Validation-pool results of parents, per (version hash, pool size).
        # The pool only grows, so its size identifies it.
        self.val_cache: dict[tuple[str, int], EvalResult] = {}
        self.milestone = MilestoneState(
            cumulative_delta_val=0.0,
            best_score=float('-inf'),
            last_certified_id=None,
            current_provisional_id=None,
            segment_start_best=float('-inf'),
        )
        self.halt = HaltTracker()
        self.halt.fresh_batches_remaining = len(fresh_batch_ids)
        self.m = ladder_m(config.sigma_d, config.K_max, len(self.pool), config.ladder_c)
        self.rounds: list[RoundResult] = []

    # -- state helpers ------------------------------------------------------

    def pointer(self) -> Version:
        """The current parent pointer (latest non-rolled-back lineage node)."""
        return self.versions[self.lineage.current_parent().version_hash]

    def val_eval(self, version: Version, purpose: str = 'screen') -> EvalResult:
        key = (version.content_hash, len(self.pool))
        if key not in self.val_cache:
            self.val_cache[key] = self.adapter.evaluate(version, list(self.pool),
                                                        self.config.k_screen, purpose=purpose)
        return self.val_cache[key]

    def _result(self, reason: str) -> LoopResult:
        return LoopResult(reason, self.lineage, self.rounds,
                          alpha_warning(self.halt.confirmations_used))

    # -- top level ----------------------------------------------------------

    def run(self, max_rounds: int) -> LoopResult:
        seed = self.seed
        # Seed node: certified by definition (it is the baseline).
        self.lineage.add_provisional(version_hash=seed.content_hash, parent_hash=None,
                                     diff_ref=None, card_ref=None, predicate_ref=None,
                                     agent_cost=0.0)
        self.lineage.certify(seed.content_hash, fresh_result={})
        self.milestone.last_certified_id = seed.content_hash
        self.milestone.current_provisional_id = seed.content_hash
        try:
            seed_eval = self.val_eval(seed, purpose='baseline')
        except EvaluationUnavailable as exc:
            log.error("baseline evaluation unavailable: %s", exc)
            return self._result(_failure_halt_reason(exc))
        self.milestone.best_score = _mean_score(seed_eval)
        self.milestone.segment_start_best = self.milestone.best_score

        round_id = 0
        while round_id < max_rounds:
            reason = self.halt.halt_reason(self.config.max_confirmations)
            if reason is not None:
                return self._result(reason)
            round_id += 1
            round_res = RoundResult(round_id=round_id, cards_generated=0, smoke_passed=0,
                                    screen_passed=0, milestone_triggered=False,
                                    confirmation_verdict=None, rolled_back=False,
                                    halt_reason=None)
            self.rounds.append(round_res)
            try:
                failure = self._round(round_res)
            except EvaluationUnavailable as exc:
                log.error("evaluation unavailable in round %d: %s", round_id, exc)
                failure = _failure_halt_reason(exc)
            round_res.val_pool_size = len(self.pool)
            round_res.ladder_m = self.m
            round_res.best_score = self.milestone.best_score
            if failure is not None:
                round_res.halt_reason = failure
                return self._result(failure)
            self.halt.record_round(round_res.screen_passed)

        return self._result(self.halt.halt_reason(self.config.max_confirmations) or 'max_rounds')

    def _round(self, round_res: RoundResult) -> Optional[str]:
        """One round; returns a failure halt reason or None."""
        parent_v = self.pointer()
        # --- 1. Diagnosis: analyst reads search-set failures ---
        # Search-set runs share k with screening (one SN measure covers search ∪ validation).
        parent_eval_search = self.adapter.evaluate(parent_v, self.search_set_ids,
                                                   self.config.k_screen, purpose='diagnose')
        selected, roles = select_analysis_cases(parent_eval_search, self.config)
        n_cards = self.config.analyst_cards or self.config.b_cards
        cards = self.adapter.analyse(selected, n_cards, case_roles=roles,
                                     parent=parent_v, round_id=round_res.round_id)
        round_res.cards_generated = len(cards)
        if not cards:
            return None
        # --- 2–5. Parallel candidates, sequential decisions ---
        return self._pipeline(cards, round_res)

    # -- parallel candidate pipeline ---------------------------------------

    def _pipeline(self, cards: list[Card], round_res: RoundResult) -> Optional[str]:
        """Implement/smoke/screen up to b cards concurrently; decide in completion order.

        Refill (§2.1 补位): every non-adopted outcome frees a slot for the next
        unused card, built on the parent pointer at submission time. After a
        milestone this round no new card is submitted; candidates already in
        flight finish and go through the parent-hash rule (rebase/re-screen).
        On EvaluationUnavailable nothing new is submitted and in-flight workers
        are drained (recorded, not decided) before the round returns.
        """
        b = max(1, self.config.b_cards)
        queue = list(enumerate(cards))
        pending: dict[Future, CandidateRecord] = {}
        stop_new = False
        failure: Optional[str] = None

        with ThreadPoolExecutor(max_workers=b, thread_name_prefix='v3-cand') as pool_ex:
            def submit_next_card() -> None:
                idx, card = queue.pop(0)
                rec = CandidateRecord(card=card, card_index=idx, parent=self.pointer())
                fut = pool_ex.submit(_build_and_measure, self.adapter, self.config, rec,
                                     self.smoke_ids, list(self.pool))
                pending[fut] = rec

            while queue and len(pending) < b:
                submit_next_card()

            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                finished: list[CandidateRecord] = []
                for fut in done:
                    rec = pending.pop(fut)
                    exc = fut.exception()
                    if exc is None:
                        finished.append(rec)
                        continue
                    if not isinstance(exc, EvaluationUnavailable):
                        raise exc
                    log.error("evaluation unavailable for card %r: %s",
                              rec.card.failure_mode_desc[:40], exc)
                    failure = failure or _failure_halt_reason(exc)
                    self._record(round_res, rec, 'error', f"{type(exc).__name__}: {exc}")
                # Several may finish between two wake-ups: keep completion order.
                finished.sort(key=lambda r: r.finished_at)
                for rec in finished:
                    if failure is not None:
                        self._record(round_res, rec, 'error', f"halted: {failure}")
                        continue
                    try:
                        action = self._decide(rec, round_res)
                    except EvaluationUnavailable as exc:  # milestone / re-measurement
                        log.error("evaluation unavailable while deciding: %s", exc)
                        failure = _failure_halt_reason(exc)
                        continue
                    if action == 'rebase':
                        fut = pool_ex.submit(_rebase_and_measure, self.adapter, self.config, rec,
                                             self.pointer(), self.smoke_ids, list(self.pool))
                        pending[fut] = rec
                    elif action == 'rescreen':
                        fut = pool_ex.submit(_screen, self.adapter, self.config, rec,
                                             list(self.pool))
                        pending[fut] = rec
                    elif action == 'milestone':
                        stop_new = True
                    elif action == 'refill' and not stop_new and queue and len(pending) < b:
                        submit_next_card()
        return failure

    def _decide(self, rec: CandidateRecord, round_res: RoundResult) -> str:
        """Apply the §2.2 A decision to one finished candidate.

        Returns 'refill' (discarded), 'adopted', 'milestone' (adopted and a
        confirmation ran), 'rebase' or 'rescreen' (measurement not decidable
        against the current parent pointer / validation pool).
        """
        if rec.stage != 'measured':
            self._record(round_res, rec, rec.stage, rec.reason)
            return 'refill'
        self.versions[rec.candidate.content_hash] = rec.candidate
        pointer = self.pointer()
        if rec.parent.content_hash != pointer.content_hash:
            return 'rebase'
        if rec.pool_size != len(self.pool):
            # Same parent, but a confirmation grew the pool since the
            # measurement: best and m now refer to the enlarged pool.
            return 'rescreen'

        cand_ev, ref_ev = rec.eval_result, rec.ref_result
        self.val_cache.setdefault((pointer.content_hash, len(self.pool)), ref_ev)
        d = paired_differences(_score_list(cand_ev, self.pool), _score_list(ref_ev, self.pool))
        rec.delta_val = mean_delta(d)
        rec.se_val = paired_se(d)
        score = _mean_score(cand_ev)
        best_before = self.milestone.best_score
        rec.screen_verdict = screen_decision(
            rec.delta_val, rec.se_val, self.config.z_screen,
            best_before, score, self.m, _safeguards_ok(cand_ev, self.config.safeguard_bounds),
        )
        self._record(round_res, rec, rec.screen_verdict, None,
                     score_val=score, best_before=best_before)
        if rec.screen_verdict != 'screen_pass':
            return 'refill'

        # --- Provisional adoption: parent pointer advances, best climbs ---
        round_res.screen_passed += 1
        cand = rec.candidate
        self.lineage.add_provisional(
            version_hash=cand.content_hash,
            parent_hash=pointer.content_hash,
            diff_ref=rec.diff.content[:60] if rec.diff else None,
            card_ref=rec.card.failure_mode_desc[:60],
            predicate_ref=(rec.card.predicate.boolean_expression if rec.card.predicate else None),
            agent_cost=sum(cand_ev.costs.values()),
        )
        self.val_cache[(cand.content_hash, len(self.pool))] = cand_ev
        self.milestone.current_provisional_id = cand.content_hash
        self.milestone.cumulative_delta_val += rec.delta_val
        self.milestone.best_score = score

        # --- Milestone trigger (§2.2 B) ---
        if self.milestone.cumulative_delta_val < self.config.T:
            return 'adopted'
        if not self.halt.can_confirm(self.config.max_confirmations):
            # No fresh batch / confirmation budget left: the segment stays
            # provisional and the loop halts at the next round boundary.
            log.warning("milestone triggered but no confirmation available; segment stays provisional")
            return 'adopted'
        round_res.milestone_triggered = True
        conf = self._milestone_confirm()
        round_res.confirmations.append(conf)
        round_res.confirmation_verdict = conf['verdict']
        if conf['verdict'] != 'confirmed':
            round_res.rolled_back = True
        self.halt.record_confirmation(conf['verdict'])
        return 'milestone'

    def _record(self, round_res: RoundResult, rec: CandidateRecord, verdict: str,
                reason: Optional[str], *, score_val: Optional[float] = None,
                best_before: Optional[float] = None) -> None:
        if rec.smoke is not None and rec.smoke.verdict != 'fail' and verdict != 'smoke_fail':
            round_res.smoke_passed += 1
        if (verdict == 'error' and rec.delta_val is None and rec.eval_result is not None
                and rec.ref_result is not None and rec.pool_size):
            # Measured but undecided (halted): keep Δ for selection accounting.
            pool = self.pool[:rec.pool_size]
            d = paired_differences(_score_list(rec.eval_result, pool),
                                   _score_list(rec.ref_result, pool))
            rec.delta_val, rec.se_val = mean_delta(d), paired_se(d)
        round_res.candidates.append(dict(
            card_ref=rec.card.ref or rec.card.failure_mode_desc[:60],
            card_index=rec.card_index,
            verdict=verdict,
            reason=reason,
            parent=rec.parent.content_hash,
            candidate=rec.candidate.content_hash if rec.candidate else None,
            rebased_from=rec.rebased_from,
            smoke_verdict=rec.smoke.verdict if rec.smoke else None,
            delta_val=rec.delta_val,
            se_val=rec.se_val,
            score_val=score_val,
            best_before=best_before,
            ladder_m=self.m,
            val_pool_size=rec.pool_size or None,
            screen_evaluations=rec.screen_evaluations,
            timings=dict(rec.timings),
        ))

    # -- milestone confirmation ---------------------------------------------

    def _milestone_confirm(self) -> dict:
        """Fresh-batch confirmation of segment end vs segment start (§2.2 B).

        Applies the state-transition table, then folds the batch into the
        validation pool (every outcome), recomputes m, and re-measures the
        post-transition parent pointer on the enlarged pool to reset best.
        """
        cfg, ms = self.config, self.milestone
        batch_index = len(self.fresh_batch_ids) - self.halt.fresh_batches_remaining
        fresh_ids = list(self.fresh_batch_ids[batch_index])
        start_v = self.versions[ms.last_certified_id]
        end_v = self.versions[ms.current_provisional_id]
        end_eval, start_eval = self.adapter.evaluate_pair(end_v, start_v, fresh_ids,
                                                          cfg.k_screen, purpose='confirm')
        self.halt.fresh_batches_remaining -= 1
        d = paired_differences(_score_list(end_eval, fresh_ids), _score_list(start_eval, fresh_ids))
        delta_fresh = mean_delta(d)
        se_fresh = paired_se(d)
        verdict = confirm_decision(delta_fresh, se_fresh, cfg.z_confirm,
                                   _safeguards_ok(end_eval, cfg.safeguard_bounds))

        if verdict == 'confirmed':
            self.lineage.certify(end_v.content_hash, fresh_result={
                'delta_fresh': delta_fresh, 'se_fresh': se_fresh, 'fresh_ids': fresh_ids,
            })
            ms.last_certified_id = end_v.content_hash
            new_pointer = end_v
        else:
            # rejected / insufficient / unmeasurable: the whole provisional
            # segment is discarded; the pointer returns to the last certified node.
            for node in self.lineage.provisional_segment(ms.last_certified_id):
                self.lineage.rollback(node.version_hash)
            new_pointer = start_v
        ms.current_provisional_id = new_pointer.content_hash
        ms.cumulative_delta_val = 0.0
        ms.n_confirmations += 1

        # Fold F_i into the validation pool (all outcomes), then recompute m
        # and best on the enlarged pool. On rollback the reference is the last
        # certified node measured on the enlarged pool, not the old best.
        known = set(self.pool)
        self.pool.extend(t for t in fresh_ids if t not in known)
        self.m = ladder_m(cfg.sigma_d, cfg.K_max, len(self.pool), cfg.ladder_c)
        ms.best_score = _mean_score(self.val_eval(new_pointer))
        ms.segment_start_best = ms.best_score
        return dict(
            verdict=verdict,
            delta_fresh=delta_fresh,
            se_fresh=se_fresh,
            n_fresh=len(fresh_ids),
            fresh_batch_index=batch_index,
            segment_start=start_v.content_hash,
            segment_end=end_v.content_hash,
            val_pool_size=len(self.pool),
            ladder_m=self.m,
            best_score=ms.best_score,
        )


# ---------------------------------------------------------------------------
# Analyst case selection (§2.3)
# ---------------------------------------------------------------------------

def select_analysis_cases(
    search_eval: EvalResult, config: ProtocolConfig,
) -> tuple[dict[str, list], dict[str, str]]:
    """Pick the search cases the analyst sees; returns (trajectories, roles).

    Failures: score < analyst_failure_threshold, worst first (ties by task id).
    Controls: passing tasks ordered by sha256(task_id), so the choice is fixed
    and does not depend on which candidate is being diagnosed. The analyst
    receives only these trajectory stubs and a failure/control label, no scores.
    """
    scores = search_eval.per_task_scores
    failures = sorted((t for t, s in scores.items() if s < config.analyst_failure_threshold),
                      key=lambda t: (scores[t], t))[:config.analyst_max_failures]
    passing = sorted((t for t, s in scores.items() if s >= config.analyst_failure_threshold),
                     key=lambda t: hashlib.sha256(t.encode()).hexdigest())[:config.analyst_n_controls]
    roles = {**{t: 'failure' for t in failures}, **{t: 'control' for t in passing}}
    trajectories = {t: search_eval.trajectories.get(t, []) for t in roles}
    return trajectories, roles


# ---------------------------------------------------------------------------
# Small score helpers
# ---------------------------------------------------------------------------

def _mean_score(ev: EvalResult) -> float:
    scores = list(ev.per_task_scores.values())
    return sum(scores) / len(scores) if scores else 0.0


def _score_list(ev: EvalResult, task_ids: list[str]) -> list[float]:
    return [ev.per_task_scores.get(tid, 0.0) for tid in task_ids]
