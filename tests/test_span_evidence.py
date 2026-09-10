from rag_eval.span_evidence import check_squad_spans


QUESTION = {
    "dataset": "squad", "gold_document_ids": ["doc-1"],
    "evidence": [{"text": "blue", "start": 2, "end": 6}],
}
DOCUMENTS = {"doc-1": {"id": "doc-1", "text": "A blue fox."}}


def test_span_is_supported_only_in_context_from_gold_document():
    record = {**QUESTION, "context_supported": True,
              "retrieval_context": ["k1: blue", "k2: irrelevant"],
              "retrieved_document_ids": ["doc-1", "doc-2"]}
    result = check_squad_spans(record, DOCUMENTS)
    assert result["status"] == "supported"
    assert result["span_supported"] is True
    assert result["support_evidence"] == [{"document_id": "doc-1", "text": "blue", "start": 2, "end": 6, "context_index": 0}]


def test_same_text_from_distractor_does_not_support_gold_span():
    record = {**QUESTION, "context_supported": True, "retrieval_context": ["k1: blue"],
              "retrieved_document_ids": ["doc-2"]}
    result = check_squad_spans(record, DOCUMENTS)
    assert result["status"] == "unsupported"
    assert result["span_supported"] is False


def test_missing_context_mapping_is_unknown():
    record = {**QUESTION, "context_supported": True, "retrieval_context": ["k1: blue"]}
    result = check_squad_spans(record, DOCUMENTS)
    assert result == {"status": "unknown", "span_supported": None, "support_evidence": [],
                      "reason": "context_document_mapping_unavailable"}


def test_invalid_frozen_offset_is_rejected():
    record = {**QUESTION, "evidence": [{"text": "green", "start": 2, "end": 7}],
              "context_supported": True, "retrieval_context": ["k1: green"],
              "retrieved_document_ids": ["doc-1"]}
    result = check_squad_spans(record, DOCUMENTS)
    assert result["status"] == "unknown"
    assert result["reason"] == "gold_span_offset_mismatch"


def test_capture_mapping_ignores_prefix_before_first_handle():
    record = {**QUESTION, "context_supported": True,
              "retrieval_context": ["instructions\n", "k1: blue"],
              "source_to_document": {"source-1": "doc-1"},
              "captures": [{"succeeded": True, "sectioned": False, "answer": "blue",
                            "context_block": "instructions\nk1: blue",
                            "id_map": {"k1": {"source_id": "source-1", "object_id": "chunk-1"}}}]}
    result = check_squad_spans(record, DOCUMENTS)
    assert result["status"] == "supported"
    assert result["support_evidence"][0]["context_index"] == 1
