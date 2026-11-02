"""Deterministic mock SystemAdapter for integration testing run_optimization.

Implements all SystemAdapter methods with toy behaviour so the full v3 loop
(smoke → screen → milestone → confirm → halt) can be exercised without any
SN server or LLM.  Behaviour is parameterised by a "script" of per-round
score deltas so tests can drive specific paths.

The loop runs per-card work in a thread pool, so every counter and registry
here is guarded by a lock.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

from rag_eval.optimizer.adapter import Card, Diff, EvalResult, Predicate, SystemAdapter, Version


class MockAdapter(SystemAdapter):
    """Toy adapter that follows a scripted score progression.

    Parameters
    ----------
    script:
        List of round descriptors.  Each round is a dict:
            {
              "cards": [<failure_mode or card spec>, ...],  # cards the analyst returns
              "deltas": {task_id: delta, ...},            # per-task score delta vs parent
              "predicate": <Predicate or None>,           # optional predicate for all cards
              "smoke_pass": True/False,                  # whether smoke passes
            }
        A card entry is either a failure-mode string (inherits the round's
        deltas / smoke_pass) or a dict spec overriding them per card:
            {"name": str, "deltas": {...}, "smoke_pass": bool,
             "sleep": seconds spent in implement(),
             "rebase": bool (rebase() returns a Version instead of None)}
        The loop reads one entry per round.  When the loop asks for more
        rounds than script provides, an empty card list is returned
        (drives the 'no_candidates' halt).
    n_tasks:
        Number of known tasks; task ids are "t0".."tN-1".
    base_score:
        Starting per-task score for the seed version (default 0.5).
    base_scores:
        Optional per-task overrides of the seed score.
    fail_on:
        purpose → exception instance; evaluate() raises it for that purpose.
    """

    def __init__(self, script: list[dict], n_tasks: int = 6, base_score: float = 0.5,
                 base_scores: Optional[dict[str, float]] = None,
                 fail_on: Optional[dict[str, Exception]] = None) -> None:
        self._script = script
        self._n_tasks = n_tasks
        self._base_score = base_score
        self._task_ids = [f"t{i}" for i in range(n_tasks)]
        self._lock = threading.Lock()
        # version_hash -> per-task scores dict
        self._scores: dict[str, dict[str, float]] = {}
        # version_hash -> (card spec, deltas) that produced it (for rebase)
        self._origin: dict[str, dict] = {}
        # card ref -> resolved card spec
        self._card_specs: dict[str, dict] = {}
        # Seed version
        self._seed_hash = "seed_v0"
        self._scores[self._seed_hash] = {tid: (base_scores or {}).get(tid, base_score)
                                         for tid in self._task_ids}
        self.fail_on: dict[str, Exception] = dict(fail_on or {})
        # Counters for inspection in tests
        self.analyse_calls = 0
        self.implement_calls = 0
        self.implement_done = 0
        self.evaluate_calls = 0
        self.materialize_calls = 0
        self.rebase_calls = 0
        # history argument of every analyse() call (§2.3 labels)
        self.history_calls: list[Optional[list[dict]]] = []
        # (method, Version) for every Version the loop hands to the adapter.
        self.received: list[tuple[str, Version]] = []
        # (purpose, version_hash, task_ids) for every evaluate() call.
        self.eval_log: list[tuple[str, str, tuple[str, ...]]] = []

    # -- helpers -------------------------------------------------------

    @property
    def seed(self) -> Version:
        return Version(content_hash=self._seed_hash,
                       metadata={"task_id": "mock", "candidate_id": self._seed_hash})

    def _round_script(self, round_id: int) -> dict:
        # round_id is 1-based in the loop; script is 0-indexed.
        idx = round_id - 1
        if idx >= len(self._script):
            return {"cards": [], "deltas": {}, "predicate": None, "smoke_pass": True}
        return self._script[idx]

    def _receive(self, method: str, version: Version) -> None:
        with self._lock:
            self.received.append((method, version))

    def _child(self, parent: Version, tag: str, spec: dict) -> Version:
        """Register parent + spec deltas as a new version (caller holds the lock)."""
        new_hash = f"{parent.content_hash}+{tag}"
        parent_scores = self._scores[parent.content_hash]
        deltas = spec.get("deltas", {})
        self._scores[new_hash] = {tid: parent_scores.get(tid, 0.0) + deltas.get(tid, 0.0)
                                  for tid in parent_scores}
        self._origin[new_hash] = spec
        meta = dict(parent.metadata)
        meta["deltas"] = {}
        meta["smoke_pass"] = spec.get("smoke_pass", True)
        meta["candidate_id"] = new_hash
        meta["parent_candidate_id"] = parent.metadata.get("candidate_id")
        return Version(content_hash=new_hash, metadata=meta)

    # -- SystemAdapter methods ----------------------------------------

    def materialize(self, diff: Diff, parent: Version) -> Version:
        self._receive("materialize", parent)
        spec = {"deltas": diff.metadata.get("deltas", {}),
                "smoke_pass": diff.metadata.get("smoke_pass", True),
                **diff.metadata.get("spec", {})}
        with self._lock:
            self.materialize_calls += 1
            return self._child(parent, f"m{self.materialize_calls}", spec)

    def rebase(self, candidate: Version, new_parent: Version) -> Optional[Version]:
        self._receive("rebase", new_parent)
        with self._lock:
            self.rebase_calls += 1
            spec = self._origin.get(candidate.content_hash, {})
            if not spec.get("rebase", False):
                return None
            return self._child(new_parent, f"r{self.rebase_calls}", spec)

    def evaluate(
        self,
        version: Version,
        task_ids: list[str],
        k: int,
        *,
        purpose: str = "screen",
    ) -> EvalResult:
        self._receive("evaluate", version)
        with self._lock:
            self.evaluate_calls += 1
            self.eval_log.append((purpose, version.content_hash, tuple(task_ids)))
            exc = self.fail_on.get(purpose)
            scores = {tid: self._scores.get(version.content_hash, {}).get(tid, 0.0)
                      for tid in task_ids}
        if exc is not None:
            raise exc
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

    def workspace(self, version: Version, card: Optional[Card] = None) -> Any:
        self._receive("workspace", version)
        return f"mock_workspace:{version.content_hash}"

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
        self.analyse_calls += 1
        self.history_calls.append(list(history) if history is not None else None)
        if parent is not None:
            self._receive("analyse", parent)
        # The loop round picks the script entry (call count if not given).
        round_id = round_id if round_id is not None else self.analyse_calls
        script = self._round_script(round_id)
        cards = []
        for i, entry in enumerate(script.get("cards", [])[:n_cards]):
            spec = {"deltas": script.get("deltas", {}),
                    "smoke_pass": script.get("smoke_pass", True)}
            if isinstance(entry, dict):
                spec.update(entry)
            else:
                spec["name"] = entry
            ref = f"R{round_id}:{spec['name']}"
            with self._lock:
                self._card_specs[ref] = spec
            cards.append(Card(
                failure_mode_desc=spec["name"],
                change_hypothesis=f"fix {spec['name']}",
                predicate=script.get("predicate"),
                ref=ref,
            ))
        return cards

    def implement(self, card: Card, workspace: Any) -> Diff:
        with self._lock:
            self.implement_calls += 1
            spec = self._card_specs[card.ref]
        if spec.get("sleep"):
            time.sleep(spec["sleep"])
        with self._lock:
            self.implement_done += 1
        return Diff(
            content=f"# diff for {card.failure_mode_desc}",
            test_refs=(),
            metadata={"deltas": spec.get("deltas", {}),
                      "smoke_pass": spec.get("smoke_pass", True),
                      "spec": spec},
        )
