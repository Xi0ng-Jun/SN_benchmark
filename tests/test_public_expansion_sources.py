import json
from pathlib import Path

import pytest

from rag_eval.public_expansion_sources import read_source


def write_jsonl(tmp_path: Path, row: dict) -> Path:
    path = tmp_path / "source.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return path


def test_mmlu_maps_numeric_choice_to_label_and_preserves_provenance(tmp_path):
    path = write_jsonl(tmp_path, {"question": "2+2?", "choices": ["3", "4", "5", "6"], "answer": 1, "subject": "arithmetic"})
    record = read_source("mmlu", path, revision="v1.0") [0]
    assert record["expected_answer"] == "B"
    assert record["task"] == "arithmetic"
    assert record["source_row_index"] == 0
    assert record["raw_record"]["answer"] == 1
    assert len(record["source_file_sha256"]) == 64


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
    assert read_source("gsm8k", path, revision="v1")[0]["expected_answer"] == "1200"


def test_gsm8k_rejects_missing_or_non_numeric_final_answer(tmp_path):
    for answer in ("####", "unknown", "work\n#### nope"):
        path = write_jsonl(tmp_path, {"question": "How many?", "answer": answer})
        with pytest.raises(ValueError, match="numeric"):
            read_source("gsm8k", path, revision="v1")


def test_other_expansion_shapes_are_supported(tmp_path):
    truthful = write_jsonl(tmp_path, {"question": "Q", "best_answer": "A", "category": "cat"})
    assert read_source("truthfulqa", truthful, revision="v1")[0]["expected_answer"] == "A"
    hellaswag = write_jsonl(tmp_path, {"ctx": "C", "endings": ["a", "b", "c", "d"], "label": " 2 "})
    assert read_source("hellaswag", hellaswag, revision="v1")[0]["expected_answer"] == "C"
    bbh = write_jsonl(tmp_path, {"input": "I", "target": "yes", "task_name": "boolean_expressions"})
    assert read_source("bbh", bbh, revision="v1")[0]["task"] == "boolean_expressions"


def test_hellaswag_rejects_bool_and_out_of_range_labels(tmp_path):
    for label in (True, 4, "4", "2.0"):
        path = write_jsonl(tmp_path, {"ctx": "C", "endings": ["a", "b", "c", "d"], "label": label})
        with pytest.raises(ValueError):
            read_source("hellaswag", path, revision="v1")


def test_hellaswag_rejects_unicode_digit_with_normalized_error(tmp_path):
    path = write_jsonl(tmp_path, {"ctx": "C", "endings": ["a", "b", "c", "d"], "label": "²"})
    with pytest.raises(ValueError, match="HellaSwag label must be an index 0\.\.3"):
        read_source("hellaswag", path, revision="v1")
