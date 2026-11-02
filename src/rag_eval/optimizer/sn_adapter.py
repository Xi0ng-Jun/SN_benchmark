"""SN system adapter — wraps the v2 workbench for the v3 protocol.

Callers construct SNAdapter with a loaded v2 config dict (from
``rag_eval.workbench.configuration.load_config``) and optional pre-built
service handles.  All heavy lifting is delegated to existing v2 workbench
modules; this file adds no business logic.

NOTE: The v2 workbench has no Protocol dataclass.  Configuration is a plain
dict keyed by the same fields as load_config() output.  SNAdapter.__init__
accepts that dict directly; pass additional service objects as keyword args.
"""
from __future__ import annotations

import hashlib
import subprocess
import time
from typing import Any, Optional

from .adapter import (BudgetExhausted, Card, Diff, EvalResult, EvaluationUnavailable, Predicate,
                      SystemAdapter, Version)


# ---------------------------------------------------------------------------
# v2 card → v3 Card conversion
# ---------------------------------------------------------------------------

def _v2_card_to_v3_card(v2_record: dict) -> Card:
    """Convert a v2 HypothesisStore record into a v3 Card.

    v2 card schema (hypotheses.py): required fields are title, component,
    mechanism, predictions, falsification, implementer_brief; optional
    contract (lcd-v2 mechanism with slice_predicate/manipulation/slice_effect).

    v3 Card: failure_mode_desc (from title), change_hypothesis (from
    implementer_brief), optional Predicate (from contract when present).
    """
    card_json = v2_record.get("card") or {}
    failure_mode_desc = card_json.get("title", "")
    change_hypothesis = card_json.get("implementer_brief", "")
    predicate: Optional[Predicate] = None
    contract = card_json.get("contract")
    if isinstance(contract, dict) and "slice_predicate" in contract:
        manipulation = contract.get("manipulation", {})
        predicate = Predicate(
            boolean_expression=contract["slice_predicate"],
            predicted_direction=manipulation.get("direction", "decrease"),
            predicted_threshold=manipulation.get("threshold"),
        )
    return Card(
        failure_mode_desc=failure_mode_desc,
        change_hypothesis=change_hypothesis,
        predicate=predicate,
        ref=v2_record.get("id"),
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class SNAdapter(SystemAdapter):
    """SystemAdapter for the SN (silicon-notebook) benchmark harness.

    Parameters
    ----------
    config:
        Loaded v2 config dict (``rag_eval.workbench.configuration.load_config``).
    repository:
        ``WorkbenchRepository`` (``rag_eval.workbench.repository``).
    run_views:
        ``RunViews`` (``rag_eval.workbench.run_views``) for score reads;
        defaults to ``round_operations.api.views``.
    iterations:
        ``IterationStore`` (``rag_eval.workbench.iterations``).
    round_operations:
        v2 ``RoundOperations`` (``rag_eval.workbench.loop_services``); its
        ``.service`` is the ``IterationService`` that owns candidates.

    A v3 Version is a v2 iteration-tree candidate (binding plan §2.1):
        content_hash = the candidate's ``frozen_commit``
        metadata     = candidate_id, parent_candidate_id, hypothesis_id,
                       parent_commit, task_id

    Config keys used for agent dispatch and candidate creation:
        iteration_id, task_id — the v2 iteration and its task
        run_id                — v3 run identity; every v2 request key derives
                                from it (no uuid), so rerunning the same command
                                replays v2 receipts instead of dispatching again
        actor                 — the v2 actor recorded on approvals/derives/freezes,
                                and on every evaluation request
    """

    def __init__(
        self,
        config: dict,
        *,
        repository=None,
        run_views=None,
        iterations=None,
        round_operations=None,
        hypotheses=None,
        analyst_profile: str = "pi-flash",
        implementer_profile: str = "pi-flash",
        agent_poll_interval: float = 5.0,
        agent_timeout: float = 600.0,
        worktree_timeout: float = 600.0,
        analyst_view_budget: int = 12000,
        eval_poll_interval: float = 30.0,
        eval_timeout: float = 7200.0,
        infra_retry: int = 1,
        execution_timeout_seconds: int = 7200,
    ) -> None:
        self._config = config
        self._repository = repository
        self._run_views = run_views
        self._iterations = iterations
        # Services required for agent dispatch (analyse + implement).
        self._round_ops = round_operations
        self._hypotheses = hypotheses
        self._analyst_profile = analyst_profile
        self._implementer_profile = implementer_profile
        self._agent_poll_interval = agent_poll_interval
        self._agent_timeout = agent_timeout
        self._worktree_timeout = worktree_timeout
        # Evaluation polling (binding plan §2.5): interval and deadline of one
        # evaluate call. infra_retry and execution_timeout_seconds are forwarded
        # to v2 and frozen with each request key: keep them constant per run.
        self._eval_poll_interval = eval_poll_interval
        self._eval_timeout = eval_timeout
        self._infra_retry = infra_retry
        self._execution_timeout_seconds = execution_timeout_seconds
        # Message text is capped at 16384 chars by the v2 message bridge; the
        # instructions take ~1K, so diagnostic views get the rest minus headroom.
        if not 0 < analyst_view_budget <= 15000:
            raise ValueError("analyst_view_budget must be in (0, 15000] characters")
        self._analyst_view_budget = analyst_view_budget
        self.last_analysis: dict = {}

        # Trajectory cache: (run_dir_str, case_id) → DiagnosticTrajectory dict.
        # Bounded to the most recent _TRAJ_CACHE_SIZE entries to cap memory use.
        self._traj_cache: dict[tuple[str, str], dict] = {}
        self._TRAJ_CACHE_SIZE = 2000
        # Admitted-execution score cache: execution_id -> {case: (value, stub)}.
        self._score_cache: dict[str, dict] = {}
        self._SCORE_CACHE_SIZE = 512

    # ------------------------------------------------------------------
    # v2 candidate plumbing: request keys, actor, versions
    # ------------------------------------------------------------------

    def _request_key(self, *parts: Any) -> str:
        """Deterministic v2 request key ``v3/{run_id}/...`` (binding plan §2.5).

        v2 replays the stored receipt for a repeated key, so rerunning the same
        run after a crash observes earlier dispatches instead of repeating them.
        v2 caps request keys at 200 characters; longer keys keep a readable
        prefix plus a digest of the full key.
        """
        run_id = self._config.get("run_id")
        if not run_id:
            raise ValueError("config must contain 'run_id' to derive v2 request keys")
        key = "/".join(["v3", str(run_id), *(str(p) for p in parts)])
        if len(key) > 200:
            key = key[:135] + "/" + hashlib.sha256(key.encode()).hexdigest()
        return key

    def _actor(self) -> str:
        actor = self._config.get("actor")
        if not actor:
            raise ValueError("config must contain 'actor' for v2 approvals, derives and freezes")
        return actor

    def _require_round_ops(self, what: str):
        if self._round_ops is None:
            raise RuntimeError(
                f"{what}() requires round_operations service; "
                "inject via SNAdapter.__init__ keyword arguments."
            )
        return self._round_ops

    def _require_ids(self) -> tuple[str, str]:
        iteration_id = self._config.get("iteration_id")
        task_id = self._config.get("task_id")
        if not iteration_id or not task_id:
            raise ValueError(
                "config must contain 'iteration_id' and 'task_id' to dispatch agents."
            )
        return iteration_id, task_id

    @staticmethod
    def candidate_id(version: Version) -> str:
        """The v2 candidate a Version stands for."""
        cid = (version.metadata or {}).get("candidate_id")
        if not cid:
            raise ValueError(
                f"Version {version.content_hash!r} carries no candidate_id; build SN "
                "versions with version_for() or materialize()."
            )
        return cid

    def _version(self, candidate: dict) -> Version:
        if candidate.get("status") != "frozen" or not candidate.get("frozen_commit"):
            raise ValueError(
                f"Candidate {candidate.get('id')!r} is not frozen "
                f"(status={candidate.get('status')!r})"
            )
        return Version(
            content_hash=candidate["frozen_commit"],
            metadata=dict(
                candidate_id=candidate["id"],
                parent_candidate_id=candidate.get("parent_candidate_id"),
                hypothesis_id=candidate.get("proposal_id"),
                parent_commit=candidate.get("parent_commit"),
                task_id=self._config.get("task_id", ""),
            ),
        )

    def version_for(self, candidate_id: str) -> Version:
        """Version of an existing frozen candidate (e.g. the iteration root as seed)."""
        ops = self._require_round_ops("version_for")
        return self._version(ops.service.candidate(candidate_id))

    # ------------------------------------------------------------------
    # 1. materialize — the coordinator freezes the candidate
    # ------------------------------------------------------------------

    def materialize(self, diff: Diff, parent: Version) -> Version:
        """Freeze the candidate the implementer edited; return its Version.

        v2 ``IterationService.freeze`` commits the worktree as the coordinator
        and records ``frozen_commit``. A candidate already frozen (rerun after
        a crash) is read back instead: v2 refuses to freeze it twice. A clean
        worktree is refused by v2 ("no changes to freeze") and propagates, so
        the loop skips the card.
        """
        ops = self._require_round_ops("materialize")
        cid = (diff.metadata or {}).get("candidate_id")
        if not cid:
            raise ValueError("Diff.metadata must carry the candidate_id from workspace()")
        candidate = ops.service.candidate(cid)
        parent_cid = (parent.metadata or {}).get("candidate_id")
        if parent_cid and candidate.get("parent_candidate_id") != parent_cid:
            raise ValueError(
                f"Candidate {cid!r} is not a child of {parent_cid!r} "
                f"(parent={candidate.get('parent_candidate_id')!r})"
            )
        if candidate.get("status") != "frozen":
            candidate = ops.service.freeze(cid, actor=self._actor())
        return self._version(candidate)

    # ------------------------------------------------------------------
    # 2. evaluate — dispatch v2 round measurements, poll, read admitted scores
    # ------------------------------------------------------------------
    #
    # Binding plan §2.2/§2.3/§2.5. Every v3 task set maps to one v2 scope:
    #   search set / validation pool  -> scope None (one v2 execution covers
    #                                    search ∪ validation), plus each completed
    #                                    fresh batch's own scope (merged pool)
    #   smoke sample                  -> registered scope ``smoke:<hash>``
    #   i-th fresh batch              -> its batch id (v2 ``segment``)
    # v2 measurements are asynchronous and idempotent per request key: the same
    # key is called again until it reports ``complete``; a rerun after a crash
    # replays the stored footprint instead of dispatching again.

    _MEASURE_PURPOSES = ("baseline", "diagnose", "screen")

    def _views(self):
        if self._run_views is not None:
            return self._run_views
        return self._require_round_ops("evaluate").api.views

    def _protocol(self) -> dict:
        """The frozen v2 iteration protocol (splits, fresh_batches, k_base)."""
        ops = self._require_round_ops("evaluate")
        iteration_id, _ = self._require_ids()
        # iteration_record reads only the row; iteration() builds the whole tree.
        read = getattr(ops.service, "iteration_record", None) or ops.service.iteration
        return read(iteration_id)["protocol"]

    def _used_batches(self, *, completed: bool = False) -> list[dict]:
        """v2 ``splits.batches``: claimed fresh batches; ``completed`` = reconciled into the pool."""
        from rag_eval.workbench.splits import batches
        ops = self._require_round_ops("evaluate")
        iteration_id, _ = self._require_ids()
        return batches(ops.repository, iteration_id, completed=completed)

    @staticmethod
    def _k_base(protocol: dict) -> int:
        return protocol.get("k_base", 2)  # v2 RoundOperations.describe uses the same default

    def partitions(self) -> dict:
        """The iteration's v3-visible partitions, read from the frozen v2 protocol.

        ``fresh_batches`` keeps protocol order (v3 consumes them in this order);
        ``completed_fresh_batch_ids`` are the batches already reconciled into
        the validation pool, in v2 completion order.
        """
        protocol = self._protocol()
        splits = protocol.get("splits") or {}
        fresh = [dict(batch_id=b["batch_id"], case_ids=list(b["case_ids"]))
                 for b in protocol.get("fresh_batches", [])]
        return dict(search=list(splits.get("search", ())),
                    validation=list(splits.get("validation", ())),
                    fresh_batches=fresh,
                    completed_fresh_batch_ids=[b["batch_id"] for b in self._used_batches(completed=True)],
                    k_base=self._k_base(protocol))

    @staticmethod
    def _check_k(k: Any, expected: int, purpose: str) -> None:
        if type(k) is not int or k != expected:
            raise ValueError(
                f"{purpose} evaluation needs k={expected} (fixed by the v2 iteration protocol); got k={k!r}"
            )

    def _case_scopes(self, task_ids: list[str], purpose: str, protocol: dict) -> dict[str, Optional[str]]:
        """task id -> v2 scope for a measure purpose; refuses ids outside the partition.

        diagnose: search set only. baseline / screen: validation pool, i.e. the
        validation split plus the cases of every COMPLETED fresh batch (v2
        ``splits.roles`` semantics). The set is never silently changed.
        """
        if not task_ids:
            raise ValueError(f"{purpose} evaluation needs at least one task id")
        splits = protocol["splits"]
        if purpose == "diagnose":
            allowed: dict[str, Optional[str]] = {c: None for c in splits["search"]}
            where = "the search set"
        else:
            allowed = {c: None for c in splits["validation"]}
            completed = {b["batch_id"] for b in self._used_batches(completed=True)}
            for batch in protocol.get("fresh_batches", []):
                if batch["batch_id"] in completed:
                    allowed.update({c: batch["batch_id"] for c in batch["case_ids"]})
            where = "the validation pool (validation split and completed fresh batches)"
        outside = [t for t in task_ids if t not in allowed]
        if outside:
            raise ValueError(
                f"{len(outside)} {purpose} task id(s) are outside {where}, e.g. {outside[:3]!r}"
            )
        return {t: allowed[t] for t in task_ids}

    def _v2_run_kwargs(self) -> dict:
        # These values are part of v2's frozen request: v2 refuses a replayed key
        # whose actor/infra_retry/timeout differ, so they must stay constant per run.
        return dict(actor=self._actor(), infra_retry=self._infra_retry,
                    timeout_seconds=self._execution_timeout_seconds)

    def _await(self, calls: dict[str, tuple[str, Any]]) -> None:
        """Re-issue each keyed v2 call until all report ``complete``.

        *calls*: label -> (request key, zero-argument callable). v2 statuses:
        running -> poll again; complete -> done; budget_exhausted ->
        BudgetExhausted (the frozen footprint waits for human confirmation);
        failed / exhausted / stopped / anything else, or the poll deadline ->
        EvaluationUnavailable. Missing measurements are never evidence.
        """
        pending = dict(calls)
        deadline = time.monotonic() + self._eval_timeout
        while True:
            for label, (key, call) in list(pending.items()):
                result = call() or {}
                status = result.get("status")
                if status == "complete":
                    del pending[label]
                elif status == "running":
                    continue
                elif status == "budget_exhausted":
                    raise BudgetExhausted(
                        f"{label}: v2 footprint exceeds the frozen budget (key={key!r}, "
                        f"action_request={result.get('action_request_id')!r})"
                    )
                else:
                    raise EvaluationUnavailable(
                        f"{label}: v2 status {status!r} (key={key!r}): "
                        f"{result.get('reason') or result.get('errors') or 'no reason given'}"
                    )
            if not pending:
                return
            if time.monotonic() >= deadline:
                raise EvaluationUnavailable(
                    f"{', '.join(pending)} not complete within {self._eval_timeout}s "
                    f"(keys={[k for k, _ in pending.values()]!r}); rerun resumes the same keys"
                )
            time.sleep(self._eval_poll_interval)

    def _measure(self, candidate_ids: list[str]) -> None:
        """v2 ``measure`` each candidate on search ∪ validation ∪ completed fresh batches.

        Key ``v3/{run_id}/measure/{cid}/{n}`` with n = completed fresh batches.
        v2 ``RoundMeasurements._plan`` freezes the measure footprint at the
        first call of a key, with the completed fresh batches known AT THAT
        TIME; replays never re-plan. Once another batch is folded into the
        pool the old key cannot top up the new scope, so n is part of the key.
        If a batch completes while we wait, measure again under the new n.
        The same candidate measured for different purposes shares one key
        (v2 replays the receipt), so diagnose and screen cost one footprint.
        """
        ops = self._require_round_ops("evaluate")
        kwargs = self._v2_run_kwargs()
        while True:
            n = len(self._used_batches(completed=True))
            calls = {}
            for cid in dict.fromkeys(candidate_ids):
                key = self._request_key("measure", cid, n)
                calls[f"measure {cid}"] = (key, lambda cid=cid, key=key: ops.measure(cid, key=key, **kwargs))
            self._await(calls)
            if len(self._used_batches(completed=True)) == n:
                return

    def _execution_values(self, execution_id: str) -> dict[str, tuple[Optional[float], dict]]:
        """case -> (raw primary score, trajectory stub) for one admitted execution.

        Values come from ``RunViews.flips`` on each partition run paired with
        itself: the same per-case value path v2 ``ComparisonService._compute_pair``
        uses (official per-case primary metric, with declared case-retry
        answers replacing failed first answers), including its shared-bundle
        optimisation. Admitted executions are terminal, so the result is cached.
        """
        hit = self._score_cache.get(execution_id)
        if hit is not None:
            return hit
        views = self._views()
        runs = views.execution_runs(execution_id)
        bundle = views.shared_bundle(runs[0].id) if len(runs) > 1 else None
        out: dict[str, tuple[Optional[float], dict]] = {}
        for run in runs:
            flips = (views.flips(run.id, run.id, bundle=bundle) if bundle is not None
                     else views.flips(run.id, run.id))
            values = ({r["case_id"]: r["left"] for r in flips["rows"]}
                      if flips.get("admission") == "admitted" else {})
            stub = dict(run_dir=str(run.run_dir), run_id=run.id, execution_id=execution_id)
            for case_id in run.case_ids:
                out[case_id] = (values.get(case_id), stub)
        if runs:
            if len(self._score_cache) >= self._SCORE_CACHE_SIZE:
                self._score_cache.pop(next(iter(self._score_cache)))
            self._score_cache[execution_id] = out
        return out

    def _read(self, candidate_id: str, case_scopes: dict[str, Optional[str]]) -> EvalResult:
        """Per-case mean raw primary score over the candidate's admitted attempts.

        Attempt set: v2 ``ComparisonService.admitted(ComparisonService.scoped(c, scope))``,
        exactly the attempts a v2 comparison averages (every admitted execution
        on that scope; scope None also includes legacy links). Attempts that
        failed admission (infrastructure failures, cancelled, incomplete
        scoring) are excluded, as in v2. Per-case mean: v2 ``noise.estimate``
        (mean over attempts with a value; None when no attempt scored it).
        """
        from rag_eval.workbench.comparisons import ComparisonService
        from rag_eval.workbench.noise import estimate
        candidate = self._round_ops.service.candidate(candidate_id)
        wanted_by_scope: dict[Optional[str], set[str]] = {}
        for case_id, scope in case_scopes.items():
            wanted_by_scope.setdefault(scope, set()).add(case_id)
        trajectories: dict[str, list[Any]] = {t: [] for t in case_scopes}
        attempts = []
        for scope, wanted in wanted_by_scope.items():
            for admitted in ComparisonService.admitted(ComparisonService.scoped(candidate, scope)):
                values = self._execution_values(admitted["execution_id"])
                rows = []
                for case_id in sorted(wanted & values.keys()):
                    value, stub = values[case_id]
                    trajectories[case_id].append(dict(stub, attempt=admitted.get("attempt"), batch_id=scope,
                                                      candidate_id=candidate_id))
                    rows.append(dict(case_id=case_id, family=stub["run_id"], value=value))
                attempts.append(dict(execution_id=admitted["execution_id"], rows=rows))
        cases = estimate(dict(attempts=attempts))["cases"] if attempts else {}
        per_task_scores = {t: cases[t]["mean"] for t in case_scopes
                           if t in cases and cases[t]["mean"] is not None}
        return EvalResult(
            per_task_scores=per_task_scores,
            trajectories=trajectories,
            # v2 exposes per-case tokens only through RunViews.observation, which
            # builds each partition's full run view; not worth it for a record
            # field. Cost stays 0.0 until a cheap per-case usage reader exists.
            costs={t: 0.0 for t in case_scopes},
            error_classes={t: None if t in per_task_scores else
                           ("no_admitted_attempt" if not trajectories[t] else "unscored")
                           for t in case_scopes},
        )

    def evaluate(
        self,
        version: Version,
        task_ids: list[str],
        k: int,
        *,
        purpose: str = "screen",
    ) -> EvalResult:
        """Measure *version* through v2 ``measure`` and read its admitted scores.

        purpose: 'diagnose' (task_ids ⊆ search set) or 'baseline' / 'screen'
        (task_ids ⊆ validation pool). k must equal the protocol's ``k_base``;
        v2 tops each scope up to k_base admitted attempts (only the missing
        ones run). Fresh batches and smoke samples are pair-only
        (``evaluate_pair``); the sealed set is the end step's.
        """
        if purpose not in self._MEASURE_PURPOSES:
            raise ValueError(
                f"SN evaluate() serves {self._MEASURE_PURPOSES}; {purpose!r} is evaluated in "
                "pairs (evaluate_pair) or by the sealed end step"
            )
        cid = self.candidate_id(version)
        protocol = self._protocol()
        self._check_k(k, self._k_base(protocol), purpose)
        scopes = self._case_scopes(list(task_ids), purpose, protocol)
        self._measure([cid])
        return self._read(cid, scopes)

    def evaluate_pair(
        self,
        version: Version,
        reference: Version,
        task_ids: list[str],
        k: int,
        *,
        purpose: str,
    ) -> tuple[EvalResult, EvalResult]:
        """(version, reference) on the same tasks, through one v2 paired measurement.

        smoke   : k=1, task_ids ⊆ search, reference = version's parent; v2
                  ``smoke`` (one attempt per side on the registered sample).
        screen  : k=k_base on the validation pool; v2 ``measure`` on both.
        confirm : k=k_base; task_ids must be exactly one unused protocol fresh
                  batch; v2 ``segment(start=reference, end=version)`` consumes it.
        """
        ops = self._require_round_ops("evaluate_pair")
        vcid, rcid = self.candidate_id(version), self.candidate_id(reference)
        protocol = self._protocol()
        task_ids = list(task_ids)
        if purpose == "screen":
            self._check_k(k, self._k_base(protocol), purpose)
            scopes = self._case_scopes(task_ids, purpose, protocol)
            self._measure([vcid, rcid])
            return self._read(vcid, scopes), self._read(rcid, scopes)
        if purpose == "smoke":
            from rag_eval.workbench.execution_scopes import smoke_scope_id
            self._check_k(k, 1, purpose)
            scope_id = smoke_scope_id(protocol, task_ids)  # refuses non-search / duplicate ids
            parent = ops.service.candidate(vcid).get("parent_candidate_id")
            if parent != rcid:
                raise ValueError(f"smoke compares a candidate with its parent ({parent!r}), not {rcid!r}")
            key = self._request_key("smoke", vcid, scope_id)
            kwargs = self._v2_run_kwargs()
            self._await({f"smoke {vcid}": (key, lambda: ops.smoke(vcid, task_ids, key=key, **kwargs))})
            scopes = {t: scope_id for t in task_ids}
            return self._read(vcid, scopes), self._read(rcid, scopes)
        if purpose == "confirm":
            from rag_eval.workbench.loop_measurements import segment_start
            self._check_k(k, self._k_base(protocol), purpose)
            wanted = set(task_ids)
            batch = next((b for b in protocol.get("fresh_batches", []) if set(b["case_ids"]) == wanted), None)
            if not task_ids or batch is None:
                raise ValueError("confirm task_ids are not exactly one fresh batch of the iteration protocol")
            batch_id = batch["batch_id"]
            claim = next((b for b in self._used_batches() if b["batch_id"] == batch_id), None)
            # A claim by this very segment (same end and start) is a resume; any other use is refused.
            if claim is not None and not (claim["candidate_id"] == vcid
                                          and segment_start(ops.repository, claim["request_key"]) == rcid):
                raise ValueError(f"fresh batch {batch_id!r} is already used")
            key = self._request_key("segment", rcid, vcid, batch_id)
            kwargs = self._v2_run_kwargs()
            self._await({f"segment {rcid}->{vcid} on {batch_id}": (
                key, lambda: ops.segment(start_id=rcid, end_id=vcid, batch_id=batch_id, key=key, **kwargs))})
            scopes = {t: batch_id for t in task_ids}
            return self._read(vcid, scopes), self._read(rcid, scopes)
        raise ValueError(f"SN evaluate_pair() serves smoke/screen/confirm, not {purpose!r}")

    # ------------------------------------------------------------------
    # 2b. rebase — re-file the change onto a new parent (v2 derive + rebase)
    # ------------------------------------------------------------------

    def rebase(self, candidate: Version, new_parent: Version) -> Optional[Version]:
        """Re-apply *candidate*'s frozen change onto *new_parent*; None on conflict.

        v2 sequence: ``RoundOperations.derive(new_parent, rebase_from=candidate)``
        re-files the approved card under the new parent and derives the child;
        once its worktree is ready, ``RoundOperations.rebase`` applies the
        source's frozen diff uncommitted (receipt: applied / conflict / empty,
        or running while the worktree is not ready). An applied rebase is
        frozen by the coordinator as in materialize(). A conflicting or empty
        rebase leaves the unfrozen child in the v2 tree and returns None.
        """
        ops = self._require_round_ops("rebase")
        cid, parent_cid = self.candidate_id(candidate), self.candidate_id(new_parent)
        actor = self._actor()
        source = ops.service.candidate(cid)
        hypothesis_id = source.get("proposal_id") or (candidate.metadata or {}).get("hypothesis_id")
        if not hypothesis_id:
            raise ValueError(f"Candidate {cid!r} has no approved card to rebase")
        derive_key = self._request_key("rebase", cid, parent_cid)
        new_cid = ops.derive(parent_cid, hypothesis_id=hypothesis_id, key=derive_key, actor=actor,
                             rebase_from=cid)
        self._await_worktree(new_cid)
        apply_key = self._request_key("rebase-apply", new_cid)
        deadline = time.monotonic() + self._worktree_timeout
        result = ops.rebase(new_cid, key=apply_key, actor=actor)
        while result.get("status") == "running":
            if time.monotonic() >= deadline:
                raise TimeoutError(f"rebase of {cid!r} onto {parent_cid!r} not applied within "
                                   f"{self._worktree_timeout}s ({result.get('reason')!r})")
            time.sleep(self._agent_poll_interval)
            result = ops.rebase(new_cid, key=apply_key, actor=actor)
        if result.get("status") in ("conflict", "empty"):
            return None
        if result.get("status") != "applied":
            raise RuntimeError(f"v2 rebase returned {result!r}")
        child = ops.service.candidate(new_cid)
        if child.get("status") != "frozen":
            child = ops.service.freeze(new_cid, actor=actor)
        return self._version(child)

    # ------------------------------------------------------------------
    # 3. features
    # ------------------------------------------------------------------

    def features(
        self,
        trajectories: dict[str, list[Any]],
        predicate: Optional[Predicate],
    ) -> dict[str, Optional[bool]]:
        """Evaluate *predicate* on each task's latest run observation.

        Observations come from ``sn_trajectory.observation`` on the complete
        row (never the analyst's truncated display). v2 signature dicts are
        matched with ``clusters._matches``; callables receive the observation.
        A task is ``None`` ("not computable") when it has no run row, or when
        any field the signature reads is unobserved; callers must drop it from
        rate estimates rather than count it as False.
        """
        if predicate is None:
            return {}
        expr = predicate.boolean_expression
        observations = self._observations(trajectories)
        fields = _signature_fields(expr) if isinstance(expr, dict) else set()
        result: dict[str, Optional[bool]] = {}
        for task_id in trajectories:
            obs = observations.get(task_id)
            if obs is None or any(_lookup(obs, f) is None for f in fields
                                  if f != "retrieval_context.*.text"):
                result[task_id] = None
                continue
            try:
                if isinstance(expr, dict):
                    from rag_eval.workbench.clusters import _matches
                    result[task_id] = bool(_matches(expr, obs))
                elif callable(expr):
                    result[task_id] = bool(expr(obs))
                else:
                    result[task_id] = None  # free-form strings are not executed
            except Exception:  # malformed signature or predicate bug
                result[task_id] = None
        return result

    # ------------------------------------------------------------------
    # trajectory loading (lazy, batched per run, cached)
    # ------------------------------------------------------------------

    def _rows_by_task(self, trajectories: dict[str, list[Any]]) -> dict[str, tuple[str, dict]]:
        """Latest run row per task: one streaming pass per run_dir.

        Returns task_id -> (run_dir, projected entry) where the entry holds the
        diagnostic view and observation. Only finished runs are cached.
        """
        from .sn_trajectory import VIEW_SCHEMA_VERSION, diagnostic_view, observation, read_rows, run_is_final
        wanted: dict[str, list[str]] = {}
        latest: dict[str, str] = {}
        out: dict[str, tuple[str, dict]] = {}
        for task_id, stubs in trajectories.items():
            run_dirs = [s.get("run_dir") for s in stubs or [] if isinstance(s, dict) and s.get("run_dir")]
            if not run_dirs:
                continue
            run_dir = run_dirs[-1]
            latest[task_id] = run_dir
            hit = self._traj_cache.get((VIEW_SCHEMA_VERSION, run_dir, task_id))
            if hit is not None:
                out[task_id] = (run_dir, hit)
            else:
                wanted.setdefault(run_dir, []).append(task_id)
        for run_dir, task_ids in wanted.items():
            rows = read_rows(run_dir, task_ids)
            final = run_is_final(run_dir)
            for task_id in task_ids:
                if task_id not in rows:
                    continue
                entry = {"view": diagnostic_view(rows[task_id]), "observation": observation(rows[task_id])}
                out[task_id] = (run_dir, entry)
                if final:
                    if len(self._traj_cache) >= self._TRAJ_CACHE_SIZE:
                        self._traj_cache.pop(next(iter(self._traj_cache)))
                    self._traj_cache[(VIEW_SCHEMA_VERSION, run_dir, task_id)] = entry
        return out

    def _observations(self, trajectories: dict[str, list[Any]]) -> dict[str, dict]:
        return {t: e["observation"] for t, (_, e) in self._rows_by_task(trajectories).items()}

    def diagnostic_views(self, trajectories: dict[str, list[Any]]) -> dict[str, dict]:
        """Analyst-facing views for the given tasks (missing tasks omitted)."""
        return {t: e["view"] for t, (_, e) in self._rows_by_task(trajectories).items()}

    # ------------------------------------------------------------------
    # 4. workspace — approve the card, derive the child candidate
    # ------------------------------------------------------------------

    def _approve(self, hypothesis_id: str, iteration_id: str, actor: str) -> dict:
        """Approve a v3 card: v2 ``_card_legal`` checks minus the cluster binding.

        v2 ``RoundOperations.approve`` looks up the card's frozen cluster and
        rejects cards that do not reference it; v3 has no clusters (§2.1), so
        that path cannot approve any v3 card. The contract shape (gate rule)
        and the benchmark-text leak scan of implementer-visible fields stay.
        """
        import json as _json
        from rag_eval.workbench.agent_tools import task_run_ids
        from rag_eval.workbench.gate import DEFAULT_RULE
        from rag_eval.workbench.hypotheses import benchmark_material, scan_text, validate_card
        ops = self._require_round_ops("workspace")
        tree = ops._tree(iteration_id)
        task_id = tree["task_id"]
        record = ops.api.hypotheses.get(task_id, hypothesis_id)
        if record["status"] != "draft":
            return dict(status=record["status"])
        try:
            card = validate_card(ops._raw_card(record["card"]), tree["protocol"].get("gate_rule", DEFAULT_RULE))
            material = benchmark_material(ops.api.views, task_run_ids(ops.api.views, task_id))
            visible = {k: card.get(k) for k in ("title", "component", "mechanism", "implementer_brief",
                                                "possible_harm", "change_scope")}
            leaked = scan_text(_json.dumps(visible, ensure_ascii=False), material)
        except (ValueError, KeyError) as exc:
            return dict(status="rejected", reason=f"invalid_card: {exc}")
        if leaked:
            return dict(status="rejected", reason="benchmark_text_leak")
        decided = ops.api.hypotheses.decide(task_id, hypothesis_id, "approve", actor=actor,
                                            note="method-v3 approval: " + self._request_key("approve", hypothesis_id))
        return dict(status=decided["status"])

    def workspace(self, version: Version, card: Optional[Card] = None) -> dict:
        """Derive a child candidate of *version* for *card*; return its binding.

        v2 sequence: approval (``_approve``: v2 card validation and leak scan,
        without v2's cluster binding), ``RoundOperations.derive`` (child at the
        parent's frozen commit, keyed so a rerun returns the same child), then
        wait until the child's worktree is ``ready``, nudging
        ``IterationService.provision`` (which only reconciles after its first
        dispatch).
        """
        ops = self._require_round_ops("workspace")
        iteration_id, _ = self._require_ids()
        if card is None or not card.ref:
            raise ValueError("SN workspace() needs a Card whose ref is the v2 hypothesis id")
        actor = self._actor()
        parent_cid = self.candidate_id(version)
        hypothesis_id = card.ref
        # Build keys first: a missing run_id must fail before any v2 write.
        derive_key = self._request_key("derive", parent_cid, hypothesis_id)
        approval = self._approve(hypothesis_id, iteration_id, actor)
        if approval.get("status") not in ("approved", "implementing"):
            raise RuntimeError(
                f"Card {hypothesis_id!r} was not approved: {approval.get('status')!r} "
                f"({approval.get('reason', 'no reason given')})"
            )
        cid = ops.derive(parent_cid, hypothesis_id=hypothesis_id, key=derive_key, actor=actor)
        candidate = self._await_worktree(cid)
        tree = candidate["worktree"]
        return dict(candidate_id=cid, parent_candidate_id=parent_cid, hypothesis_id=hypothesis_id,
                    path=tree["path"], branch=tree.get("branch"),
                    base_revision=tree.get("base_revision"),
                    parent_commit=candidate.get("parent_commit"))

    def _await_worktree(self, candidate_id: str) -> dict:
        service = self._round_ops.service
        candidate = service.candidate(candidate_id)
        deadline = time.monotonic() + self._worktree_timeout
        while not _worktree_ready(candidate):
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Worktree of candidate {candidate_id!r} not ready within "
                    f"{self._worktree_timeout}s (worktree={candidate.get('worktree')!r})"
                )
            time.sleep(self._agent_poll_interval)
            candidate = service.provision(candidate_id)
        return candidate

    # ------------------------------------------------------------------
    # 5. analyse — dispatch analyst agent, poll until idle, read cards
    # ------------------------------------------------------------------

    def analyse(
        self,
        search_set_trajectories: dict[str, list[Any]],
        n_cards: int,
        *,
        case_roles: Optional[dict[str, str]] = None,
        parent: Optional[Version] = None,
        round_id: Optional[int] = None,
    ) -> list[Card]:
        """Dispatch an analyst agent on accountant-selected search cases; return its new Cards.

        *search_set_trajectories* holds only the cases the loop selected (§2.3:
        selection is the accountant's, by a pre-registered rule). Each case is
        rendered as a diagnostic view under opaque refs (c1, c2, ...) within
        ``analyst_view_budget`` characters, so the Agent needs no tool calls
        to gather evidence. Cards are bound to this request and to the parent
        candidate: only hypotheses created after dispatch and drafted under the
        parent are returned (v2 derive requires the card's parent to match).

        The request key is ``v3/{run_id}/analyse/{round_id}/{parent_cid}``:
        round ids grow monotonically within a run and are restored with the
        loop state on resume, so the same round replays the same agent receipt.
        *parent* / *round_id* default to config ``parent_candidate_id`` /
        ``round_id``.
        """
        if self._round_ops is None or self._hypotheses is None:
            raise RuntimeError(
                "analyse() requires round_operations and hypotheses services; "
                "inject them via SNAdapter.__init__ keyword arguments."
            )
        iteration_id, task_id = self._require_ids()
        parent_cid = ((parent.metadata or {}).get("candidate_id") if parent is not None else None) \
            or self._config.get("parent_candidate_id")
        round_id = round_id if round_id is not None else self._config.get("round_id")
        if round_id is None:
            raise ValueError("analyse() needs round_id (argument or config) for a deterministic key")
        analyst_key = self._request_key("analyse", round_id, parent_cid or "-")
        from .sn_trajectory import render_views
        roles = case_roles or {}
        views = self.diagnostic_views(search_set_trajectories)
        ordered = sorted(views, key=lambda t: (roles.get(t, "failure") != "failure",
                                               list(search_set_trajectories).index(t)))
        items = [(f"c{i + 1}", roles.get(t, "failure"), views[t]) for i, t in enumerate(ordered)]
        evidence, n_shown, n_omitted = render_views(items, self._analyst_view_budget)
        self.last_analysis = dict(selected=len(search_set_trajectories), with_view=len(views),
                                  shown=n_shown, omitted=n_omitted, evidence_chars=len(evidence))
        where = (f"Draft each card under the parent node with `proposal draft --parent {parent_cid} "
                 f"--file <card.json>`. " if parent_cid else "")
        analyst_text = (
            f"Diagnose the parent version's failures on the search cases below and draft up to "
            f"{n_cards} legal lcd-v2 mechanism cards, one per distinct failure mode. Each card needs "
            f"title, component, mechanism, implementer_brief, falsification, predictions (may be []), "
            f"and a contract with slice_predicate, manipulation and slice_effect. A slice_predicate "
            f"may read behaviour fields and trace.* step features (e.g. trace.n_empty_retrievals, "
            f"trace.clarification_requested). Cases marked [failure] were selected by a fixed rule; "
            f"[control] cases are for contrast. {where}Do not request more evidence; draft the "
            f"cards now.\n\n"
            + evidence
        )
        before = {r["id"] for r in self._hypotheses.list(task_id)}
        call = dict(iteration_id=iteration_id, key=analyst_key, profile=self._analyst_profile,
                    text=analyst_text, scope="iteration")
        self._poll_agent("analyst", None, call)
        fresh = [r for r in self._hypotheses.list(task_id)
                 if r["id"] not in before and r.get("status") in ("draft", "approved")
                 and (parent_cid is None or r.get("parent_candidate_id") == parent_cid)]
        return [_v2_card_to_v3_card(r) for r in fresh[:n_cards]]

    # ------------------------------------------------------------------
    # 6. implement — dispatch implementer agent, poll, record the diff
    # ------------------------------------------------------------------

    def implement(self, card: Card, workspace: Any) -> Diff:
        """Dispatch the implementer on the derived candidate; return its worktree diff.

        *workspace* is the binding from workspace(). Per method-v3 §2.1 the
        implementer only edits and runs relevant tests: it does not commit,
        freeze, evaluate or request runs. The coordinator freezes in
        materialize(). ``Diff.content`` is the uncommitted worktree diff, kept
        as a record; the frozen commit is the version.
        """
        ops = self._require_round_ops("implement")
        iteration_id, _ = self._require_ids()
        if not isinstance(workspace, dict) or not workspace.get("candidate_id"):
            raise ValueError(f"workspace does not contain a candidate_id: {workspace!r}")
        cid = workspace["candidate_id"]
        hypothesis_id = workspace.get("hypothesis_id") or card.ref
        text = (
            f"Implement the approved card (hypothesis {hypothesis_id}) in candidate {cid}, "
            f"editing only that candidate's worktree. Required order: implementation -> only "
            f"relevant SN tests -> leak-check. Leave your changes uncommitted. Do not commit, "
            f"do not freeze the candidate, and do not run or request any evaluation (no node, "
            f"cluster or fresh runs): the coordinator freezes and evaluates the candidate after "
            f"you finish. Use only approved implementer material; never inspect gold or "
            f"validation cases."
        )
        call = dict(iteration_id=iteration_id, key=self._request_key("implement", cid),
                    profile=self._implementer_profile, text=text, hypothesis_id=hypothesis_id)
        self._poll_agent("implementer", cid, call)
        return Diff(
            content=_extract_worktree_diff(workspace),
            test_refs=(),
            metadata=dict(candidate_id=cid, hypothesis_id=hypothesis_id,
                          parent_candidate_id=workspace.get("parent_candidate_id")),
        )

    def _poll_agent(self, role: str, candidate_id: Optional[str], call: dict) -> dict:
        """Call ``RoundOperations.agent`` with one key until the agent is idle."""
        result = self._round_ops.agent(role, candidate_id, **call)
        deadline = time.monotonic() + self._agent_timeout
        while result.get("status") in ("running", "unknown"):
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"{role} agent did not finish within {self._agent_timeout}s "
                    f"(key={call['key']!r})"
                )
            time.sleep(self._agent_poll_interval)
            result = self._round_ops.agent(role, candidate_id, **call)
        if result.get("status") != "idle":
            raise RuntimeError(
                f"{role} agent ended with status {result.get('status')!r}: "
                f"{result.get('reason', 'no reason given')}"
            )
        return result

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _signature_fields(signature: dict) -> set[str]:
    """Fields a v2 signature reads (all_of / any_of / not / leaf)."""
    children = signature.get("all_of") or signature.get("any_of") or (
        [signature["not"]] if "not" in signature else [])
    fields = {f for child in children if isinstance(child, dict) for f in _signature_fields(child)}
    if "field" in signature:
        fields.add(signature["field"])
    return fields


def _lookup(observation: dict, field: str) -> Any:
    value: Any = observation
    for part in field.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _worktree_ready(candidate: dict) -> bool:
    tree = candidate.get("worktree") or {}
    return tree.get("status") == "ready" and bool(tree.get("path"))


def _extract_worktree_diff(workspace: Any) -> str:
    """Uncommitted changes in the candidate worktree, as a unified diff.

    *workspace* is the binding from SNAdapter.workspace() (``path`` key).
    Tracked changes come from ``git diff HEAD``; untracked, non-ignored files
    are appended as new-file diffs (read-only: the index is not touched).
    Returns "" when the path is unavailable or git fails.
    """
    path = workspace.get("path") if isinstance(workspace, dict) else None
    if not path:
        return ""

    def git(*args: str) -> Optional[subprocess.CompletedProcess]:
        try:
            return subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                                  text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None

    tracked = git("diff", "HEAD")
    if tracked is None or tracked.returncode != 0:
        return ""
    parts = [tracked.stdout]
    untracked = git("ls-files", "--others", "--exclude-standard", "-z")
    for name in (untracked.stdout.split("\0") if untracked and untracked.returncode == 0 else []):
        if name:
            added = git("diff", "--no-index", "--", "/dev/null", name)
            if added is not None and added.returncode in (0, 1):  # 1 = files differ
                parts.append(added.stdout)
    return "".join(parts)
