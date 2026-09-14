"""Versioned public task materials; preparation never imports DeepEval or SN.

Only whitelisted question fields reach a document or Ask prompt. Frozen labels
stay on the evaluation side, and historical Native applicability is untouched.
"""
from __future__ import annotations

from copy import deepcopy

from .public_expansion_protocol import BBH_TASK_KINDS, EXPANSION_SUITES, EXPANSION_VERSION, native_protocol
from .public_expansion_sources import make_expansion_case, truthfulqa_choice_order
from .starter_product import document
from .starter_protocol import VERSION, fingerprint, make_case

SYSTEM_VERSION = "sn-public-system-v1"

# This live registry is independent of the frozen Native suite metadata.
SYSTEM_SUITES = {
    "logiqa": {"product": True, "document_fields": ["text"],
               "question_fields": ["question", "options"], "material_role": "passage_evidence"},
    "gsm8k": {"product": True, "document_fields": ["question"],
              "question_fields": ["question"], "material_role": "self_contained_task"},
    "bbh": {"product": True, "document_fields": ["input"],
            "question_fields": ["input"], "material_role": "task_specific"},
    "mmlu": {"product": True, "document_fields": ["question", "choices"],
             "question_fields": ["question", "choices"], "material_role": "knowledge_task"},
    "truthfulqa": {"product": True, "document_fields": ["question", "mc1_targets.choices"],
                   "question_fields": ["question", "mc1_targets.choices"], "material_role": "knowledge_task"},
    "hellaswag": {"product": True, "document_fields": ["ctx", "endings"],
                  "question_fields": ["ctx", "endings"], "material_role": "commonsense_task"},
    "ifeval": {"product": True, "document_fields": ["prompt"],
               "question_fields": ["prompt"], "material_role": "instruction_task"},
}

# Language/world knowledge tasks are separated from tasks whose stated symbolic
# conditions determine the result. Neither category is gold factual evidence.
_BBH_COMMONSENSE_TASKS = frozenset({
    "causal_judgement", "disambiguation_qa", "hyperbaton", "movie_recommendation",
    "ruin_names", "salient_translation_error_detection", "snarks", "sports_understanding",
})
MATERIAL_SEMANTICS = {
    "passage_evidence": "Original passage supplies reasoning premises; source coverage is diagnostic, not proof of a correct inference.",
    "self_contained_task": "Task conditions are self-contained; importing the task does not create gold answer evidence.",
    "commonsense_task": "Task requires language or commonsense knowledge; candidates are alternatives, not asserted facts.",
    "knowledge_task": "Knowledge question and candidate choices; task citations cannot establish factual correctness.",
    "instruction_task": "Original instructions only; evaluate the complete response body, not retrieval truthfulness.",
}


def _canonical(case, source):
    """Rebuild executable fields from the raw row without loading the SDK."""
    suite = case.get("suite")
    if suite not in SYSTEM_SUITES or source.get("suite") != suite:
        raise ValueError("System case suite differs from frozen source manifest")
    if type(case.get("source_row_index")) is not int or case["source_row_index"] < 0:
        raise ValueError("Frozen source row index must be a nonnegative integer")
    if suite in EXPANSION_SUITES:
        if source.get("protocol_version") != EXPANSION_VERSION:
            raise ValueError("System adapter requires the frozen expansion v2 source")
        origin = source.get("source", {})
        options = native_protocol(suite)
        if (source.get("release_gate") is not False or source.get("native_protocol") != options
                or source.get("n_shots") != options["n_shots"]
                or source.get("scorer") != EXPANSION_SUITES[suite]["scorer"]
                or source.get("applicability") != EXPANSION_SUITES[suite]):
            raise ValueError("Frozen Native source settings changed")
        rebuilt = make_expansion_case(
            suite, case["raw_row"], case["source_row_index"], revision=origin["revision"],
            source_file_sha256=origin["export_sha256"], source_line_sha256=case["source_line_sha256"],
        )
        if source.get("case_fingerprints", {}).get(case["case_id"]) != fingerprint(rebuilt):
            raise ValueError("Case identity differs from the frozen source manifest")
    else:
        if source.get("protocol_version") != VERSION:
            raise ValueError("System adapter requires the frozen starter source protocol")
        rebuilt = make_case(suite, case["raw_row"], case["source_row_index"], case["task"])
    if case != rebuilt:
        raise ValueError("Case fields differ from the frozen canonical source row")
    return rebuilt


def answer_domain(case):
    """Pinned product extraction domain, checked against SDK schema at scoring."""
    suite = case["suite"]
    if suite in {"logiqa", "mmlu", "hellaswag"}:
        return {"kind": "enum", "values": list("ABCD")}
    if suite == "truthfulqa":
        return {"kind": "enum", "values": [str(i + 1) for i in range(len(case["raw_row"]["mc1_targets"]["choices"]))]}
    if suite == "gsm8k":
        # Product textual extraction intentionally preserves commas/decimals.
        # Native NumberSchema remains unchanged and is recorded with the score.
        return {"kind": "numeric_text"}
    if suite == "ifeval":
        return {"kind": "full_body"}
    if suite != "bbh" or case.get("task") not in BBH_TASK_KINDS:
        raise ValueError("Supported suite and explicit BBH task required")
    kind = BBH_TASK_KINDS[case["task"]]
    if kind.startswith("choice_"):
        return {"kind": "enum", "values": [f"({letter})" for letter in "ABCDEFGHIJKLMNOPQR"[:int(kind.split("_")[1])]]}
    values = {"boolean": ["True", "False"], "yes_no": ["Yes", "No"],
              "yes_no_lower": ["yes", "no"], "validity": ["valid", "invalid"]}
    if kind in values:
        return {"kind": "enum", "values": values[kind]}
    if kind == "numeric":
        return {"kind": "integer_text"}
    return {"kind": "closing_brackets" if case["task"] == "dyck_languages" else "text"}


def _answer_requirement(case):
    domain = answer_domain(case)
    kind = domain["kind"]
    if kind == "enum":
        description = "exactly one of " + ", ".join(domain["values"])
    elif kind == "numeric_text":
        description = "the final numeric value as text, preserving any thousands separators; no units or explanation on that line"
    elif kind == "integer_text":
        description = "an integer, without units or explanation on that line"
    elif kind == "closing_brackets":
        description = "only the required closing brackets, in order, separated by spaces"
    else:
        description = "the task's complete requested answer text, without explanation on that line"
    return ("Give your final answer on exactly one separate line in this format: Final answer: <answer>. "
            "Replace <answer> with " + description + ". Do not include the angle brackets or a trailing period. "
            "Any explanation or citations must be on other lines.")


def _task_material(case):
    """Read explicit fields only; never serialize or traverse the raw row."""
    suite, row = case["suite"], case["raw_row"]
    role = SYSTEM_SUITES[suite]["material_role"]
    if suite == "logiqa":
        options = "\n".join(f"{label}. {option}" for label, option in zip("ABCD", row["options"]))
        return row["text"], row["question"] + "\nCandidate options (alternatives, not asserted facts):\n" + options, role
    if suite in {"gsm8k", "ifeval", "bbh"}:
        key = {"gsm8k": "question", "ifeval": "prompt", "bbh": "input"}[suite]
        if suite == "bbh":
            role = "commonsense_task" if case["task"] in _BBH_COMMONSENSE_TASKS else "self_contained_task"
        return row[key], row[key], role
    if suite == "truthfulqa":
        # The permutation uses choice count only, never labels. Canonical
        # reconstruction separately verifies the frozen evaluation-side mapping.
        order = truthfulqa_choice_order(row)
        choices = row["mc1_targets"]["choices"]
        options = "\n".join(f"{i + 1}. {choices[j]}" for i, j in enumerate(order))
        task = row["question"] + "\nCandidate options (alternatives, not asserted facts):\n" + options
    elif suite == "mmlu":
        options = "\n".join(f"{label}. {option}" for label, option in zip("ABCD", row["choices"]))
        task = row["question"] + "\nCandidate options (alternatives, not asserted facts):\n" + options
    else:
        options = "\n".join(f"{label}. {option}" for label, option in zip("ABCD", row["endings"]))
        task = (row["ctx"] + "\nChoose the most plausible continuation.\n"
                "Candidate continuations (alternatives, not events known to have happened):\n" + options)
    return task, task, role


def build_system_bundle(cases, source, max_documents=40):
    """Return all frozen case memberships and their deduplicated material union.

    ``source`` is the original frozen manifest, as used by build_request. This
    function performs local reconstruction only; load_bundle validates artifact
    bytes before the runner calls it. No suitability approval is fabricated.
    """
    cases = list(cases)
    suites = {case.get("suite") for case in cases}
    if len(suites) != 1 or not suites <= SYSTEM_SUITES.keys():
        raise ValueError("Exactly one supported system suite and nonempty cases required")
    if type(max_documents) is not int or not 1 <= max_documents <= 40:
        raise ValueError("System document cap must be in 1..40")
    suite = next(iter(suites))
    documents, questions, decisions, roles, seen = {}, [], [], set(), set()
    for supplied in cases:
        case = _canonical(supplied, source)
        if case["case_id"] in seen:
            raise ValueError("Duplicate frozen case membership; case IDs must be unique")
        seen.add(case["case_id"])
        material, task, role = _task_material(case)
        roles.add(role)
        doc = document(material, suite)
        documents.setdefault(doc["id"], doc)
        ask = task if suite == "ifeval" else task + "\n\n" + _answer_requirement(case)
        references = deepcopy(case["references"])
        questions.append({
            "id": case["case_id"], "dataset": case["dataset"], "split": case["split"],
            "question": ask, "expected_answer": references[0] if references else "", "references": references,
            "gold_document_ids": [doc["id"]] if suite == "logiqa" else [],
            "case_id": case["case_id"], "sample_id": case["sample_id"], "suite": suite, "task": case["task"],
            "product_protocol": SYSTEM_VERSION, "protocol_version": SYSTEM_VERSION, "track": "R",
            "material_role": role, "material_document_ids": [doc["id"]],
            "original_question": case["question"], "answer_domain": answer_domain(case),
        })
        decisions.append({
            "case_id": case["case_id"], "sample_id": case["sample_id"], "task": case["task"],
            "status": "applicable", "basis": "machine_field_extraction",
            "reason": "Whitelisted original task fields under the independent system adapter; no human suitability judgment inferred",
            "product_protocol": SYSTEM_VERSION, "material_role": role,
        })
    if len(documents) > max_documents:
        raise ValueError("Deduplicated task materials exceed document cap; explicitly prepare a smaller frozen selection")
    docs = list(documents.values())
    return {"questions": questions, "documents": docs, "decisions": decisions, "manifest": {
        "protocol_version": SYSTEM_VERSION, "product_protocol": SYSTEM_VERSION, "suite": suite, "track": "R",
        "source_protocol_version": source["protocol_version"], "source_manifest_fingerprint": fingerprint(source),
        "planned_case_count": len(cases), "question_count": len(questions),
        "distinct_sample_count": len({case["sample_id"] for case in cases}), "document_count": len(docs),
        "gold_document_count": len(docs) if suite == "logiqa" else 0, "distractor_document_count": 0,
        "requested_document_cap": max_documents, "unfilled_document_slots": max_documents - len(docs),
        "questions_sha256": fingerprint(questions), "documents_sha256": fingerprint(docs),
        "decisions_sha256": fingerprint(decisions), "field_whitelist": deepcopy(SYSTEM_SUITES[suite]),
        "material_roles": sorted(roles), "material_semantics": {role: MATERIAL_SEMANTICS[role] for role in sorted(roles)},
        "evidence_semantics": "passage_coverage_diagnostic_only" if suite == "logiqa" else "task_material_is_not_gold_answer_evidence",
        "citation_semantics": "Source existence is diagnostic; it does not establish that the answer is supported",
        "source_scope": "entire frozen corpus, never per-question gold", "conversation": [], "memory_injection": False,
        "human_suitability_review": "not_claimed", "status": "prepared_not_validated",
    }}
