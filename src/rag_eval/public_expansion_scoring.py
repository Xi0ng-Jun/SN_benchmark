"""Diagnostic parsing and Product applicability for expansion suites.

Official Native scoring lives in public_expansion_native.score_prediction and
requires a frozen case, request and SDK manifest. These compatibility helpers
never return a benchmark score or claim semantic TruthfulQA correctness.
"""
from __future__ import annotations

from typing import Any

from .public_expansion_protocol import EXPANSION_SUITES, normalize_answer

PRODUCT_SUPPORTED = frozenset({"squad", "drop", "boolq"})
PRODUCT_UNSUPPORTED = frozenset({*EXPANSION_SUITES, "bigbenchhard"})


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)) and len(value) == 1:
        return _text(value[0])
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def normalize_mmlu_label(value: Any) -> str | None:
    return normalize_answer("mmlu", _text(value))["normalized_answer"]


def normalize_gsm8k_number(value: Any) -> str | None:
    return normalize_answer("gsm8k", _text(value))["normalized_answer"]


def _diagnostic(suite: str, prediction: Any, expected: Any, *, task: str | None = None) -> dict[str, Any]:
    parsed = normalize_answer(suite, _text(prediction), task=task)
    expected_parsed = normalize_answer(suite, _text(expected), task=task)
    left, right = parsed["normalized_answer"], expected_parsed["normalized_answer"]
    return {
        "status": "diagnostic", "score": None, "score_kind": "diagnostic_only",
        "diagnostic_score": float(left == right) if left is not None and right is not None else None,
        "normalization": parsed, "normalized_answer": left, "raw_answer": prediction,
        "reason": "Recovery normalization is not the official Native scoring protocol",
    }


def score_mmlu(prediction: Any, expected: Any) -> dict[str, Any]:
    """Compatibility diagnostic; use the frozen Native API for official scores."""
    return _diagnostic("mmlu", prediction, expected)


def score_gsm8k(prediction: Any, expected: Any) -> dict[str, Any]:
    """Compatibility diagnostic; normalized numeric equality is not SDK accuracy."""
    return _diagnostic("gsm8k", prediction, expected)


def score_truthfulqa(prediction: Any, expected: Any, *, behavior_label: str | None = None,
                     evidence: Any = None) -> dict[str, Any]:
    """Preserve optional human-review annotations without fabricating a grade."""
    return {
        "status": "not_applicable", "score": None, "score_kind": "diagnostic_only",
        "raw_answer": prediction, "normalized_answer": None,
        "behavior_label": behavior_label, "evidence": evidence,
        "annotation_status": "caller_supplied_unverified",
        "reason": "TruthfulQA Native requires frozen MC1 choices and the official SDK scorer",
        "applicability": "free-text semantic truthfulness and refusal behavior require separate human review",
    }


def product_applicability(suite: str) -> dict[str, Any]:
    """Unsupported Product suites are explicit N/A, never failed benchmark items."""
    if suite in PRODUCT_SUPPORTED:
        return {"status": "applicable", "applicable": True, "reason": None}
    if suite in PRODUCT_UNSUPPORTED:
        info = EXPANSION_SUITES.get(suite, EXPANSION_SUITES["bbh"])
        return {"status": "not_applicable", "applicable": False,
                "product_candidate": info["product_candidate"], "reason": info["product_reason"]}
    return {"status": "not_applicable", "applicable": False,
            "reason": f"unknown Product suite: {suite}"}


def score_expansion(suite: str, prediction: Any, expected: Any, **kwargs: Any) -> dict[str, Any]:
    """Legacy diagnostic dispatcher; intentionally cannot yield an official score."""
    if suite == "truthfulqa":
        return score_truthfulqa(prediction, expected, behavior_label=kwargs.get("behavior_label"),
                               evidence=kwargs.get("evidence"))
    if suite in EXPANSION_SUITES:
        return _diagnostic(suite, prediction, expected, task=kwargs.get("task"))
    return {"status": "not_applicable", "score": None,
            "reason": f"unknown expansion suite: {suite}", "raw_answer": prediction}
