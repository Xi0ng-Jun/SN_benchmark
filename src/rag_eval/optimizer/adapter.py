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
    ref: Optional[str] = None          # target-system card id (SN: v2 hypothesis id)


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
    # Adapter-private provenance of the result (SN: candidate id and the scope of
    # each task), read back by the same adapter, e.g. by ``safeguards``.
    context: dict = field(default_factory=dict, compare=False)


@dataclass
class SmokeResult:
    """Result of the §2.2 cheap-discard smoke check."""
    candidate_version: str
    card_ref: str
    behavioral_check_ran: bool
    feature_present_before: Optional[bool]   # None when no predicate
    feature_present_after: Optional[bool]    # None when no predicate
    verdict: str                             # 'pass' | 'fail' | 'unchecked'


PURPOSES = ("baseline", "diagnose", "screen", "smoke", "confirm", "sealed")


class EvaluationUnavailable(RuntimeError):
    """An evaluation could not produce results (infrastructure, timeout, refusal).

    The loop stops with a process-failure reason rather than treating the
    candidate as rejected: missing measurements are not evidence (§2.5).
    """


class BudgetExhausted(EvaluationUnavailable):
    """The frozen run budget does not admit the requested evaluation."""


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
        purpose: str = "screen",
    ) -> EvalResult:
        """Scores (and trajectory stubs) of one version on task_ids, k runs each.

        *purpose* (one of PURPOSES) tells the adapter which partition is meant,
        so it never has to guess from the id set: 'baseline' / 'diagnose' /
        'screen' read the search set or validation pool. Fresh batches and
        smoke samples are only evaluated in pairs (``evaluate_pair``).
        An adapter may run fewer new attempts when earlier ones already exist
        (top-up semantics); it must raise rather than silently change k.
        """

    def evaluate_pair(
        self,
        version: Version,
        reference: Version,
        task_ids: list[str],
        k: int,
        *,
        purpose: str,
    ) -> tuple[EvalResult, EvalResult]:
        """(version, reference) on the same task_ids, k runs each, for paired d_i.

        purpose: 'smoke' (candidate vs parent on the smoke sample, k=1),
        'screen' (candidate vs parent on the validation pool), 'confirm'
        (segment end vs last certified node on one fresh batch; consumes it).
        Default: two independent ``evaluate`` calls, adequate for systems
        without paired dispatch. Adapters whose runtime pairs runs override.
        """
        return (self.evaluate(version, task_ids, k, purpose=purpose),
                self.evaluate(reference, task_ids, k, purpose=purpose))

    def rebase(self, candidate: Version, new_parent: Version) -> Optional[Version]:
        """Re-apply *candidate*'s change onto *new_parent* (§2.2 A precondition).

        Returns the rebased, frozen Version, or None when unsupported or on
        conflict — the loop then discards the candidate and refills from the
        card queue.
        """
        return None

    @abc.abstractmethod
    def features(
        self,
        trajectories: dict[str, list[Any]],
        predicate: Optional[Predicate],
    ) -> dict[str, Optional[bool]]:
        """Evaluate predicate against each task's trajectories.

        Returns task_id → bool, or None when not computable for that task
        (missing observation); callers drop None from rates.  Must return {}
        when predicate is None.
        """

    @abc.abstractmethod
    def workspace(self, version: Version, card: Optional[Card] = None) -> Any:
        """Return an isolated workspace handle, derived from *version*, for implementing *card*.

        Systems whose versions are tree nodes (SN) create the child node here;
        the handle then identifies it for implement() and materialize().
        """

    @abc.abstractmethod
    def analyse(
        self,
        search_set_trajectories: dict[str, list[Any]],
        n_cards: int,
        *,
        case_roles: Optional[dict[str, str]] = None,
        parent: Optional[Version] = None,
        round_id: Optional[int] = None,
        history: Optional[list[dict]] = None,
    ) -> list[Card]:
        """Run the analyst agent on search-set trajectories only (§2.3 isolation).

        The loop passes only accountant-selected cases; *case_roles* labels each
        'failure' or 'control'. No scores are passed. *parent* is the version
        being diagnosed and *round_id* the loop round; adapters use them to bind
        cards to the parent and to derive restart-stable request keys.

        *history* (§2.3 历史标签): hypotheses already processed under this
        parent, oldest first, as ``{'title', 'label'}`` with label one of
        smoke_fail / screen_fail / provisional_adopted / confirmed / rejected /
        insufficient / unmeasurable, plus optional ``attribution`` (supported /
        refuted / insufficient / unattributed) and
        ``attribution_possibly_biased``. Never scores, deltas or task ids.

        Returns up to n_cards Cards.
        """

    @abc.abstractmethod
    def implement(self, card: Card, workspace: Any) -> Diff:
        """Run the implementer agent for one card; return a Diff."""

    # -- safeguards (§1 保护指标) --------------------------------------------

    def safeguards(self, candidate: EvalResult, reference: EvalResult, bounds: dict) -> dict:
        """Pre-registered safeguards of one paired decision (screen / confirmation).

        *candidate* and *reference* come from the same ``evaluate_pair`` call;
        *bounds* is ``ProtocolConfig.safeguard_bounds``. Returns
        ``{source, ok, reasons, checks}``: ``ok`` False (a breach, or a
        safeguard that could not be observed) makes the decision unmeasurable
        (§2.2). ``checks`` holds one row per safeguard with its observed values.

        Default: each bound is a maximum of ``candidate.safeguard_values[metric]``
        (absent counts as 0.0). Systems with their own pre-registered
        safeguards (SN: the iteration protocol) override this.
        """
        checks = []
        for metric, max_delta in (bounds or {}).items():
            value = candidate.safeguard_values.get(metric, 0.0)
            checks.append(dict(key=metric, margin=max_delta, subject=value, breached=value > max_delta))
        reasons = [f"safeguard_breached:{c['key']}" for c in checks if c["breached"]]
        return dict(source="protocol_config", ok=not reasons, reasons=reasons, checks=checks)

    def describe_safeguards(self, bounds: dict) -> dict:
        """Which safeguards are in force for this run (recorded in state.json and the ledger)."""
        return dict(source="protocol_config", bounds=dict(bounds or {}))

    # -- evaluation request generation (operator retry) ----------------------

    @property
    def key_generation(self) -> int:
        """Generation suffix for restart-stable evaluation request keys (default 0).

        Systems that replay a stored receipt per request key (SN/v2) also
        replay a *failed* result forever. After a process-failure halt the
        operator may request a retry; the loop then bumps the generation (it
        is persisted in state.json) and calls ``set_key_generation`` so the
        adapter issues fresh measurement keys. Adapters without keyed replay
        ignore it.
        """
        return getattr(self, "_key_generation", 0)

    def set_key_generation(self, generation: int) -> None:
        if type(generation) is not int or generation < 0:
            raise ValueError(f"key_generation must be a non-negative int, got {generation!r}")
        self._key_generation = generation
