"""Append-only event storage and coverage-aware summaries for the starter."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from threading import Lock

from .starter_protocol import VERSION, fingerprint, require_text
from .public_expansion_protocol import TraceEnvelope

STATUSES = {"scored", "error", "unparsed", "not_applicable", "unscored"}
GROUP_FIELDS = ("run_id", "protocol_id", "suite", "task", "track", "mode", "scorer")


class EventJournal:
    """Single-process threaded sink; exclusive file creation, no implicit resume."""
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._handle = os.fdopen(descriptor, "w", encoding="utf-8")
        self._lock = Lock()

    def __call__(self, event):
        row = {**event, "recorded_at": datetime.now(timezone.utc).isoformat()}
        encoded = json.dumps(row, ensure_ascii=False, allow_nan=False)
        with self._lock:
            self._handle.write(encoded + "\n")
            self._handle.flush()
            os.fsync(self._handle.fileno())

    def close(self):
        with self._lock:
            self._handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def planned_result(case, *, run_id, protocol_id, track, mode=None, scorer=None):
    """Create one expected result identity, before execution.

    protocol_id hashes source/SDK/adapter/model configuration and, for R, corpus,
    review decisions and isolation settings. Do not share it across configurations.
    """
    require_text(run_id, "run_id")
    require_text(protocol_id, "protocol_id")
    if track not in {"N", "R"} or (track == "N" and mode is not None) or (track == "R" and mode not in {"chunk", "reasoning"}):
        raise ValueError("Native track has no product mode; R requires chunk or reasoning")
    if track == "R":
        require_text(scorer, "product scorer (must be explicit)")
    row = {"protocol_version": VERSION, "run_id": run_id, "protocol_id": protocol_id,
           "suite": case["suite"], "task": case.get("task", case["suite"]),
           "track": track, "mode": mode,
           "case_id": case["case_id"], "sample_id": case["sample_id"],
           "scorer": scorer or case["scorer"]}
    product_review = case.get("product_review", {})
    row["applicability"] = {"status": "applicable" if track == "N" else product_review.get("status", "pending"),
                             "reason": None if track == "N" else product_review.get("reason")}
    row["result_id"] = fingerprint(row)
    return row


def result_record(planned, *, status, score=None, output_available=False, reason=None,
                  details=None, normalized_answer=None, trace=None):
    if status not in STATUSES or type(output_available) is not bool:
        raise ValueError("Invalid result status/output availability")
    if status == "scored":
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1 or not output_available:
            raise ValueError("Scored result requires a finite 0..1 score and observed output")
    elif score is not None or not isinstance(reason, str) or not reason.strip():
        raise ValueError("Unscored result needs null score and explicit reason")
    envelope = trace if isinstance(trace, TraceEnvelope) else TraceEnvelope.missing() if trace is None else TraceEnvelope(**trace)
    return {**planned, "status": status, "score": score, "output_available": output_available,
            "normalized_answer": normalized_answer, "trace": envelope.to_dict(),
            "reason": reason, "details": details or {}}


def summarize(planned_rows, results):
    """No cross-suite/model/track average; missing results stay in denominators."""
    planned = {}
    groups = defaultdict(list)
    for row in planned_rows:
        row.setdefault("task", row.get("suite"))
        row.setdefault("applicability", {"status": "applicable", "reason": None})
        identity = row["result_id"]
        if identity in planned:
            raise ValueError("Duplicate planned result")
        planned[identity] = row
        groups[tuple(row[k] for k in GROUP_FIELDS)].append(row)
    observed = {}
    for result in results:
        identity = result["result_id"]
        if identity not in planned or identity in observed:
            raise ValueError("Unexpected or duplicate result; retries need explicit separate identity")
        if any(result.get(k) != v for k, v in planned[identity].items()):
            raise ValueError("Result identity differs from plan")
        result_record(planned[identity], status=result["status"], score=result["score"],
                      output_available=result["output_available"], reason=result.get("reason"),
                      normalized_answer=result.get("normalized_answer"), trace=result.get("trace"))
        observed[identity] = result
    summaries = []
    for key, rows in groups.items():
        available = [observed[r["result_id"]] for r in rows if r["result_id"] in observed]
        statuses = Counter(r["status"] for r in available)
        scores = [r["score"] for r in available if r["status"] == "scored"]
        valid_outputs = sum(r["output_available"] for r in available)
        trace_counts = Counter((r.get("trace") or {}).get("completeness", "none") for r in available)
        summaries.append({**dict(zip(GROUP_FIELDS, key)), "planned": len(rows),
                          "distinct_questions": len({r["sample_id"] for r in rows}),
                          "recorded": len(available), "missing": len(rows) - len(available),
                          "outputs": valid_outputs, "scored": len(scores),
                          "status_counts": {s: statuses[s] for s in sorted(STATUSES)},
                          "non_applicable": statuses["not_applicable"],
                          "trace_completeness": {s: trace_counts[s] for s in ("none", "partial", "complete")},
                          "agent_metrics_suppressed": trace_counts["complete"] == 0,
                          "score_coverage": len(scores) / len(rows),
                          "output_coverage": valid_outputs / len(rows),
                          "label_parse_coverage_over_outputs": (len(scores) / valid_outputs if valid_outputs else None)
                          if key[-1] == "product.boolq.explicit_conclusion.v1" else None,
                          "mean_over_scored": sum(scores) / len(scores) if scores else None,
                          "release_gate": False})
    return summaries
