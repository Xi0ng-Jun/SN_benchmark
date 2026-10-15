"""Small, append-only run ledger for agent-facing evaluation bookkeeping.

The ledger is deliberately a projection of files already written by a run.  It
does not load the bundle or gold data, and it never invents values for missing
state, scores, or stage metrics.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any, Iterable

from .artifacts import jsonl_line


FIELDS = (
    "run_dir",
    "suite",
    "bundle_id",
    "partition_id",
    "case_ids_count",
    "mode",
    "sn_source_ref",
    "service_config_sha256",
    "runtime_settings_sha256",
    "state",
    "exit_code",
    "stderr_summary",
    "official_score",
    "official_scores_path",
    "task_worktree",
    "stage_metrics",
    "created_at",
)


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON: {path}") from exc


def _first(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _identity(manifest: dict[str, Any]) -> dict[str, Any]:
    value = manifest.get("identity", {})
    return value if isinstance(value, dict) else {}


def _bundle_id(manifest: dict[str, Any], refs: dict[str, Any]) -> Any:
    identity = _identity(manifest)
    product = identity.get("product_bundle") or {}
    if not isinstance(product, dict):
        product = {}
    return _first(
        product.get("bundle_id"),
        product.get("source_manifest_fingerprint"),
        identity.get("bundle_id"),
        identity.get("source_manifest_fingerprint"),
        manifest.get("bundle_id"),
        ((refs.get("bundle") or {}).get("id") if isinstance(refs.get("bundle"), dict) else None),
    )


def _partition_id(manifest: dict[str, Any]) -> Any:
    identity = _identity(manifest)
    context = identity.get("notebook_context") or {}
    product = identity.get("product_bundle") or {}
    return _first(
        context.get("partition_id") if isinstance(context, dict) else None,
        product.get("partition_id") if isinstance(product, dict) else None,
        identity.get("partition_id"),
        manifest.get("partition_id"),
    )


def _case_count(run: Path, manifest: dict[str, Any]) -> int | None:
    identity = _identity(manifest)
    context = identity.get("notebook_context") or {}
    case_ids = context.get("case_ids") if isinstance(context, dict) else None
    if isinstance(case_ids, list):
        return len(case_ids)
    planned = manifest.get("planned_predictions")
    if isinstance(planned, int) and not isinstance(planned, bool):
        return planned
    planned_path = run / "planned.jsonl"
    if not planned_path.exists():
        return None
    ids = set()
    try:
        for line in planned_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if isinstance(row, dict) and row.get("case_id") is not None:
                    ids.add(row["case_id"])
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid planned ledger: {planned_path}") from exc
    return len(ids)


def _score_value(path: Path | None) -> Any:
    if path is None:
        return None
    source = path / "scores.json" if path.is_dir() else path
    data = _json(source)
    if data is None:
        return None
    if isinstance(data, dict):
        if "official_score" in data:
            return data["official_score"]
        if "metrics" in data:
            return data["metrics"]
        if "score" in data:
            return data["score"]
    return data


def _stage_value(path: Path | None) -> Any:
    if path is None:
        return None
    source = path / "stage-metrics.json" if path.is_dir() else path
    data = _json(source)
    if isinstance(data, dict) and "stage_metrics" in data:
        return data["stage_metrics"]
    if isinstance(data, dict) and "summary" in data:
        return data["summary"]
    return data


def _created_at(run: Path, manifest: dict[str, Any], state: dict[str, Any]) -> str:
    identity = _identity(manifest)
    value = _first(
        manifest.get("created_at"),
        identity.get("created_at"),
        state.get("created_at"),
        state.get("started_at"),
    )
    if isinstance(value, str) and value:
        return value
    return datetime.fromtimestamp(run.stat().st_mtime, tz=timezone.utc).isoformat()


def derive_run(run_dir: str | Path, *, scores_path: str | Path | None = None,
               stage_metrics_path: str | Path | None = None,
               exit_code: int | None = None, stderr_summary: str | None = None) -> dict[str, Any]:
    """Derive one ledger row from a run directory without reading benchmark gold."""
    if exit_code is not None and type(exit_code) is not int:
        raise ValueError('exit_code must be an integer or null')
    if stderr_summary is not None:
        if not isinstance(stderr_summary, str):
            raise ValueError('stderr_summary must be text or null')
        stderr_summary = re.sub(r'(?i)\b(?:https?|wss?)://\S+', '[redacted-address]', stderr_summary)
        stderr_summary = re.sub(
            r'(?im)\b(?:authorization|api[_-]?key|token|password|secret|credential)\b[^\n]*',
            '[redacted-credential]', stderr_summary)[-4096:]
    run = Path(run_dir).expanduser().resolve()
    if not run.is_dir():
        raise ValueError(f"run directory does not exist: {run}")
    manifest = _json(run / "manifest.json")
    if not isinstance(manifest, dict):
        raise ValueError(f"run manifest missing or invalid: {run / 'manifest.json'}")
    state = _json(run / "state.json")
    if state is None:
        state = {}
    if not isinstance(state, dict):
        raise ValueError(f"run state must be an object: {run / 'state.json'}")
    refs = _json(run / "artifact-refs.json") or {}
    if not isinstance(refs, dict):
        refs = {}
    if scores_path is None and (run / "scores.json").is_file():
        scores_path = run / "scores.json"
    if stage_metrics_path is None and (run / "stage-metrics.json").is_file():
        stage_metrics_path = run / "stage-metrics.json"
    identity = _identity(manifest)
    runtime = _json(run / "runtime-identity.json") or {}
    if not isinstance(runtime, dict):
        runtime = {}
    sources = refs.get("sources") or {}
    sn_ref = sources.get("sn") if isinstance(sources, dict) else None
    sn_ref = sn_ref.get("id") if isinstance(sn_ref, dict) else sn_ref
    row = {
        "run_dir": str(run),
        "suite": manifest.get("suite"),
        "bundle_id": _bundle_id(manifest, refs),
        "partition_id": _partition_id(manifest),
        "case_ids_count": _case_count(run, manifest),
        "mode": manifest.get("mode"),
        "sn_source_ref": _first(
            sn_ref,
            identity.get("sn_source_ref"),
            (identity.get("code") or {}).get("source_manifest_sha256")
            if isinstance(identity.get("code"), dict) else None,
        ),
        "service_config_sha256": _first(
            identity.get("product_services"), runtime.get("service_config_sha256")),
        "runtime_settings_sha256": _first(
            identity.get("runtime_settings"),
            runtime.get("comparable_settings_sha256"),
            runtime.get("settings_sha256")),
        "state": state.get("phase", "unknown"),
        "exit_code": exit_code,
        "stderr_summary": stderr_summary,
        "official_score": _score_value(Path(scores_path).expanduser() if scores_path else None),
        "official_scores_path": str(Path(scores_path).expanduser().resolve()) if scores_path else None,
        "task_worktree": identity.get('task_worktree'),
        "stage_metrics": _stage_value(Path(stage_metrics_path).expanduser()
                                      if stage_metrics_path else None),
        "created_at": _created_at(run, manifest, state),
    }
    return {field: row[field] for field in FIELDS}


def _read_rows(ledger: str | Path) -> list[dict[str, Any]]:
    path = Path(ledger).expanduser()
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"unable to read ledger: {path}") from exc
    return _parse_rows(path, text)


def _parse_rows(path: Path, text: str, first_line: int = 1) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), first_line):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid ledger JSON at {path}:{line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"ledger row is not an object at {path}:{line_number}")
        rows.append(row)
    return rows


def append_run(ledger: str | Path, run_dir: str | Path, *,
               scores_path: str | Path | None = None,
               stage_metrics_path: str | Path | None = None,
               exit_code: int | None = None, stderr_summary: str | None = None) -> dict[str, Any]:
    """Append a derived run, refusing to reuse an existing run directory."""
    row = derive_run(run_dir, scores_path=scores_path, stage_metrics_path=stage_metrics_path,
                     exit_code=exit_code, stderr_summary=stderr_summary)
    path = Path(ledger).expanduser()
    existing = _read_rows(path)
    if any(item.get("run_dir") == row["run_dir"] for item in existing):
        raise ValueError(f"duplicate run_dir: {row['run_dir']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(jsonl_line(row) + "\n")
        handle.flush()
    return row


class LedgerAppender:
    """append_run for one long-lived writer, e.g. a batch coordinator.

    append_run re-parses the whole ledger per call, which is quadratic over a
    batch. This keeps the parsed run_dir set and reads only bytes appended since
    the last call, so other appenders are still seen and every line is still
    validated once. A shrunk or replaced file is re-read in full. It assumes the
    documented append-only use; in-place rewrites of earlier rows are not
    re-validated.
    """

    def __init__(self, ledger: str | Path):
        self.path = Path(ledger).expanduser()
        self._identity: tuple[int, int] | None = None
        self._offset = 0
        self._lines = 0
        self._run_dirs: set[Any] = set()

    def _refresh(self) -> None:
        try:
            info = self.path.stat()
        except FileNotFoundError:
            self._identity, self._offset, self._lines, self._run_dirs = None, 0, 0, set()
            return
        identity = (info.st_dev, info.st_ino)
        if identity != self._identity or info.st_size < self._offset:
            self._identity, self._offset, self._lines, self._run_dirs = identity, 0, 0, set()
        if info.st_size == self._offset:
            return
        try:
            with self.path.open("rb") as handle:
                handle.seek(self._offset)
                data = handle.read()
        except OSError as exc:
            raise ValueError(f"unable to read ledger: {self.path}") from exc
        try:
            text = data.decode("utf-8")
        except UnicodeError as exc:
            raise ValueError(f"unable to read ledger: {self.path}") from exc
        # As in _read_rows, an incomplete tail line is rejected, not skipped.
        rows = _parse_rows(self.path, text, self._lines + 1)
        self._run_dirs.update(item.get("run_dir") for item in rows)
        self._lines += len(text.splitlines())
        self._offset += len(data)

    def append(self, run_dir: str | Path, *, scores_path: str | Path | None = None,
               stage_metrics_path: str | Path | None = None,
               exit_code: int | None = None, stderr_summary: str | None = None) -> dict[str, Any]:
        row = derive_run(run_dir, scores_path=scores_path, stage_metrics_path=stage_metrics_path,
                         exit_code=exit_code, stderr_summary=stderr_summary)
        self._refresh()
        if row["run_dir"] in self._run_dirs:
            raise ValueError(f"duplicate run_dir: {row['run_dir']}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(jsonl_line(row) + "\n")
            handle.flush()
        return row


def query_runs(ledger: str | Path, *, suite: str | None = None,
               mode: str | None = None, state: str | None = None) -> list[dict[str, Any]]:
    """Return ledger rows matching all supplied filters, preserving file order."""
    rows = _read_rows(ledger)
    for row in rows:
        row.setdefault('exit_code', None)
        row.setdefault('stderr_summary', None)
        row.setdefault('official_scores_path', None)
        row.setdefault('task_worktree', None)
    return [row for row in rows
            if (suite is None or row.get("suite") == suite)
            and (mode is None or row.get("mode") == mode)
            and (state is None or row.get("state") == state)]


def render_table(rows: Iterable[dict[str, Any]]) -> str:
    rows = list(rows)
    columns = ("run_dir", "suite", "partition_id", "mode", "state", "official_score", "created_at")
    values = [[str(row.get(column, "")) for column in columns] for row in rows]
    widths = [max([len(column), *(len(row[index]) for row in values)])
              if values else len(column) for index, column in enumerate(columns)]
    header = "  ".join(column.ljust(widths[index]) for index, column in enumerate(columns))
    divider = "  ".join("-" * width for width in widths)
    body = ["  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
            for row in values]
    return "\n".join([header, divider, *body])


# Short wrappers make the module convenient for an agent or a tiny Python call.
def append(ledger: str | Path, run_dir: str | Path, scores: str | Path | None = None, *,
           scores_path: str | Path | None = None,
           stage_metrics_path: str | Path | None = None,
           exit_code: int | None = None, stderr_summary: str | None = None) -> dict[str, Any]:
    if scores is not None and scores_path is not None:
        raise TypeError("pass either scores or scores_path")
    return append_run(ledger, run_dir, scores_path=scores_path or scores,
                      stage_metrics_path=stage_metrics_path, exit_code=exit_code,
                      stderr_summary=stderr_summary)


query = query_runs
