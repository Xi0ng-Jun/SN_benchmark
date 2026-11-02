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
import uuid
from pathlib import Path
from typing import Any, Optional

from .adapter import Card, Diff, EvalResult, Predicate, SystemAdapter, Version


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
    ) -> None:
        self._config = config
        self._repository = repository
        self._run_views = run_views
        self._iterations = iterations
        self._hypothesis_worktrees = hypothesis_worktrees

        # Workspace root for materialised versions (configurable, defaults to
        # the v2 workspace.location so both v2 and v3 share the same disk tree).
        workspace_cfg = config.get("workspace", {})
        self._workspace_root = Path(
            workspace_cfg.get("location", tempfile.gettempdir())
        )

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
    ) -> dict[str, bool]:
        """Evaluate *predicate* against each task's trajectory list.

        The predicate's ``boolean_expression`` may be a Python callable or any
        serialisable spec.  Callables are invoked with the trajectory list for
        that task.  All exceptions are caught and map to ``False``.
        """
        if predicate is None:
            return {}

        expr = predicate.boolean_expression
        result: dict[str, bool] = {}
        for task_id, traj_list in trajectories.items():
            try:
                if callable(expr):
                    value = expr(traj_list)
                else:
                    # Serialisable spec: evaluate as a compiled expression in a
                    # restricted namespace that exposes only the trajectory list.
                    code = compile(str(expr), "<predicate>", "eval")
                    value = eval(code, {"__builtins__": {}}, {"trajectories": traj_list})  # noqa: S307
                result[task_id] = bool(value)
            except Exception:
                result[task_id] = False
        return result

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
    # 5. analyse (stub — LLM call)
    # ------------------------------------------------------------------

    def analyse(
        self,
        search_set_trajectories: dict[str, list[Any]],
        n_cards: int,
    ) -> list[Card]:
        """Stub — return empty list until the LLM bridge is wired.

        TODO: delegate to AgentBridgeAdapter (rag_eval.workbench.bridge_adapter)
        for the listProviders / readAgent / readTimeline round-trip, then parse
        the analyst agent's output into Card objects.  See bridge_adapter.py's
        AgentBridgeAdapter.call() for the envelope format.
        """
        return []

    # ------------------------------------------------------------------
    # 6. implement (stub — LLM call)
    # ------------------------------------------------------------------

    def implement(self, card: Card, workspace: Any) -> Diff:
        """Stub — not yet implemented.

        TODO: delegate to the implementer agent via AgentBridgeAdapter, passing
        workspace binding as the worktree context.  Return a Diff whose content
        is the unified-diff output from the agent session.
        """
        raise NotImplementedError(
            "implement() requires the LLM bridge; "
            "see rag_eval.workbench.bridge_adapter.AgentBridgeAdapter"
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

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
