"""Versioned local contracts for DeepEval public benchmark expansion.

Normalization is diagnostic only. Official Native scores consume the original
schema answer with the pinned SDK scorer, never a normalized substitute.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import re
from typing import Any

EXPANSION_VERSION = "public-expansion-v2"

EXPANSION_SUITES = {
    "mmlu": {
        "dataset": "cais/mmlu", "split": "test", "native": True,
        "product": False, "product_candidate": True,
        "product_reason": "Product adapter and reviewed supporting corpus are pending",
        "task_kind": "mcq", "scorer": "deepeval.exact_match_score.MMLU",
    },
    "gsm8k": {
        "dataset": "openai/gsm8k", "split": "test", "native": True,
        "product": False, "product_candidate": True,
        "product_reason": "Product adapter and reviewed supporting corpus are pending",
        "task_kind": "numeric", "scorer": "deepeval.exact_match_score.GSM8K",
    },
    "truthfulqa": {
        "dataset": "truthfulqa", "split": "validation", "native": True,
        "product": False, "product_candidate": True,
        "product_reason": "Product adapter, reviewed supporting corpus and behavior review are pending",
        "task_kind": "mc1", "scorer": "deepeval.exact_match_score.TruthfulQA.MC1",
    },
    "hellaswag": {
        "dataset": "Rowan/hellaswag", "split": "validation", "native": True,
        "product": False, "product_candidate": False,
        "product_reason": "benchmark is primarily standalone commonsense completion",
        "task_kind": "mcq", "scorer": "deepeval.exact_match_score.HellaSwag",
    },
    "bbh": {
        "dataset": "lukaemon/bbh", "split": "test", "native": True,
        "product": False, "product_candidate": False,
        "product_reason": "selected BBH tasks require task-specific standalone prompts",
        "task_kind": "task_specific", "scorer": "deepeval.exact_match_score.BBH",
    },
}
SUITES = EXPANSION_SUITES

# Diagnostic answer kinds mirror SDK 4.2.2 bbh_models_dict. Actual schemas are
# imported from that SDK during explicit request construction, not copied here.
BBH_TASK_KINDS = {
    "boolean_expressions": "boolean",
    "causal_judgement": "yes_no", "date_understanding": "choice_6",
    "disambiguation_qa": "choice_3", "dyck_languages": "text",
    "formal_fallacies": "validity", "geometric_shapes": "choice_11",
    "hyperbaton": "choice_2", "logical_deduction_three_objects": "choice_3",
    "logical_deduction_five_objects": "choice_5", "logical_deduction_seven_objects": "choice_7",
    "movie_recommendation": "choice_5", "multistep_arithmetic_two": "numeric",
    "navigate": "yes_no", "object_counting": "numeric",
    "penguins_in_a_table": "choice_5", "reasoning_about_colored_objects": "choice_18",
    "ruin_names": "choice_4", "salient_translation_error_detection": "choice_6",
    "snarks": "choice_2", "sports_understanding": "yes_no_lower",
    "temporal_sequences": "choice_4", "tracking_shuffled_objects_three_objects": "choice_3",
    "tracking_shuffled_objects_five_objects": "choice_5",
    "tracking_shuffled_objects_seven_objects": "choice_7",
    "web_of_lies": "yes_no", "word_sorting": "text",
}


def native_protocol(suite: str) -> dict[str, Any]:
    """Fixed protocol options; changing these requires a new bundle version."""
    if suite not in EXPANSION_SUITES:
        raise ValueError(f"Unknown expansion suite: {suite}")
    return {
        "n_shots": None if suite == "truthfulqa" else 0,
        "enable_cot": False if suite in {"gsm8k", "bbh"} else None,
        "mode": "MC1" if suite == "truthfulqa" else None,
        "shuffle_seed": 42 if suite == "truthfulqa" else None,
        "builtin_examples": 6 if suite == "truthfulqa" else 0,
        "prediction_path": "single_schema_answer",
        "normalization_for_scoring": False,
    }


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
              "status": "parsed" if normalized is not None else "unparsed",
              "purpose": "diagnostic_only", "used_for_official_score": False}
    if reason is not None:
        result["reason"] = reason
    return result


def _normalize_mcq(raw_output: Any, *, count: int = 4, parentheses: bool = False) -> dict[str, Any]:
    if not isinstance(raw_output, str) or not raw_output.strip():
        return _parsed(raw_output, None, "empty model output")
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[:count]
    marked = re.findall(
        rf"(?i)(?:final\s+answer|answer|choice|option)(?:\s+is)?\s*[:=\-]?\s*\(?([{letters}])\)?\b",
        raw_output,
    )
    if not marked:
        match = re.match(rf"\s*\(?([{letters}])\)?(?:[\s.):-]|$)", raw_output, flags=re.I)
        marked = [match.group(1)] if match else []
    labels = {label.upper() for label in marked}
    label = next(iter(labels)) if len(labels) == 1 else None
    if label is not None and parentheses:
        label = f"({label})"
    return _parsed(raw_output, label, None if label else "no unambiguous choice found")


def _normalize_numeric(raw_output: Any) -> dict[str, Any]:
    if not isinstance(raw_output, str) or not raw_output.strip():
        return _parsed(raw_output, None, "empty model output")
    pattern = r"[-+]?\$?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?"
    candidates = re.findall(pattern, raw_output)
    marker = re.search(r"####\s*(" + pattern + r")", raw_output)
    value = marker.group(1) if marker else (candidates[-1] if candidates else None)
    if value is None:
        return _parsed(raw_output, None, "no numeric answer found")
    try:
        number = Decimal(value.replace("$", "").replace(",", ""))
    except InvalidOperation:
        return _parsed(raw_output, None, "numeric answer is invalid")
    if not number.is_finite():
        return _parsed(raw_output, None, "numeric answer is not finite")
    normalized = format(number, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return _parsed(raw_output, normalized or "0")


def normalize_answer(suite: str, raw_output: Any, *, task: str | None = None) -> dict[str, Any]:
    """Return task-aware diagnostic parsing; never report an official score."""
    if suite not in EXPANSION_SUITES:
        raise ValueError(f"Unknown expansion suite: {suite}")
    if task is not None and (not isinstance(task, str) or not task.strip()):
        raise ValueError("task must be nonempty text or None")
    kind = EXPANSION_SUITES[suite]["task_kind"]
    if suite == "bbh":
        if task not in BBH_TASK_KINDS:
            raise ValueError("BBH normalization requires an explicit supported task")
        kind = BBH_TASK_KINDS[task]
    if kind == "mcq" or kind.startswith("choice_"):
        result = _normalize_mcq(raw_output, count=int(kind.split("_")[1]) if kind.startswith("choice_") else 4,
                                parentheses=suite == "bbh")
    elif kind == "numeric":
        result = _normalize_numeric(raw_output)
    elif kind == "mc1":
        text = raw_output if isinstance(raw_output, str) else ""
        match = re.fullmatch(r"\s*(?:[Aa]nswer\s*:\s*)?([1-9][0-9]*)\s*[.)]?\s*", text)
        result = _parsed(raw_output, match.group(1) if match else None,
                         None if match else "MC1 requires a numeric choice index")
    elif kind in {"boolean", "yes_no", "yes_no_lower", "validity"}:
        vocab = {"boolean": ("True", "False"), "yes_no": ("Yes", "No"),
                 "yes_no_lower": ("yes", "no"), "validity": ("valid", "invalid")}[kind]
        text = raw_output.strip().casefold() if isinstance(raw_output, str) else ""
        result = _parsed(raw_output, next((value for value in vocab if value.casefold() == text), None))
    else:
        text = " ".join(raw_output.split()) if isinstance(raw_output, str) else ""
        result = _parsed(raw_output, text or None)
    if task is not None:
        result["task"] = task
    return result


normalize_prediction = normalize_answer
