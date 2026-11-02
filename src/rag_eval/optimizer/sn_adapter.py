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
import shutil
import subprocess
import tempfile
import time
import uuid
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
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dir_hash(directory: Path) -> str:
    """Stable sha256 over sorted (relative-posix-path, file-bytes) pairs."""
    h = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            h.update(path.relative_to(directory).as_posix().encode())
            h.update(b"\x00")
            h.update(path.read_bytes())
            h.update(b"\x00")
    return h.hexdigest()


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
    hypothesis_worktrees:
        ``HypothesisWorktreeService`` (``rag_eval.workbench.hypothesis_worktrees``).
        Optional; ``workspace()`` raises ``RuntimeError`` when absent.

    Version.metadata contract (round-tripped through all calls):
        content_hash : str   — sha256 of the materialised workspace tree
        task_id      : str   — SN task the version belongs to
        execution_id : str   — v2 execution_id of the latest admitted run batch
    """

    def __init__(
        self,
        config: dict,
        *,
        repository=None,
        run_views=None,
        iterations=None,
        hypothesis_worktrees=None,
        round_operations=None,
        hypotheses=None,
        analyst_profile: str = "pi-flash",
        implementer_profile: str = "pi-flash",
        agent_poll_interval: float = 5.0,
        agent_timeout: float = 600.0,
        analyst_view_budget: int = 12000,
    ) -> None:
        self._config = config
        self._repository = repository
        self._run_views = run_views
        self._iterations = iterations
        self._hypothesis_worktrees = hypothesis_worktrees
        # Services required for agent dispatch (analyse + implement).
        self._round_ops = round_operations
        self._hypotheses = hypotheses
        self._analyst_profile = analyst_profile
        self._implementer_profile = implementer_profile
        self._agent_poll_interval = agent_poll_interval
        self._agent_timeout = agent_timeout
        # Message text is capped at 16384 chars by the v2 message bridge; the
        # instructions take ~1K, so diagnostic views get the rest minus headroom.
        if not 0 < analyst_view_budget <= 15000:
            raise ValueError("analyst_view_budget must be in (0, 15000] characters")
        self._analyst_view_budget = analyst_view_budget
        self.last_analysis: dict = {}

        workspace_cfg = config.get("workspace", {})
        self._workspace_root = Path(
            workspace_cfg.get("location", tempfile.gettempdir())
        )
        # Trajectory cache: (run_dir_str, case_id) → DiagnosticTrajectory dict.
        # Bounded to the most recent _TRAJ_CACHE_SIZE entries to cap memory use.
        self._traj_cache: dict[tuple[str, str], dict] = {}
        self._TRAJ_CACHE_SIZE = 2000

    # ------------------------------------------------------------------
    # 1. materialize
    # ------------------------------------------------------------------

    def materialize(self, diff: Diff, parent: Version) -> Version:
        """Apply *diff* on top of *parent*; return a new frozen Version.

        Strategy: copy the parent snapshot directory, write diff.content to a
        temp file, apply with ``patch -p1``, hash the result tree.  The patched
        tree is kept under ``{workspace_root}/v3-snapshots/{new_hash[:16]}/``.

        If *diff.content* is empty the parent's tree is re-used as-is (identity
        materialisation, useful for baseline seeding).
        """
        parent_dir = self._snapshot_dir(parent)
        if not parent_dir.exists():
            raise FileNotFoundError(
                f"Parent snapshot directory not found: {parent_dir}"
            )

        # Work in a temporary directory so failures leave nothing behind.
        tmp = Path(tempfile.mkdtemp(prefix="sn_adapter_materialize_"))
        try:
            dest = tmp / "tree"
            shutil.copytree(parent_dir, dest)

            if diff.content.strip():
                patch_file = tmp / "candidate.patch"
                patch_file.write_text(diff.content, encoding="utf-8")
                result = subprocess.run(
                    ["patch", "-p1", "--input", str(patch_file)],
                    cwd=str(dest),
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    raise RuntimeError(
                        f"patch failed (exit {result.returncode}):\n"
                        + result.stderr
                    )

            content_hash = _dir_hash(dest)
            final_dir = self._workspace_root / "v3-snapshots" / content_hash[:16]
            if not final_dir.exists():
                shutil.copytree(dest, final_dir)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        meta = dict(parent.metadata)
        meta["content_hash"] = content_hash
        # Carry forward task_id; execution_id will be populated by evaluate().
        meta.setdefault("task_id", "")
        meta.pop("execution_id", None)
        return Version(content_hash=content_hash, metadata=meta)

    def _snapshot_dir(self, version: Version) -> Path:
        """Resolve the local directory for a materialised version."""
        # Callers may supply an explicit path in metadata; fall back to the
        # conventional location under workspace_root.
        explicit = (version.metadata or {}).get("snapshot_dir")
        if explicit:
            return Path(explicit)
        return self._workspace_root / "v3-snapshots" / version.content_hash[:16]

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
    # 4. workspace
    # ------------------------------------------------------------------

    def workspace(self, version: Version) -> Any:
        """Return the HypothesisWorktree binding for an implementer.

        Delegates to ``HypothesisWorktreeService.get(task_id, hypothesis_id)``
        from ``rag_eval.workbench.hypothesis_worktrees``.  The hypothesis_id is
        taken from ``version.metadata['hypothesis_id']`` when present, otherwise
        a new uuid is used (the worktree will need to be created separately).

        NOTE: hypothesis_worktrees.create() / .activate() are asynchronous in
        v2 (they shell out to Paseo CLI).  If the worktree does not yet exist,
        callers must create it through the v2 server before calling workspace().
        """
        if self._hypothesis_worktrees is None:
            raise RuntimeError("hypothesis_worktrees service required for workspace()")

        task_id = (version.metadata or {}).get("task_id", "")
        hypothesis_id = (version.metadata or {}).get("hypothesis_id", str(uuid.uuid4()))

        binding = self._hypothesis_worktrees.get(task_id, hypothesis_id)
        return binding

    # ------------------------------------------------------------------
    # 5. analyse — dispatch analyst agent, poll until idle, read cards
    # ------------------------------------------------------------------

    def analyse(
        self,
        search_set_trajectories: dict[str, list[Any]],
        n_cards: int,
        *,
        case_roles: Optional[dict[str, str]] = None,
    ) -> list[Card]:
        """Dispatch an analyst agent on accountant-selected search cases; return its new Cards.

        *search_set_trajectories* holds only the cases the loop selected (§2.3:
        selection is the accountant's, by a pre-registered rule). Each case is
        rendered as a diagnostic view under opaque refs (c1, c2, ...) within
        ``analyst_view_budget`` characters, so the Agent needs no tool calls
        to gather evidence. Cards are bound to this request: only hypotheses
        created after dispatch (and, if configured, under the current parent
        candidate) are returned.
        """
        if self._round_ops is None or self._hypotheses is None:
            raise RuntimeError(
                "analyse() requires round_operations and hypotheses services; "
                "inject them via SNAdapter.__init__ keyword arguments."
            )
        iteration_id = self._config.get("iteration_id")
        task_id = self._config.get("task_id")
        if not iteration_id or not task_id:
            raise ValueError(
                "config must contain 'iteration_id' and 'task_id' to dispatch agents."
            )
        from .sn_trajectory import render_views
        roles = case_roles or {}
        views = self.diagnostic_views(search_set_trajectories)
        ordered = sorted(views, key=lambda t: (roles.get(t, "failure") != "failure",
                                               list(search_set_trajectories).index(t)))
        items = [(f"c{i + 1}", roles.get(t, "failure"), views[t]) for i, t in enumerate(ordered)]
        evidence, n_shown, n_omitted = render_views(items, self._analyst_view_budget)
        self.last_analysis = dict(selected=len(search_set_trajectories), with_view=len(views),
                                  shown=n_shown, omitted=n_omitted, evidence_chars=len(evidence))
        analyst_text = (
            f"Diagnose the parent version's failures on the search cases below and draft up to "
            f"{n_cards} legal lcd-v2 mechanism cards, one per distinct failure mode. Each card needs "
            f"title, component, mechanism, implementer_brief, falsification, predictions (may be []), "
            f"and a contract with slice_predicate, manipulation and slice_effect. A slice_predicate "
            f"may read behaviour fields and trace.* step features (e.g. trace.n_empty_retrievals, "
            f"trace.clarification_requested). Cases marked [failure] were selected by a fixed rule; "
            f"[control] cases are for contrast. Do not request more evidence; draft the cards now.\n\n"
            + evidence
        )
        before = {r["id"] for r in self._hypotheses.list(task_id)}
        analyst_key = f"v3/analyst/{uuid.uuid4().hex}"
        call = dict(iteration_id=iteration_id, key=analyst_key, profile=self._analyst_profile,
                    text=analyst_text, scope="iteration")
        result = self._round_ops.agent("analyst", None, **call)
        deadline = time.monotonic() + self._agent_timeout
        while result.get("status") in ("running", "unknown"):
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Analyst agent did not finish within {self._agent_timeout}s "
                    f"(key={analyst_key!r})"
                )
            time.sleep(self._agent_poll_interval)
            result = self._round_ops.agent("analyst", None, **call)
        if result.get("status") != "idle":
            raise RuntimeError(
                f"Analyst agent ended with status {result.get('status')!r}: "
                f"{result.get('reason', 'no reason given')}"
            )
        parent = self._config.get("parent_candidate_id")
        fresh = [r for r in self._hypotheses.list(task_id)
                 if r["id"] not in before and r.get("status") in ("draft", "approved")
                 and (parent is None or r.get("parent_candidate_id") in (None, parent))]
        return [_v2_card_to_v3_card(r) for r in fresh[:n_cards]]

    # ------------------------------------------------------------------
    # 6. implement — dispatch implementer agent, poll, extract diff
    # ------------------------------------------------------------------

    def implement(self, card: Card, workspace: Any) -> Diff:
        """Dispatch an implementer agent, poll until idle, extract the resulting diff.

        *workspace* is the binding returned by self.workspace() — a dict with
        a 'hypothesis_id' key from HypothesisWorktreeService.

        The implementer is created against the hypothesis for this card.  Once
        idle we read the worktree diff with ``git diff HEAD`` against the parent
        snapshot to obtain the unified-diff string.

        Requires self._round_ops to be injected.
        """
        if self._round_ops is None:
            raise RuntimeError(
                "implement() requires round_operations service; "
                "inject via SNAdapter.__init__ keyword arguments."
            )
        iteration_id = self._config.get("iteration_id")
        task_id = self._config.get("task_id")
        if not iteration_id or not task_id:
            raise ValueError(
                "config must contain 'iteration_id' and 'task_id' to dispatch agents."
            )
        # workspace is from HypothesisWorktreeService.get() — dict with hypothesis_id.
        hypothesis_id: Optional[str] = None
        if isinstance(workspace, dict):
            hypothesis_id = workspace.get("hypothesis_id") or workspace.get("binding", {}) and workspace["binding"].get("hypothesis_id")
        if not hypothesis_id:
            raise ValueError(
                f"workspace does not contain a hypothesis_id: {workspace!r}"
            )
        implementer_key = f"v3/implementer/{hypothesis_id}"
        implementer_text = (
            f"Implement the approved card for candidate (hypothesis {hypothesis_id}). "
            f"Required order: implementation → relevant SN tests → node freeze → "
            f"node run. Use only approved implementer material."
        )
        result = self._round_ops.agent(
            "implementer",
            None,
            iteration_id=iteration_id,
            key=implementer_key,
            profile=self._implementer_profile,
            text=implementer_text,
            hypothesis_id=hypothesis_id,
        )
        # Poll until idle/done.
        deadline = time.monotonic() + self._agent_timeout
        while result.get("status") in ("running", "unknown"):
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Implementer agent did not finish within {self._agent_timeout}s "
                    f"(hypothesis={hypothesis_id!r})"
                )
            time.sleep(self._agent_poll_interval)
            result = self._round_ops.agent(
                "implementer",
                None,
                iteration_id=iteration_id,
                key=implementer_key,
                profile=self._implementer_profile,
                text=implementer_text,
                hypothesis_id=hypothesis_id,
            )
        if result.get("status") != "idle":
            raise RuntimeError(
                f"Implementer agent ended with status {result.get('status')!r}: "
                f"{result.get('reason', 'no reason given')}"
            )
        # Extract diff from the worktree.
        diff_content = _extract_worktree_diff(workspace)
        return Diff(
            content=diff_content,
            test_refs=(),
            metadata={"hypothesis_id": hypothesis_id},
        )


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


def _extract_worktree_diff(workspace: Any) -> str:
    """Extract the uncommitted diff from a v2 hypothesis worktree binding.

    *workspace* is the dict returned by HypothesisWorktreeService.get():
        {
            "binding": {
                "path": "/abs/path/to/worktree",
                "branch": "...",
                "base_revision": "<sha>",
                ...
            },
            ...
        }

    Runs ``git diff HEAD`` in the worktree directory.  Returns the unified-diff
    string, or an empty string if the worktree is clean or path unavailable.
    """
    if not isinstance(workspace, dict):
        return ""
    binding = workspace.get("binding") or {}
    worktree_path = binding.get("path") or (workspace.get("path") if isinstance(workspace, dict) else None)
    if not worktree_path:
        return ""
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=str(worktree_path),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout
        return ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


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
