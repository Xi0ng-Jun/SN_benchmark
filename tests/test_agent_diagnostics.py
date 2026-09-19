from rag_eval.agent_diagnostics import compare_modes, diagnose_trace
from rag_eval.agent_trace import AgentTraceEnvelope


def envelope(mode="reasoning"):
    return AgentTraceEnvelope(
        trace_id="trace-1",
        case_id="case-1",
        mode=mode,
        status="success",
        completeness="partial",
        completeness_reason="declared_partial",
        final_output_available=True,
        context_available=True,
        citations_available=True,
        steps=[
            {"index": 0, "type": "intent", "status": "completed", "summary": "", "detail": {}, "duration_ms": 1},
            {"index": 1, "type": "retrieve", "status": "completed", "summary": "", "detail": {"anchor_evidence_ids": ["a1"]}, "duration_ms": 12},
            {"index": 2, "type": "retrieve", "status": "completed", "summary": "", "detail": {"anchors": ["a1", "a2"]}, "duration_ms": 18},
            {"index": 3, "type": "reflect", "status": "completed", "summary": "", "detail": {"termination_reason": "model_end"}, "duration_ms": 5},
            {"index": 4, "type": "fallback", "status": "completed", "summary": "", "detail": {}, "duration_ms": 2},
            {"index": 5, "type": "answer", "status": "completed", "summary": "", "detail": {}, "duration_ms": 3},
        ],
    )


def test_diagnose_trace_counts_actions_repeats_and_duration():
    result = diagnose_trace(envelope())

    assert result["action_seq"] == ["retrieve", "retrieve", "fallback"]
    assert result["action_counts"] == {"fallback": 1, "retrieve": 2}
    assert result["retrieval_count"] == 2
    assert result["repeated_action_count"] == 1
    assert result["repeated_action_types"] == ["retrieve"]
    assert result["reflect_turns"] == 1
    assert result["fallback_count"] == 1
    assert result["answer_present"] is True
    assert result["termination_reason"] == "model_end"
    assert result["anchor_count"] == 2
    assert result["total_duration_ms"] == 41


def test_compare_modes_does_not_fill_missing_mode_with_zero():
    chunk = dict(case_id="case-1", mode="chunk", diagnostics={"step_count": 0, "total_duration_ms": None})
    reasoning = dict(case_id="case-1", mode="reasoning", diagnostics={"step_count": 3, "total_duration_ms": 40})
    unpaired = dict(case_id="case-2", mode="reasoning", diagnostics={"step_count": 2, "total_duration_ms": 10})

    rows = compare_modes([chunk, reasoning, unpaired])
    paired = next(row for row in rows if row["case_id"] == "case-1")
    missing = next(row for row in rows if row["case_id"] == "case-2")

    assert paired["status"] == "paired"
    assert paired["delta"]["step_count"] == 3
    assert paired["delta"]["total_duration_ms"] is None
    assert missing["status"] == "unpaired"
    assert missing["delta"] is None
