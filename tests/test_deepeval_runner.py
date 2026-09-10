import json

import pytest

from rag_eval.deepeval_runner import load_result_records


def test_load_result_records_requires_adapter_contract(tmp_path):
    path = tmp_path / "results.jsonl"
    path.write_text(json.dumps({"question": "q", "answer": "a"}) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="retrieval_context"):
        load_result_records(path)


def test_load_result_records_preserves_retrieval_ids(tmp_path):
    path = tmp_path / "results.jsonl"
    path.write_text(
        json.dumps(
            {
                "question": "q",
                "answer": "a",
                "expected_answer": "e",
                "retrieval_context": ["ctx"],
                "retrieved_ids": ["d1"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert load_result_records(path)[0]["retrieved_ids"] == ["d1"]
