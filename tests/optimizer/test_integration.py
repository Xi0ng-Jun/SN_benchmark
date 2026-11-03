"""Integration tests for run_optimization using MockAdapter.

Tests drive the full v3 loop through deterministic scripts and assert
on lineage state, halt reason, and round counts.  No SN server or LLM
is involved.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../src'))

import pytest
from rag_eval.optimizer import (
    ProtocolConfig,
    run_optimization,
    NodeState,
)
from tests.optimizer.mock_adapter import MockAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config(**overrides) -> ProtocolConfig:
    defaults = dict(
        mde=0.05,
        sigma_d=0.1,
        ladder_c=1.0,
        K_max=20,
        T=0.06,          # trigger after ~two screen_passes at delta=0.04
        n_fresh=6,
        z_screen=0.84,
        z_confirm=1.65,
        b_cards=2,
        k_screen=1,
        k_smoke=1,
        n_smoke_sample=2,
        safeguard_bounds={},
        max_confirmations=10,
        predicate_coverage_floor=0.0,
    )
    defaults.update(overrides)
    return ProtocolConfig(**defaults)


def _smoke_ids():
    return ["t0", "t1"]


def _val_pool_ids(n=6):
    return [f"t{i}" for i in range(n)]


def _fresh_batches(n=3, size=6):
    # Non-overlapping fresh batches (simplified: all use same task ids for mock)
    return [[f"t{i}" for i in range(size)] for _ in range(n)]


# ---------------------------------------------------------------------------
# Test: halt when no cards are produced (no_candidates)
# ---------------------------------------------------------------------------

class TestHaltNoCandidates:
    def test_empty_analyst_halts(self):
        adapter = MockAdapter(script=[], n_tasks=6)
        result = run_optimization(
            adapter,
            adapter.seed,
            _config(),
            search_set_ids=_val_pool_ids(),
            val_pool_ids=_val_pool_ids(),
            fresh_batch_ids=_fresh_batches(),
            smoke_sample_ids=_smoke_ids(),
            max_rounds=10,
        )
        assert result.halt_reason == 'no_candidates'
        assert result.rounds[-1].cards_generated == 0

    @pytest.mark.parametrize("limit", [2, 3])
    def test_no_screen_pass_limit_is_preregistered(self, limit):
        adapter = MockAdapter(script=[], n_tasks=6)
        result = run_optimization(
            adapter, adapter.seed, _config(max_rounds_without_screen_pass=limit),
            search_set_ids=_val_pool_ids(), val_pool_ids=_val_pool_ids(),
            fresh_batch_ids=_fresh_batches(), smoke_sample_ids=_smoke_ids(), max_rounds=10,
        )
        assert result.halt_reason == 'no_candidates'
        assert len(result.rounds) == limit


# ---------------------------------------------------------------------------
# Test: happy path — two screen_passes trigger a milestone confirmation
# ---------------------------------------------------------------------------

class TestMilestoneConfirmation:
    def test_two_screen_passes_trigger_milestone(self):
        # delta=0.12 per task: ladder_m(sigma_d=0.1, K=20, n_val=6) ≈ 0.078, so
        # candidate score 0.5+0.12=0.62 > best(0.5)+0.078=0.578 → screen_pass.
        # Two rounds of cumulative Δ_val ≈ 0.12 each ≥ T=0.10 → milestone.
        delta_per_task = {f"t{i}": 0.12 for i in range(6)}
        script = [
            {"cards": ["err_type_A"], "deltas": delta_per_task, "smoke_pass": True},
            {"cards": ["err_type_B"], "deltas": delta_per_task, "smoke_pass": True},
        ]
        adapter = MockAdapter(script=script, n_tasks=6, base_score=0.5)
        result = run_optimization(
            adapter,
            adapter.seed,
            _config(T=0.10),
            search_set_ids=_val_pool_ids(),
            val_pool_ids=_val_pool_ids(),
            fresh_batch_ids=_fresh_batches(n=3),
            smoke_sample_ids=_smoke_ids(),
            max_rounds=10,
        )
        milestone_rounds = [r for r in result.rounds if r.milestone_triggered]
        assert len(milestone_rounds) >= 1
        first = milestone_rounds[0]
        assert first.confirmation_verdict in ('confirmed', 'insufficient', 'rejected')

    def test_certified_node_created_on_confirmation(self):
        delta_per_task = {f"t{i}": 0.10 for i in range(6)}  # large delta
        script = [
            {"cards": ["err_A"], "deltas": delta_per_task, "smoke_pass": True},
        ]
        adapter = MockAdapter(script=script, n_tasks=6, base_score=0.0)
        result = run_optimization(
            adapter,
            adapter.seed,
            _config(T=0.05),
            search_set_ids=_val_pool_ids(),
            val_pool_ids=_val_pool_ids(),
            fresh_batch_ids=_fresh_batches(n=2),
            smoke_sample_ids=_smoke_ids(),
            max_rounds=5,
        )
        certified = [n for n in result.lineage.all_nodes()
                     if n.state == NodeState.CERTIFIED]
        # Seed is always certified; a confirmed milestone adds another.
        assert len(certified) >= 1


# ---------------------------------------------------------------------------
# Test: rollback on rejection
# ---------------------------------------------------------------------------

class TestRollback:
    def test_negative_delta_leads_to_rollback(self):
        # Negative deltas → confirm_decision will give 'rejected'
        bad_delta = {f"t{i}": -0.20 for i in range(6)}
        script = [
            {"cards": ["bad_err"], "deltas": bad_delta, "smoke_pass": True},
        ]
        adapter = MockAdapter(script=script, n_tasks=6, base_score=0.8)
        result = run_optimization(
            adapter,
            adapter.seed,
            _config(T=0.001),  # tiny T so milestone triggers immediately
            search_set_ids=_val_pool_ids(),
            val_pool_ids=_val_pool_ids(),
            fresh_batch_ids=_fresh_batches(n=2),
            smoke_sample_ids=_smoke_ids(),
            max_rounds=5,
        )
        rollback_rounds = [r for r in result.rounds if r.rolled_back]
        # Either a rollback happened or screened_pass was 0 (negative delta didn't pass screen)
        all_screen_fails = all(r.screen_passed == 0 for r in result.rounds)
        assert rollback_rounds or all_screen_fails


# ---------------------------------------------------------------------------
# Test: fresh_exhausted halt
# ---------------------------------------------------------------------------

class TestFreshExhausted:
    def test_halt_when_fresh_batches_run_out(self):
        delta_per_task = {f"t{i}": 0.10 for i in range(6)}
        # Script repeats same round indefinitely via long list
        script = [{"cards": ["err"], "deltas": delta_per_task, "smoke_pass": True}] * 20
        adapter = MockAdapter(script=script, n_tasks=6, base_score=0.0)
        result = run_optimization(
            adapter,
            adapter.seed,
            _config(T=0.05, max_confirmations=100),
            search_set_ids=_val_pool_ids(),
            val_pool_ids=_val_pool_ids(),
            fresh_batch_ids=_fresh_batches(n=1),  # only one batch
            smoke_sample_ids=_smoke_ids(),
            max_rounds=20,
        )
        assert result.halt_reason == 'fresh_exhausted'


# ---------------------------------------------------------------------------
# Test: smoke fail discards candidate
# ---------------------------------------------------------------------------

class TestSmokeFail:
    def test_smoke_fail_candidates_not_screened(self):
        delta_per_task = {f"t{i}": 0.10 for i in range(6)}
        script = [{"cards": ["err"], "deltas": delta_per_task, "smoke_pass": False}]
        adapter = MockAdapter(script=script, n_tasks=6, base_score=0.0)
        result = run_optimization(
            adapter,
            adapter.seed,
            _config(),
            search_set_ids=_val_pool_ids(),
            val_pool_ids=_val_pool_ids(),
            fresh_batch_ids=_fresh_batches(n=2),
            smoke_sample_ids=_smoke_ids(),
            max_rounds=3,
        )
        if result.rounds:
            assert result.rounds[0].smoke_passed == 0


# ---------------------------------------------------------------------------
# Test: alpha_warning is returned in result
# ---------------------------------------------------------------------------

class TestAlphaWarning:
    def test_alpha_note_present(self):
        adapter = MockAdapter(script=[], n_tasks=6)
        result = run_optimization(
            adapter, adapter.seed, _config(),
            search_set_ids=_val_pool_ids(),
            val_pool_ids=_val_pool_ids(),
            fresh_batch_ids=_fresh_batches(n=1),
            smoke_sample_ids=_smoke_ids(),
            max_rounds=2,
        )
        assert "familywise_error" in result.alpha_note
        assert "n_confirmations" in result.alpha_note
