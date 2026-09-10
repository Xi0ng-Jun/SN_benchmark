"""Native DeepEval metrics for the public benchmark protocol."""
from __future__ import annotations

import json
from typing import Any


PRIMARY_METRICS = ("Answer Correctness", "Faithfulness", "Answer Relevancy")
DIAGNOSTIC_METRICS = ("Contextual Recall", "Contextual Precision", "Contextual Relevancy")


def _references(record: dict[str, Any]) -> list[str]:
    values = record.get("references") or []
    if isinstance(values, str):
        values = [values]
    return [str(value) for value in values if str(value).strip()]


def metric_availability(record: dict[str, Any], diagnostic: bool = False) -> dict[str, dict[str, Any]]:
    """Describe which metrics can be scored without inventing unavailable inputs."""
    references = _references(record)
    context_ok = bool(record.get("context_supported") and record.get("retrieval_context"))
    result: dict[str, dict[str, Any]] = {
        "Answer Correctness": {
            "available": bool(references),
            "status": "available" if references else "skipped",
            "reason": None if references else "official reference answers unavailable",
        },
        "Faithfulness": {
            "available": context_ok,
            "status": "available" if context_ok else "skipped",
            "reason": None if context_ok else "actual synthesis context capture unavailable",
        },
        "Answer Relevancy": {"available": True, "status": "available", "reason": None},
    }
    if diagnostic:
        for name in DIAGNOSTIC_METRICS:
            needs_reference = name != "Contextual Relevancy"
            ranking_available = bool((record.get("deterministic") or {}).get("ranking_available"))
            available = context_ok and (bool(references) or not needs_reference)
            if name == "Contextual Precision":
                available = available and ranking_available
            reason = None
            if not context_ok:
                reason = "actual synthesis context capture unavailable"
            elif needs_reference and not references:
                reason = "official reference answers unavailable"
            elif name == "Contextual Precision" and not ranking_available:
                reason = "comparable retrieval ranking unavailable"
            result[name] = {
                "available": available,
                "status": "available" if available else "skipped",
                "reason": reason,
            }
    return result


def expected_answer(record: dict[str, Any]) -> str:
    """Serialize every official alternative as the judge's expected output."""
    return json.dumps(_references(record), ensure_ascii=False)


def _correctness_steps(dataset: str) -> list[str]:
    steps = [
        "Read EXPECTED_OUTPUT as a JSON array of alternative acceptable full answers; matching any applicable alternative is sufficient.",
        "Identify every fact, number, date, unit, and qualification requested by INPUT.",
        "Compare ACTUAL_OUTPUT with the acceptable alternatives, allowing equivalent wording while checking omissions and contradictions.",
    ]
    if dataset.lower() == "drop":
        steps.append("Accept a valid calculation from the passage values when it yields an acceptable numerical, date, or count answer.")
    steps.append("Assign the rubric category that reflects correctness and completeness; topical overlap alone is insufficient.")
    return steps


def build_quality_metrics(record: dict[str, Any], model: Any, diagnostic: bool = False) -> list[Any]:
    """Build only the native DeepEval metrics applicable to one saved output."""
    from deepeval.metrics import (
        AnswerRelevancyMetric,
        ContextualPrecisionMetric,
        ContextualRecallMetric,
        ContextualRelevancyMetric,
        FaithfulnessMetric,
        GEval,
    )
    from deepeval.metrics.g_eval import Rubric
    from deepeval.test_case import SingleTurnParams

    available = metric_availability(record, diagnostic)
    metrics: list[Any] = []
    if available["Answer Correctness"]["available"]:
        metrics.append(GEval(
            name="Answer Correctness",
            evaluation_steps=_correctness_steps(str(record.get("dataset", ""))),
            evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.EXPECTED_OUTPUT],
            rubric=[
                Rubric(score_range=(0, 2), expected_outcome="Wrong, contradictory, or does not answer the question."),
                Rubric(score_range=(3, 6), expected_outcome="Partly correct but missing or misstating required information."),
                Rubric(score_range=(7, 10), expected_outcome="Correct and complete, allowing equivalent wording or valid calculation."),
            ],
            model=model,
            async_mode=False,
        ))
    if available["Faithfulness"]["available"]:
        metrics.append(FaithfulnessMetric(model=model, async_mode=False))
    metrics.append(AnswerRelevancyMetric(model=model, async_mode=False))
    if diagnostic:
        if available["Contextual Recall"]["available"]:
            metrics.append(ContextualRecallMetric(model=model, async_mode=False))
        if available["Contextual Precision"]["available"]:
            metrics.append(ContextualPrecisionMetric(model=model, async_mode=False))
        if available["Contextual Relevancy"]["available"]:
            metrics.append(ContextualRelevancyMetric(model=model, async_mode=False))
    return metrics
