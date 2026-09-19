import pytest

pytest.importorskip("deepeval")

from rag_eval.agent_deepeval import (  # noqa: E402
    available_agent_metrics,
    build_test_case,
    evaluate_trajectory,
)
from rag_eval.agent_trace import AgentTraceEnvelope  # noqa: E402


def partial_envelope():
    return AgentTraceEnvelope(
        trace_id="trace-1",
        case_id="case-1",
        mode="reasoning",
        status="success",
        completeness="partial",
        completeness_reason="declared_partial",
        steps=[{"index": 0, "type": "retrieve", "status": "completed", "summary": "", "detail": {}}],
        final_output_available=True,
        context_available=True,
        citations_available=True,
    )


def test_available_metrics_are_the_first_trajectory_layer():
    assert available_agent_metrics() == (
        "task_completion",
        "step_efficiency",
        "plan_quality",
        "plan_adherence",
    )


def test_build_test_case_keeps_task_output_and_private_trace():
    test_case = build_test_case(
        {"case_id": "case-1", "suite": "qasper", "question": "What happened?", "references": ["An event"]},
        {"answer": "An event", "retrieval_context": ["The event happened."], "citations_available": True},
        partial_envelope(),
        {"step_count": 1},
    )

    assert test_case.input == "What happened?"
    assert test_case.actual_output == "An event"
    assert test_case.expected_output == "An event"
    assert test_case.retrieval_context == ["The event happened."]
    assert test_case._trace_dict["steps"][0]["type"] == "retrieve"
    assert test_case.metadata["completeness"] == "partial"


def test_partial_trace_is_not_applicable_for_judge_metrics():
    test_case = build_test_case(
        {"case_id": "case-1", "question": "What happened?", "references": []},
        {"answer": "Unknown"},
        partial_envelope(),
        {},
    )

    rows = evaluate_trajectory(test_case, partial_envelope(), metrics=["task_completion"])
    assert rows == [{
        "metric": "task_completion",
        "status": "not_applicable",
        "score": None,
        "reason": "trace_not_complete:declared_partial",
    }]
