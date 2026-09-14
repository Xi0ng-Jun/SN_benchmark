"""Unexecuted regression specifications for the public system adapter."""
from copy import deepcopy
import json

import pytest

from rag_eval.public_expansion_protocol import EXPANSION_SUITES, EXPANSION_VERSION, native_protocol
from rag_eval.public_expansion_sources import make_expansion_case
from rag_eval.starter_protocol import SUITES, VERSION, fingerprint, make_case
from rag_eval.system_product import SYSTEM_SUITES, SYSTEM_VERSION, build_system_bundle


ROWS = {
    "logiqa": {"id": "logic", "text": "All robins are birds.", "question": "What follows?",
               "options": ["A robin is a bird", "No birds", "No robins", "Nothing"], "answer": 0},
    "gsm8k": {"question": "How many apples are in 12 boxes of 100?", "answer": "SECRET_WORK\n#### 1,200"},
    "mmlu": {"question": "Which value equals two plus two?", "choices": ["3", "4", "5", "6"],
             "answer": 1, "subject": "elementary_mathematics"},
    "truthfulqa": {"question": "Which is true?", "mc1_targets": {
        "choices": ["First candidate", "Second candidate", "Third candidate", "Fourth candidate"],
        "labels": [1, 0, 0, 0]}},
    "hellaswag": {"ctx": "A person holds a brush.", "endings": ["a", "b", "c", "d"],
                  "label": "2", "activity_label": "Applying sunscreen"},
    "bbh": {"input": "Evaluate True and False.", "target": "False", "task_name": "boolean_expressions"},
    "ifeval": {"key": 1, "prompt": "  Write a line.\r\nKeep it brief.  \n",
               "instruction_id_list": ["length_constraints:number_words"], "kwargs": [{"num_words": 20}]},
}


def case_source(suite, row=None, index=0, task=None):
    row = deepcopy(ROWS[suite] if row is None else row)
    row["explanation"] = "SECRET_EXPLANATION"
    row["metadata"] = {"answer": "SECRET_METADATA"}
    if suite in EXPANSION_SUITES:
        case = make_expansion_case(suite, row, index, revision="fixture-v1",
                                   source_file_sha256="a" * 64, source_line_sha256="b" * 64)
        source = {"protocol_version": EXPANSION_VERSION, "suite": suite,
                  "source": {"revision": "fixture-v1", "export_sha256": "a" * 64},
                  "native_protocol": native_protocol(suite), "n_shots": case["n_shots"],
                  "scorer": EXPANSION_SUITES[suite]["scorer"], "applicability": EXPANSION_SUITES[suite],
                  "release_gate": False, "case_fingerprints": {case["case_id"]: fingerprint(case)}}
    else:
        case = make_case(suite, row, index, task or suite)
        source = {"protocol_version": VERSION, "suite": suite}
    return case, source


@pytest.mark.parametrize("suite", ROWS)
def test_only_whitelisted_task_fields_reach_documents_and_ask(suite):
    case, source = case_source(suite)
    original = deepcopy(case)
    bundle = build_system_bundle([case], source)
    generated = json.dumps([bundle["documents"], bundle["questions"][0]["question"]])
    assert "SECRET_" not in generated
    assert case == original
    question = bundle["questions"][0]
    assert {"id", "dataset", "split", "question", "expected_answer", "references", "gold_document_ids",
            "case_id", "sample_id", "suite", "task", "product_protocol", "material_role"} <= question.keys()
    assert question["product_protocol"] == SYSTEM_VERSION
    assert bool(question["gold_document_ids"]) is (suite == "logiqa")
    assert bundle["decisions"][0]["status"] == "applicable"
    assert bundle["decisions"][0]["basis"] == "machine_field_extraction"
    assert "reviewer" not in bundle["decisions"][0]
    if suite != "ifeval":
        assert "Final answer:" in question["question"]


def test_registry_supersedes_history_without_mutating_it():
    assert set(SYSTEM_SUITES) == set(ROWS)
    assert all(info["product"] is True for info in SYSTEM_SUITES.values())
    assert SUITES["logiqa"]["product"] is False
    assert all(info["product"] is False for info in EXPANSION_SUITES.values())


def test_truthfulqa_choice_order_is_the_frozen_seed42_permutation():
    case, source = case_source("truthfulqa")
    bundle = build_system_bundle([case], source)
    assert case["choice_order"] == [2, 1, 3, 0]
    for text in [bundle["documents"][0]["text"], bundle["questions"][0]["question"]]:
        assert "1. Third candidate\n2. Second candidate\n3. Fourth candidate\n4. First candidate" in text
        assert "candidate" in text.lower()
    assert bundle["questions"][0]["expected_answer"] == "4"


@pytest.mark.parametrize("suite", ["logiqa", "gsm8k", "mmlu", "truthfulqa", "hellaswag", "bbh"])
def test_changing_answer_annotation_cannot_change_generator_inputs(suite):
    row = deepcopy(ROWS[suite])
    first, first_source = case_source(suite, row)
    if suite in {"logiqa", "mmlu"}:
        row["answer"] = (row["answer"] + 1) % 4
    elif suite == "gsm8k":
        row["answer"] = "A different SECRET_WORK solution\n#### 9,876"
    elif suite == "truthfulqa":
        row["mc1_targets"]["labels"] = [0, 1, 0, 0]
    elif suite == "hellaswag":
        row["label"] = "1"
    else:
        row["target"] = "True"
    second, second_source = case_source(suite, row)
    original = build_system_bundle([first], first_source)
    relabeled = build_system_bundle([second], second_source)
    assert original["documents"] == relabeled["documents"]
    assert original["questions"][0]["question"] == relabeled["questions"][0]["question"]
    assert original["questions"][0]["expected_answer"] != relabeled["questions"][0]["expected_answer"]


def test_manifest_binds_exact_inputs_and_retains_requested_denominator():
    case, source = case_source("gsm8k")
    bundle = build_system_bundle([case], source)
    manifest = bundle["manifest"]
    assert manifest["suite"] == "gsm8k"
    assert manifest["protocol_version"] == manifest["product_protocol"] == SYSTEM_VERSION
    assert manifest["questions_sha256"] == fingerprint(bundle["questions"])
    assert manifest["documents_sha256"] == fingerprint(bundle["documents"])
    assert manifest["decisions_sha256"] == fingerprint(bundle["decisions"])
    assert manifest["planned_case_count"] == manifest["question_count"] == 1
    assert manifest["gold_document_count"] == 0


def test_ifeval_prompt_is_identical_including_all_whitespace():
    case, source = case_source("ifeval")
    bundle = build_system_bundle([case], source)
    original = case["raw_row"]["prompt"]
    assert bundle["questions"][0]["question"].encode() == original.encode()
    assert bundle["documents"][0]["text"].encode() == original.encode()


def test_logiqa_shared_source_retains_both_task_memberships():
    first, source = case_source("logiqa", task="Necessary Conditional Reasoning")
    second, _ = case_source("logiqa", task="Sufficient Conditional Reasoning")
    bundle = build_system_bundle([first, second], source)
    assert len(bundle["questions"]) == len(bundle["decisions"]) == 2
    assert len(bundle["documents"]) == 1
    assert len({q["id"] for q in bundle["questions"]}) == 2
    assert bundle["documents"][0]["text"] == first["raw_row"]["text"]
    assert first["raw_row"]["text"] not in bundle["questions"][0]["question"]


@pytest.mark.parametrize("field,value", [("question", "edited"), ("references", ["D"]),
                                        ("scorer", "edited"), ("n_shots", 3)])
@pytest.mark.parametrize("suite", ["logiqa", "gsm8k"])
def test_modified_case_rejected_before_preparation(suite, field, value):
    case, source = case_source(suite)
    case[field] = value
    with pytest.raises(ValueError, match="frozen|canonical|source row"):
        build_system_bundle([case], source)


def test_document_cap_fails_without_truncating_selection():
    cases = []
    for index in range(41):
        row = deepcopy(ROWS["logiqa"])
        row.update(id=index, text=f"Distinct passage {index}")
        case, source = case_source("logiqa", row, index=index)
        cases.append(case)
    with pytest.raises(ValueError, match="limit|cap"):
        build_system_bundle(cases, source)


@pytest.mark.parametrize("task,target,role", [
    ("boolean_expressions", "True", "self_contained_task"),
    ("sports_understanding", "yes", "commonsense_task"),
    ("movie_recommendation", "(A)", "commonsense_task"),
])
def test_bbh_material_semantics_are_task_specific(task, target, role):
    case, source = case_source("bbh", {"input": "Original task", "target": target, "task_name": task})
    bundle = build_system_bundle([case], source)
    assert bundle["questions"][0]["material_role"] == role
    assert bundle["manifest"]["material_roles"] == [role]
    assert bundle["questions"][0]["gold_document_ids"] == []
