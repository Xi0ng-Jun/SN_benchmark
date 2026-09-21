"""Optional DeepEval trajectory metrics for complete offline traces."""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from .agent_trace import AgentTraceEnvelope


_METRIC_NAMES = (
    "task_completion",
    "step_efficiency",
    "plan_quality",
    "plan_adherence",
)


def available_agent_metrics() -> tuple[str, ...]:
    return _METRIC_NAMES


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _references(case: Mapping[str, Any]) -> str | None:
    references = case.get("references")
    if isinstance(references, list):
        for reference in references:
            if isinstance(reference, str) and reference.strip():
                return reference
    value = case.get("expected_answer")
    return value if isinstance(value, str) and value.strip() else None


def _context(output: Mapping[str, Any]) -> list[str] | None:
    value = output.get("retrieval_context")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    product = output.get("product_record")
    if isinstance(product, Mapping):
        value = product.get("retrieval_context")
        if isinstance(value, list):
            return [item for item in value if isinstance(item, str)]
    return None


def build_test_case(
    case: Mapping[str, Any],
    output: Mapping[str, Any],
    envelope: AgentTraceEnvelope,
    diagnostics: Mapping[str, Any],
):
    """Build a DeepEval case without importing DeepEval at module load time."""
    try:
        from deepeval.test_case import LLMTestCase
    except ImportError as exc:  # pragma: no cover - exercised without extra
        raise RuntimeError("DeepEval is required for Agent trajectory scoring; install .[deepeval]") from exc
    question = _text(case.get("question")) or _text(case.get("original_question")) or _text(case.get("input"))
    answer = _text(output.get("answer")) or _text(output.get("prediction")) or _text(output.get("actual_output"))
    metadata = {
        "case_id": case.get("case_id"),
        "suite": case.get("suite"),
        "task": case.get("task"),
        "mode": envelope.mode,
        "completeness": envelope.completeness,
        "completeness_reason": envelope.completeness_reason,
        "diagnostics": dict(diagnostics),
    }
    test_case = LLMTestCase(
        input=question,
        actual_output=answer,
        expected_output=_references(case),
        retrieval_context=_context(output),
        metadata=metadata,
    )
    # DeepEval's trajectory metrics intentionally consume this private field
    # when evaluating a saved trace outside an instrumented process.
    test_case._trace_dict = envelope.to_deepeval_dict()
    return test_case


def _metric(name: str, *, model: object | None):
    from deepeval.metrics import (
        PlanAdherenceMetric,
        PlanQualityMetric,
        StepEfficiencyMetric,
        TaskCompletionMetric,
    )
    classes = {
        "task_completion": TaskCompletionMetric,
        "step_efficiency": StepEfficiencyMetric,
        "plan_quality": PlanQualityMetric,
        "plan_adherence": PlanAdherenceMetric,
    }
    kwargs = {"model": model} if model is not None else {}
    if name == "task_completion":
        kwargs["task"] = None
    return classes[name](**kwargs)


def trajectory_skip_reason(envelope: AgentTraceEnvelope, name: str) -> str | None:
    """Shared eligibility for scoring and offline input inspection."""
    if envelope.completeness != "complete":
        return f"trace_not_complete:{envelope.completeness_reason}"
    if not envelope.final_output_available:
        return "final_output_missing"
    if envelope.execution_trace is None:
        return "native_execution_trace_missing"
    if name in {"plan_quality", "plan_adherence"} and not any(
            s['type'] == 'plan' and s.get('detail', {}).get('output_present') for s in envelope.steps):
        return "explicit_plan_missing"
    return None


def evaluate_trajectory(
    test_case: Any,
    envelope: AgentTraceEnvelope,
    *,
    metrics: Iterable[str] | None = None,
    model: object | None = None,
) -> list[dict[str, Any]]:
    """Run selected metrics, or return explicit N/A for incomplete traces."""
    selected = tuple(metrics or _METRIC_NAMES)
    unknown = sorted(set(selected) - set(_METRIC_NAMES))
    if unknown:
        raise ValueError("Unknown Agent metric(s): " + ", ".join(unknown))
    results: list[dict[str, Any]] = []
    for name in selected:
        reason = trajectory_skip_reason(envelope, name)
        if reason:
            results.append({'metric': name, 'status': 'not_applicable', 'score': None,
                            'reason': reason})
            continue
        try:
            metric = _metric(name, model=model)
            metric.measure(test_case)
            score = metric.score
            if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(score):
                raise ValueError("DeepEval metric returned a non-finite score")
            results.append({
                "metric": name,
                "status": "scored",
                "score": float(score),
                "reason": getattr(metric, "reason", None),
                'details': {'trajectory_input': 'deepeval_native_span_tree'},
            })
        except Exception as exc:
            results.append({
                "metric": name,
                "status": "error",
                "score": None,
                "reason": "DeepEval metric failed",
                "details": {"error_type": type(exc).__name__, "error": str(exc)},
            })
    return results
