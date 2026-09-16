"""Regression specifications only: tests have not been executed in this phase."""
import sys
from types import SimpleNamespace

import pytest

from rag_eval import starter_native
from rag_eval.system_scoring import EXTRACTION_VERSION, parse_system_answer, primary_scorer, score_system_answer


@pytest.fixture
def scoring(monkeypatch):
    calls = []

    def build(case, source):
        domain = case.get("schema", {"type": "string"})
        return {"expected_output": case["expected"], "schema_name": case.get("schema_name", "StringSchema"),
                "schema": {"properties": {"answer": domain}}, "sdk_identity": "fixture-sdk"}, object

    def exact(target, prediction):
        calls.append((target, prediction))
        return int(target.strip() == prediction.strip())

    monkeypatch.setattr(starter_native, "build_request", build)
    monkeypatch.setitem(sys.modules, "deepeval.scorer", SimpleNamespace(
        Scorer=lambda: SimpleNamespace(exact_match_score=exact)))
    return calls


@pytest.mark.parametrize("answer", ["The value is 42.", "Reasoning gives A", "Final answer: A\nFinal answer: B",
                                   "Final answer: A\nFinal answer: A", "Final answer: A or B", "Final answer: A [1]",
                                   "final answer: A", "**Final answer: A**", "", None])
def test_never_guess_or_repair_an_ambiguous_final_choice(scoring, answer):
    result = score_system_answer({"suite": "mmlu", "task": "test", "expected": "A"}, answer, {})
    assert result["status"] == "unparsed"
    assert result["score"] is result["normalized_answer"] is None
    assert result["reason"]
    assert scoring == []


def test_original_numeric_text_is_the_exact_match_input(scoring):
    case = {"suite": "gsm8k", "task": "gsm8k", "expected": "1,200",
            "schema_name": "NumberSchema", "schema": {"type": "integer"}}
    result = score_system_answer(case, "12 boxes of 100.\nFinal answer: 1,200", {})
    assert result["score"] == 1.0
    assert result["normalized_answer"] == "1,200"
    assert scoring == [("1,200", "1,200")]
    assert result["details"]["score_kind"] == "adapted_product"
    assert result["details"]["extraction_version"] == EXTRACTION_VERSION
    assert result["details"]["official_native"] is False
    assert score_system_answer(case, "Final answer: 1200", {})["score"] == 0.0


@pytest.mark.parametrize("task,target,bad", [
    ("date_understanding", "(F)", "F"),
    ("reasoning_about_colored_objects", "(R)", "(S)"),
    ("boolean_expressions", "True", "true"),
    ("sports_understanding", "yes", "Yes"),
    ("formal_fallacies", "valid", "True"),
    ("object_counting", "12", "12.0"),
    ("word_sorting", "apple pear", ""),
    ("dyck_languages", "] )", "Explanation instead"),
])
def test_bbh_exact_task_output_domains(scoring, task, target, bad):
    case = {"suite": "bbh", "task": task, "expected": target}
    assert score_system_answer(case, "Final answer: " + target, {})["score"] == 1.0
    assert score_system_answer(case, "Final answer: " + bad, {})["status"] == "unparsed"


def test_truthfulqa_numeric_index_must_exist(scoring):
    case = {"suite": "truthfulqa", "task": "truthfulqa_mc1", "expected": "4",
            "raw_row": {"mc1_targets": {"choices": ["a", "b", "c", "d"]}}}
    assert score_system_answer(case, "Final answer: 4", {})["score"] == 1.0
    assert score_system_answer(case, "Final answer: 5", {})["status"] == "unparsed"


def test_extracted_value_must_also_match_official_literal_schema(scoring):
    case = {"suite": "mmlu", "task": "test", "expected": "A", "schema": {"enum": ["A", "B"]}}
    assert score_system_answer(case, "Final answer: C", {})["status"] == "unparsed"
    assert scoring == []


def test_ifeval_passes_full_body_directly_without_stripping(scoring, monkeypatch):
    calls = []
    def score(case, request, prediction, source):
        calls.append(prediction)
        return {"status": "scored", "score": 1.0,
                "details": [{"instruction_id": "fixture", "status": "scored", "score": 1}]}
    monkeypatch.setattr(starter_native, "score_prediction", score)
    answer = "  A response.\r\n[1] citation\n"
    result = score_system_answer({"suite": "ifeval", "task": "ifeval", "expected": ""}, answer, {})
    assert calls == [answer]
    assert result["normalized_answer"] == answer
    assert result["status"] == "scored"
    assert result["score"] == 1.0
    assert result["details"]["instruction_results"][0]["instruction_id"] == "fixture"


def test_primary_scorer_is_separate_from_native():
    assert primary_scorer("gsm8k").startswith("product.")
    assert "ifeval" in primary_scorer("ifeval")
    with pytest.raises(ValueError):
        primary_scorer("boolq")


def test_public_parser_is_independent_of_sdk_and_scoring(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("pure parsing must not construct an SDK request")

    monkeypatch.setattr(starter_native, "build_request", forbidden)
    case = {"suite": "mmlu", "task": "test"}
    assert parse_system_answer(case, "Reasoning.\nFinal answer: B") == {
        "status": "parsed", "value": "B", "reason": None, "extraction_version": EXTRACTION_VERSION,
    }
    result = parse_system_answer(case, "The last letter is B.")
    assert result["status"] == "unparsed"
    assert result["value"] is None
    assert result["reason"]


def test_ifeval_public_parser_preserves_body_and_excludes_label_coverage():
    body = "  Full body.\r\n[1] citation\n"
    result = parse_system_answer({"suite": "ifeval"}, body)
    assert result["status"] == "not_applicable"
    assert result["value"].encode() == body.encode()
    assert result["extraction_version"] == EXTRACTION_VERSION
