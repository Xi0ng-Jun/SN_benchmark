import pytest

from rag_eval.public_expansion_protocol import (
    EXPANSION_SUITES,
    EXPANSION_VERSION,
    TraceEnvelope,
    native_protocol,
    normalize_answer,
)


def test_expansion_suite_metadata_declares_tracks_and_task_kinds():
    assert set(EXPANSION_SUITES) == {"mmlu", "gsm8k", "truthfulqa", "hellaswag", "bbh"}
    assert all(EXPANSION_SUITES[name]["native"] is True for name in EXPANSION_SUITES)
    assert all(info["product"] is False for info in EXPANSION_SUITES.values())
    assert {suite for suite, info in EXPANSION_SUITES.items() if info["product_candidate"]} == {"mmlu", "gsm8k", "truthfulqa"}
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


def test_normalize_gsm8k_preserves_trailing_integer_zeroes():
    result = normalize_answer("gsm8k", "#### 1000")
    assert result["normalized_answer"] == "1000"


def test_normalize_truthfulqa_keeps_behavior_text_separate():
    result = normalize_answer("truthfulqa", "I cannot verify that claim.  ")
    assert result["normalized_answer"] is None
    assert result["raw_output"] == "I cannot verify that claim.  "
    assert "behavior" not in result


def test_truthfulqa_mc1_has_fixed_examples_and_numeric_diagnostic_choice():
    assert EXPANSION_VERSION == "public-expansion-v2"
    assert native_protocol("truthfulqa")["n_shots"] is None
    assert native_protocol("truthfulqa")["builtin_examples"] == 6
    result = normalize_answer("truthfulqa", "Answer: 12")
    assert result["normalized_answer"] == "12"
    assert result["purpose"] == "diagnostic_only"
    assert result["used_for_official_score"] is False


@pytest.mark.parametrize("task,prediction,expected", [
    ("boolean_expressions", "true", "True"),
    ("sports_understanding", "Yes", "yes"),
    ("formal_fallacies", "invalid", "invalid"),
    ("date_understanding", "(F)", "(F)"),
    ("reasoning_about_colored_objects", "(R)", "(R)"),
    ("object_counting", "Answer: 12", "12"),
    ("word_sorting", "apple  pear", "apple pear"),
])
def test_bbh_diagnostics_use_task_answer_kind(task, prediction, expected):
    assert normalize_answer("bbh", prediction, task=task)["normalized_answer"] == expected


def test_trace_envelope_missing_is_explicit_and_serializable():
    trace = TraceEnvelope.missing()
    assert trace.trace_id is None
    assert trace.completeness == "none"
    assert trace.spans == []
    assert trace.to_dict() == {"trace_id": None, "completeness": "none", "spans": []}


def test_trace_envelope_rejects_unknown_completeness():
    with pytest.raises(ValueError, match="completeness"):
        TraceEnvelope(trace_id="trace-1", completeness="unknown", spans=[])
