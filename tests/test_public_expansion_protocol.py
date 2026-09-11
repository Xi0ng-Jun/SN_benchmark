import pytest

from rag_eval.public_expansion_protocol import (
    EXPANSION_SUITES,
    TraceEnvelope,
    normalize_answer,
)


def test_expansion_suite_metadata_declares_tracks_and_task_kinds():
    assert set(EXPANSION_SUITES) == {"mmlu", "gsm8k", "truthfulqa", "hellaswag", "bbh"}
    assert all(EXPANSION_SUITES[name]["native"] is True for name in EXPANSION_SUITES)
    assert EXPANSION_SUITES["mmlu"]["product"] is True
    assert EXPANSION_SUITES["gsm8k"]["product"] is True
    assert EXPANSION_SUITES["truthfulqa"]["product"] is True
    assert EXPANSION_SUITES["hellaswag"]["product"] is False
    assert EXPANSION_SUITES["bbh"]["product"] is False
    assert EXPANSION_SUITES["hellaswag"]["product_reason"]
    assert EXPANSION_SUITES["bbh"]["product_reason"]


def test_normalize_mmlu_choice_keeps_raw_output():
    result = normalize_answer("mmlu", "The correct option is (c), because ...", task="college_biology")
    assert result["raw_output"] == "The correct option is (c), because ..."
    assert result["normalized_answer"] == "C"
    assert result["status"] == "parsed"


def test_normalize_gsm8k_prefers_final_numeric_marker():
    result = normalize_answer("gsm8k", "First I get 12. Then 1,234.50\n#### 1,234.50")
    assert result["normalized_answer"] == "1234.5"
    assert result["raw_output"].startswith("First I get")


def test_normalize_truthfulqa_keeps_behavior_text_separate():
    result = normalize_answer("truthfulqa", "I cannot verify that claim.  ")
    assert result["normalized_answer"] == "I cannot verify that claim."
    assert result["raw_output"] == "I cannot verify that claim.  "
    assert "behavior" not in result


def test_trace_envelope_missing_is_explicit_and_serializable():
    trace = TraceEnvelope.missing()
    assert trace.trace_id is None
    assert trace.completeness == "none"
    assert trace.spans == []
    assert trace.to_dict() == {"trace_id": None, "completeness": "none", "spans": []}


def test_trace_envelope_rejects_unknown_completeness():
    with pytest.raises(ValueError, match="completeness"):
        TraceEnvelope(trace_id="trace-1", completeness="unknown", spans=[])
