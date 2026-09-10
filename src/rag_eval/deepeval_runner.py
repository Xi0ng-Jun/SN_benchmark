from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .cases import to_test_case


def build_metrics(thresholds: dict[str, float] | None = None, model: Any | None = None) -> list[Any]:
    """Build the five native DeepEval RAG metrics lazily.

    DeepEval remains optional so dataset loading and deterministic retrieval
    checks can run without model credentials or network access.
    """
    try:
        from deepeval.metrics import (
            AnswerRelevancyMetric,
            ContextualPrecisionMetric,
            ContextualRecallMetric,
            ContextualRelevancyMetric,
            FaithfulnessMetric,
        )
    except ImportError as exc:
        raise RuntimeError("Install the optional dependency with: pip install -e '.[deepeval]'") from exc
    values = {
        "contextual_recall": 0.70,
        "contextual_precision": 0.70,
        "contextual_relevancy": 0.70,
        "faithfulness": 0.80,
        "answer_relevancy": 0.75,
    }
    values.update(thresholds or {})
    return [
        ContextualRecallMetric(threshold=values["contextual_recall"], model=model),
        ContextualPrecisionMetric(threshold=values["contextual_precision"], model=model),
        ContextualRelevancyMetric(threshold=values["contextual_relevancy"], model=model),
        FaithfulnessMetric(threshold=values["faithfulness"], model=model),
        AnswerRelevancyMetric(threshold=values["answer_relevancy"], model=model),
    ]


def load_result_records(path: str | Path) -> list[dict[str, Any]]:
    """Load adapter output JSONL for DeepEval.

    Each row must contain question, answer, expected_answer and retrieval_context.
    Extra fields such as retrieved_ids are retained for deterministic reports.
    """
    rows: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            required = {"question", "answer", "retrieval_context"}
            missing = required - row.keys()
            if missing:
                raise ValueError(f"{path}:{line_number} missing fields: {sorted(missing)}")
            rows.append(row)
    return rows


def evaluate_records(records: list[dict[str, Any]], *, thresholds: dict[str, float] | None = None, model: Any | None = None) -> Any:
    """Run DeepEval on adapter records and return its native evaluation result."""
    try:
        from deepeval import evaluate
    except ImportError as exc:
        raise RuntimeError("Install the optional dependency with: pip install -e '.[deepeval]'") from exc
    return evaluate(
        test_cases=[to_test_case(record) for record in records],
        metrics=build_metrics(thresholds, model=model),
    )
