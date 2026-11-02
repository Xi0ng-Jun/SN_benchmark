"""System adapter interface and canonical data objects for the v3 protocol.

Defines the four-capability ABC (§2.6) and the agent-role methods (§2.3) from
method-v3.md.  All model, SDK, and SN-specific code lives behind these
interfaces; the method-core package never imports a model client directly.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class Version:
    """Frozen harness snapshot identified by a content hash."""
    content_hash: str
    metadata: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class Predicate:
    """Mechanically computable trajectory predicate (sandboxed).

    boolean_expression may be a callable or a serialisable spec; callers must
    sandbox it before execution (no file/network/reflection access allowed).
    """
    boolean_expression: Any            # callable or serialisable spec
    predicted_direction: str           # 'decrease' | 'increase'
    predicted_threshold: Optional[float] = None


@dataclass(frozen=True)
class Card:
    """Analysis-agent output: failure-mode description, change hypothesis, optional predicate."""
    failure_mode_desc: str
    change_hypothesis: str
    predicate: Optional[Predicate] = None


@dataclass(frozen=True)
class Diff:
    """Implementer output: textual or structured diff against the parent version."""
    content: str
    test_refs: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class EvalResult:
    """Per-version evaluation result for a batch of tasks."""
    per_task_scores: dict[str, float]        # task_id → mean score over k runs
    trajectories: dict[str, list[Any]]       # task_id → list of k trajectories
    costs: dict[str, float]                  # task_id → aggregate cost
    error_classes: dict[str, Optional[str]]  # task_id → error class or None
    safeguard_values: dict[str, float] = field(default_factory=dict, compare=False)


@dataclass
class SmokeResult:
    """Result of the §2.2 cheap-discard smoke check."""
    candidate_version: str
    card_ref: str
    behavioral_check_ran: bool
    feature_present_before: Optional[bool]   # None when no predicate
    feature_present_after: Optional[bool]    # None when no predicate
    verdict: str                             # 'pass' | 'fail' | 'unchecked'


class SystemAdapter(abc.ABC):
    """Four-capability interface the method-core package depends on (§2.6).

    All SN-specific, model-client, and filesystem concerns live in subclasses.
    """

    @abc.abstractmethod
    def materialize(self, diff: Diff, parent: Version) -> Version:
        """Apply diff to parent; return a new frozen Version."""

    @abc.abstractmethod
    def evaluate(
        self,
        version: Version,
        task_ids: list[str],
        k: int,
        *,
        reference_version: Optional[Version] = None,
    ) -> EvalResult:
        """Run version on task_ids k times each.

        When reference_version is supplied, run both interleaved for better
        paired-difference estimates.
        """

    @abc.abstractmethod
    def features(
        self,
        trajectories: dict[str, list[Any]],
        predicate: Optional[Predicate],
    ) -> dict[str, bool]:
        """Evaluate predicate against each task's trajectories.

        Returns task_id → bool.  Must return {} when predicate is None.
        """

    @abc.abstractmethod
    def workspace(self, version: Version) -> Any:
        """Return an isolated workspace handle for an implementer agent."""

    @abc.abstractmethod
    def analyse(
        self,
        search_set_trajectories: dict[str, list[Any]],
        n_cards: int,
    ) -> list[Card]:
        """Run the analyst agent on search-set trajectories only (§2.3 isolation).

        Returns up to n_cards Cards.
        """

    @abc.abstractmethod
    def implement(self, card: Card, workspace: Any) -> Diff:
        """Run the implementer agent for one card; return a Diff."""
