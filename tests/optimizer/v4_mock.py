"""Deterministic method-v4 mock adapter: scripted cards, additive per-card deltas, hash noise.

A version's score on task t, attempt i = base + Σ deltas of its cards + noise(t, i),
noise in [-0.05, 0.05). Trajectories are dicts naming version, task and attempt;
predicates are callables on those dicts. The reviewer is scripted by ``reviewer``:
'honest' (sees the change on the candidate side iff the card spec says effect),
'sycophant' (claims an effect on every pair), 'gaming' (flags gaming), None (no reviewer).
"""
from __future__ import annotations

import hashlib
import threading
from typing import Optional

from rag_eval.optimizer.adapter import (Card, Diff, EvalResult, EvaluationUnavailable, Predicate, SystemAdapter,
                                        Version)


def noise(task: str, attempt: int) -> float:
    return (int(hashlib.sha256(f"{task}:{attempt}".encode()).hexdigest()[:8], 16) % 1000) / 10000 - 0.05


class V4Mock(SystemAdapter):
    def __init__(self, rounds: list[list[dict]], *, n_search=20, n_val=40, base=0.5, reviewer="honest"):
        self.rounds, self.base, self.reviewer = rounds, base, reviewer
        self.search = [f"s{i:02d}" for i in range(n_search)]
        self.validation = [f"v{i:02d}" for i in range(n_val)]
        self.seed = Version("seed", metadata=dict(candidate_id="seed"))
        self.cards: dict[str, dict] = {}          # card ref -> spec
        self.chain: dict[str, list[str]] = {"seed": []}   # version -> card refs
        self.parent_of: dict[str, Optional[str]] = {"seed": None}
        self.measured: set = set()                  # distinct (version, subset, task ids, k): runs on SN
        self.lock = threading.Lock()
        self.calls: dict[str, int] = {}
        self.fail_once: dict[str, Exception] = {}   # "<stage>:<card name>" -> raised once
        self.reviews: list[dict] = []
        self.safeguard_harm: dict[str, float] = {}  # card name -> infrastructure failure rate added

    def _count(self, name):
        with self.lock:
            self.calls[name] = self.calls.get(name, 0) + 1

    def _maybe_fail(self, stage, version: Version):
        for ref in self.chain.get(version.content_hash, []):
            key = f"{stage}:{self.cards[ref]['name']}"
            with self.lock:
                exc = self.fail_once.pop(key, None)
            if exc is not None:
                raise exc

    # -- scores -------------------------------------------------------------
    def score(self, h: str, task: str, attempt: int) -> float:
        delta = sum(self.cards[r].get("delta", 0.0) for r in self.chain[h])
        return min(1.0, max(0.0, self.base + delta + noise(task, attempt)))

    def _result(self, version: Version, ids, k: int) -> EvalResult:
        h = version.content_hash
        attempts = [{t: self.score(h, t, i) for t in ids} for i in range(k)]
        trajectories = {t: [dict(version=h, task=t, attempt=i) for i in range(k)] for t in ids}
        harm = sum(self.safeguard_harm.get(self.cards[r]["name"], 0.0) for r in self.chain[h])
        return EvalResult(per_task_scores={t: sum(a[t] for a in attempts) / k for t in ids}, trajectories=trajectories,
                          costs={t: 0.0 for t in ids}, error_classes={t: None for t in ids},
                          safeguard_values=dict(infrastructure_failure_rate=harm), attempt_scores=attempts,
                          context=dict(version=h))

    def evaluate(self, version, task_ids, k, *, purpose="screen"):
        self._count("evaluate:" + purpose)
        return self._result(version, task_ids, k)

    def evaluate_pair(self, version, reference, task_ids, k, *, purpose):
        self._count("pair:" + purpose)
        self._maybe_fail("smoke", version)
        return self._result(version, task_ids, k), self._result(reference, task_ids, k)

    def evaluate_subset(self, version, reference, subset, task_ids, k):
        self._count(f"subset:{subset}")
        with self.lock:
            self.measured.add((version.content_hash, subset, tuple(sorted(task_ids)), k))
        self._maybe_fail(subset, version)
        return self._result(version, task_ids, k), (self._result(reference, task_ids, k) if reference else None)

    def observe_safeguards(self, candidate, reference):
        return {"infrastructure_failure_rate": dict(direction="lower_is_better",
                                                    subject=candidate.safeguard_values["infrastructure_failure_rate"],
                                                    reference=reference.safeguard_values["infrastructure_failure_rate"])}

    def features(self, trajectories, predicate):
        if predicate is None:
            return {}
        name = predicate.boolean_expression["has_card"]
        return {t: any(self.cards[r]["name"] == name for r in self.chain[trajs[-1]["version"]])
                for t, trajs in trajectories.items() if trajs}

    # -- agents ---------------------------------------------------------------
    def analyse(self, search_set_trajectories, n_cards, *, case_roles=None, parent=None, round_id=None,
                history=None, no_adoption_rounds=0, **kwargs):
        self._count("analyse")
        self.last_history = list(history or [])
        specs = self.rounds[round_id - 1] if round_id - 1 < len(self.rounds) else []
        out = []
        for spec in specs[:n_cards]:
            ref = f"r{round_id}-{spec['name']}"
            self.cards[ref] = spec
            pred = None
            if spec.get("predicate"):
                pred = Predicate(boolean_expression={"has_card": spec["name"]}, predicted_direction="increase")
            out.append(Card(failure_mode_desc=spec["name"], change_hypothesis="h", predicate=pred, ref=ref))
        return out

    def workspace(self, version, card=None):
        return dict(parent=version.content_hash, ref=card.ref)

    def implement(self, card, workspace):
        self._count("implement")
        spec = self.cards[card.ref]
        if spec.get("implement_error"):
            raise RuntimeError("implementer failed")
        lines = spec.get("lines", 3)
        return Diff(content="\n".join(f"+line {i}" for i in range(lines)), metadata=dict(workspace, attempt=1))

    def materialize(self, diff, parent):
        ref, attempt = diff.metadata["ref"], diff.metadata.get("attempt", 1)
        h = f"{parent.content_hash}+{ref}" + (f"#a{attempt}" if attempt > 1 else "")
        with self.lock:
            self.chain[h] = self.chain[parent.content_hash] + [ref]
            self.parent_of[h] = parent.content_hash
            if attempt == 1 and self.cards[ref].get("smoke_behaviour_fails"):
                self.chain[h] = self.chain[parent.content_hash]      # change did not take effect
        return Version(h, metadata=dict(candidate_id=h))

    def repair(self, card, parent, failed, *, smoke, candidate_smoke, parent_smoke, attempt):
        self._count("repair")
        if not self.cards[card.ref].get("repairable"):
            return None
        return Diff(content="+fixed", metadata=dict(parent=parent.content_hash, ref=card.ref, attempt=attempt))

    def rebase(self, candidate, new_parent):
        self._count("rebase")
        # As v2 rebase: only the source's own change (its diff against its own parent) is applied.
        parent = self.parent_of[candidate.content_hash]
        own = [r for r in self.chain[candidate.content_hash] if r not in self.chain[parent]]
        if any(self.cards[r].get("conflict") for r in own):
            return None
        h = f"{new_parent.content_hash}*{'+'.join(own)}"
        with self.lock:
            self.chain[h] = self.chain[new_parent.content_hash] + own
            self.parent_of[h] = new_parent.content_hash
        return Version(h, metadata=dict(candidate_id=h))

    def review(self, card, diff, pairs, *, candidate, key):
        self._count("review")
        self.reviews.append(dict(key=key, pairs=pairs))
        if self.reviewer is None:
            return None
        spec = self.cards[card.ref]
        out = []
        for p in pairs:
            sides = {s: p[s][0]["version"] for s in "AB"}
            changed = next((s for s, v in sides.items() if v == candidate.content_hash), None)
            if self.reviewer == "sycophant":
                out.append(dict(pair=p["pair"], effect=True, changed_side="A", side_effect=False, gaming=False, note="x"))
            elif self.reviewer == "gaming":
                out.append(dict(pair=p["pair"], effect=changed is not None, changed_side=changed, side_effect=False,
                                gaming=changed is not None, note="answers shortened, content unchanged"))
            else:
                eff = bool(spec.get("effect")) and changed is not None
                out.append(dict(pair=p["pair"], effect=eff, changed_side=changed if eff else None,
                                side_effect=False, gaming=False, note=""))
        return out
