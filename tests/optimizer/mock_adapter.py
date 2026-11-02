"""Deterministic mock SystemAdapter for integration testing run_optimization.

Implements all six SystemAdapter methods with toy behaviour so the full
v3 loop (smoke → screen → milestone → confirm → halt) can be exercised
without any SN server or LLM.  Behaviour is parameterised by a "script"
of per-round score deltas so tests can drive specific paths.
"""
from __future__ import annotations

from typing import Any, Optional

from rag_eval.optimizer.adapter import Card, Diff, EvalResult, Predicate, SystemAdapter, Version


class MockAdapter(SystemAdapter):
    """Toy adapter that follows a scripted score progression.

    Parameters
    ----------
    script:
        List of round descriptors.  Each round is a dict:
            {
              "cards": [<failure_mode>, ...],            # cards the analyst returns
              "deltas": {task_id: delta, ...},            # per-task score delta vs parent
              "predicate": <Predicate or None>,           # optional predicate for all cards
              "smoke_pass": True/False,                  # whether smoke passes
            }
        The loop reads one entry per round.  When the loop asks for more
        rounds than script provides, an empty card list is returned
        (drives the 'no_candidates' halt).
    n_tasks:
        Number of tasks in the validation pool; task ids are "t0".."tN-1".
    base_score:
        Starting per-task score for the seed version (default 0.5).
    """

    def __init__(self, script: list[dict], n_tasks: int = 6, base_score: float = 0.5) -> None:
        self._script = script
        self._n_tasks = n_tasks
        self._base_score = base_score
        self._task_ids = [f"t{i}" for i in range(n_tasks)]
        # version_hash -> per-task scores dict
        self._scores: dict[str, dict[str, float]] = {}
        # Seed version
        self._seed_hash = "seed_v0"
        self._scores[self._seed_hash] = {tid: base_score for tid in self._task_ids}
        # Counters for inspection in tests
        self.analyse_calls = 0
        self.implement_calls = 0
        self.evaluate_calls = 0
        self.materialize_calls = 0

    # -- helpers -------------------------------------------------------

    @property
    def seed(self) -> Version:
        return Version(content_hash=self._seed_hash, metadata={"task_id": "mock"})

    def _round_script(self, round_id: int) -> dict:
        # round_id is 1-based in the loop; script is 0-indexed.
        idx = round_id - 1
        if idx >= len(self._script):
            return {"cards": [], "deltas": {}, "predicate": None, "smoke_pass": True}
        return self._script[idx]

    # -- SystemAdapter methods ----------------------------------------

    def materialize(self, diff: Diff, parent: Version) -> Version:
        self.materialize_calls += 1
        new_hash = f"{parent.content_hash}+m{self.materialize_calls}"
        parent_scores = self._scores[parent.content_hash]
        # Bake deltas into scores ONCE here, so evaluate() can stay stateless.
        deltas = diff.metadata.get("deltas", {})
        smoke_pass = diff.metadata.get("smoke_pass", True)
        child_scores = {
            tid: parent_scores.get(tid, 0.0) + deltas.get(tid, 0.0)
            for tid in parent_scores
        }
        self._scores[new_hash] = child_scores
        meta = dict(parent.metadata)
        meta["deltas"] = {}
        meta["smoke_pass"] = smoke_pass
        return Version(content_hash=new_hash, metadata=meta)

    def evaluate(
        self,
        version: Version,
        task_ids: list[str],
        k: int,
        *,
        reference_version: Optional[Version] = None,
    ) -> EvalResult:
        self.evaluate_calls += 1
        scores = {tid: self._scores.get(version.content_hash, {}).get(tid, 0.0)
                  for tid in task_ids}
        smoke_pass = (version.metadata or {}).get("smoke_pass", True)
        if smoke_pass:
            trajectories = {tid: [{"run": 1, "ok": True}] for tid in task_ids}
        else:
            trajectories = {tid: [] for tid in task_ids}
        costs = {tid: 0.0 for tid in task_ids}
        error_classes = {tid: None for tid in task_ids}
        return EvalResult(
            per_task_scores=scores,
            trajectories=trajectories,
            costs=costs,
            error_classes=error_classes,
        )

    def features(
        self,
        trajectories: dict[str, list[Any]],
        predicate: Optional[Predicate],
    ) -> dict[str, bool]:
        if predicate is None:
            return {}
        # Toy: predicate activation = first trajectory's "ok" flag inverted.
        return {tid: not traj[0].get("ok", True) for tid, traj in trajectories.items()}

    def workspace(self, version: Version) -> Any:
        return f"mock_workspace:{version.content_hash}"

    def analyse(
        self,
        search_set_trajectories: dict[str, list[Any]],
        n_cards: int,
        *,
        case_roles: Optional[dict[str, str]] = None,
    ) -> list[Card]:
        self.analyse_calls += 1
        # Use the call count to pick the script entry; analyse is called
        # once per round at the start.
        round_id = self.analyse_calls
        script = self._round_script(round_id)
        cards = []
        for i, fm in enumerate(script.get("cards", [])[:n_cards]):
            cards.append(Card(
                failure_mode_desc=fm,
                change_hypothesis=f"fix {fm}",
                predicate=script.get("predicate"),
            ))
        return cards

    def implement(self, card: Card, workspace: Any) -> Diff:
        self.implement_calls += 1
        # Find this card's round to pull its deltas.
        round_id = self.implement_calls  # rough mapping
        script = self._round_script(round_id)
        deltas = script.get("deltas", {})
        smoke_pass = script.get("smoke_pass", True)
        return Diff(
            content=f"# diff for {card.failure_mode_desc}",
            test_refs=(),
            metadata={"deltas": deltas, "smoke_pass": smoke_pass},
        )
