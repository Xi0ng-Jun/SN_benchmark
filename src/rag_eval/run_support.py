"""Run state persistence and observed citation-object diagnostics."""
import json
from pathlib import Path
import time

from .artifacts import save_json

CITATIONS = "product.citation_object.existence_ratio"


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]

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
