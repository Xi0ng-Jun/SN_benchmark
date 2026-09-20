"""Offline normalization and completeness auditing for Silicon Notebook traces.

The product stores reasoning steps, while DeepEval trajectory metrics expect a
complete ordered execution trace.  This module keeps that distinction explicit
and deliberately downgrades uncertain traces instead of inferring completeness.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


SCHEMA_VERSION = "sn-agent-trace-v1"
COMPLETENESS_VALUES = frozenset({"none", "partial", "complete"})
_TERMINAL_TYPES = frozenset({"answer", "synthesis", "termination", "finish"})


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _nonempty_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _normalise_span(raw: Mapping[str, Any], index: int) -> dict[str, Any]:
    step_type = _nonempty_text(raw.get("type")) or _nonempty_text(raw.get("step_type"))
    if step_type is None:
        raise ValueError("trace span type must be nonempty text")
    raw_index = raw.get("index", index)
    if isinstance(raw_index, bool) or not isinstance(raw_index, int) or raw_index < 0:
        raise ValueError("trace span index must be a nonnegative integer")
    status = _nonempty_text(raw.get("status")) or "completed"
    summary = raw.get("summary", "")
    if not isinstance(summary, str):
        summary = str(summary)
    detail = raw.get("detail", {})
    if not isinstance(detail, Mapping):
        detail = {}
    result = {
        "index": raw_index,
        "type": step_type,
        "status": status,
        "summary": summary,
        "detail": dict(detail),
    }
    duration = raw.get("duration_ms")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
        result["duration_ms"] = duration
    return result


def audit_trace(
    raw: Mapping[str, Any] | None,
    *,
    final_output_available: bool,
    context_available: bool,
    citations_available: bool,
) -> tuple[str, str]:
    """Return conservative ``(completeness, reason)`` for a raw envelope."""
    if raw is None:
        return "none", "trace_missing"
    declared = raw.get("completeness", "partial")
    if declared not in COMPLETENESS_VALUES:
        raise ValueError("trace completeness must be none, partial, or complete")
    spans = raw.get("spans", raw.get("steps", []))
    if not isinstance(spans, list):
        raise ValueError("trace spans must be a list of objects")
    if declared == "none" or not spans:
        return "none", "trace_missing" if not spans else "declared_none"
    if declared != "complete":
        return "partial", str(raw.get("completeness_reason") or "declared_partial")
    if not _nonempty_text(raw.get("trace_id")):
        return "partial", "trace_id_missing"
    for index, span in enumerate(spans):
        if not isinstance(span, Mapping):
            raise ValueError("trace spans must be a list of objects")
        # Explicit completeness requires fields that the current SN reasoning
        # projection does not emit.  This prevents accidental promotion.
        if "index" not in span:
            return "partial", "span_index_missing"
        if not (_nonempty_text(span.get("type")) or _nonempty_text(span.get("step_type"))):
            return "partial", "span_type_missing"
        if not _nonempty_text(span.get("status")):
            return "partial", "span_status_missing"
    last_type = _nonempty_text(spans[-1].get("type")) or _nonempty_text(spans[-1].get("step_type"))
    if last_type not in _TERMINAL_TYPES:
        return "partial", "terminal_step_missing"
    if not final_output_available:
        return "partial", "final_output_missing"
    if not context_available:
        return "partial", "context_availability_missing"
    if not citations_available:
        return "partial", "citation_availability_missing"
    return "complete", "complete"


@dataclass(frozen=True)
class AgentTraceEnvelope:
    """Serializable trace contract used by the offline Agent evaluator."""

    trace_id: str | None
    case_id: str
    mode: str
    status: str
    completeness: str
    completeness_reason: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    final_output_available: bool = False
    context_available: bool = False
    citations_available: bool = False
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.completeness not in COMPLETENESS_VALUES:
            raise ValueError("trace completeness must be none, partial, or complete")
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("case_id must be nonempty text")
        if not isinstance(self.mode, str) or not self.mode.strip():
            raise ValueError("mode must be nonempty text")
        if not isinstance(self.steps, list) or not all(isinstance(step, dict) for step in self.steps):
            raise ValueError("trace steps must be a list of objects")
        if self.completeness == "none" and self.steps:
            raise ValueError("none trace completeness cannot contain steps")

    @classmethod
    def missing(cls, *, case_id: str, mode: str, status: str = "unknown") -> "AgentTraceEnvelope":
        return cls(
            trace_id=None,
            case_id=case_id,
            mode=mode,
            status=status,
            completeness="none",
            completeness_reason="trace_missing",
        )

    @classmethod
    def from_record(
        cls,
        record: Mapping[str, Any],
        *,
        case_id: str,
        mode: str,
        final_output_available: bool | None = None,
        context_available: bool | None = None,
        citations_available: bool | None = None,
    ) -> "AgentTraceEnvelope":
        if not isinstance(record, Mapping):
            raise ValueError("trace record must be an object")
        raw = record.get("trace")
        if raw is None and ("spans" in record or "steps" in record):
            raw = record
        if raw is None:
            # No trajectory does not mean no answer/context: chunk normally has
            # all three without any reasoning steps.
            raw = {"completeness": "none", "spans": []}
        if not isinstance(raw, Mapping):
            raise ValueError("trace must be an object")
        raw_spans = raw.get("spans", raw.get("steps", []))
        if not isinstance(raw_spans, list):
            raise ValueError("trace spans must be a list of objects")
        if raw_spans and not all(isinstance(span, Mapping) for span in raw_spans):
            raise ValueError("trace spans must be a list of objects")
        output_available = (
            bool(record.get("answer", "").strip())
            if final_output_available is None and isinstance(record.get("answer"), str)
            else bool(final_output_available)
        )
        context = bool(record.get("context_available", raw.get("context_available", False))) if context_available is None else bool(context_available)
        citations = bool(record.get("citations_available", raw.get("citations_available", False))) if citations_available is None else bool(citations_available)
        completeness, reason = audit_trace(
            raw,
            final_output_available=output_available,
            context_available=context,
            citations_available=citations,
        )
        steps = [_normalise_span(span, index) for index, span in enumerate(raw_spans)]
        if completeness == "none":
            steps = []
        return cls(
            trace_id=_nonempty_text(raw.get("trace_id")),
            case_id=case_id,
            mode=mode,
            status=str(record.get("status") or "unknown"),
            completeness=completeness,
            completeness_reason=reason,
            steps=steps,
            final_output_available=output_available,
            context_available=context,
            citations_available=citations,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "trace_id": self.trace_id,
            "case_id": self.case_id,
            "mode": self.mode,
            "status": self.status,
            "completeness": self.completeness,
            "completeness_reason": self.completeness_reason,
            "steps": self.steps,
            "final_output_available": self.final_output_available,
            "context_available": self.context_available,
            "citations_available": self.citations_available,
        }

    def to_deepeval_dict(self) -> dict[str, Any]:
        """Return a stable JSON object for DeepEval's private trace input."""
        return {
            "trace_id": self.trace_id,
            "mode": self.mode,
            "status": self.status,
            "completeness": self.completeness,
            "steps": self.steps,
            "final_output_available": self.final_output_available,
            "context_available": self.context_available,
            "citations_available": self.citations_available,
        }
