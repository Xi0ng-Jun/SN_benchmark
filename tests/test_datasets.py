from rag_eval.datasets import load_jsonl


def test_load_jsonl_ignores_blank_lines(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text('{"id": "a"}\n\n{"id": "b"}\n', encoding="utf-8")

    assert load_jsonl(path) == [{"id": "a"}, {"id": "b"}]
