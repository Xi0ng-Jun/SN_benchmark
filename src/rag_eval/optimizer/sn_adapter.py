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
import json
import re
import subprocess
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Optional

from .adapter import (BudgetExhausted, Card, Diff, EvalResult, EvaluationCancelled, EvaluationUnavailable, Predicate,
                      SmokeResult, SystemAdapter, Version)


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
    wait_for_running:
        Allow confirmed running evaluations/Agents to exceed local polling
        deadlines when ``configure_runtime`` supplies the overall deadline.
        Continuous unknown Agent outcomes get an ``agent_timeout`` window,
        reset by confirmed running observations; the global deadline still
        bounds all waits. Execution and bridge request timeouts are unchanged.

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
        key_generation        — optional non-negative int (default 0). v2 replays
                                a stored ``failed`` measurement result forever for
                                the same key, so an operator-requested retry after
                                a process-failure halt needs new keys. A generation
                                g > 0 appends ``/g{g}`` to measure and smoke keys
                                only; segment keys never change (a claimed fresh
                                batch must not be claimed again), nor do agent,
                                derive, approve or rebase keys. ``run_optimization``
                                persists the generation in state.json and sets it
                                through ``set_key_generation`` (the config value is
                                only the default for a new run).
                                A new key alone re-runs nothing: v2 counts a failed
                                attempt as a consumed slot under every key and re-runs
                                it only through its bounded infra-retry chain. So the
                                generation-g measure and smoke keys carry
                                ``infra_retry * (g + 1)``: each operator retry grants
                                every failed slot ``infra_retry`` more v2 retries.
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
        analyst_view_budget_step: int = 0,
        eval_poll_interval: float = 30.0,
        eval_timeout: float = 7200.0,
        infra_retry: int = 1,
        execution_timeout_seconds: int = 7200,
        evidence_dir=None,
        wait_for_running: bool = False,
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
        # evaluate call (the deadline is raised to what v2 needs to settle, see
        # _await). infra_retry and execution_timeout_seconds are forwarded
        # to v2 and frozen with each request key: keep them constant per run.
        self._eval_poll_interval = eval_poll_interval
        self._eval_timeout = eval_timeout
        self._infra_retry = infra_retry
        self._execution_timeout_seconds = execution_timeout_seconds
        self._wait_for_running = wait_for_running
        # Characters of history + diagnostic views in the analyst's evidence file
        # (analyse). The file, not the message, carries them: v2's message caps
        # (8 KiB text, 16384 UTF-16 units with the brief) do not limit evidence.
        if not 0 < analyst_view_budget <= MAX_ANALYST_VIEW_BUDGET:
            raise ValueError(f"analyst_view_budget must be in (0, {MAX_ANALYST_VIEW_BUDGET}] characters")
        self._analyst_view_budget = analyst_view_budget
        # §2.1 逐级加码: characters added per consecutive no-adoption round under the
        # analysed parent, the total capped by MAX_ANALYST_VIEW_BUDGET.
        if type(analyst_view_budget_step) is not int or analyst_view_budget_step < 0:
            raise ValueError("analyst_view_budget_step must be a non-negative int")
        self._analyst_view_budget_step = analyst_view_budget_step
        self.evidence_dir = None
        if evidence_dir is not None:
            self.set_evidence_dir(evidence_dir)
        self.last_analysis: dict = {}
        self.last_repair: dict = {}       # stats of the last repair feedback (file, sha256, counts)
        # Full text of the last analyst message (instructions + history + evidence),
        # set before dispatch so a failed dispatch can still be inspected.
        self.last_analyst_text: Optional[str] = None
        self.set_key_generation(config.get("key_generation", 0))

        # Trajectory cache: (run_dir_str, case_id) → DiagnosticTrajectory dict.
        # Bounded to the most recent _TRAJ_CACHE_SIZE entries to cap memory use.
        self._traj_cache: dict[tuple[str, str], dict] = {}
        self._TRAJ_CACHE_SIZE = 2000
        # Admitted-execution score cache: execution_id -> {case: (value, stub)}.
        self._score_cache: dict[str, dict] = {}
        self._SCORE_CACHE_SIZE = 512
        # Per-candidate locks held around the FIRST call of a v2 measurement key
        # (see _await): v2 plans a footprint from the attempts that exist when
        # the key is first called, so concurrent first calls touching one
        # candidate (two siblings' smoke on their shared parent) would both top
        # it up. The first call plans AND launches, so later plans see the attempts.
        self._plan_locks: dict[str, threading.Lock] = {}
        self._plan_locks_guard = threading.Lock()
        self._runtime_remaining = None
        self._runtime_cancel = threading.Event()
        self._cancel_remote_requested = threading.Event()
        self._execution_admission = None
        self._agent_requests = {}
        self._agent_requests_lock = threading.Lock()
        self.cancellation_report = []

    def configure_runtime(self, *, remaining: Callable[[], float],
                          max_concurrent_executions: int) -> None:
        from rag_eval.workbench.execution_admission import ExecutionAdmission
        if not callable(remaining):
            raise ValueError("remaining must be callable")
        if type(max_concurrent_executions) is not int or max_concurrent_executions < 1:
            raise ValueError("max_concurrent_executions must be a positive int")
        self._runtime_remaining = remaining
        self._runtime_cancel.clear()
        self._cancel_remote_requested.clear()
        self.cancellation_report = []
        self._execution_admission = ExecutionAdmission(max_concurrent_executions, self.check_runtime,
                                                       reports=self.cancellation_report)
        measurements = getattr(self._round_ops, "measurements", None)
        if measurements is not None:
            measurements.restore_admission(self._execution_admission,
                                           self._request_key(""))

    def check_runtime(self) -> None:
        if self._runtime_remaining is None:
            return
        if self._runtime_cancel.is_set():
            raise EvaluationCancelled("optimisation work cancelled; original request identities retained")
        if self._runtime_remaining() <= 0:
            raise EvaluationCancelled("optimisation deadline exceeded; original request identities retained")

    def _runtime_wait(self, seconds: float) -> None:
        self.check_runtime()
        if self._runtime_remaining is None:
            time.sleep(seconds)
            return
        self._runtime_cancel.wait(max(0, min(seconds, self._runtime_remaining())))
        self.check_runtime()

    @contextmanager
    def _runtime_slot(self, slot):
        acquired = False
        try:
            self.check_runtime()
            if self._runtime_remaining is None:
                slot.acquire()
                acquired = True
            else:
                while not acquired:
                    self.check_runtime()
                    acquired = slot.acquire(timeout=max(0, min(.05, self._runtime_remaining())))
            self.check_runtime()
            yield
        finally:
            if acquired:
                slot.release()

    def stop_polling(self) -> None:
        if self._runtime_remaining is not None:
            self._runtime_cancel.set()

    def cancel_pending(self) -> None:
        """Request local execution stop and remote archive; neither is a join.

        Receipts and sessions stay available for recovery. Archive uncertainty
        is reported explicitly; a remote Agent can outlive the local deadline.
        """
        if self._runtime_remaining is None:
            return
        self._cancel_remote_requested.set()
        self._runtime_cancel.set()
        if self._execution_admission is not None:
            self._execution_admission.cancel(actor=self._actor())
        with self._agent_requests_lock:
            requests = dict(self._agent_requests)
        for key, session_id in requests.items():
            self._cancel_agent(key, session_id)

    def _cancel_agent(self, key, session_id):
        ops = self._round_ops
        try:
            if session_id is None:
                creation_key = ops._key("agent-create", key)
                task_id = self._config.get("task_id")
                intent = next((r for r in ops.creations.store.list(task_id, actor=ops._agent_actor(key))
                               if r["request_key"] == creation_key), {})
                session_id = intent.get("session_id")
            if not session_id:
                self.cancellation_report.append(dict(key=key, status="remote_creation_unknown"))
                return
            session, _ = ops.sessions.scope(self._config["task_id"], session_id)
            response = ops.sessions.bridge.call(dict(op="archiveAgent", agentId=session["agent_id"]))
            self.cancellation_report.append(dict(key=key, session_id=session_id,
                archive=response.get("result"), remote_stop_confirmed=False))
        except Exception as exc:
            self.cancellation_report.append(dict(key=key, status="cancel_unavailable", error=type(exc).__name__))

    def _track_agent(self, key, result):
        if self._runtime_remaining is None:
            return
        session_id = result.get('session_id')
        with self._agent_requests_lock:
            self._agent_requests[key] = session_id
        if self._cancel_remote_requested.is_set():
            self._cancel_agent(key, session_id)
        self.check_runtime()

    def set_evidence_dir(self, directory) -> None:
        """Where analyse() writes the analyst's evidence files (inside the v3 run directory)."""
        from pathlib import Path
        self.evidence_dir = Path(directory).resolve()

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
        key = "/".join([self._KEY_PREFIX, str(run_id), *(str(p) for p in parts)])
        if len(key) > 200:
            key = key[:135] + "/" + hashlib.sha256(key.encode()).hexdigest()
        return key

    def _generation(self) -> tuple[str, ...]:
        """Key suffix for measure/smoke keys: () for generation 0, ('g{n}',) otherwise."""
        g = self.key_generation
        return (f"g{g}",) if g else ()

    def _actor(self) -> str:
        actor = self._config.get("actor")
        if not actor:
            raise ValueError("config must contain 'actor' for v2 approvals, derives and freezes")
        return actor

    def _require_round_ops(self, what: str):
        self.check_runtime()
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
    #   search set                    -> registered scope ``split:search:<hash>``
    #   validation pool               -> ``split:validation:<hash>`` plus each
    #                                    completed fresh batch's own scope
    # (v2's base plan, scope None, runs search ∪ validation in one execution; a
    # screen reads only the validation pool, so it measures only that.)
    #   smoke sample                  -> registered scope ``smoke:<hash>``
    #   i-th fresh batch              -> its batch id (v2 ``segment``)
    # v2 measurements are asynchronous and idempotent per request key: the same
    # key is called again until it reports ``complete``; a rerun after a crash
    # replays the stored footprint instead of dispatching again.

    _MEASURE_PURPOSES = ("baseline", "diagnose", "screen")
    _KEY_PREFIX = "v3"   # first segment of every v2 request key (method-v4's adapter uses "v4")
    _HISTORY_VOCABULARY = "v3"   # which history labels analyse() renders (see _history_line)
    _BRIEF = "method-v3"         # v2 brief selector of every Agent this adapter creates

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
        from rag_eval.workbench.splits import require_frozen
        splits = require_frozen(protocol, "Reading the iteration partitions") or {}
        fresh = [dict(batch_id=b["batch_id"], case_ids=list(b["case_ids"]))
                 for b in protocol.get("fresh_batches", [])]
        return dict(search=list(splits.get("search", ())),
                    validation=list(splits.get("validation", ())),
                    fresh_batches=fresh,
                    completed_fresh_batch_ids=[b["batch_id"] for b in self._used_batches(completed=True)],
                    k_base=self._k_base(protocol),
                    safeguards=list(protocol.get("safeguards") or []))

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
        from rag_eval.workbench.splits import require_frozen
        splits = require_frozen(protocol, f"A {purpose} evaluation")
        from rag_eval.workbench.execution_scopes import split_scope_id
        if purpose == "diagnose":
            search_scope = split_scope_id(protocol, "search")
            allowed: dict[str, Optional[str]] = {c: search_scope for c in splits["search"]}
            where = "the search set"
        else:
            validation_scope = split_scope_id(protocol, "validation")
            allowed = {c: validation_scope for c in splits["validation"]}
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

    def _v2_run_kwargs(self, *, generational: bool = True) -> dict:
        # These values are part of v2's frozen request: v2 refuses a replayed key
        # whose actor/infra_retry/timeout differ, so they must stay constant per key.
        # Generation-keyed requests (measure, smoke) get infra_retry * (g + 1);
        # segment keys never change, so they keep the pre-registered value.
        g = self.key_generation if generational else 0
        runtime = (dict(execution_admission=self._execution_admission)
                   if self._execution_admission is not None else {})
        return dict(actor=self._actor(), infra_retry=self._infra_retry * (g + 1),
                    timeout_seconds=self._execution_timeout_seconds, **runtime)

    def _first_call(self, candidate_ids: tuple, call):
        """Run the planning (first) call of a key while holding every touched candidate's lock."""
        with self._plan_locks_guard:
            locks = [self._plan_locks.setdefault(c, threading.Lock())
                     for c in sorted({c for c in candidate_ids if c})]
        from contextlib import ExitStack
        with ExitStack() as stack:
            for lock in locks:  # sorted order: no deadlock between two callers
                stack.enter_context(self._runtime_slot(lock))
            self.check_runtime()
            return call()

    def _await(self, calls: dict[str, tuple[str, Any]], touches: Optional[dict[str, tuple]] = None) -> None:
        """Re-issue each keyed v2 call until all report ``complete``.

        *calls*: label -> (request key, zero-argument callable). *touches*:
        label -> candidate ids whose attempts the key's v2 plan may add; the
        first call of the label holds their locks (``_first_call``). v2 statuses:
        running -> poll again; complete -> done; budget_exhausted ->
        BudgetExhausted (the frozen footprint waits for human confirmation);
        failed / exhausted / stopped / anything else, or the poll deadline ->
        EvaluationUnavailable. Missing measurements are never evidence.

        The poll deadline never ends before v2 itself must settle: v2 stops an
        execution at execution_timeout_seconds and runs at most infra_retry
        retry generations one after another. A shorter deadline (R3 had both at
        4 h) halts the whole run while v2 is legitimately retrying a timed-out
        execution, leaving it idle until someone reruns it.

        With wait_for_running and a configured runtime budget, pending running
        evaluations instead wait until completion, failure, or global cancellation.
        """
        pending = dict(calls)
        planned: set[str] = set()
        retries = self._infra_retry * (self.key_generation + 1)
        execution_timeout = getattr(self, '_recovery_execution_timeout_seconds', None) or self._execution_timeout_seconds
        settle = 1.25 * execution_timeout * (retries + 1)
        deadline = time.monotonic() + max(self._eval_timeout, settle)
        while True:
            for label, (key, call) in list(pending.items()):
                self.check_runtime()
                if label in planned:
                    result = call() or {}
                else:
                    planned.add(label)
                    result = self._first_call(tuple((touches or {}).get(label, ())), call) or {}
                self.check_runtime()
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
            if (time.monotonic() >= deadline
                    and not (self._wait_for_running and self._runtime_remaining is not None)):
                raise EvaluationUnavailable(
                    f"{', '.join(pending)} not complete within {self._eval_timeout}s "
                    f"(keys={[k for k, _ in pending.values()]!r}); rerun resumes the same keys"
                )
            self._runtime_wait(self._eval_poll_interval)

    def _measure(self, candidate_ids: list[str], split: str) -> None:
        """v2 ``split`` each candidate on one whole split ('search' or 'validation').

        'validation' is the validation pool: the validation split plus every
        completed fresh batch, with the parent topped up on the same scopes.
        Key ``v3/{run_id}/split/{split}/{cid}/{n}[/g{generation}]``; for
        'validation' n = completed fresh batches (0 for 'search'). v2 freezes a
        footprint at the first call of a key with the batches completed AT THAT
        TIME and replays never re-plan, so once another batch is folded into the
        pool the new n gives a new key, which tops up only that batch. If a batch
        completes while we wait, measure again under the new n. The same
        candidate measured for different purposes on one split shares one key.
        """
        ops = self._require_round_ops("evaluate")
        kwargs = self._v2_run_kwargs()
        pooled = split == "validation"
        while True:
            n = len(self._used_batches(completed=True)) if pooled else 0
            calls, touches = {}, {}
            for cid in dict.fromkeys(candidate_ids):
                key = self._request_key("split", split, cid, n, *self._generation())
                calls[f"{split} {cid}"] = (key, lambda cid=cid, key=key: ops.split(cid, split, key=key, **kwargs))
                # A validation measurement also tops up the parent on the same scopes.
                touches[f"{split} {cid}"] = (cid, ops.service.candidate(cid).get("parent_candidate_id")
                                              if pooled else None)
            self._await(calls, touches)
            if not pooled or len(self._used_batches(completed=True)) == n:
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
        on that scope; only scope None would include legacy links). Attempts that
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
        attempt_scores = [{r["case_id"]: r["value"] for r in a["rows"] if r["value"] is not None}
                          for a in attempts]
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
            context=dict(candidate_id=candidate_id, scopes=dict(case_scopes)),
            attempt_scores=attempt_scores,
        )

    # ------------------------------------------------------------------
    # 2a. safeguards — the iteration's pre-registered v2 safeguards, v2's rule
    # ------------------------------------------------------------------

    def describe_safeguards(self, bounds: dict) -> dict:
        if bounds:
            raise ValueError("on SN the safeguards are the iteration protocol's pre-registered v2 safeguards; "
                             "ProtocolConfig.safeguard_bounds must be empty (one source per quantity)")
        return dict(source="iteration_protocol", specs=list(self._protocol().get("safeguards") or []),
                    infrastructure_rule=V3_INFRASTRUCTURE_RULE)

    def safeguards(self, candidate: EvalResult, reference: EvalResult, bounds: dict) -> dict:
        """v2 lcd-v2 safeguards of one pair, computed exactly as a v2 comparison does.

        Specs: the iteration protocol's frozen ``safeguards``. Observations:
        ``comparisons.observe_safeguards`` over the run observations of the
        admitted attempts behind both results (answer_language_match, and
        cost_per_task as the subject/reference token ratio); infrastructure
        failure rate over every terminal execution of each side on the pair's
        scopes, by ``_infrastructure`` (v3 rule, see V3_INFRASTRUCTURE_RULE).
        Rule: ``gate.safeguard_checks`` (breached or unobserved blocks the verdict).
        """
        from rag_eval.workbench.comparisons import ComparisonService, observe_safeguards
        from rag_eval.workbench.gate import safeguard_checks
        specs = self.describe_safeguards(bounds)["specs"]
        if not specs:
            # v2 lcd-v2 requires pre-registered safeguards; without them nothing is observed.
            return dict(source="iteration_protocol", ok=False, reasons=["safeguards_not_preregistered"], checks=[])
        ops, views = self._require_round_ops("safeguards"), self._views()
        comparisons = ComparisonService(ops.repository, ops.service, views, initialize=False)
        trajectories, infra, sides = {}, {}, (("subject", candidate), ("reference", reference))
        for side, result in sides:
            cid = (result.context or {}).get("candidate_id")
            if not cid:
                raise ValueError("safeguards need SN results from evaluate_pair (context.candidate_id)")
            cases: dict[str, list] = {}
            for case_id, stubs in result.trajectories.items():
                for stub in stubs or []:
                    try:
                        cases.setdefault(case_id, []).append(views.observation(stub["run_id"], case_id))
                    except (KeyError, ValueError):
                        continue
            trajectories[side] = cases
            node = ops.service.candidate(cid)
            rows = [row for scope in sorted(set(result.context["scopes"].values()),
                                            key=lambda x: (x is not None, x or ""))
                    for row in self._infrastructure(comparisons, node, scope)]
            infra[side] = dict(total=sum(r["total"] for r in rows), failed=sum(r["failed"] for r in rows),
                               unobserved=sum(r["unobserved"] for r in rows), attempts=rows)
        observed, detail = observe_safeguards(trajectories)
        rows, reasons = safeguard_checks(observed, infra, specs)
        return dict(source="iteration_protocol", ok=not reasons, reasons=reasons, checks=rows,
                    observations={side: {k: detail.get(side, {}).get(k) for k in
                                         ("observations", "language_known", "tokens_per_task", "tokens_known")}
                                  for side, _ in sides},
                    infrastructure={side: dict(infra[side]) for side, _ in sides})

    def _infrastructure(self, comparisons, node: dict, scope) -> list[dict]:
        """Per terminal attempt of *node* on *scope*: v2's counts, with the v3 rule for dead attempts.

        v2 ``ComparisonService._infrastructure`` counts error/missing cases over
        every terminal execution and marks one that published no per-case
        result as unobserved (which makes the safeguard unobserved). v3
        (V3_INFRASTRUCTURE_RULE) counts such an attempt as all of its planned
        cases failed, the planned count read from the attempt's job plan
        snapshot; without that durable fact it stays unobserved, with a reason.
        v2's function and its v2 semantics are untouched.
        """
        from rag_eval.workbench.comparisons import ComparisonService
        scoped = ComparisonService.scoped(node, scope)
        measurements = {m["execution_id"]: m for m in ComparisonService.terminal(scoped)}
        _, task_id = self._require_ids()
        out = []
        for r in comparisons._infrastructure(scoped)["attempts"]:
            row = dict(execution_id=r["execution_id"], scope=scope, state=r["execution_state"],
                       total=r["total"], failed=r["failed"], unobserved=False, counted_from_plan=False)
            if r["unobserved"]:
                planned = self._planned_case_count(task_id, measurements.get(r["execution_id"]))
                if planned:
                    row.update(total=planned, failed=planned, counted_from_plan=True)
                else:
                    row.update(unobserved=True, reason="no published per-case result and no job plan snapshot")
            out.append(row)
        return out

    def _planned_case_count(self, task_id: str, measurement: Optional[dict]) -> Optional[int]:
        """Cases the attempt's frozen job plan scheduled (None when that record is unavailable)."""
        job_id = (measurement or {}).get("job_id")
        if not job_id:
            return None
        try:
            job = self._round_ops.service.jobs.get(task_id, job_id)
            return len(job["plan_snapshot"]["content"]["selection"]["case_ids"]) or None
        except (KeyError, LookupError, TypeError, ValueError):
            return None

    def evaluate(
        self,
        version: Version,
        task_ids: list[str],
        k: int,
        *,
        purpose: str = "screen",
    ) -> EvalResult:
        """Measure *version* through v2 ``measure`` and read its admitted scores.

        purpose: 'diagnose' (task_ids ⊆ search set, v2 ``split`` search) or
        'baseline' / 'screen' (task_ids ⊆ validation pool, v2 ``split``
        validation). k must equal the protocol's ``k_base``;
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
        self._measure([cid], "search" if purpose == "diagnose" else "validation")
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
        screen  : k=k_base on the validation pool; v2 ``split`` validation on both.
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
            self._measure([vcid, rcid], "validation")
            return self._read(vcid, scopes), self._read(rcid, scopes)
        if purpose == "smoke":
            from rag_eval.workbench.execution_scopes import smoke_scope_id
            self._check_k(k, 1, purpose)
            scope_id = smoke_scope_id(protocol, task_ids)  # refuses non-search / duplicate ids
            parent = ops.service.candidate(vcid).get("parent_candidate_id")
            if parent != rcid:
                raise ValueError(f"smoke compares a candidate with its parent ({parent!r}), not {rcid!r}")
            key = self._request_key("smoke", vcid, scope_id, *self._generation())
            kwargs = self._v2_run_kwargs()
            self._await({f"smoke {vcid}": (key, lambda: ops.smoke(vcid, task_ids, key=key, **kwargs))},
                        {f"smoke {vcid}": (vcid, rcid)})
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
            kwargs = self._v2_run_kwargs(generational=False)
            label = f"segment {rcid}->{vcid} on {batch_id}"
            self._await({label: (key, lambda: ops.segment(start_id=rcid, end_id=vcid, batch_id=batch_id,
                                                          key=key, **kwargs))}, {label: (rcid, vcid)})
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
        result = self._apply_source(new_cid, self._request_key("rebase-apply", new_cid),
                                    f"rebase of {cid!r} onto {parent_cid!r}")
        if result.get("status") in ("conflict", "empty"):
            return None
        if result.get("status") != "applied":
            raise RuntimeError(f"v2 rebase returned {result!r}")
        child = ops.service.candidate(new_cid)
        if child.get("status") != "frozen":
            child = ops.service.freeze(new_cid, actor=actor)
        return self._version(child)

    def _apply_source(self, candidate_id: str, key: str, what: str) -> dict:
        """v2 ``RoundOperations.rebase``: apply the source's frozen diff uncommitted; poll while not ready."""
        self.check_runtime()
        ops, actor = self._round_ops, self._actor()
        deadline = time.monotonic() + self._worktree_timeout
        result = ops.rebase(candidate_id, key=key, actor=actor)
        while result.get("status") == "running":
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{what} not applied within {self._worktree_timeout}s "
                                   f"({result.get('reason')!r})")
            self._runtime_wait(self._agent_poll_interval)
            result = ops.rebase(candidate_id, key=key, actor=actor)
        return result

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
        self.check_runtime()
        service = self._round_ops.service
        candidate = service.candidate(candidate_id)
        deadline = time.monotonic() + self._worktree_timeout
        while not _worktree_ready(candidate):
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Worktree of candidate {candidate_id!r} not ready within "
                    f"{self._worktree_timeout}s (worktree={candidate.get('worktree')!r})"
                )
            self._runtime_wait(self._agent_poll_interval)
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
        history: Optional[list[dict]] = None,
        no_adoption_rounds: int = 0,
        case_scores: Optional[dict[str, float]] = None,
    ) -> list[Card]:
        """Dispatch an analyst agent on accountant-selected search cases; return its new Cards.

        *search_set_trajectories* holds only the cases the loop selected (§2.3:
        selection is the accountant's, by a pre-registered rule). Each case is
        rendered as a diagnostic view under opaque refs (c1, c2, ...) within
        ``analyst_view_budget`` characters into an evidence file in the run
        directory (``evidence_dir``/round-<id>-<parent>.md, written once,
        atomically); the message names its absolute path and sha256, so the
        Agent needs no other evidence and the audit knows what it read. Cards are bound to this request and to the parent
        candidate: the analyst's drafts (``proposal draft``, author
        ``agent:analyst``) under the parent created after this request's v2
        Agent creation intent was prepared (v2 derive requires the card's
        parent to match). The intent is durable and keyed, so a rerun after a
        kill while the Agent was still drafting replays the same Agent and
        still finds every card it drafted, before or after the restart.
        ``run_optimization(state_dir=...)`` persists the round's Cards right
        after this call and never calls analyse again for that round.

        *history* (§2.3): hypotheses already processed under this parent, as
        ``{title, label[, attribution, attribution_possibly_biased]}``. It is
        rendered as a short labels-only section ahead of the evidence and
        counts against ``analyst_view_budget`` (at most a quarter of it; the
        oldest entries are trimmed first). Entries whose label is not in the
        §2.3 vocabulary are dropped, so no score can reach the prompt.

        *no_adoption_rounds* N (§2.1 逐级加码): the budget is
        ``analyst_view_budget + N * analyst_view_budget_step`` (at most
        MAX_ANALYST_VIEW_BUDGET), and for N >= 1 the message states the count
        and asks for failure modes or components other than the history titles.

        The request key is ``v3/{run_id}/analyse/{round_id}/{parent_cid}``:
        round ids grow monotonically within a run and are restored with the
        loop state on resume, so the same round replays the same agent receipt.
        *parent* / *round_id* default to config ``parent_candidate_id`` /
        ``round_id``; a parent is required: the Agent is a v2 node analyst
        bound to it (``candidate_id=<parent>``), which v2 allows only for a
        frozen candidate with a finished run.
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
        if not parent_cid:
            # v2 binds every analyst of an iteration to a candidate or to the whole
            # iteration; v3's analyst is a node analyst of the round's parent.
            raise ValueError("analyse() needs the parent candidate (argument or config) to bind the analyst")
        analyst_key = self._request_key("analyse", round_id, parent_cid)
        from .sn_trajectory import render_views
        roles = case_roles or {}
        views = self.diagnostic_views(search_set_trajectories)
        ordered = sorted(views, key=lambda t: (roles.get(t, "failure") != "failure",
                                               list(search_set_trajectories).index(t)))
        # method-v4 §5: the analyst may see search-set scores (never validation or test scores).
        scored = case_scores or {}
        items = [(f"c{i + 1}", roles.get(t, "failure") + (f" score={scored[t]:.2f}" if t in scored else ""), views[t])
                 for i, t in enumerate(ordered)]
        if self.evidence_dir is None:
            raise RuntimeError("analyse() needs an evidence directory (set_evidence_dir): the analyst reads "
                               "its evidence from a file in the v3 run directory")
        if type(no_adoption_rounds) is not int or no_adoption_rounds < 0:
            raise ValueError(f"no_adoption_rounds must be a non-negative int, got {no_adoption_rounds!r}")
        budget = min(MAX_ANALYST_VIEW_BUDGET,
                     self._analyst_view_budget + no_adoption_rounds * self._analyst_view_budget_step)
        tried, n_hist_shown, n_hist_omitted = self._analysis_history(history or [], budget // 4, parent_cid)
        evidence, n_shown, n_omitted = render_views(items, budget - len(tried))
        stuck = (f"no adoption under this parent for {no_adoption_rounds} consecutive round(s)\n"
                 if no_adoption_rounds else "")
        worktree_map = _render_worktree_map(self._round_ops, parent_cid)
        content = (f"# method-{self._KEY_PREFIX} analyst evidence\nformat: {EVIDENCE_FORMAT}\nround: {round_id}\n"
                   f"parent: {parent_cid}\ncases: {len(items)} with a view of {len(search_set_trajectories)} "
                   f"selected; {n_shown} shown, {n_omitted} omitted (budget {budget} chars)\n"
                   + stuck + "\n" + worktree_map + self._evidence_extra() + tried + evidence)
        path, digest, reused = self._write_analysis_evidence(
            f"round-{round_id}-{parent_cid[:12]}.md", content, items, ordered, n_shown)
        self._analysis_evidence_ready(path, digest, items, ordered, n_shown, reused)
        analyst_text = (
            f"Diagnose the parent version's failures and draft up to {n_cards} legal lcd-v2 mechanism cards, "
            f"one per distinct failure mode. Your evidence is the file {path} (sha256 {digest}). Read it in "
            f"full: it holds the search cases selected by a fixed rule as c1, c2, ... ([failure] cases, and "
            f"[control] cases for contrast) and the outcome labels of hypotheses already tried under this "
            f"parent. " + self._analyst_input_text() +
            f"Each card needs title, component, mechanism, implementer_brief, falsification, "
            f"predictions (may be []), and a contract with slice_predicate, manipulation and slice_effect. "
            f"A slice_predicate may read behaviour fields and trace.* step features (e.g. "
            f"trace.n_empty_retrievals, trace.clarification_requested). Draft each card under the parent "
            f"node with `proposal draft --parent {parent_cid} --file <card.json>`; "
            + self._analyst_implementation_guidance() +
            "IMPORTANT: the implementer edits only the SN product worktree (backend/, frontend/, etc.). "
            "If the root cause is in a file whose path starts with src/rag_eval/ (the evaluation harness), "
            "the implementer_brief must identify the EQUIVALENT file in the SN product repo that controls "
            "the same behaviour (e.g. the answerer prompt in backend/app/services/), not the harness file. "
            "Never write a brief that names src/rag_eval/ paths as the files to change. "
            + self._analyst_stuck_text(no_adoption_rounds)
            + self._analyst_extra_text()
            + "Draft the cards now."
        )
        from rag_eval.workbench.agent_messages import MAX_MESSAGE_BYTES, MessageInputRejected
        stats = dict(selected=len(search_set_trajectories), with_view=len(views), shown=n_shown,
                     omitted=n_omitted, evidence_chars=len(evidence), history_shown=n_hist_shown,
                     history_omitted=n_hist_omitted, history_chars=len(tried), budget=budget,
                     no_adoption_rounds=no_adoption_rounds,
                     evidence_path=str(path), evidence_sha256=digest, evidence_reused=reused,
                     message_bytes=len(analyst_text.encode("utf-8")))
        self.last_analysis, self.last_analyst_text = stats, analyst_text
        if stats["message_bytes"] > MAX_MESSAGE_BYTES:
            raise RuntimeError(f"analyst message is {stats['message_bytes']} bytes, over v2's {MAX_MESSAGE_BYTES}")
        # A node analyst bound to the round's parent (v2 ``agent_creations``: a
        # frozen candidate with a finished run). Not scope="iteration": v2 refuses
        # an iteration analyst until two candidates are evaluated, and in round 1
        # only the root is. Each round's parent gets its own Agent (the key names it).
        # brief=V3_BRIEF: v2 attaches the short v3 analyst brief, not its own.
        call = dict(iteration_id=iteration_id, key=analyst_key, profile=self._analyst_profile,
                    text=analyst_text, brief=self._BRIEF)
        try:
            self._poll_agent("analyst", parent_cid, call)
        except MessageInputRejected as exc:
            raise RuntimeError(f"v2 rejected the analyst message for {analyst_key!r} ({exc}); message "
                               f"{stats['message_bytes']} bytes plus the v3 brief") from exc
        since = self._analyst_requested_at(task_id, analyst_key)
        fresh = [r for r in self._hypotheses.list(task_id)
                 if r.get("parent_candidate_id") == parent_cid and r.get("status") in ("draft", "approved")
                 and r.get("author") == ANALYST_AUTHOR and _instant(r["created_at"]) >= since]
        cards = [_v2_card_to_v3_card(r) for r in fresh[:n_cards]]
        self._cards_analysed(cards, path, digest)
        return cards

    # Hooks for subclasses (method-v4); the v3 adapter adds nothing.
    def _analysis_history(self, history, budget, parent_id):
        return _render_history(history, budget, vocabulary=self._HISTORY_VOCABULARY)

    def _write_analysis_evidence(self, name, content, items, ordered, shown):
        return self._write_evidence(name, content)

    def _analysis_evidence_ready(self, path, digest, items, ordered, shown, reused):
        return None

    def _analyst_input_text(self):
        return "It is your only input: do not request more evidence and do not read any other file under var/. "

    def _analyst_implementation_guidance(self):
        return ("Each card's implementer_brief must name the specific file(s) to change, the exact "
                "pattern or logic to modify, and the pytest command(s) for verification (e.g. "
                "`pytest tests/test_ask_service.py -k test_language`); a brief that only restates "
                "the failure mode is not acceptable. ")

    def _analyst_stuck_text(self, no_adoption_rounds):
        return (f"No hypothesis has been adopted under this parent for {no_adoption_rounds} consecutive "
                "round(s): change direction. Target failure modes or components different from every "
                "hypothesis listed as already tried in the evidence file. " if no_adoption_rounds else "")

    def _implementation_metadata(self, card, workspace, *, frozen=False):
        return {}

    def _implementation_order(self, *, repair=False):
        return ("fix" if repair else "implementation") + " -> only relevant SN tests -> leak-check. "

    def _repair_input_text(self):
        return "read it in full and read no other file under var/. "

    def _evidence_extra(self) -> str:
        return ""

    def _analyst_extra_text(self) -> str:
        return ""

    def _cards_analysed(self, cards: list[Card], path, digest: str) -> None:
        return None

    def _implementer_extra_text(self, card: Card, workspace: dict) -> str:
        return ""

    def _write_evidence(self, name: str, content: str):
        """Write the evidence file once (atomic); a replay keeps the file a message may already name.

        Returns (absolute path, sha256 of its bytes, reused). The content is a
        pure function of the round's inputs, so a replay normally finds identical
        bytes; if they differ the existing file stays authoritative (logged).
        """
        import logging
        from . import ledger_io
        path = self.evidence_dir / name
        data = content.encode("utf-8")
        reused = path.exists()
        if reused:
            existing = path.read_bytes()
            if existing != data:
                logging.getLogger(__name__).warning(
                    "evidence file %s exists with different content; keeping it (its sha256 is the one "
                    "the analyst message names)", path)
            data = existing
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            ledger_io.atomic_text(path, content)
        return path, hashlib.sha256(data).hexdigest(), reused

    def _analyst_requested_at(self, task_id: str, key: str):
        """When v2 prepared the creation intent of the analyst Agent for *key* (durable)."""
        ops = self._round_ops
        creation_key = ops._key("agent-create", key)
        actor = ops._agent_actor(key)
        intent = next((i for i in ops.creations.store.list(task_id, actor=actor)
                       if i["request_key"] == creation_key), None)
        if intent is None:
            raise RuntimeError(f"analyst Agent creation intent for {key!r} not found after dispatch")
        return _instant(intent["prepared_at"])

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
        candidate = ops.service.candidate(cid)
        if candidate.get("status") == "frozen":
            # A crash may follow freeze but precede coordinator persistence.
            # The worktree is clean now: reconstruct the immutable change,
            # without waking the implementer on an already frozen candidate.
            parent_id = workspace.get("parent_candidate_id")
            if parent_id and candidate.get("parent_candidate_id") != parent_id:
                raise ValueError("frozen implementation belongs to another parent")
            extra = self._implementation_metadata(card, workspace, frozen=True)
            return Diff(content=_extract_frozen_diff(candidate), metadata=dict(candidate_id=cid,
                hypothesis_id=hypothesis_id, parent_candidate_id=candidate.get("parent_candidate_id"), **extra))
        text = (
            f"Implement the approved card (hypothesis {hypothesis_id}) in candidate {cid}, "
            f"Card failure mode: {card.failure_mode_desc}. Implementer brief: {card.change_hypothesis}. "
            f"editing only that candidate's worktree. Required order: " + self._implementation_order() +
            f"Leave your changes uncommitted. Do not commit, "
            f"do not freeze the candidate, and do not run or request any evaluation (no node, "
            f"cluster or fresh runs): the coordinator freezes and evaluates the candidate after "
            f"you finish. Use only approved implementer material; never inspect gold or "
            f"validation cases."
        ) + self._implementer_extra_text(card, workspace)
        call = dict(iteration_id=iteration_id, key=self._request_key("implement", cid),
                    profile=self._implementer_profile, text=text, hypothesis_id=hypothesis_id, brief=self._BRIEF)
        self._poll_agent("implementer", cid, call)
        extra = self._implementation_metadata(card, workspace)
        return Diff(
            content=_extract_worktree_diff(workspace),
            test_refs=(),
            metadata=dict(candidate_id=cid, hypothesis_id=hypothesis_id,
                          parent_candidate_id=workspace.get("parent_candidate_id"), **extra),
        )

    # ------------------------------------------------------------------
    # 6b. repair — §2.1 smoke repair on a sibling candidate (v2 derive + rebase)
    # ------------------------------------------------------------------

    def repair(
        self,
        card: Card,
        parent: Version,
        failed: Version,
        *,
        smoke: SmokeResult,
        candidate_smoke: EvalResult,
        parent_smoke: EvalResult,
        attempt: int,
    ) -> Diff:
        """Second attempt of *card* after a behavioural smoke failure; returns the new child's Diff.

        v2 freezes a candidate for good ("derive a child for further changes"),
        and a child of *failed* would not be a child of *parent* (smoke, the
        screen pair and the parent-hash rule all need the same parent). So the
        repair is a sibling: ``RoundOperations.derive(parent, rebase_from=failed)``
        (v2 re-files the approved card under the same parent as an approved
        copy and records ``integrated_from`` = failed, cherry-pick), then
        ``RoundOperations.rebase`` applies the failed attempt's frozen diff
        uncommitted to the sibling's worktree, then a new implementer Agent
        (keyed ``repair-implement/<sibling>``) gets the repair message. The
        coordinator freezes it in ``materialize``. Every step is keyed by the
        failed candidate and *attempt*, so a rerun replays.

        The message names a feedback file in the evidence directory (path +
        sha256, written once like the analyst's): per smoke case (opaque refs
        s1, s2, ...; the smoke sample is part of the search set) the predicate's
        value on the parent and on the failed attempt, and both diagnostic
        views (whitelisted projection: no gold, no scores, no task ids), within
        ``analyst_view_budget`` characters.
        """
        ops = self._require_round_ops("repair")
        iteration_id, _ = self._require_ids()
        if self.evidence_dir is None:
            raise RuntimeError("repair() needs an evidence directory (set_evidence_dir) for its feedback file")
        actor = self._actor()
        failed_cid, parent_cid = self.candidate_id(failed), self.candidate_id(parent)
        source = ops.service.candidate(failed_cid)
        if source.get("parent_candidate_id") != parent_cid:
            raise ValueError(f"repair of {failed_cid!r} needs its own parent {source.get('parent_candidate_id')!r}, "
                             f"not {parent_cid!r}")
        hypothesis_id = source.get("proposal_id")
        if not hypothesis_id:
            raise ValueError(f"Candidate {failed_cid!r} has no approved card to repair")
        content, stats = self._repair_feedback(card, failed_cid, attempt, candidate_smoke, parent_smoke)
        path, digest, reused = self._write_evidence(f"repair-{failed_cid[:12]}-a{attempt}.md", content)
        new_cid = ops.derive(parent_cid, hypothesis_id=hypothesis_id, key=self._request_key("repair", failed_cid, attempt),
                             actor=actor, rebase_from=failed_cid)
        candidate = self._await_worktree(new_cid)
        workspace = dict(candidate_id=new_cid, parent_candidate_id=parent_cid,
                         hypothesis_id=candidate.get("proposal_id"), path=(candidate.get("worktree") or {}).get("path"))
        if candidate.get("status") == "frozen":
            extra = self._implementation_metadata(card, workspace, frozen=True)
            return Diff(content=_extract_frozen_diff(candidate), metadata=dict(candidate_id=new_cid,
                hypothesis_id=candidate.get("proposal_id"), parent_candidate_id=parent_cid,
                repair_of=failed_cid, attempt=attempt, **extra))
        applied = self._apply_source(new_cid, self._request_key("repair-apply", new_cid),
                                     f"repair start (diff of {failed_cid!r}) in {new_cid!r}")
        if applied.get("status") != "applied":
            raise RuntimeError(f"the failed attempt's change could not be applied to the repair candidate: {applied!r}")
        tree = candidate["worktree"]
        workspace = dict(candidate_id=new_cid, parent_candidate_id=parent_cid,
                         hypothesis_id=candidate.get("proposal_id"), path=tree["path"])
        text = (
            f"Repair the approved card (hypothesis {workspace['hypothesis_id']}) in candidate {new_cid}, "
            f"attempt {attempt}. Card failure mode: {card.failure_mode_desc}. Implementer brief: "
            f"{card.change_hypothesis}. The previous attempt's change is already applied, uncommitted, in "
            f"this worktree. The coordinator's smoke check ran it on a few search cases and the predicted "
            f"behaviour change did not happen (the card's trajectory predicate did not move in the predicted "
            f"direction), so the change most likely did not take effect. The file {path} (sha256 {digest}) "
            f"holds, per smoke case, the predicate's value on the parent and on the previous attempt and both "
            f"diagnostic views; " + self._repair_input_text() + "Find why the change did "
            f"not take effect and fix it, editing only this candidate's worktree. Required order: "
            + self._implementation_order(repair=True) +
            f"Leave your changes uncommitted. Do not commit, do not freeze "
            f"the candidate, and do not run or request any evaluation: the coordinator freezes and smokes the "
            f"candidate again after you finish. Never inspect gold or validation cases."
        )
        text += self._implementer_extra_text(card, workspace)
        self.last_repair = dict(stats, feedback_path=str(path), feedback_sha256=digest, feedback_reused=reused,
                                candidate_id=new_cid, repair_of=failed_cid, attempt=attempt,
                                message_bytes=len(text.encode("utf-8")))
        call = dict(iteration_id=iteration_id, key=self._request_key("repair-implement", new_cid),
                    profile=self._implementer_profile, text=text, hypothesis_id=workspace["hypothesis_id"],
                    brief=self._BRIEF)
        self._poll_agent("implementer", new_cid, call)
        extra = self._implementation_metadata(card, workspace)
        return Diff(
            content=_extract_worktree_diff(workspace),
            test_refs=(),
            metadata=dict(candidate_id=new_cid, hypothesis_id=workspace["hypothesis_id"],
                          parent_candidate_id=parent_cid, repair_of=failed_cid, attempt=attempt, **extra),
        )

    def _repair_feedback(self, card: Card, failed_cid: str, attempt: int,
                         candidate_smoke: EvalResult, parent_smoke: EvalResult) -> tuple[str, dict]:
        """Feedback file of a smoke repair: predicate values and diagnostic views, no scores or ids."""
        from .sn_trajectory import render_views
        cand_traj = dict((candidate_smoke.trajectories if candidate_smoke is not None else {}) or {})
        par_traj = dict((parent_smoke.trajectories if parent_smoke is not None else {}) or {})
        task_ids = list(dict.fromkeys([*par_traj, *cand_traj]))
        before = self.features(par_traj, card.predicate) if card.predicate else {}
        after = self.features(cand_traj, card.predicate) if card.predicate else {}
        par_views, cand_views = self.diagnostic_views(par_traj), self.diagnostic_views(cand_traj)

        def value(v: Optional[bool]) -> str:
            return "not computable" if v is None else ("true" if v else "false")

        def rate(values: dict) -> str:
            known = [v for v in values.values() if v is not None]
            return f"{sum(known)} of {len(known)} computable case(s) true"

        pred = card.predicate
        lines = [f"# method-v3 smoke repair feedback\nformat: {REPAIR_FORMAT}\nattempt: {attempt}\n"
                 f"previous attempt: {failed_cid}\ncard: {' '.join(card.failure_mode_desc.split())}\n"]
        if pred is not None:
            expr = pred.boolean_expression
            lines.append(f"predicate: {json.dumps(expr, ensure_ascii=False, sort_keys=True) if isinstance(expr, dict) else '(not serialisable)'}\n"
                         f"predicted: the share of cases where the predicate is true should "
                         f"{pred.predicted_direction} from parent to candidate\n"
                         f"observed: parent {rate(before)}; previous attempt {rate(after)} (not as predicted)\n")
        lines.append("\nPer smoke case (s1, s2, ...): predicate values, then the parent's and the previous "
                     "attempt's diagnostic views.\n")
        items, header = [], []
        for i, task_id in enumerate(task_ids, 1):
            ref = f"s{i}"
            header.append(f"- {ref}: predicate parent={value(before.get(task_id))}, "
                          f"previous attempt={value(after.get(task_id))}\n")
            if task_id in par_views:
                items.append((ref, "parent", par_views[task_id]))
            if task_id in cand_views:
                items.append((ref, "previous attempt", cand_views[task_id]))
        head = "".join(lines) + "".join(header) + "\n"
        views, n_shown, n_omitted = render_views(items, max(0, self._analyst_view_budget - len(head)))
        stats = dict(cases=len(task_ids), views=len(items), views_shown=n_shown, views_omitted=n_omitted,
                     budget=self._analyst_view_budget)
        return head + views, stats

    def _poll_agent(self, role: str, candidate_id: Optional[str], call: dict) -> dict:
        """Call ``RoundOperations.agent`` with one key until the agent is idle."""
        self.check_runtime()
        key = call['key']
        if self._runtime_remaining is not None:
            with self._agent_requests_lock:
                self._agent_requests.setdefault(key, None)
        result = self._round_ops.agent(role, candidate_id, **call)
        self._track_agent(key, result)
        deadline = time.monotonic() + self._agent_timeout
        wait_for_running = self._wait_for_running and self._runtime_remaining is not None
        unconfirmed_deadline = None
        while result.get("status") in ("running", "unknown"):
            # Failed observations are reported as running by RoundOperations,
            # without proof of activity. Give each continuous observation gap
            # a bounded window, including gaps after long confirmed activity.
            confirmed_running = (result.get("status") == "running"
                                 and result.get("reason") != "agent_observation_unavailable")
            now = time.monotonic()
            if wait_for_running:
                if confirmed_running:
                    unconfirmed_deadline = None
                elif unconfirmed_deadline is None:
                    unconfirmed_deadline = now + self._agent_timeout
                expired = unconfirmed_deadline is not None and now >= unconfirmed_deadline
            else:
                expired = now >= deadline
            if expired:
                reason = (f"remained unconfirmed for {self._agent_timeout}s" if wait_for_running
                          else f"did not finish within {self._agent_timeout}s")
                raise TimeoutError(
                    f"{role} agent {reason} (key={call['key']!r})"
                )
            self._runtime_wait(self._agent_poll_interval)
            result = self._round_ops.agent(role, candidate_id, **call)
            self._track_agent(key, result)
        if self._runtime_remaining is not None and result.get('status') == 'idle':
            with self._agent_requests_lock:
                self._agent_requests.pop(key, None)
        if result.get("status") != "idle":
            raise RuntimeError(
                f"{role} agent ended with status {result.get('status')!r}: "
                f"{result.get('reason', 'no reason given')}"
            )
        return result

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

# Evidence budget ceiling: the evidence lives in a file, not in the v2 message.
MAX_ANALYST_VIEW_BUDGET = 60000
# Marks v2 Agents created by method v3: v2 then attaches the short v3 brief.
V3_BRIEF = "method-v3"
EVIDENCE_FORMAT = "sn-v3-analyst-evidence-v1"
REPAIR_FORMAT = "sn-v3-repair-feedback-v1"
# User decision (differs from v2, where such an attempt is unobserved and blocks the verdict).
V3_INFRASTRUCTURE_RULE = (
    "infrastructure_failure_rate = failed / total over every terminal attempt of a side on the pair's scopes; "
    "an attempt that died before publishing per-case results counts all cases its job plan scheduled as "
    "failed (v3 only; v2 treats it as unobserved)")

# v2 author of a card drafted by an analyst Agent (``sn-eval proposal draft``).
ANALYST_AUTHOR = "agent:analyst"


def _instant(stamp: str):
    from datetime import datetime, timezone
    value = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


_HISTORY_LABELS = ("smoke_fail", "screen_fail", "provisional_adopted", "confirmed",
                   "rejected", "insufficient", "unmeasurable")
# method-v4 §5 vocabulary (SNAdapterV4 renders only these).
V4_MECHANISM_TIERS = ("confirmed", "unconfirmed", "suspicious")
V4_HISTORY_LABELS = ("smoke_fail", "minibatch_fail", "below_threshold", "qualified_not_adopted",
                     "adopted", "safeguard_breached", "pruned", "run_failed", "in_progress",
                     "preflight_skipped", "implementation_failed")
_ATTRIBUTION_LABELS = ("supported", "refuted", "insufficient", "unattributed")
_HISTORY_TITLE_CHARS = 160
# §2.1 失败后的处理 details: whitelisted tokens only (never values).
_SMOKE_DETAILS = {"behaviour_changed": "smoke: behaviour changed as predicted",
                  "unchecked": "smoke: behaviour unchecked"}
_SAFEGUARD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")


def _history_line(entry: dict, vocabulary: str = "v3") -> Optional[str]:
    """``- <title> -> <label>[; smoke: ...][; breached safeguards: k1, k2][; after repair]
    [; tried in a rolled-back segment][; attribution: <label>[ (possibly biased)]]``; None if not whitelisted."""
    label = entry.get("label") if isinstance(entry, dict) else None
    if label not in (V4_HISTORY_LABELS if vocabulary == "v4" else _HISTORY_LABELS):
        return None
    title = " ".join(str(entry.get("title") or "(untitled)").split())[:_HISTORY_TITLE_CHARS]
    line = f"- {title} -> {label}"
    if label == "screen_fail" and entry.get("smoke") in _SMOKE_DETAILS:
        line += "; " + _SMOKE_DETAILS[entry["smoke"]]
    names = entry.get("safeguards_breached") if label in ("unmeasurable", "safeguard_breached") else None
    names = [n for n in names if isinstance(n, str) and _SAFEGUARD_NAME.fullmatch(n)] \
        if isinstance(names, (list, tuple)) else []
    if names:
        line += "; breached safeguards: " + ", ".join(names)
    if entry.get("repaired") is True:
        line += "; after repair"
    if vocabulary == "v4" and entry.get("mechanism") in V4_MECHANISM_TIERS:
        line += f"; mechanism: {entry['mechanism']}"
    if vocabulary == "v4":
        from .v4.agent_context import feedback_text
        feedback = feedback_text(entry)
        if feedback:
            line += "; " + feedback
    if vocabulary == "v4" and entry.get("earlier_parent") is True:
        line += "; tried on an earlier parent (that parent's change may already be in this one)"
    if entry.get("rolled_back_segment") is True:
        line += "; tried in a rolled-back segment"
    attribution = entry.get("attribution")
    if attribution in _ATTRIBUTION_LABELS:
        line += f"; attribution: {attribution}"
        if entry.get("attribution_possibly_biased"):
            line += " (possibly biased)"
    return line + "\n"


def _render_worktree_map(round_ops: Any, candidate_id: str) -> str:
    """§2.1 路 B: list the SN product worktree's file tree so the analyst can write
    implementer_brief entries that point at real product files rather than harness paths.

    Runs ``git ls-files`` in the candidate worktree; capped at 4000 chars. Returns
    "" if the worktree path is unavailable (e.g. in tests with a mock adapter that
    has no real worktree).
    """
    import subprocess
    try:
        candidate = round_ops.service.candidate(candidate_id)
        tree = (candidate.get("worktree") or {})
        wt_path = tree.get("path") if isinstance(tree, dict) else None
        if not wt_path:
            return ""
        result = subprocess.run(
            ["git", "ls-files"], cwd=wt_path,
            capture_output=True, text=True, timeout=10)
        if result.returncode != 0 or not result.stdout.strip():
            return ""
        lines = result.stdout.strip().splitlines()
        # Render top-level directory structure first, then all files.
        dirs = sorted({l.split("/")[0] for l in lines if "/" in l} |
                      {l for l in lines if "/" not in l})
        section = "## SN product worktree file tree\n"
        section += ("The implementer's worktree contains only these product files. "
                    "The implementer_brief must reference paths from this list, "
                    "not src/rag_eval/ or any evaluation-harness path.\n")
        section += "\n".join(dirs) + "\n\n"
        detail = "\n".join(lines)
        cap = 4000 - len(section)
        if len(detail) > cap:
            detail = detail[:cap] + "\n...(truncated)"
        return section + detail + "\n\n"
    except Exception:
        return ""


def _render_history(history: list[dict], budget_chars: int, vocabulary: str = "v3") -> tuple[str, int, int]:
    """Labels-only section of hypotheses already tried under this parent (§2.3).

    Returns (text, n_shown, n_omitted). Only whitelisted labels and details
    pass (``_history_line``); titles are collapsed to one line and capped.
    When the section exceeds *budget_chars* the oldest entries are dropped
    first; "" if none fit.
    """
    lines = [line for line in (_history_line(e, vocabulary) for e in history) if line is not None]
    total = len(lines)

    def section(kept: list[str]) -> str:
        note = f"[{total - len(kept)} older entr(ies) omitted]\n" if len(kept) < total else ""
        return ("Hypotheses already tried under this parent (outcome labels only, oldest first; "
                "do not re-propose these unchanged):\n" + note + "".join(kept) + "\n") if vocabulary != "v4" else (
                "Hypotheses already tried in this run, on this parent and on earlier ones (outcome labels only, "
                "oldest first). Do not duplicate adopted or in-progress changes. Refuted hypotheses need new evidence; "
                "implementation/run failures and inconclusive checks do not refute a direction. "
                "A qualified_not_adopted card may be reconsidered on this parent:\n" + note + "".join(kept) + "\n")

    while lines and len(section(lines)) > budget_chars:
        lines.pop(0)   # oldest first
    if not lines:
        return "", 0, total
    return section(lines), len(lines), total - len(lines)


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


def _extract_frozen_diff(candidate: dict) -> str:
    """Recover the exact parent-to-candidate change; mutable worktree edits stay out."""
    path = (candidate.get("worktree") or {}).get("path")
    parent, commit = candidate.get("parent_commit"), candidate.get("frozen_commit")
    if not path or not parent or not commit:
        raise EvaluationUnavailable("frozen candidate diff provenance is unavailable")
    try:
        result = subprocess.run(["git", "-C", str(path), "diff", "--binary", parent, commit],
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvaluationUnavailable("frozen candidate diff could not be recovered") from exc
    if result.returncode:
        raise EvaluationUnavailable("frozen candidate diff could not be recovered")
    return result.stdout


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
