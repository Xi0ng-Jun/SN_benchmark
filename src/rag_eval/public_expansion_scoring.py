"""Offline scorers for the public benchmark expansion.

These helpers deliberately operate on saved text and annotations only.  They do
not construct a model or call DeepEval; the Native runner can bind the
corresponding official template when it is available.
"""
from __future__ import annotations

import math
import re
from typing import Any


PRODUCT_SUPPORTED = frozenset({"squad", "drop", "boolq", "mmlu", "gsm8k", "truthfulqa"})
PRODUCT_UNSUPPORTED = frozenset({"hellaswag", "bbh", "bigbenchhard"})


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _status(score: float | None, *, normalized: Any = None, raw: Any = None,
            reason: str | None = None, **extra: Any) -> dict[str, Any]:
    result = {"status": "scored" if score is not None else "unparsed",
              "score": score, "normalized_answer": normalized,
              "raw_answer": raw, "reason": reason}
    result.update(extra)
    return result


def normalize_mmlu_label(value: Any) -> str | None:
    """Extract one multiple-choice label, preserving deterministic precedence.

    A standalone label on the first non-empty line or after a conventional
    ``final answer`` marker wins.  Otherwise an unambiguous standalone A-D
    token is accepted; prose containing multiple choices is unparsed.
    """
    text = _text(value)
    if not text:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        match = re.fullmatch(r"(?:final\s+answer\s*[:\-]?\s*)?([A-D])(?:[.)]|\s*)", line, re.I)
        if match:
            return match.group(1).upper()
    marked = re.findall(r"\b(?:final\s+answer|answer)\s*[:\-]\s*([A-D])\b", text, re.I)
    if marked:
        return marked[-1].upper() if len(set(x.upper() for x in marked)) == 1 else None
    tokens = re.findall(r"\b([A-D])\b", text, re.I)
    return tokens[0].upper() if len(tokens) == 1 else None


def score_mmlu(prediction: Any, expected: Any) -> dict[str, Any]:
    expected_label = normalize_mmlu_label(expected)
    predicted_label = normalize_mmlu_label(prediction)
    if expected_label is None:
        raise ValueError("MMLU expected answer must contain a single A-D label")
    if predicted_label is None:
        return _status(None, normalized=None, raw=prediction,
                       reason="could not deterministically parse one MMLU label",
                       expected_label=expected_label)
    return _status(float(predicted_label == expected_label), normalized=predicted_label,
                   raw=prediction, expected_label=expected_label)


_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def normalize_gsm8k_number(value: Any) -> float | None:
    """Extract GSM8K's final numeric answer (``#### n`` when present)."""
    text = _text(value).replace(",", "")
    if not text:
        return None
    marked = re.findall(r"####\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)", text)
    candidates = marked or _NUMBER.findall(text)
    if not candidates:
        return None
    try:
        number = float(candidates[-1])
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def score_gsm8k(prediction: Any, expected: Any) -> dict[str, Any]:
    expected_number = normalize_gsm8k_number(expected)
    predicted_number = normalize_gsm8k_number(prediction)
    if expected_number is None:
        raise ValueError("GSM8K expected answer must contain a finite number")
    if predicted_number is None:
        return _status(None, normalized=None, raw=prediction,
                       reason="could not deterministically parse GSM8K final number",
                       expected_number=expected_number)
    return _status(float(predicted_number == expected_number), normalized=predicted_number,
                   raw=prediction, expected_number=expected_number)


def score_truthfulqa(prediction: Any, expected: Any, *, behavior_label: str | None = None,
                     evidence: Any = None) -> dict[str, Any]:
    """Score answer matching while keeping behavior and evidence review fields separate.

    TruthfulQA's correction/refusal behavior is a human-review dimension; it is
    intentionally not folded into the deterministic answer score.
    """
    answer = _text(prediction)
    references = expected if isinstance(expected, list) else [expected]
    refs = {_text(item).casefold() for item in references if _text(item)}
    if not answer:
        return _status(None, normalized=None, raw=prediction, reason="empty answer",
                       behavior_label=behavior_label, evidence=evidence)
    score = float(answer.casefold() in refs) if refs else None
    return _status(score, normalized=answer, raw=prediction,
                   reason=None if score is not None else "no usable TruthfulQA reference",
                   behavior_label=behavior_label, evidence=evidence)


def product_applicability(suite: str) -> dict[str, Any]:
    """Return explicit Product applicability; unsupported suites are not zeroes."""
    if suite in PRODUCT_SUPPORTED:
        return {"status": "applicable", "applicable": True, "reason": None}
    if suite in PRODUCT_UNSUPPORTED:
        return {"status": "not_applicable", "applicable": False,
                "reason": f"Product track is not implemented for {suite}"}
    return {"status": "not_applicable", "applicable": False,
            "reason": f"unknown Product suite: {suite}"}


def score_expansion(suite: str, prediction: Any, expected: Any, **kwargs: Any) -> dict[str, Any]:
    scorers = {"mmlu": score_mmlu, "gsm8k": score_gsm8k, "truthfulqa": score_truthfulqa}
    scorer = scorers.get(suite)
    if scorer is None:
        return {"status": "not_applicable", "score": None,
                "reason": f"no expansion scorer for suite: {suite}",
                "raw_answer": prediction}
    if suite != "truthfulqa":
        kwargs = {}
    return scorer(prediction, expected, **kwargs)
