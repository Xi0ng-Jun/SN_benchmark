"""Run state persistence and observed citation-object diagnostics."""
import json
from pathlib import Path
import time

from .artifacts import save_json

CITATIONS = "product.citation_object.existence_ratio"


def iter_rows(path):
    """Stream JSONL rows; only '\\n' ends a row, unlike str.splitlines()."""
    with Path(path).open(encoding="utf-8", newline="\n") as handle:
        for line in handle:
            yield json.loads(line[:-1] if line.endswith("\n") else line)


def read_rows(path):
    return list(iter_rows(path))

def update_state(run, phase, **fields):
    save_json(run / "state.json", {"phase": phase, "updated_at": time.time(), **fields})


def citation_score(record):
    """Object existence is a diagnostic, never semantic citation support."""
    if "deterministic" not in record:
        return {"status": "not_applicable", "score": None, "reason": "citation object checks unavailable"}
    checks = record["deterministic"]
    if not checks["citation_count"]:
        return {"status": "not_applicable", "score": None, "reason": "no citation objects; absence is not a passing score"}
    return {"status": "scored", "score": checks["citation_valid_count"] / checks["citation_count"],
            "details": {**checks, "claim_support": "not_evaluated", "body_anchor_support": "not_evaluated"}}
