"""Main v3 optimization loop orchestration.

Implements the §2.2 A→B state machine, §1 Ladder-gated screening,
milestone confirmation, rollback, and §2.5 halt conditions from
method-v3.md.  All SN-specific logic is behind SystemAdapter.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .adapter import Card, Diff, EvalResult, SmokeResult, SystemAdapter, Version
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
    b_cards: int = 4               # analyst cards per round
    k_screen: int = 3              # eval repetitions per task at screening
    k_smoke: int = 1               # eval repetitions for smoke sample
    n_smoke_sample: int = 5        # search-set tasks sampled for smoke check
    safeguard_bounds: dict = field(default_factory=dict)  # metric → max_delta
    max_confirmations: int = 10    # §7 α accumulation upper bound
    predicate_coverage_floor: float = 0.60  # §6 min fraction of cards with predicate


# ---------------------------------------------------------------------------
# Per-round book-keeping
# ---------------------------------------------------------------------------

@dataclass
class CandidateRecord:
    """One candidate being tracked through the screen→confirm pipeline."""
    candidate: Version
    parent: Version            # version this candidate was built against
    diff: Diff
    card: Card
    smoke: SmokeResult
    eval_result: Optional[EvalResult] = None
    delta_val: Optional[float] = None
    se_val: Optional[float] = None
    screen_verdict: Optional[str] = None


@dataclass
class RoundResult:
    """Summary of one optimization round."""
    round_id: int
    cards_generated: int
    smoke_passed: int
    screen_passed: int
    milestone_triggered: bool
    confirmation_verdict: Optional[str]   # 'confirmed' | 'rejected' | 'insufficient' | None
    rolled_back: bool
    halt_reason: Optional[str]


@dataclass
class LoopResult:
    """Full result returned after the loop terminates."""
    halt_reason: str      # 'fresh_exhausted' | 'no_improvement' | 'no_candidates' | 'max_confirmations'
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

    def record_round(self, screen_passed: int, confirmation_verdict: Optional[str]) -> None:
        if screen_passed == 0:
            self.consecutive_no_screen_pass += 1
        else:
            self.consecutive_no_screen_pass = 0
        if confirmation_verdict == 'rejected':
            self.consecutive_rejected_confirmations += 1
        elif confirmation_verdict in ('confirmed', 'insufficient'):
            self.consecutive_rejected_confirmations = 0
        if confirmation_verdict in ('confirmed', 'rejected', 'insufficient'):
            self.confirmations_used += 1

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

    When no predicate is given, verdict is 'unchecked' (not a failure).
    When a predicate is given, checks that the feature rate dropped on the
    smoke sample.
    """
    if card.predicate is None:
        return SmokeResult(
            candidate_version=candidate.content_hash,
            card_ref=card.failure_mode_desc[:40],
            behavioral_check_ran=False,
            feature_present_before=None,
            feature_present_after=None,
            verdict='unchecked',
        )
    # Evaluate both versions on smoke sample
    parent_eval = adapter.evaluate(parent, smoke_task_ids, k=1)
    cand_eval = adapter.evaluate(candidate, smoke_task_ids, k=1)
    before_features = adapter.features(parent_eval.trajectories, card.predicate)
    after_features = adapter.features(cand_eval.trajectories, card.predicate)
    r_before = sum(before_features.values()) / max(1, len(before_features))
    r_after = sum(after_features.values()) / max(1, len(after_features))
    # Predicate should have a lower activation rate after the fix
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
        SN-specific four-capability adapter.
    seed : Version
        Starting parent version (the harness baseline).
    config : ProtocolConfig
        All pre-registered parameters.
    search_set_ids : list[str]
        Search-set task ids (analyst reads failures here).
    val_pool_ids : list[str]
        Validation pool task ids (used for screening; grows as fresh batches
        are folded in).
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
    lineage = Lineage()
    milestone = MilestoneState(
        cumulative_delta_val=0.0,
        best_score=float('-inf'),
        last_certified_id=None,
        current_provisional_id=None,
        segment_start_best=float('-inf'),
    )
    # Seed node: certified by definition (it is the baseline).
    lineage.add_provisional(
        version_hash=seed.content_hash,
        parent_hash=None,
        diff_ref=None,
        card_ref=None,
        predicate_ref=None,
        agent_cost=0.0,
    )
    seed_node = lineage.current_parent()
    lineage.certify(seed.content_hash, fresh_result={})
    milestone.last_certified_id = seed.content_hash
    milestone.current_provisional_id = seed.content_hash

    # Measure seed on validation pool to set best_score.
    seed_eval = adapter.evaluate(seed, val_pool_ids, config.k_screen)
    milestone.best_score = _mean_score(seed_eval)
    milestone.segment_start_best = milestone.best_score

    # Ladder threshold m, constant within a run (K and n_val fixed).
    m = ladder_m(config.sigma_d, config.K_max, len(val_pool_ids), config.ladder_c)

    halt = HaltTracker()
    halt.fresh_batches_remaining = len(fresh_batch_ids)

    rounds: list[RoundResult] = []
    round_id = 0

    while round_id < max_rounds:
        reason = halt.halt_reason(config.max_confirmations)
        if reason is not None:
            return LoopResult(reason, lineage, rounds, alpha_warning(halt.confirmations_used))

        round_id += 1
        round_res = RoundResult(
            round_id=round_id,
            cards_generated=0,
            smoke_passed=0,
            screen_passed=0,
            milestone_triggered=False,
            confirmation_verdict=None,
            rolled_back=False,
            halt_reason=None,
        )

        parent = lineage.current_parent()
        parent_version = parent.version_hash

        # --- 1. Diagnosis: analyst reads search-set failures ---
        # Build trajectories dict for the analyst: search-set failures only.
        parent_eval_search = adapter.evaluate(
            Version(parent_version, metadata={}), search_set_ids, config.k_smoke,
        )
        cards = adapter.analyse(parent_eval_search.trajectories, config.b_cards)
        round_res.cards_generated = len(cards)
        if not cards:
            halt.record_round(screen_passed=0, confirmation_verdict=None)
            rounds.append(round_res)
            continue

        # --- 2. Implement + smoke, filling back up to b_cards that reach screen ---
        candidates: list[CandidateRecord] = []
        card_idx = 0
        while len(candidates) < config.b_cards and card_idx < len(cards):
            card = cards[card_idx]
            card_idx += 1
            ws = adapter.workspace(Version(parent_version, metadata={}))
            try:
                diff = adapter.implement(card, ws)
            except Exception as exc:  # implementer failure: skip card
                log.warning("implementer failed on card %r: %s", card.failure_mode_desc[:40], exc)
                continue
            try:
                cand_version = adapter.materialize(diff, Version(parent_version, metadata={}))
            except Exception as exc:  # materialize failure: skip card
                log.warning("materialize failed: %s", exc)
                continue
            smoke = _run_smoke(adapter, cand_version, Version(parent_version, metadata={}),
                               card, smoke_sample_ids)
            if smoke.verdict == 'fail':
                # Cheap discard; do not count toward screen-eligible count.
                continue
            candidates.append(CandidateRecord(
                candidate=cand_version,
                parent=Version(parent_version, metadata={}),
                diff=diff,
                card=card,
                smoke=smoke,
            ))
            round_res.smoke_passed += 1

        if not candidates:
            halt.record_round(screen_passed=0, confirmation_verdict=None)
            rounds.append(round_res)
            continue

        # --- 3. Screen each candidate against current best (Ladder gate) ---
        certified_best = milestone.best_score
        confirmed_this_round = None
        for rec in candidates:
            ev = adapter.evaluate(rec.candidate, val_pool_ids, config.k_screen,
                                   reference_version=rec.parent)
            rec.eval_result = ev
            d = paired_differences(
                _score_list(ev, val_pool_ids),
                _score_list(adapter.evaluate(rec.parent, val_pool_ids, config.k_screen),
                            val_pool_ids),
            )
            rec.delta_val = mean_delta(d)
            rec.se_val = paired_se(d)
            ok = _safeguards_ok(ev, config.safeguard_bounds)
            rec.screen_verdict = screen_decision(
                rec.delta_val, rec.se_val, config.z_screen,
                certified_best, _mean_score(ev), m, ok,
            )
            if rec.screen_verdict == 'screen_pass':
                round_res.screen_passed += 1
                # Tentative advance: provisional node.
                lineage.add_provisional(
                    version_hash=rec.candidate.content_hash,
                    parent_hash=parent_version,
                    diff_ref=rec.diff.content[:60],
                    card_ref=rec.card.failure_mode_desc[:60],
                    predicate_ref=(rec.card.predicate.boolean_expression
                                    if rec.card.predicate else None),
                    agent_cost=sum(ev.costs.values()),
                )
                milestone.current_provisional_id = rec.candidate.content_hash
                milestone.cumulative_delta_val += rec.delta_val
                # Advance best for subsequent candidates this round (Ladder keeps climbing).
                certified_best = max(certified_best, _mean_score(ev))
                # --- 4. Milestone trigger check ---
                if milestone.cumulative_delta_val >= config.T:
                    round_res.milestone_triggered = True
                    verdict = _milestone_confirm(
                        adapter, lineage, milestone, config, fresh_batch_ids, halt,
                    )
                    round_res.confirmation_verdict = verdict
                    if verdict == 'confirmed':
                        # Segment certified; reset cumulative and continue from new best.
                        milestone.cumulative_delta_val = 0.0
                    elif verdict in ('rejected', 'insufficient', 'unmeasurable'):
                        # §2.2 state-transition table: all three outcomes roll back.
                        # 'insufficient' does NOT keep state (that would allow sequential
                        # α inflation by re-triggering on the same provisional segment).
                        round_res.rolled_back = True
                        milestone.cumulative_delta_val = 0.0
                        milestone.best_score = milestone.segment_start_best
                        milestone.current_provisional_id = milestone.last_certified_id
                        break  # restart diagnosis next round
                    # 'confirmed': continue inner candidates loop if any remain.
                    break  # one milestone per round, by design
                # Not at milestone yet: continue to next candidate.
        # End of candidates loop.

        halt.record_round(round_res.screen_passed, round_res.confirmation_verdict)
        rounds.append(round_res)

    return LoopResult(
        halt.halt_reason(config.max_confirmations) or 'max_rounds',
        lineage, rounds, alpha_warning(halt.confirmations_used),
    )


# ---------------------------------------------------------------------------
# Milestone confirmation
# ---------------------------------------------------------------------------

def _milestone_confirm(
    adapter: SystemAdapter,
    lineage: Lineage,
    milestone: MilestoneState,
    config: ProtocolConfig,
    fresh_batch_ids: list[list[str]],
    halt: HaltTracker,
) -> str:
    """Run a fresh-batch confirmation between segment start and segment end.

    Consumes one fresh batch.  On 'confirmed' or 'insufficient' the batch is
    folded into the validation pool (caller updates val_pool_ids).  On
    'rejected' the segment is rolled back to the last certified node.
    """
    if halt.fresh_batches_remaining <= 0:
        log.warning("milestone triggered but no fresh batch left; treating as insufficient")
        return 'insufficient'
    fresh_ids = fresh_batch_ids[len(fresh_batch_ids) - halt.fresh_batches_remaining]
    halt.fresh_batches_remaining -= 1

    start_node = lineage._get(milestone.last_certified_id)  # noqa: SLF001 (internal)
    end_node = lineage._get(milestone.current_provisional_id)  # noqa: SLF001

    start_eval = adapter.evaluate(Version(start_node.version_hash, metadata={}),
                                  fresh_ids, config.k_screen)
    end_eval = adapter.evaluate(Version(end_node.version_hash, metadata={}),
                                fresh_ids, config.k_screen,
                                reference_version=Version(start_node.version_hash, metadata={}))
    d = paired_differences(
        _score_list(end_eval, fresh_ids),
        _score_list(start_eval, fresh_ids),
    )
    delta_fresh = mean_delta(d)
    se_fresh = paired_se(d)
    ok = _safeguards_ok(end_eval, config.safeguard_bounds)
    verdict = confirm_decision(delta_fresh, se_fresh, config.z_confirm, ok)

    if verdict == 'confirmed':
        lineage.certify(end_node.version_hash, fresh_result={
            'delta_fresh': delta_fresh, 'se_fresh': se_fresh,
            'fresh_ids': fresh_ids,
        })
        milestone.last_certified_id = end_node.version_hash
        milestone.segment_start_best = _mean_score(end_eval)
        milestone.best_score = max(milestone.best_score, _mean_score(end_eval))
    else:
        # §2.2 state-transition table: rejected, insufficient, and unmeasurable
        # all roll back the provisional segment to the last certified node.
        # best is restored to segment_start_best by the caller.
        for node in lineage.provisional_segment(milestone.last_certified_id):
            lineage.rollback(node.version_hash)
    return verdict


# ---------------------------------------------------------------------------
# Small score helpers
# ---------------------------------------------------------------------------

def _mean_score(ev: EvalResult) -> float:
    scores = list(ev.per_task_scores.values())
    return sum(scores) / len(scores) if scores else 0.0


def _score_list(ev: EvalResult, task_ids: list[str]) -> list[float]:
    return [ev.per_task_scores.get(tid, 0.0) for tid in task_ids]


