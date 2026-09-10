import json

from rag_eval.public_benchmarks import normalize_drop, normalize_squad, build_bundle, _stratified_take


def test_squad_keeps_distinct_references_and_offsets():
    row = {"id": "q1", "title": "Article", "context": "A blue fox.", "question": "What color?", "answers": {"text": ["blue", "blue"], "answer_start": [2, 2]}}
    got = normalize_squad(row, "debug", True)
    assert got["references"] == ["blue"]
    assert got["evidence"][0] == {"text": "blue", "start": 2, "end": 6}
    assert len(got["evidence"]) == 2
    assert got["gold_document_ids"] == ["squad-Article-q1"]


def test_squad_accepts_official_list_of_answer_objects():
    row = {"id": "q1", "title": "Article", "context": "A blue fox.", "question": "What color?", "answers": [{"text": "blue", "answer_start": 2}]}
    assert normalize_squad(row, "debug")["references"] == ["blue"]


def test_drop_preserves_multi_span_answer_as_one_reference():
    row = {"section_id": "s1", "query_id": "q1", "passage": "Alpha scored twice. Beta scored once.", "question": "Who scored?", "answers_spans": {"spans": ["Alpha", "Beta"], "types": ["spans", "spans"]}}
    got = normalize_drop(row, "regression", False)
    assert got["references"] == ["Alpha; Beta"]
    assert got["answer_type"] == "spans"
    assert got["gold_document_ids"] == ["drop-s1"]


def test_build_bundle_rejects_duplicate_passage_selection(tmp_path):
    rows = [{"id": "q1", "title": "A", "context": "text", "question": "q", "answers": {"text": ["x"], "answer_start": [0]}}]
    try:
        build_bundle(rows + rows, [], tmp_path, seed=1)
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate passages must be rejected")


def test_stratified_take_retains_rare_answer_types():
    rows = [{"kind": "number", "id": str(i)} for i in range(20)] + [{"kind": "date", "id": "d"}]
    selected, remaining = _stratified_take(rows, 5, lambda row: row["kind"])
    assert {row["kind"] for row in selected} == {"number", "date"}
    assert len(selected) == 5 and len(remaining) == 16
