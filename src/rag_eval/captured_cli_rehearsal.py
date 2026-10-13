"""Minimal offline E5 captured-CLI rehearsal.

The rehearsal validates already captured inputs, observes one standard-library
child through the reviewed R1 owner, and writes evidence that is deliberately
separate from official scoring.  It never imports Silicon Notebook or reads a
host project configuration.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Mapping

from .artifacts import save_json, save_jsonl
from .bundle_index import find_installed_bundle, load_partition
from .runtime_dependency_identity import parse_dependency_requirements


DEFAULT_OWNER_DRIVER = (
    Path(__file__).resolve().parents[2]
    / "apps/sn-workbench/test-results/source-execution-20261012/e5-owner-fix-r1/owner_driver.py"
)
_FORBIDDEN_PROJECT = Path("/home/wabiwabi/silicon-notebook/project").resolve()
_SECRETISH = re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*=|https?://")


def _path(value, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return path.resolve(strict=False)


def _fresh_path(path: Path, label: str) -> None:
    if path.exists():
        raise ValueError(f"{label} must be fresh and nonexistent")


def _non_nested(left: Path, right: Path) -> None:
    if left == right or left.is_relative_to(right) or right.is_relative_to(left):
        raise ValueError("owner evidence and CLI run directories must be non-nested")


def _load_owner_driver(path: str | Path | None):
    path = _path(path or DEFAULT_OWNER_DRIVER, "owner driver")
    if not path.is_file():
        raise ValueError(f"owner driver is missing: {path}")
    spec = importlib.util.spec_from_file_location("e5_r1_owner_driver", path)
    if spec is None or spec.loader is None:
        raise ValueError("unable to load owner driver")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "run_owned_command"):
        raise ValueError("owner driver has no run_owned_command")
    return module


def _unknown_receipt(reason: str) -> dict:
    return {
        "spawn": {"started": False, "failure": reason, "pidfd_error": {"error": reason}},
        "exit": {"confirmed": False, "status": "unknown", "returncode": None, "termination_signal": None},
        "outputs": {"drain_complete": False, "drain_status": "not_started",
                    "stdout_observed_prefix_bytes": 0, "stderr_observed_prefix_bytes": 0},
        "network_guard": {"requested": True, "available": False, "mechanism": None, "failure": reason},
        "residuals": {"completeness": "unknown", "clean_claimable": False},
    }


def _validate_project(project: Path) -> None:
    if project == _FORBIDDEN_PROJECT:
        raise ValueError("synthetic project must not be the real Silicon Notebook project")
    if not project.is_dir():
        raise ValueError("synthetic project directory is missing")
    catalog = project / ".local/model-services.toml"
    if not catalog.is_file():
        raise ValueError("synthetic project requires credential-free .local/model-services.toml")
    if _SECRETISH.search(catalog.read_text(encoding="utf-8")):
        raise ValueError("synthetic model catalog contains credential or service-address material")
    dotenv = project / ".env"
    if dotenv.exists() and (not dotenv.is_file() or _SECRETISH.search(dotenv.read_text(encoding="utf-8"))):
        raise ValueError("synthetic .env must be absent or credential-free")


def _validate_refs(*, bundle_dir: Path, artifact_root: Path, artifact_index_id: str,
                   partition_id: str, case_id: str, sn_source_ref: Mapping,
                   dependency_requirements, expected_interpreter, runtime_refs: Mapping):
    if not isinstance(artifact_index_id, str) or not artifact_index_id:
        raise ValueError("artifact_index_id ref is required")
    refs = find_installed_bundle(bundle_dir, artifact_root, expected_index_id=artifact_index_id)
    if not isinstance(sn_source_ref, Mapping) or sn_source_ref.get("kind") != "sn":
        raise ValueError("sn_source_ref is required and must be an sn artifact ref")
    from .artifact_store import ArtifactStore
    ArtifactStore(artifact_root).resolve(dict(sn_source_ref))
    capsule = load_partition(artifact_root, refs, partition_id, "notebook-request-v3")
    if capsule.get("manifest", {}).get("suite") != "qasper":
        raise ValueError("rehearsal requires a QASPER partition")
    if case_id not in {case.get("case_id") for case in capsule.get("cases", [])}:
        raise ValueError("QASPER case is not in the pinned partition")
    if not isinstance(runtime_refs, Mapping) or not runtime_refs:
        raise ValueError("runtime_refs are required")
    if dependency_requirements is None or expected_interpreter is None:
        raise ValueError("captured dependency requirements and interpreter expectation are required")
    requirements = _path(dependency_requirements, "dependency requirements")
    if not requirements.is_file():
        raise ValueError("dependency requirements file is missing")
    requirement_bytes = requirements.read_bytes()
    parse_dependency_requirements(requirement_bytes)
    requirement_sha256 = hashlib.sha256(requirement_bytes).hexdigest()
    declared_sha256 = runtime_refs.get("requirements_sha256")
    if declared_sha256 is not None and declared_sha256 != requirement_sha256:
        raise ValueError("runtime requirements reference hash changed")
    if (not isinstance(expected_interpreter, Mapping)
            or set(expected_interpreter) != {"executable", "prefix"}
            or not Path(expected_interpreter["executable"]).is_absolute()
            or not Path(expected_interpreter["prefix"]).is_absolute()):
        raise ValueError("invalid expected interpreter reference")
    declared_interpreter = runtime_refs.get("interpreter")
    if declared_interpreter is not None and dict(declared_interpreter) != dict(expected_interpreter):
        raise ValueError("runtime interpreter reference changed")
    return refs, capsule, requirement_sha256


def _synthetic_command(case_id: str) -> list[str]:
    code = (
        "import json,sys; "
        "sys.stdout.write(json.dumps({'kind':'e5-offline-wrapper','case_id':%r,'sn_imported':False})+'\\n'); "
        "sys.stdout.flush()"
    ) % case_id
    return [sys.executable, "-I", "-B", "-c", code]


def run_captured_cli_rehearsal(*, bundle_dir, artifact_root, artifact_index_id=None,
                               s1_refs=None, partition_id, case_id, project,
                               owner_evidence_dir, cli_run_dir, sn_source_ref=None,
                               runtime_refs=None, dependency_requirements=None,
                               expected_interpreter=None, owner_driver_path=None,
                               timeout_seconds: float = 2.0,
                               terminate_grace_seconds: float = 0.25) -> dict:
    """Run one bounded, zero-network rehearsal and persist its receipts."""
    if artifact_index_id is None and isinstance(s1_refs, Mapping):
        artifact_index_id = (s1_refs.get("index") or {}).get("id")
    if sn_source_ref is None and isinstance(s1_refs, Mapping):
        sn_source_ref = (s1_refs.get("sources") or {}).get("sn")
    bundle_dir = _path(bundle_dir, "bundle directory")
    artifact_root = _path(artifact_root, "artifact root")
    project = _path(project, "synthetic project")
    owner_dir = _path(owner_evidence_dir, "owner evidence directory")
    cli_dir = _path(cli_run_dir, "CLI run directory")
    _non_nested(owner_dir, cli_dir)
    _fresh_path(owner_dir, "owner evidence directory")
    _fresh_path(cli_dir, "CLI run directory")
    _validate_project(project)
    refs, capsule, requirements_sha256 = _validate_refs(
        bundle_dir=bundle_dir, artifact_root=artifact_root, artifact_index_id=artifact_index_id,
        partition_id=partition_id, case_id=case_id, sn_source_ref=sn_source_ref,
        dependency_requirements=dependency_requirements, expected_interpreter=expected_interpreter,
        runtime_refs=runtime_refs or {},
    )

    command = _synthetic_command(case_id)
    owner_error = None
    try:
        owner = _load_owner_driver(owner_driver_path)
        owner_result = owner.run_owned_command(
            command, run_dir=owner_dir, timeout_seconds=timeout_seconds,
            terminate_grace_seconds=terminate_grace_seconds, network_guard=True,
            env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}, cwd=str(project),
        )
    except Exception as exc:  # Persist inability to observe; never upgrade to success.
        owner_error = type(exc).__name__
        owner_result = _unknown_receipt(owner_error)
        if not owner_dir.exists():
            owner_dir.mkdir(parents=True, exist_ok=False)
        save_json(owner_dir / "result.json", owner_result)
        save_json(owner_dir / "diagnostic.json", {"drain_complete": False, "no_retry": True, "status": "unknown"})
    save_json(owner_dir / "owner-receipt.json", owner_result)

    guard = owner_result.get("network_guard", {})
    exit_fact = owner_result.get("exit", {})
    ok = bool(exit_fact.get("confirmed") is True and exit_fact.get("returncode") == 0
              and guard.get("available") is True and owner_result.get("spawn", {}).get("pidfd_error") is None)
    reason = "owner_observed" if ok else (
        "owner_driver_unavailable" if owner_error else
        "network_guard_unavailable" if guard.get("available") is not True else "owner_status_unknown"
    )

    cli_dir.mkdir(parents=True, exist_ok=False)
    wrapper_facts = {
        "format": "sn-captured-cli-wrapper-facts-v1", "offline": True, "evidence_only": True,
        "command": command, "case_id": case_id, "partition_id": partition_id,
        "project": str(project), "network_guard_requested": True,
        "sn_source_ref": dict(sn_source_ref), "artifact_refs": refs,
        "runtime_refs": dict(runtime_refs or {}),
        "requirements_sha256": requirements_sha256,
        "project_catalog_sha256": hashlib.sha256(
            (project / ".local/model-services.toml").read_bytes()).hexdigest(),
        "source_case_sha256": hashlib.sha256(json.dumps(capsule["cases"], sort_keys=True).encode()).hexdigest(),
    }
    save_json(cli_dir / "wrapper-facts.json", wrapper_facts)
    save_jsonl(cli_dir / "outputs.jsonl", [{
        "case_id": case_id, "suite": "qasper", "status": "rehearsal_only",
        "prediction": None, "official": False, "owner_status": exit_fact.get("status", "unknown"),
    }])
    score = {
        "format": "sn-captured-cli-diagnostic-score-v1", "score_kind": "diagnostic",
        "official": False, "official_score": None, "checks": {
            "captured_refs_valid": True, "synthetic_wrapper_observed": ok,
            "network_guard_available": guard.get("available") is True,
        }, "score": 1.0 if ok else None, "reason": reason,
    }
    save_json(cli_dir / "diagnostic-score.json", score)
    pending = {
        "format": "sn-official-score-pending-v1", "status": "pending", "official_score": None,
        "reason": reason, "explanation": "offline rehearsal is not an official model execution", "eligible": False,
        "diagnostic_score_path": "diagnostic-score.json",
    }
    save_json(cli_dir / "official-score-pending.json", pending)
    (cli_dir / "official-score-pending.md").write_text(
        "# Official score pending\n\n"
        "This evidence-only rehearsal never ran Silicon Notebook or a model. "
        "Official scoring remains pending and must use a reviewed captured CLI run.\n",
        encoding="utf-8",
    )
    state = {
        "format": "sn-captured-cli-rehearsal-state-v1", "phase": "diagnostic_complete" if ok else "blocked",
        "execution_status": "rehearsal_complete" if ok else "blocked", "offline": True,
        "evidence_only": True, "official_score_status": "pending", "job_executable": False,
        "owner_reason": reason, "owner_evidence_dir": str(owner_dir),
    }
    save_json(cli_dir / "state.json", state)
    return {"ok": ok, "reason": reason, "run": cli_dir, "owner_evidence": owner_dir, "state": state}


def main(argv=None) -> int:  # pragma: no cover - optional thin CLI surface
    raise SystemExit("Use run_captured_cli_rehearsal() with explicit captured inputs")


__all__ = ["run_captured_cli_rehearsal"]
