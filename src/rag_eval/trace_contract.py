"""Truthful optional trace envelope shared by SN execution and result records."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

COMPLETENESS_VALUES = frozenset({"none", "partial", "complete"})


@dataclass(frozen=True)
class TraceEnvelope:
    """Optional, truthful execution trace attached to a result record."""

    trace_id: str | None
    completeness: str
    spans: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if self.completeness not in COMPLETENESS_VALUES:
            raise ValueError("trace completeness must be none, partial, or complete")
        if self.trace_id is not None and (not isinstance(self.trace_id, str) or not self.trace_id.strip()):
            raise ValueError("trace_id must be nonempty text or None")
        if not isinstance(self.spans, list) or not all(isinstance(span, dict) for span in self.spans):
            raise ValueError("trace spans must be a list of objects")
        if self.completeness == "none" and self.spans:
            raise ValueError("none trace completeness cannot contain spans")

    @classmethod
    def missing(cls) -> "TraceEnvelope":
        return cls(trace_id=None, completeness="none", spans=[])

    def to_dict(self) -> dict[str, Any]:
        return {"trace_id": self.trace_id, "completeness": self.completeness, "spans": self.spans}
