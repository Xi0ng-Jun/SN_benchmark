#!/usr/bin/env python3
"""Audit public expansion artifacts using only local files.

The checker intentionally does not import a dataset SDK, contact a URL, or
construct a model.  It validates the frozen manifest contract and scans saved
JSON artifacts for truthful trace envelopes and applicability values.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag_eval.public_expansion_protocol import EXPANSION_SUITES, TraceEnvelope

_HASH_FIELDS = ("origin_sha256", "export_sha256")
_HASH_RE = set("0123456789abcdef")
_VALID_APPLICABILITY = {"applicable", "not_applicable", "pending"}


def _error(code: str, path: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "path": path, **details}


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HASH_RE


def audit_manifest(manifest: Any, *, path: str = "manifest.json") -> list[dict[str, Any]]:
    """Return manifest contract errors without reading or fetching anything."""
    errors: list[dict[str, Any]] = []
    if not isinstance(manifest, dict):
        return [_error("manifest_not_object", path)]
    suite = manifest.get("suite")
    info = EXPANSION_SUITES.get(suite)
    if manifest.get("protocol_version") != "public-starter-v1":
        errors.append(_error("unsupported_protocol", f"{path}.protocol_version"))
    if info is None:
        errors.append(_error("unknown_suite", f"{path}.suite", value=suite))
        return errors

    source = manifest.get("source")
    if not isinstance(source, dict):
        errors.append(_error("missing_source", f"{path}.source"))
    else:
        for field in _HASH_FIELDS:
            value = source.get(field)
            if value is None:
                errors.append(_error("missing_source_hash", f"{path}.source.{field}"))
            elif not _is_sha256(value):
                errors.append(_error("invalid_source_hash", f"{path}.source.{field}"))
        for field in ("dataset", "split", "revision", "source_url", "license", "license_url", "conversion"):
            if not isinstance(source.get(field), str) or not source[field].strip():
                errors.append(_error("missing_source_field", f"{path}.source.{field}"))
        if source.get("dataset") != info["dataset"] or source.get("split") != info["split"]:
            errors.append(_error("suite_source_mismatch", f"{path}.source", suite=suite))

    if manifest.get("scorer") != info["scorer"]:
        errors.append(_error("suite_scorer_mismatch", f"{path}.scorer", suite=suite))

    review = manifest.get("product_review")
    if review is not None:
        if not isinstance(review, dict) or review.get("status") not in _VALID_APPLICABILITY:
            errors.append(_error("invalid_applicability", f"{path}.product_review.status"))
        else:
            expected = "pending" if info["product"] else "not_applicable"
            if review["status"] != expected:
                errors.append(_error("manifest_product_applicability_mismatch",
                                     f"{path}.product_review.status", expected=expected,
                                     actual=review["status"]))
            if not info["product"] and not isinstance(review.get("reason"), str):
                errors.append(_error("missing_applicability_reason", f"{path}.product_review.reason"))
    return errors


def _audit_trace(value: Any, path: str) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return [_error("invalid_trace", path)]
    try:
        TraceEnvelope(**value)
    except (TypeError, ValueError) as exc:
        code = "invalid_trace_completeness" if "completeness" in str(exc) else "invalid_trace"
        return [_error(code, path)]
    return []


def _scan_values(value: Any, path: str) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "trace" in value:
            errors.extend(_audit_trace(value["trace"], f"{path}.trace"))
        for key, child in value.items():
            if key != "trace":
                errors.extend(_scan_values(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_scan_values(child, f"{path}[{index}]"))
    return errors


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def audit_path(path: str | Path) -> dict[str, Any]:
    """Audit a manifest, bundle directory, or saved JSON run artifact."""
    path = Path(path)
    errors: list[dict[str, Any]] = []
    checked: list[str] = []
    if path.is_dir():
        manifest_path = path / "manifest.json"
        if not manifest_path.exists():
            return {"path": str(path), "checked": [], "errors": [_error("missing_manifest", str(manifest_path))]}
        try:
            manifest = _load_json(manifest_path)
        except (OSError, json.JSONDecodeError):
            return {"path": str(path), "checked": [], "errors": [_error("malformed_manifest", str(manifest_path))]}
        errors.extend(audit_manifest(manifest, path=str(manifest_path)))
        checked.append(str(manifest_path))
        artifacts = manifest.get("artifacts", {}) if isinstance(manifest, dict) else {}
        if isinstance(artifacts, dict):
            for relative, expected in artifacts.items():
                artifact = (path / relative).resolve()
                if not artifact.is_relative_to(path.resolve()) or not artifact.is_file():
                    errors.append(_error("missing_artifact", str(artifact)))
                    continue
                actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
                checked.append(str(artifact))
                if actual != expected:
                    errors.append(_error("artifact_hash_mismatch", str(artifact)))
        json_paths = sorted(path.rglob("*.json")) + sorted(path.rglob("*.jsonl"))
    else:
        json_paths = [path] if path.suffix in {".json", ".jsonl"} else []
    for json_path in json_paths:
        if json_path == path and not json_path.exists():
            errors.append(_error("missing_path", str(json_path)))
            continue
        try:
            if json_path.suffix == ".jsonl":
                values = [_load_json_line(line) for line in json_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            else:
                values = [_load_json(json_path)]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            errors.append(_error("malformed_json", str(json_path)))
            continue
        if str(json_path) not in checked:
            checked.append(str(json_path))
        for index, value in enumerate(values):
            errors.extend(_scan_values(value, f"{json_path}[{index}]" if json_path.suffix == ".jsonl" else str(json_path)))
    return {"path": str(path), "checked": checked, "errors": errors}


def _load_json_line(line: str) -> Any:
    return json.loads(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="local bundle directories or JSON artifacts")
    args = parser.parse_args(argv)
    reports = [audit_path(path.resolve()) for path in args.paths]
    for report in reports:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if any(report["errors"] for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
