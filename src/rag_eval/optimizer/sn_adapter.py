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
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from .adapter import Card, Diff, EvalResult, Predicate, SystemAdapter, Version


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
        ``RunViews`` (``rag_eval.workbench.run_views``).
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
        actor                 — the v2 actor recorded on approvals/derives/freezes
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
    # 2. evaluate
    # ------------------------------------------------------------------

    def evaluate(
        self,
        version: Version,
        task_ids: list[str],
        k: int,
        *,
        reference_version: Optional[Version] = None,
    ) -> EvalResult:
        """Collect k admitted run-batches for *version* from the v2 run_views.

        Precondition: evaluation has already been dispatched through the v2
        server (EvaluationJobStore / JobExecutionService).  This method is a
        *reader* — it does not trigger new executions; that remains a v2
        server-side concern.

        Per-task score: mean raw ``value`` across all case rows in k runs.
        Trajectories: list of k dicts, each the run's manifest (run_dir,
        case_ids, scores path, execution_id) — kept lightweight so callers
        that only care about scores don't pay for full output loading.

        NOTE: ``reference_version`` is accepted for API compatibility but the
        pairing is not currently implemented; reference runs are ignored.  Add
        interleaved pairing here when the v2 job dispatch is wired end-to-end.
        """
        if self._run_views is None:
            raise RuntimeError("run_views service required for evaluate()")

        execution_id = (version.metadata or {}).get("execution_id")
        if not execution_id:
            raise ValueError(
                "Version.metadata must contain 'execution_id'; "
                "run v2 evaluation first and store the id."
            )

        runs = self._run_views.execution_runs(execution_id)
        if not runs:
            raise RuntimeError(
                f"No admitted runs found for execution_id={execution_id!r}"
            )

        # Restrict to the requested task_ids when run_views supports it; many
        # v2 run sets span a single task so the filter is usually a no-op.
        # NOTE: v2 runs carry .case_ids (list); filter by overlap with task_ids.
        task_id_set = set(task_ids)

        per_task_scores: dict[str, float] = {}
        trajectories: dict[str, list[Any]] = {}
        costs: dict[str, float] = {}
        error_classes: dict[str, Optional[str]] = {}

        # k most-recent runs (v2 returns them in creation order).
        selected_runs = runs[-k:] if len(runs) >= k else runs

        for run in selected_runs:
            run_case_ids = getattr(run, "case_ids", [])
            relevant = [c for c in run_case_ids if not task_id_set or c in task_id_set]
            run_scores = _load_scores(run)
            traj_entry = {
                "run_id": run.id,
                "run_dir": str(getattr(run, "run_dir", "")),
                "execution_id": execution_id,
                "case_ids": relevant,
            }
            for case_id in relevant:
                trajectories.setdefault(case_id, []).append(traj_entry)
                score_val = (run_scores or {}).get(case_id)
                if score_val is not None:
                    per_task_scores.setdefault(case_id, []).append(float(score_val))  # type: ignore[assignment]

        # Average accumulated lists.
        per_task_scores = {
            tid: sum(vals) / len(vals)
            for tid, vals in per_task_scores.items()  # type: ignore[assignment]
            if vals
        }

        # Cost: token-based proxy where available, else 0.
        for task_id in task_ids:
            costs[task_id] = 0.0
            error_classes[task_id] = None

        return EvalResult(
            per_task_scores=per_task_scores,
            trajectories=trajectories,
            costs=costs,
            error_classes=error_classes,
        )

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

def _load_scores(run) -> dict[str, float] | None:
    """Load per-case raw scores from a v2 run record.

    v2 run objects carry a ``.scores`` path attribute pointing to a JSON file
    with a ``{case_id: {score: float, ...}, ...}`` structure.  Returns None
    when the file is absent or malformed.
    """
    scores_path = getattr(run, "scores", None)
    if not scores_path:
        return None
    try:
        raw = json.loads(Path(scores_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    result = {}
    for case_id, entry in raw.items():
        if isinstance(entry, (int, float)):
            result[case_id] = float(entry)
        elif isinstance(entry, dict):
            val = entry.get("score")
            if val is not None:
                result[case_id] = float(val)
    return result
