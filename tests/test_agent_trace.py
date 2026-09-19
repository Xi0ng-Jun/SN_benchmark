import json

import pytest

from rag_eval.agent_trace import AgentTraceEnvelope, audit_trace


def complete_raw():
    return {
        "trace_id": "trace-1",
        "completeness": "complete",
        "spans": [
            {"index": 0, "type": "plan", "status": "completed", "summary": "plan"},
            {"index": 1, "type": "retrieve", "status": "completed", "summary": "retrieve"},
            {"index": 2, "type": "answer", "status": "completed", "summary": "answer"},
        ],
    }


def test_missing_trace_is_none_and_cannot_be_evaluated():
    envelope = AgentTraceEnvelope.from_record(
        {}, case_id="case-1", mode="chunk", final_output_available=False
    )

    assert envelope.completeness == "none"
    assert envelope.steps == []
    assert envelope.completeness_reason == "trace_missing"
    assert envelope.to_dict()["schema_version"] == "sn-agent-trace-v1"


def test_sn_reasoning_steps_are_conservatively_partial():
    envelope = AgentTraceEnvelope.from_record(
        {
            "trace": {
                "trace_id": "answer-1",
                "completeness": "partial",
                "spans": [{"step_type": "intent", "summary": "understand"}],
            }
        },
        case_id="case-1",
        mode="reasoning",
        final_output_available=True,
    )

    assert envelope.completeness == "partial"
    assert envelope.steps[0]["type"] == "intent"
    assert envelope.steps[0]["index"] == 0
    assert envelope.to_deepeval_dict()["steps"][0]["status"] == "completed"


def test_explicit_complete_requires_all_execution_fields():
    envelope = AgentTraceEnvelope.from_record(
        {
            "trace": complete_raw(),
            "context_available": True,
            "citations_available": True,
        },
        case_id="case-1",
        mode="reasoning",
        final_output_available=True,
    )

    assert envelope.completeness == "complete"
    assert envelope.completeness_reason == "complete"
    assert envelope.final_output_available is True
    assert envelope.context_available is True
    assert envelope.citations_available is True


def test_complete_claim_is_downgraded_when_terminal_step_is_missing():
    raw = complete_raw()
    raw["spans"] = raw["spans"][:-1]
    status, reason = audit_trace(
        raw,
        final_output_available=True,
        context_available=True,
        citations_available=True,
    )

    assert status == "partial"
    assert reason == "terminal_step_missing"


def test_malformed_trace_is_rejected_instead_of_being_marked_complete():
    with pytest.raises(ValueError, match="trace spans"):
        AgentTraceEnvelope.from_record(
            {"trace": {"trace_id": "bad", "completeness": "complete", "spans": "bad"}},
            case_id="case-1",
            mode="reasoning",
            final_output_available=True,
        )


def test_serialization_is_json_safe():
    envelope = AgentTraceEnvelope.from_record(
        {"trace": complete_raw(), "context_available": True, "citations_available": True},
        case_id="case-1",
        mode="reasoning",
        final_output_available=True,
    )

    encoded = json.dumps(envelope.to_dict(), ensure_ascii=False)
    assert json.loads(encoded)["case_id"] == "case-1"
