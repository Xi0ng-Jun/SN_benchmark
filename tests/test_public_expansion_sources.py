import json
from pathlib import Path

import pytest

from rag_eval.public_expansion_sources import read_source, select_expansion_cases
from rag_eval.starter_protocol import fingerprint


def write_jsonl(tmp_path: Path, row: dict) -> Path:
    path = tmp_path / "source.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return path


def test_mmlu_maps_numeric_choice_to_label_and_preserves_provenance(tmp_path):
    path = write_jsonl(tmp_path, {"question": "2+2?", "choices": ["3", "4", "5", "6"], "answer": 1, "subject": "elementary_mathematics"})
    record = read_source("mmlu", path, revision="v1.0") [0]
    assert record["expected_answer"] == "B"
    assert record["task"] == "elementary_mathematics"
    assert record["source_row_index"] == 0
    assert record["raw_record"]["answer"] == 1
    assert len(record["source_file_sha256"]) == 64
    assert record["raw_record_sha256"] == fingerprint(record["raw_record"])
    assert len(record["source_line_sha256"]) == 64


def test_missing_or_moving_revision_is_rejected(tmp_path):
    path = write_jsonl(tmp_path, {"question": "2+2?", "choices": ["3", "4", "5", "6"], "answer": 1})
    with pytest.raises(ValueError, match="revision"):
        read_source("mmlu", path, revision="")
    with pytest.raises(ValueError, match="fixed"):
        read_source("mmlu", path, revision="main")


def test_mmlu_rejects_bool_answer_and_empty_fields(tmp_path):
    path = write_jsonl(tmp_path, {"question": "", "choices": ["3", "4", "5", "6"], "answer": True})
    with pytest.raises(ValueError):
        read_source("mmlu", path, revision="v1")


def test_gsm8k_extracts_final_answer(tmp_path):
    path = write_jsonl(tmp_path, {"question": "How many?", "answer": "work\n#### 1,200"})
    assert read_source("gsm8k", path, revision="v1")[0]["expected_answer"] == "1,200"


def test_gsm8k_rejects_missing_or_non_numeric_final_answer(tmp_path):
    for answer in ("####", "unknown", "work\n#### nope"):
        path = write_jsonl(tmp_path, {"question": "How many?", "answer": answer})
        with pytest.raises(ValueError, match="numeric"):
            read_source("gsm8k", path, revision="v1")


def test_other_expansion_shapes_are_supported(tmp_path):
    truthful = write_jsonl(tmp_path, {"question": "Q", "mc1_targets": {"choices": ["right", "wrong1", "wrong2", "wrong3"], "labels": [1, 0, 0, 0]}})
    assert read_source("truthfulqa", truthful, revision="v1")[0]["expected_answer"] == "4"
    hellaswag = write_jsonl(tmp_path, {"ctx": "C", "endings": ["a", "b", "c", "d"], "label": " 2 ", "activity_label": "Applying sunscreen"})
    assert read_source("hellaswag", hellaswag, revision="v1")[0]["expected_answer"] == "C"
    bbh = write_jsonl(tmp_path, {"input": "I", "target": "True", "task_name": "boolean_expressions"})
    assert read_source("bbh", bbh, revision="v1")[0]["task"] == "boolean_expressions"


def test_hellaswag_rejects_bool_and_out_of_range_labels(tmp_path):
    for label in (True, 4, "4", "2.0"):
        path = write_jsonl(tmp_path, {"ctx": "C", "endings": ["a", "b", "c", "d"], "label": label})
        with pytest.raises(ValueError):
            read_source("hellaswag", path, revision="v1")


def test_hellaswag_rejects_unicode_digit_with_normalized_error(tmp_path):
    path = write_jsonl(tmp_path, {"ctx": "C", "endings": ["a", "b", "c", "d"], "label": "²"})
    with pytest.raises(ValueError, match=r"HellaSwag label must be an index 0\.\.3"):
        read_source("hellaswag", path, revision="v1")


@pytest.mark.parametrize("targets", [
    None,
    {"choices": ["a", "b"], "labels": [1, 1]},
    {"choices": ["a", "b"], "labels": [1]},
    {"choices": ["a", "b"], "labels": [True, False]},
])
def test_truthfulqa_rejects_generation_and_invalid_mc1_labels(tmp_path, targets):
    row = {"question": "Q", "best_answer": "a"}
    if targets is not None:
        row["mc1_targets"] = targets
    with pytest.raises(ValueError, match="TruthfulQA"):
        read_source("truthfulqa", write_jsonl(tmp_path, row), revision="v1")


@pytest.mark.parametrize("row", [
    {"input": "I", "target": "yes"},
    {"input": "I", "target": "yes", "task_name": "invented"},
    {"input": "I", "target": "yes", "task_name": "boolean_expressions"},
    {"input": "I", "target": "(G)", "task_name": "date_understanding"},
])
def test_bbh_rejects_missing_task_and_incompatible_answer_kind(tmp_path, row):
    with pytest.raises(ValueError, match="BBH"):
        read_source("bbh", write_jsonl(tmp_path, row), revision="v1")


def test_selection_records_only_supplied_tasks_and_preserves_order(tmp_path):
    path = tmp_path / "bbh.jsonl"
    rows = [
        {"input": "I", "target": "True", "task_name": "boolean_expressions"},
        {"input": "J", "target": "(F)", "task_name": "date_understanding"},
        {"input": "K", "target": "False", "task_name": "boolean_expressions"},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    cases, selection = select_expansion_cases("bbh", path, revision="v1")
    assert [case["raw_row"] for case in cases] == rows
    assert selection["selected_tasks"] == ["boolean_expressions", "date_understanding"]
    assert selection["task_counts"] == {"boolean_expressions": 2, "date_understanding": 1}
    assert selection["coverage"] == "local_selection_only"
    assert [case["answer_type"] for case in cases] == ["boolean", "choice_6", "boolean"]
