"""Offline contracts for the second public benchmark expansion.

This module only validates and normalizes saved values.  It does not acquire
benchmark data or invoke a model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import re
from typing import Any


EXPANSION_SUITES = {
    "mmlu": {
        "dataset": "cais/mmlu",
        "split": "test",
        "native": True,
        "product": True,
        "product_reason": None,
        "task_kind": "mcq",
        "scorer": "deepeval.mmlu",
    },
    "gsm8k": {
        "dataset": "openai/gsm8k",
        "split": "test",
        "native": True,
        "product": True,
        "product_reason": None,
        "task_kind": "numeric",
        "scorer": "deepeval.gsm8k",
    },
    "truthfulqa": {
        "dataset": "truthfulqa",
        "split": "validation",
        "native": True,
        "product": True,
        "product_reason": None,
        "task_kind": "truthfulness",
        "scorer": "deepeval.truthfulqa",
    },
    "hellaswag": {
        "dataset": "Rowan/hellaswag",
        "split": "validation",
        "native": True,
        "product": False,
        "product_reason": "benchmark is primarily standalone commonsense completion",
        "task_kind": "mcq",
        "scorer": "deepeval.hellaswag",
    },
    "bbh": {
        "dataset": "lukaemon/bbh",
        "split": "test",
        "native": True,
        "product": False,
        "product_reason": "selected BBH tasks require task-specific standalone prompts",
        "task_kind": "mcq",
        "scorer": "deepeval.bbh",
    },
}
# Short name for consumers that handle starter and expansion manifests through
# the same suite metadata interface.
SUITES = EXPANSION_SUITES

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


def _parsed(raw_output: Any, normalized: str | None, reason: str | None = None) -> dict[str, Any]:
    result = {"raw_output": raw_output, "normalized_answer": normalized,
              "status": "parsed" if normalized is not None else "unparsed"}
    if reason is not None:
        result["reason"] = reason
    return result


def _normalize_mcq(raw_output: Any) -> dict[str, Any]:
    if not isinstance(raw_output, str) or not raw_output.strip():
        return _parsed(raw_output, None, "empty model output")
    # Prefer explicit answer markers and parenthesized labels; only then use a
    # standalone letter at the beginning, avoiding arbitrary prose initials.
    match = re.search(r"(?i)(?:final\s+answer|answer|choice|option)(?:\s+is)?\s*[:=\-]?\s*\(?([A-D])\)?\b", raw_output)
    if match is None:
        match = re.match(r"\s*\(?([A-D])\)?(?:[\s.):-]|$)", raw_output, flags=re.IGNORECASE)
    return _parsed(raw_output, match.group(1).upper() if match else None,
                   None if match else "no A-D choice found")


def _normalize_numeric(raw_output: Any) -> dict[str, Any]:
    if not isinstance(raw_output, str) or not raw_output.strip():
        return _parsed(raw_output, None, "empty model output")
    candidates = re.findall(r"[-+]?\$?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?", raw_output)
    marker = re.search(r"####\s*([-+]?\$?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?)", raw_output)
    value = marker.group(1) if marker else (candidates[-1] if candidates else None)
    if value is None:
        return _parsed(raw_output, None, "no numeric answer found")
    try:
        number = Decimal(value.replace("$", "").replace(",", ""))
    except InvalidOperation:
        return _parsed(raw_output, None, "numeric answer is invalid")
    normalized = format(number, "f").rstrip("0").rstrip(".") or "0"
    return _parsed(raw_output, normalized)


def _normalize_truthfulqa(raw_output: Any) -> dict[str, Any]:
    if not isinstance(raw_output, str) or not raw_output.strip():
        return _parsed(raw_output, None, "empty model output")
    # TruthfulQA is free text: retain it and only canonicalize surrounding
    # whitespace. Behavioral labels and evidence remain separate fields.
    return _parsed(raw_output, " ".join(raw_output.split()))


def normalize_answer(suite: str, raw_output: Any, *, task: str | None = None) -> dict[str, Any]:
    """Return a deterministic answer view while preserving the raw output."""
    if suite not in EXPANSION_SUITES:
        raise ValueError(f"Unknown expansion suite: {suite}")
    kind = EXPANSION_SUITES[suite]["task_kind"]
    if kind == "mcq":
        return _normalize_mcq(raw_output)
    if kind == "numeric":
        return _normalize_numeric(raw_output)
    return _normalize_truthfulqa(raw_output)


# Descriptive alias for callers that use the protocol terminology.
normalize_prediction = normalize_answer
