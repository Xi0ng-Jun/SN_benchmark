"""Local JSONL adapters for the public benchmark expansion.

Adapters deliberately accept files already present on disk.  They never fetch
datasets or import a dataset SDK.  Each returned record keeps the source row
verbatim and carries enough provenance to reproduce the conversion.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from .public_expansion_protocol import EXPANSION_SUITES

_MOVING_REVISIONS = {"main", "master", "latest", "unknown", "pending"}


def _revision(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source revision is required")
    value = value.strip()
    if value.lower() in _MOVING_REVISIONS:
        raise ValueError("source revision must be fixed, not a moving branch")
    return value


def _answer_number(answer: Any) -> str | None:
    if not isinstance(answer, str):
        return None
    match = re.search(r"####\s*([-+]?\$?[\d,]+(?:\.\d+)?)\s*$", answer)
    return match.group(1).replace("$", "").replace(",", "") if match else None


def _mmlu(row: dict[str, Any]) -> tuple[str, str, Any]:
    choices = row.get("choices")
    answer = row.get("answer")
    if (not isinstance(row.get("question"), str) or not row["question"].strip()
            or not isinstance(choices, list) or len(choices) != 4
            or any(not isinstance(choice, str) or not choice.strip() for choice in choices)
            or type(answer) is not int or answer not in range(4)):
        raise ValueError("MMLU requires four choices and an answer index 0..3")
    task = row.get("subject") or row.get("task")
    return str(task or "mmlu"), str(row.get("question", "")), "ABCD"[answer]


def _gsm8k(row: dict[str, Any]) -> tuple[str, str, Any]:
    question, answer = row.get("question"), row.get("answer")
    if not isinstance(question, str) or not question.strip() or not isinstance(answer, str):
        raise ValueError("GSM8K requires question and answer text")
    expected = _answer_number(answer)
    if expected is None or not expected.strip():
        raise ValueError("GSM8K answer has no final numeric value")
    return "gsm8k", question, expected


def _truthfulqa(row: dict[str, Any]) -> tuple[str, str, Any]:
    question = row.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("TruthfulQA requires question text")
    expected = row.get("best_answer", row.get("correct_answers"))
    if expected is None:
        raise ValueError("TruthfulQA requires best_answer or correct_answers")
    return str(row.get("category") or "truthfulqa"), question, expected


def _hellaswag(row: dict[str, Any]) -> tuple[str, str, Any]:
    context = row.get("ctx", row.get("context"))
    endings, label = row.get("endings"), row.get("label")
    if (not isinstance(context, str) or not context.strip() or not isinstance(endings, list)
            or len(endings) != 4 or any(not isinstance(ending, str) or not ending.strip() for ending in endings)):
        raise ValueError("HellaSwag requires context and four endings")
    if isinstance(label, bool) or not (
            type(label) is int
            or (isinstance(label, str) and label.strip().isascii() and label.strip().isdigit())):
        raise ValueError("HellaSwag label must be an index 0..3") from None
    index = int(label)
    if index not in range(4):
        raise ValueError("HellaSwag label must be an index 0..3")
    return str(row.get("activity_label") or "hellaswag"), context, "ABCD"[index]


def _bbh(row: dict[str, Any]) -> tuple[str, str, Any]:
    question = row.get("input", row.get("question"))
    target = row.get("target")
    if (not isinstance(question, str) or not question.strip() or not isinstance(target, str)
            or not target.strip()):
        raise ValueError("BBH requires input and target")
    return str(row.get("task_name") or row.get("task") or "bbh"), question, target


_ADAPTERS: dict[str, Callable[[dict[str, Any]], tuple[str, str, Any]]] = {
    "mmlu": _mmlu, "gsm8k": _gsm8k, "truthfulqa": _truthfulqa,
    "hellaswag": _hellaswag, "bbh": _bbh,
}


def read_source(suite: str, path: str | Path, *, revision: str) -> list[dict[str, Any]]:
    """Read a local expansion JSONL file and return normalized source records."""
    if suite not in _ADAPTERS:
        raise ValueError(f"Unknown expansion suite: {suite}")
    revision = _revision(revision)
    path = Path(path)
    data = path.read_bytes()
    file_hash = hashlib.sha256(data).hexdigest()
    records = []
    for line_number, line in enumerate(data.decode("utf-8").splitlines()):
        if not line.strip():
            raise ValueError(f"blank source line at {line_number}")
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at source line {line_number}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"source line {line_number} must be an object")
        task, question, expected = _ADAPTERS[suite](raw)
        records.append({
            "suite": suite, "task": task, "domain": task,
            "question": question, "expected_answer": expected,
            "source_revision": revision, "source_file_sha256": file_hash,
            "source_sha256": file_hash,
            "source_row_index": line_number, "raw_record": raw,
            "raw_record_sha256": hashlib.sha256(line.encode("utf-8")).hexdigest(),
        })
    if not records:
        raise ValueError("source JSONL is empty")
    return records


def read_mmlu(path: str | Path, *, revision: str) -> list[dict[str, Any]]:
    return read_source("mmlu", path, revision=revision)


def read_gsm8k(path: str | Path, *, revision: str) -> list[dict[str, Any]]:
    return read_source("gsm8k", path, revision=revision)


def read_truthfulqa(path: str | Path, *, revision: str) -> list[dict[str, Any]]:
    return read_source("truthfulqa", path, revision=revision)


def read_hellaswag(path: str | Path, *, revision: str) -> list[dict[str, Any]]:
    return read_source("hellaswag", path, revision=revision)


def read_bbh(path: str | Path, *, revision: str) -> list[dict[str, Any]]:
    return read_source("bbh", path, revision=revision)


def load_expansion_bundle(directory: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load and verify a bundle emitted for an expansion suite."""
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    suite = manifest.get("suite")
    if manifest.get("protocol_version") != "public-starter-v1" or suite not in EXPANSION_SUITES:
        raise ValueError("Unsupported expansion manifest")
    artifacts = manifest.get("artifacts", {})
    required = {"raw.jsonl", "cases.jsonl", "instruction-audit.jsonl", "cards.md"}
    if not isinstance(artifacts, dict) or not required <= artifacts.keys():
        raise ValueError("Incomplete expansion artifact manifest")
    for relative, expected in artifacts.items():
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory) or not path.exists():
            raise ValueError("Invalid expansion artifact path")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError("Frozen artifact changed: " + relative)
    try:
        cases = [json.loads(line) for line in (directory / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed expansion cases artifact") from exc
    if not isinstance(cases, list) or any(not isinstance(case, dict) or case.get("suite") != suite for case in cases):
        raise ValueError("Expansion case suite differs from manifest")
    return manifest, cases
