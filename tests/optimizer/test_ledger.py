"""Unit tests for rag_eval.optimizer.ledger (lineage and node-state machine)."""
from __future__ import annotations

import pytest

from rag_eval.optimizer.ledger import (
    Lineage,
    MilestoneState,
    NodeState,
)


def _add(
    lineage: Lineage,
    version_hash: str,
    parent_hash: str | None = None,
    *,
    agent_cost: float = 1.0,
):
    return lineage.add_provisional(
        version_hash=version_hash,
        parent_hash=parent_hash,
        diff_ref=f"diff-{version_hash}",
        card_ref=f"card-{version_hash}",
        predicate_ref=None,
        agent_cost=agent_cost,
    )


# ---------------------------------------------------------------------------
# NodeState / MilestoneState basics
# ---------------------------------------------------------------------------

def test_node_state_values():
    assert NodeState.PROVISIONAL.value == "provisional"
    assert NodeState.CERTIFIED.value == "certified"
    assert NodeState.ROLLED_BACK.value == "rolled_back"


def test_milestone_state_defaults():
    state = MilestoneState(
        cumulative_delta_val=0.0,
        best_score=0.5,
        last_certified_id=None,
        current_provisional_id=None,
        segment_start_best=0.5,
    )

    assert state.n_confirmations == 0
    state.n_confirmations += 1
    assert state.n_confirmations == 1


# ---------------------------------------------------------------------------
# add_provisional
# ---------------------------------------------------------------------------

def test_add_provisional_appends_node():
    lineage = Lineage()

    node = _add(lineage, "v1")

    assert node.version_hash == "v1"
    assert node.parent_hash is None
    assert node.diff_ref == "diff-v1"
    assert node.card_ref == "card-v1"
    assert node.predicate_ref is None
    assert node.agent_cost == 1.0
    assert node.created_at  # ISO-8601 timestamp should be present
    assert node.state is NodeState.PROVISIONAL
    assert lineage.all_nodes() == [node]


def test_add_provisional_duplicate_hash_raises():
    lineage = Lineage()
    _add(lineage, "v1")

    with pytest.raises(ValueError):
        _add(lineage, "v1")

    assert len(lineage.all_nodes()) == 1


# ---------------------------------------------------------------------------
# certify
# ---------------------------------------------------------------------------

def test_certify_transitions_provisional_to_certified():
    lineage = Lineage()
    node = _add(lineage, "v1")

    lineage.certify("v1", {"delta_fresh": 0.2, "verdict": "confirmed"})

    assert node.state is NodeState.CERTIFIED
    assert lineage.current_certified() is node


def test_certify_unknown_hash_raises():
    lineage = Lineage()

    with pytest.raises(ValueError):
        lineage.certify("missing", {})


def test_certify_already_certified_raises():
    lineage = Lineage()
    _add(lineage, "v1")
    lineage.certify("v1", {})

    with pytest.raises(ValueError):
        lineage.certify("v1", {})


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------

def test_rollback_transitions_to_rolled_back():
    lineage = Lineage()
    node = _add(lineage, "v1")

    lineage.rollback("v1")

    assert node.state is NodeState.ROLLED_BACK


def test_rollback_on_already_rolled_back_raises():
    lineage = Lineage()
    _add(lineage, "v1")
    lineage.rollback("v1")

    with pytest.raises(ValueError):
        lineage.rollback("v1")


def test_rollback_unknown_hash_raises():
    lineage = Lineage()

    with pytest.raises(ValueError):
        lineage.rollback("missing")


# ---------------------------------------------------------------------------
# current_parent / current_certified
# ---------------------------------------------------------------------------

def test_current_parent_skips_rolled_back_nodes():
    lineage = Lineage()
    _add(lineage, "v1")
    _add(lineage, "v2", "v1")
    _add(lineage, "v3", "v2")

    assert lineage.current_parent().version_hash == "v3"

    lineage.rollback("v3")
    assert lineage.current_parent().version_hash == "v2"

    lineage.rollback("v2")
    assert lineage.current_parent().version_hash == "v1"

    lineage.rollback("v1")
    assert lineage.current_parent() is None


def test_current_parent_returns_empty_lineage_none():
    assert Lineage().current_parent() is None


def test_current_certified_finds_most_recent_certified():
    lineage = Lineage()
    _add(lineage, "v1")
    _add(lineage, "v2", "v1")
    _add(lineage, "v3", "v2")

    lineage.certify("v1", {"round": 1})
    lineage.certify("v2", {"round": 2})

    # v3 is still provisional and must not affect the result.
    assert lineage.current_certified().version_hash == "v2"


def test_current_certified_ignores_rolled_back_certified_node():
    lineage = Lineage()
    _add(lineage, "v1")
    _add(lineage, "v2", "v1")
    lineage.certify("v1", {})
    lineage.certify("v2", {})
    lineage.rollback("v2")

    assert lineage.current_certified().version_hash == "v1"


def test_current_certified_none_when_nothing_certified():
    lineage = Lineage()
    _add(lineage, "v1")

    assert lineage.current_certified() is None


# ---------------------------------------------------------------------------
# provisional_segment
# ---------------------------------------------------------------------------

def test_provisional_segment_returns_only_provisional_after_given_node():
    lineage = Lineage()
    _add(lineage, "v1")
    lineage.certify("v1", {})
    _add(lineage, "v2", "v1")
    _add(lineage, "v3", "v2")
    lineage.certify("v2", {})
    _add(lineage, "v4", "v3")

    segment = lineage.provisional_segment("v1")

    assert [node.version_hash for node in segment] == ["v3", "v4"]
    assert all(node.state is NodeState.PROVISIONAL for node in segment)


def test_provisional_segment_from_start_when_no_certified_id():
    lineage = Lineage()
    _add(lineage, "v1")
    _add(lineage, "v2", "v1")
    lineage.certify("v1", {})
    _add(lineage, "v3", "v1")

    segment = lineage.provisional_segment(None)

    assert [node.version_hash for node in segment] == ["v2", "v3"]


def test_provisional_segment_excludes_rolled_back_nodes():
    lineage = Lineage()
    _add(lineage, "v1")
    lineage.certify("v1", {})
    _add(lineage, "v2", "v1")
    _add(lineage, "v3", "v2")
    lineage.rollback("v2")

    segment = lineage.provisional_segment("v1")

    assert [node.version_hash for node in segment] == ["v3"]


def test_provisional_segment_unknown_start_raises():
    lineage = Lineage()
    _add(lineage, "v1")

    with pytest.raises(ValueError):
        lineage.provisional_segment("missing")
