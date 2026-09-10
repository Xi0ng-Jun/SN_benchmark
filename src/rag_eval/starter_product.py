"""Product input preparation and deterministic BoolQ checks; no product imports."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re

from .public_benchmarks import normalize_drop, normalize_squad, sha256_text
from .starter_protocol import VERSION, fingerprint, make_case, require_text

BOOLQ_REQUIREMENT = (
    "Answer using the notebook sources. The first nonempty line must be exactly "
    "'Final answer: Yes' or 'Final answer: No'. Then explain briefly with citations. "
    "If the sources are insufficient, say so instead of guessing a Yes/No answer."
)


def document(passage, suite):
    require_text(passage, "passage")
    text_hash = sha256_text(passage)
    identity = f"{suite}-passage-{text_hash}"
    # Neutral metadata never exposes a reference answer or gold/distractor role.
    title = f"Public passage {text_hash[:12]}"
    return {"id": identity, "title": title, "text": passage, "text_sha256": text_hash,
            "document_sha256": fingerprint({"id": identity, "title": title, "text": passage})}


def product_bundle(cases, reviews, distractor_passages, *, max_documents=40):
    """Convert reviewed SQuAD/DROP/BoolQ cases into existing runtime input shapes.

    reviews: {sample_id: {status: approved|excluded|pending, reason, reviewer,
                         raw_row_sha256}} supplied by a human, never inferred here.
    distractor_passages: same-dataset original passages in frozen source order.
    DROP needs official complete annotations; flattened SDK spans alone are not
    promoted to product completeness gold. Exclusions remain in the returned log.
    """
    suites = {c["suite"] for c in cases}
    if len(suites) != 1 or not suites <= {"squad", "drop", "boolq"}:
        raise ValueError("Exactly one supported product suite required")
    if type(max_documents) is not int or not 1 <= max_documents <= 40:
        raise ValueError("Starter corpus limit must be in 1..40")
    suite = next(iter(suites))
    questions, decisions, documents, seen = [], [], {}, set()
    unknown = set(reviews) - {c["sample_id"] for c in cases}
    if unknown:
        raise ValueError("Review contains sample IDs outside this frozen selection")
    for case in cases:
        canonical = make_case(suite, case["raw_row"], case["source_row_index"], case["task"])
        if any(case.get(k) != canonical[k] for k in ("sample_id", "case_id", "question", "passage", "raw_row_sha256")):
            raise ValueError("Product case differs from the frozen public row")
        sample_id = case["sample_id"]
        if sample_id in seen:
            continue
        seen.add(sample_id)
        review = reviews.get(sample_id, {"status": "pending", "reason": "human suitability review required"})
        status = review.get("status")
        if status not in {"approved", "excluded", "pending"}:
            raise ValueError("Unknown human review status")
        if status != "pending":
            require_text(review.get("reviewer"), "reviewer")
            require_text(review.get("reason"), "review reason")
            if review.get("raw_row_sha256") != case["raw_row_sha256"]:
                raise ValueError("Human review refers to different source content")
        if status != "approved":
            decisions.append({"sample_id": sample_id, **review})
            continue
        raw = deepcopy(case["raw_row"])
        if fingerprint(raw) != case["raw_row_sha256"]:
            raise ValueError("Frozen raw row changed")
        if suite == "drop" and not raw.get("_annotations"):
            decisions.append({"sample_id": sample_id, "status": "not_applicable",
                              "reason": "complete official DROP annotations not attached; SDK flattened spans alone are insufficient"})
            continue
        doc = document(case["passage"], suite)
        documents.setdefault(doc["id"], doc)
        raw["_document_id"] = doc["id"]
        if suite in {"squad", "drop"}:
            question = (normalize_squad if suite == "squad" else normalize_drop)(raw, "starter")
        else:
            reference = "Yes" if raw["answer"] else "No"
            question = {"id": sample_id, "dataset": "boolq", "question": raw["question"],
                        "references": [reference], "expected_answer": reference,
                        "gold_document_ids": [doc["id"]], "split": "starter",
                        "group": doc["id"], "answer_type": "boolean", "evidence": [],
                        "diagnostic": False, "calibration": False, "smoke": False}
        question.update(protocol_version=VERSION, track="R", suite=suite,
                        case_id=case["case_id"], sample_id=sample_id,
                        original_question=case["question"], product_review=deepcopy(review))
        if suite == "boolq":
            question["question"] = case["question"] + "\n\n" + BOOLQ_REQUIREMENT
            question["answer_check"] = "product.boolq.explicit_conclusion.v1"
        questions.append(question)
        decisions.append({"sample_id": sample_id, **review})
    if len(documents) > max_documents:
        raise ValueError("Gold passages exceed corpus limit; revise the selection explicitly")
    gold_count = len(documents)
    # Materialize to record the exact distractor input identity, even if capacity is full.
    distractor_passages = list(distractor_passages)
    for passage in distractor_passages:
        if len(documents) >= max_documents:
            break
        doc = document(passage, suite)
        documents.setdefault(doc["id"], doc)
    return {"questions": questions, "documents": list(documents.values()), "decisions": decisions,
            "manifest": {"protocol_version": VERSION, "suite": suite, "track": "R",
                         "question_count": len(questions), "document_count": len(documents),
                         "gold_document_count": gold_count, "distractor_document_count": len(documents) - gold_count,
                         "requested_document_cap": max_documents,
                         "unfilled_document_slots": max_documents - len(documents),
                         "questions_sha256": fingerprint(questions), "documents_sha256": fingerprint(list(documents.values())),
                         "reviews_sha256": fingerprint(reviews), "distractor_input_sha256": fingerprint(distractor_passages),
                         "distractor_provenance": "caller must bind same-dataset source manifest in protocol_id",
                         "conversation": [], "memory_injection": False,
                         "status": "prepared_not_validated"}}


def check_boolq(answer, expected, *, product_status="success"):
    if expected not in {"Yes", "No"}:
        raise ValueError("Expected BoolQ label must be Yes or No")
    if product_status != "success":
        return {"status": "unscored", "score": None, "label": None,
                "reason": "product did not return a normal answer", "raw_answer": answer}
    if not isinstance(answer, str) or not answer.strip():
        return {"status": "unparsed", "score": None, "label": None, "reason": "empty answer", "raw_answer": answer}
    first_line = next(line.strip() for line in answer.splitlines() if line.strip())
    match = re.fullmatch(r"Final answer: (Yes|No)", first_line)
    labels = {s.lower() for s in re.findall(r"\b(?:yes|no)\b", answer, flags=re.IGNORECASE)}
    if match is None or len(labels) != 1:
        return {"status": "unparsed", "score": None, "label": None,
                "reason": "missing exact first-line conclusion or both labels present", "raw_answer": answer}
    label = match[1]
    return {"status": "scored", "score": float(label == expected), "label": label,
            "raw_answer": answer, "reason": None,
            "limitation": "label match only; explanation, contradictions and citations need separate review"}


def isolation_plan(run_root, product_root, suite, mode):
    """Describe paths/overrides only; starter_runtime enforces execution isolation."""
    if suite not in {"squad", "drop", "boolq"} or mode not in {"chunk", "reasoning"}:
        raise ValueError("Unsupported product cell")
    root, product = Path(run_root).resolve(), Path(product_root).resolve()
    if root.is_relative_to(product) or product.is_relative_to(root):
        raise ValueError("Evaluation root must be separate from the product directory")
    cell = root / suite / mode
    private = cell / "runtime"
    return {"cell": str(cell), "requires_fresh_runtime": True, "conversation": [],
            "source_scope": "entire frozen corpus, never per-question gold",
            "overrides": {
                "DATABASE_URL": "sqlite:///" + str(private / "database.db"),
                "SILICON_NOTEBOOK_STORAGE_DIR": str(private / "storage"),
                "LLM_CACHE_PATH": str(private / "llm-cache.db"),
                "EVENT_LOG_DIR": str(private / "logs"), "LLM_LOG_PATH": str(private / "logs/llm.jsonl"),
                "LLM_CACHE_ENABLED": "false", "AGENT_PROFILE_ENABLED": "false",
                "USER_SEARCH_PROFILE_ENABLED": "false", "RETRIEVAL_EXPERIENCE_ENABLED": "false",
                "RETRIEVAL_EXPERIENCE_INJECT_ENABLED": "false", "REASONING_CONSULT_MEMORY_ENABLED": "false",
                "GENERATED_QUESTION_INDEX_MODE": "off", "CHUNK_KG_OVERLAY_ENABLED": "false", "KG_AUTO_EXTRACT": "false"}}
