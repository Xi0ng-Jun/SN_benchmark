"""Versioned, uncapped local selection with one decision per physical JSONL row.

This module only reads local bytes. Complete local enumeration never verifies
that an export contains an entire upstream split.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .public_expansion_protocol import EXPANSION_SUITES
from .public_expansion_sources import _revision, make_expansion_case
from .starter_protocol import SUITES, fingerprint, make_case, require_text

SELECTION_VERSION = "public-selection-v1"
MMLU_TASKS = (
    "high_school_computer_science", "high_school_physics",
    "high_school_world_history", "high_school_macroeconomics",
)
BBH_TASKS = (
    "logical_deduction_three_objects", "date_understanding", "object_counting",
    "tracking_shuffled_objects_three_objects",
)
LOGIQA_TASKS = ("Necessary Conditional Reasoning", "Sufficient Conditional Reasoning")
EXPECTED_TASKS = {
    "squad": ("Normans", "Steam_engine"), "drop": ("history", "nfl"),
    "logiqa": LOGIQA_TASKS, "mmlu": MMLU_TASKS, "bbh": BBH_TASKS,
    "boolq": ("boolq",), "gsm8k": ("gsm8k",), "ifeval": ("ifeval",),
    "truthfulqa": ("truthfulqa_mc1",), "hellaswag": ("hellaswag",),
}


def selection_policy(suite):
    if suite not in EXPECTED_TASKS:
        raise ValueError("Unknown public selection suite")
    info = {**SUITES, **EXPANSION_SUITES}[suite]
    return {"protocol_version": SELECTION_VERSION, "suite": suite,
            "dataset": info["dataset"], "split": info["split"],
            "expected_tasks": list(EXPECTED_TASKS[suite]),
            "method": "all valid in-scope physical rows in source order; no cap or label balancing",
            "upstream_coverage": "not inferred from local enumeration"}


def _scope(suite, row):
    """Return task memberships and coverage buckets; missing scope is invalid."""
    if suite == "squad":
        task = require_text(row.get("title"), "title")
        return ([task], [task]) if task in EXPECTED_TASKS[suite] else ([], [])
    if suite == "drop":
        task = require_text(row.get("section_id"), "section_id")
        category, separator, suffix = task.partition("_")
        return ([task], [category]) if separator and suffix and category in EXPECTED_TASKS[suite] else ([], [])
    if suite == "logiqa":
        types = row.get("type")
        if not isinstance(types, dict) or not types or any(type(value) is not bool for value in types.values()):
            raise ValueError("LogiQA type must contain boolean task metadata")
        tasks = [task for task in LOGIQA_TASKS if types.get(task) is True]
        return tasks, tasks
    if suite in {"mmlu", "bbh"}:
        primary = "subject" if suite == "mmlu" else "task_name"
        task = require_text(row.get(primary, row.get("task")), primary)
        if primary in row and "task" in row and row[primary] != row["task"]:
            raise ValueError("Conflicting task metadata")
        return ([task], [task]) if task in EXPECTED_TASKS[suite] else ([], [])
    return [EXPECTED_TASKS[suite][0]], [EXPECTED_TASKS[suite][0]]


def _strict_constant(value):
    raise ValueError("Nonfinite JSON constant: " + value)


def _public_id(row):
    return row.get("id", row.get("query_id", row.get("key")))


def _question_signature(case):
    row = case["raw_row"]
    # Annotation labels and source IDs do not hide otherwise identical questions.
    return fingerprint({"question": case["question"], "passage": case["passage"],
                        "choices": row.get("choices", row.get("options", row.get("endings"))),
                        "instructions": row.get("instruction_id_list"), "kwargs": row.get("kwargs")})


def select_public_rows(suite, raw_path, source):
    """Select a local export; quarantine errors without discarding their identities."""
    policy = selection_policy(suite)
    if not isinstance(source, dict):
        raise ValueError("Source provenance must be an object")
    revision = _revision(source.get("revision"))
    for field in ("dataset", "split"):
        if field in source and source[field] != policy[field]:
            raise ValueError("Source dataset/split differs from selection policy")
    declaration = source.get("coverage_declaration")
    if declaration is not None and (not isinstance(declaration, dict) or not declaration):
        raise ValueError("coverage_declaration must be a nonempty operator declaration object")
    data = Path(raw_path).read_bytes()
    file_hash = hashlib.sha256(data).hexdigest()
    decisions, cases = [], []
    public_identities, case_identities = {}, set()
    coverage_counts = Counter()
    duplicate_questions = defaultdict(list)
    # bytes.splitlines keeps malformed UTF-8 quarantinable and counts blank rows.
    for index, line in enumerate(data.splitlines()):
        line_hash = hashlib.sha256(line).hexdigest()
        decision = {"source_row_index": index, "source_file_sha256": file_hash,
                    "source_line_sha256": line_hash, "source_revision": revision,
                    "raw_row_sha256": None, "public_id": None,
                    "status": "invalid", "scope": "unknown", "reason": None,
                    "tasks": [], "coverage_tasks": [], "case_ids": []}
        try:
            raw = json.loads(line.decode("utf-8"), parse_constant=_strict_constant)
            if not isinstance(raw, dict):
                raise ValueError("Source row must be a JSON object")
            decision["raw_row_sha256"] = fingerprint(raw)
            public_id = _public_id(raw)
            if public_id is not None and (type(public_id) not in (str, int) or (isinstance(public_id, str) and not public_id.strip())):
                raise ValueError("Public identity must be a nonempty string or integer")
            decision["public_id"] = public_id
            tasks, buckets = _scope(suite, raw)
            decision.update(tasks=tasks, coverage_tasks=buckets, scope="in_scope" if tasks else "out_of_scope")
            if not tasks:
                decision.update(status="out_of_scope", reason="outside selected task scope")
            else:
                if suite in EXPANSION_SUITES:
                    row_cases = [make_expansion_case(suite, raw, index, revision=revision,
                                 source_file_sha256=file_hash, source_line_sha256=line_hash)]
                else:
                    row_cases = [make_case(suite, raw, index, task) for task in tasks]
                # Old canonical starter IDs cannot be changed without changing
                # Native protocol. Ambiguous identities therefore stay quarantined.
                identity = str(public_id) if public_id is not None else None
                if identity is not None and identity in public_identities and public_identities[identity] != decision["raw_row_sha256"]:
                    raise ValueError("Conflicting public identity points to different raw rows")
                if any(case["case_id"] in case_identities for case in row_cases):
                    raise ValueError("Duplicate canonical identity cannot represent another physical row")
                if identity is not None:
                    public_identities[identity] = decision["raw_row_sha256"]
                case_identities.update(case["case_id"] for case in row_cases)
                cases.extend(row_cases)
                coverage_counts.update(buckets)
                duplicate_questions[_question_signature(row_cases[0])].append(index)
                decision.update(status="selected", reason="valid record in selected scope",
                                tasks=[case["task"] for case in row_cases],
                                case_ids=[case["case_id"] for case in row_cases])
        except (ValueError, TypeError, KeyError, IndexError, UnicodeError) as exc:
            decision.update(status="invalid", reason=f"{type(exc).__name__}: {exc}")
        decisions.append(decision)
    statuses = Counter(row["status"] for row in decisions)
    task_counts = dict(Counter(case["task"] for case in cases))
    selected_rows = statuses["selected"]
    summary = {
        "protocol_version": SELECTION_VERSION, "method": policy["method"],
        "source_rows": len(decisions), "selected_rows": selected_rows,
        "out_of_scope_rows": statuses["out_of_scope"], "invalid_rows": statuses["invalid"],
        "in_scope_rows": sum(row["scope"] == "in_scope" for row in decisions),
        "invalid_in_scope_rows": sum(row["status"] == "invalid" and row["scope"] == "in_scope" for row in decisions),
        "unknown_scope_rows": sum(row["scope"] == "unknown" for row in decisions),
        "selected_memberships": len(cases), "distinct_questions": selected_rows,
        "unique_question_texts": len(duplicate_questions),
        "selected_tasks": list(task_counts), "task_counts": task_counts,
        "expected_tasks": list(EXPECTED_TASKS[suite]),
        "expected_task_counts": {task: coverage_counts[task] for task in EXPECTED_TASKS[suite]},
        "missing_expected_tasks": [task for task in EXPECTED_TASKS[suite] if not coverage_counts[task]],
        "duplicate_groups": [{"question_sha256": key, "source_row_indices": indices}
                             for key, indices in duplicate_questions.items() if len(indices) > 1],
        "label_counts": dict(Counter(case["references"][0] for case in cases if case["references"]))
                        if suite in {"boolq", "logiqa", "mmlu", "hellaswag", "truthfulqa"} else None,
        "answer_type_counts": dict(Counter(case["answer_type"] for case in cases)),
        "topic_counts": dict(Counter(case["raw_row"].get("category", "unknown") for case in cases)) if suite == "truthfulqa" else {},
        "instruction_counts": dict(Counter(instruction for case in cases
                                   for instruction in case["raw_row"].get("instruction_id_list", []))) if suite == "ifeval" else {},
        "product_review_counts": dict(Counter(case["product_review"]["status"] for case in cases)),
        "coverage": "local_selection_only", "local_enumeration_complete": True,
        "upstream_coverage": {"status": "unknown", "verified": False} if declaration is None else {
            "status": "operator_declared", "verified": False, "declaration": deepcopy(declaration)},
        "shortages": [],
    }
    return {"cases": cases, "decisions": decisions, "summary": summary}
