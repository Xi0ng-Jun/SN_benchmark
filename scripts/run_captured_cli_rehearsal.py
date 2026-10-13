#!/usr/bin/env python3
"""Run the evidence-only captured CLI rehearsal.

This command deliberately exercises the owner/wrapper boundary only. It never
imports Silicon Notebook or calls a provider; the module writes diagnostic and
official-score-pending artifacts separately.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _json_file(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Unable to read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must contain a JSON object: {path}")
    return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--s1-refs", type=Path, required=True,
                        help="Materialized S1 refs JSON")
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--runtime-refs", type=Path, required=True,
                        help="Captured interpreter expectation JSON")
    parser.add_argument("--dependency-requirements", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True,
                        help="Pinned bundle object directory")
    parser.add_argument("--project", type=Path, required=True,
                        help="Evidence-owned synthetic project")
    parser.add_argument("--owner-evidence-dir", type=Path, required=True)
    parser.add_argument("--cli-run-dir", type=Path, required=True)
    parser.add_argument("--partition-id", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--owner-driver", type=Path)
    args = parser.parse_args(argv)

    refs = _json_file(args.s1_refs, "S1 refs")
    runtime_refs = _json_file(args.runtime_refs, "runtime refs")
    try:
        index_id = refs["bundle_refs"]["index"]["id"]
        sn_ref = refs["sn_ref"]
    except (KeyError, TypeError) as exc:
        raise SystemExit("S1 refs must contain bundle_refs.index.id and sn_ref") from exc

    from rag_eval.captured_cli_rehearsal import run_captured_cli_rehearsal

    try:
        result = run_captured_cli_rehearsal(
            bundle_dir=args.bundle,
            artifact_root=args.artifact_root,
            artifact_index_id=index_id,
            partition_id=args.partition_id,
            case_id=args.case_id,
            project=args.project,
            owner_evidence_dir=args.owner_evidence_dir,
            cli_run_dir=args.cli_run_dir,
            sn_source_ref=sn_ref,
            runtime_refs=runtime_refs,
            dependency_requirements=args.dependency_requirements,
            expected_interpreter=runtime_refs,
            owner_driver_path=args.owner_driver,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({
        "ok": result["ok"], "reason": result["reason"],
        "run": str(result["run"]), "owner_evidence": str(result["owner_evidence"]),
        "state": result["state"],
    }, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
