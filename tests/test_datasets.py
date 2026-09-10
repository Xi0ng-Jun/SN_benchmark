import json

from rag_eval.datasets import load_dataset, load_jsonl


def test_load_jsonl_ignores_blank_lines(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text('{"id": "a"}\n\n{"id": "b"}\n', encoding="utf-8")

    assert load_jsonl(path) == [{"id": "a"}, {"id": "b"}]


def test_load_crud_rag_joins_question_annotation_and_documents(tmp_path):
    root = tmp_path / "crud-rag" / "full"
    root.mkdir(parents=True)
    (root / "questions.jsonl").write_text(
        json.dumps({"id": "q1", "question": "问题", "language": "zh"}) + "\n",
        encoding="utf-8",
    )
    (root / "annotations.jsonl").write_text(
        json.dumps(
            {
                "question_id": "q1",
                "references": ["参考答案"],
                "unanswerable": False,
                "metadata": {"evidence_document_ids": ["d1"]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "documents.jsonl").write_text(
        json.dumps({"id": "d1", "contents": "证据文本"}) + "\n",
        encoding="utf-8",
    )

    rows = load_dataset(root)

    assert rows[0]["question"] == "问题"
    assert rows[0]["expected_answer"] == "参考答案"
    assert rows[0]["gold_document_ids"] == ["d1"]
    assert rows[0]["gold_context"] == ["证据文本"]


def test_load_scifact_uses_relevance_document_ids(tmp_path):
    root = tmp_path / "scifact" / "full"
    root.mkdir(parents=True)
    (root / "questions.jsonl").write_text(
        json.dumps({"id": "1", "question": "claim"}) + "\n", encoding="utf-8"
    )
    (root / "annotations.jsonl").write_text(
        json.dumps(
            {
                "question_id": "1",
                "references": [],
                "relevance": {"doc-1": 1},
                "unanswerable": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "documents.jsonl").write_text(
        json.dumps({"id": "doc-1", "title": "T", "abstract": ["A", "B"]})
        + "\n",
        encoding="utf-8",
    )

    rows = load_dataset(root)

    assert rows[0]["gold_document_ids"] == ["doc-1"]
    assert rows[0]["gold_context"] == ["T\nA\nB"]
