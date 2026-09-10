"""Deterministic SQuAD answer-span support in actually delivered context."""

from .protocol import capture_context


def _unknown(reason):
    return {"status": "unknown", "span_supported": None, "support_evidence": [], "reason": reason}


def _context_documents(record):
    contexts = record.get("retrieval_context")
    documents = record.get("retrieved_document_ids")
    if isinstance(contexts, list) and isinstance(documents, list) and len(contexts) == len(documents):
        return list(zip(contexts, documents, range(len(contexts))))

    captures = [capture for capture in record.get("captures", [])
                if capture.get("succeeded") and str(capture.get("answer", "")).strip()
                and not capture.get("sectioned")]
    source_to_document = record.get("source_to_document")
    if not captures or not isinstance(source_to_document, dict):
        return None
    captured = capture_context(captures[-1])
    if len(captured["retrieval_context"]) != len(captured["source_ids"]):
        # capture_context may retain a pre-handle prefix as an extra context.
        prefix_count = len(captured["retrieval_context"]) - len(captured["source_ids"])
    else:
        prefix_count = 0
    if prefix_count not in (0, 1):
        return None
    result = []
    for index, (context, source) in enumerate(zip(captured["retrieval_context"][prefix_count:], captured["source_ids"]), prefix_count):
        document = source_to_document.get(source)
        if document:
            result.append((context, document, index))
    return result or None


def check_squad_spans(record, documents_by_id):
    """Check exact original offsets against context mapped to the gold source.

    This proves only that an annotated SQuAD answer span reached synthesis. It
    does not claim complete evidence coverage or reasoning support.
    """
    if record.get("dataset") != "squad":
        return _unknown("not_squad")
    if not record.get("context_supported"):
        return _unknown("actual_context_unavailable")
    gold_ids = record.get("gold_document_ids")
    evidence = record.get("evidence")
    if not isinstance(gold_ids, list) or not gold_ids or not isinstance(evidence, list) or not evidence:
        return _unknown("gold_span_unavailable")
    validated = []
    for document_id in gold_ids:
        document = documents_by_id.get(document_id)
        if not isinstance(document, dict) or not isinstance(document.get("text"), str):
            return _unknown("gold_document_unavailable")
        for span in evidence:
            start, end, text = span.get("start"), span.get("end"), span.get("text")
            if not isinstance(start, int) or not isinstance(end, int) or document["text"][start:end] != text:
                return _unknown("gold_span_offset_mismatch")
            validated.append((document_id, text, start, end))
    mapped = _context_documents(record)
    if mapped is None:
        return _unknown("context_document_mapping_unavailable")
    support = []
    for document_id, text, start, end in validated:
        for context, actual_document, index in mapped:
            if actual_document == document_id and text in context:
                support.append({"document_id": document_id, "text": text, "start": start,
                                "end": end, "context_index": index})
    return {"status": "supported" if support else "unsupported",
            "span_supported": bool(support), "support_evidence": support, "reason": None}
