"""Local-only public starter selection. No SDK, dataset, or model imports."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .artifacts import digest

VERSION = "public-starter-v1"
SDK_VERSION = "4.2.2"
# Expansion contracts remain separate so frozen starter bundles keep their
# existing suite definitions and protocol version.
from .public_expansion_protocol import EXPANSION_SUITES, TraceEnvelope

SUITES = {
    "squad": {"dataset": "rajpurkar/squad", "split": "validation",
              "scorer": "deepeval.squad_score.binary_judge", "product": True},
    "drop": {"dataset": "ucinlp/drop", "split": "validation",
             "scorer": "deepeval.quasi_contains_score", "product": True},
    "boolq": {"dataset": "boolq/default", "split": "validation",
              "scorer": "deepeval.exact_match_score.YesNo", "product": True},
    "logiqa": {"dataset": "csitfun/LogiQA2.0/logiqa/DATA/LOGIQA/test.txt",
               "split": "test", "scorer": "deepeval.exact_match_score.ABCD", "product": False},
    "ifeval": {"dataset": "google/IFEval", "split": "train",
               "scorer": "deepeval.ifeval.audited_all_instructions", "product": False},
}


def fingerprint(value) -> str:
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(content.encode()).hexdigest()


def require_text(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value


def _texts(values, name):
    if not isinstance(values, list) or not values:
        raise ValueError(f"{name} must be a nonempty list")
    return [require_text(v, name) for v in values]


def make_case(suite: str, row: dict, row_index: int, task: str) -> dict:
    """Preserve raw public annotations; do not convert composite answers to aliases."""
    info = SUITES[suite]
    question = require_text(row.get("prompt" if suite == "ifeval" else "question"), "question")
    passage, references, notes = None, [], []
    if suite == "squad":
        passage = require_text(row.get("context"), "context")
        references = _texts(row["answers"]["text"], "answers.text")
        starts = row["answers"]["answer_start"]
        if len(starts) != len(references):
            raise ValueError("SQuAD answer offsets differ in length")
        for answer, start in zip(references, starts):
            if type(start) is not int or start < 0 or passage[start:start + len(answer)] != answer:
                raise ValueError("SQuAD answer offset mismatch")
        answer_type = "span"
    elif suite == "drop":
        passage = require_text(row.get("passage"), "passage")
        references = _texts(row["answers_spans"]["spans"], "answers_spans.spans")
        types = _texts(row["answers_spans"]["types"], "answers_spans.types")
        if len(types) != len(references) or any(t not in {"number", "date", "span"} for t in types):
            raise ValueError("Unsupported DROP flattened answer types")
        answer_type = types[0]
        if len(references) > 1:
            notes.append("native scorer accepts individual spans; completeness not established")
    elif suite == "boolq":
        passage = require_text(row.get("passage"), "passage")
        if type(row.get("answer")) is not bool:
            raise ValueError("BoolQ answer must be a JSON boolean, not a truthy string")
        references, answer_type = ["Yes" if row["answer"] else "No"], "boolean"
    elif suite == "logiqa":
        passage = require_text(row.get("text"), "text")
        if len(_texts(row["options"], "options")) != 4:
            raise ValueError("LogiQA requires four options")
        if type(row.get("answer")) is not int or row["answer"] not in range(4):
            raise ValueError("LogiQA answer must be an integer in 0..3")
        references, answer_type = ["ABCD"[row["answer"]]], "choice"
    else:
        ids = _texts(row["instruction_id_list"], "instruction_id_list")
        kwargs = row["kwargs"]
        if not isinstance(kwargs, list) or len(ids) != len(kwargs) or not all(isinstance(k, dict) for k in kwargs):
            raise ValueError("IFEval instruction/kwargs alignment is required")
        answer_type = "instructions"
        notes.append("all instruction instances require verifier audit before scoring")
    public_id = row.get("id", row.get("query_id", row.get("key")))
    sample_id = f"{suite}-{public_id}" if public_id is not None else f"{suite}-{fingerprint(row)}"
    return {
        "protocol_version": VERSION, "suite": suite, "dataset": info["dataset"],
        "split": info["split"], "sample_id": sample_id,
        "case_id": f"{sample_id}:{fingerprint(task)[:16]}", "task": task,
        "source_row_index": row_index, "raw_row_sha256": fingerprint(row), "raw_row": deepcopy(row),
        "question": question, "passage": passage, "references": references,
        "answer_type": answer_type, "n_shots": None if suite == "ifeval" else 0,
        "scorer": info["scorer"], "limitations": notes,
        "product_review": {"status": "pending" if info["product"] else "not_applicable", "reason": None},
    }


def select_cases(suite: str, rows: list[dict]) -> tuple[list[dict], dict]:
    """Original-order prefixes, never shuffled or silently topped up."""
    if suite not in SUITES or not rows or not all(isinstance(r, dict) for r in rows):
        raise ValueError("Known suite and nonempty list of raw row objects required")
    groups = []
    shortages = []
    if suite == "squad":
        groups = [(title, [i for i, r in enumerate(rows) if r.get("title") == title], 10)
                  for title in ("Normans", "Steam_engine")]
    elif suite == "drop":
        counts = Counter(require_text(r.get("section_id"), "section_id") for r in rows)
        for category in ("history", "nfl"):
            candidates = [s for s, n in counts.items() if s.startswith(category + "_") and n >= 10]
            if not candidates:
                shortages.append({"task": category, "missing": 10, "reason": "no section with ten rows"})
                continue
            section = candidates[0]
            groups.append((section, [i for i, r in enumerate(rows) if r["section_id"] == section], 10))
    elif suite == "logiqa":
        groups = [(task, [i for i, r in enumerate(rows) if r.get("type", {}).get(task) is True], 10)
                  for task in ("Necessary Conditional Reasoning", "Sufficient Conditional Reasoning")]
    else:
        groups = [(suite, list(range(len(rows))), 20)]
    cases = []
    for task, indices, count in groups:
        cases.extend(make_case(suite, rows[i], i, task) for i in indices[:count])
        if len(indices) < count:
            shortages.append({"task": task, "missing": count - len(indices), "reason": "insufficient source rows"})
    ids = [c["case_id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate sample within the same native task")
    identities = {}
    for case in cases:
        previous = identities.setdefault(case["sample_id"], case["raw_row_sha256"])
        if previous != case["raw_row_sha256"]:
            raise ValueError("A public sample ID points to different raw rows")
    return cases, {
        "method": "original-order task prefixes; DROP first eligible section per category",
        "requested_memberships": 20, "selected_memberships": len(cases),
        "distinct_questions": len(identities), "shortages": shortages,
        "label_counts": dict(Counter(c["references"][0] for c in cases)) if suite in {"boolq", "logiqa"} else None,
        "answer_type_counts": dict(Counter(c["answer_type"] for c in cases)),
    }


def load_bundle(directory):
    """Read a complete local bundle and check recorded bytes; never acquire data."""
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("suite") in EXPANSION_SUITES:
        from .public_expansion_sources import load_expansion_bundle
        return load_expansion_bundle(directory)
    if manifest.get("protocol_version") != VERSION or manifest.get("suite") not in SUITES:
        raise ValueError("Unsupported starter manifest")
    artifacts = manifest.get("artifacts", {})
    if not {"raw.jsonl", "cases.jsonl", "instruction-audit.jsonl", "cards.md"} <= artifacts.keys():
        raise ValueError("Incomplete artifact manifest")
    for relative, expected_hash in artifacts.items():
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory) or digest(path) != expected_hash:
            raise ValueError("Frozen artifact changed: " + relative)
    rows = [json.loads(line) for line in (directory / "raw.jsonl").read_text(encoding="utf-8").splitlines()]
    cases = [json.loads(line) for line in (directory / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    rebuilt, selection = select_cases(manifest["suite"], rows)
    if cases != rebuilt or selection != manifest["selection"]:
        raise ValueError("Frozen cases no longer reproduce the declared source selection")
    return manifest, cases
