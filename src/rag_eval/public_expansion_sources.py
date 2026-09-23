"""Local JSONL conversion and canonical reconstruction of expansion bundles.

No dataset SDK, model, or network access is used by this module. Source hashes
are operator-declared provenance, not an authentication signature.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import re
from typing import Any, Callable

from .artifacts import digest
from .public_expansion_protocol import (
    BBH_TASK_KINDS, EXPANSION_SUITES, EXPANSION_VERSION, native_protocol,
)
from .starter_protocol import SDK_VERSION, fingerprint

_MOVING_REVISIONS = {"main", "master", "latest", "unknown", "pending"}
_REPREPARE = "Reprepare the local JSONL with scripts/prepare_public_starter.py into a new directory."


def _revision(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source revision is required")
    value = value.strip()
    if value.lower() in _MOVING_REVISIONS:
        raise ValueError("source revision must be fixed, not a moving branch")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value


def _choices(value: Any, name: str, count: int | None = None) -> list[str]:
    if not isinstance(value, list) or len(value) < 2 or (count is not None and len(value) != count):
        raise ValueError(f"{name} requires {count or 'at least two'} choices")
    return [_text(item, name) for item in value]


def _mmlu(row: dict[str, Any]) -> tuple[str, str, str]:
    question = _text(row.get("question"), "MMLU question")
    _choices(row.get("choices"), "MMLU choices", 4)
    answer = row.get("answer")
    if type(answer) is not int or answer not in range(4):
        raise ValueError("MMLU requires an answer index 0..3")
    task = _text(row.get("subject", row.get("task")), "MMLU subject")
    return task, question, "ABCD"[answer]


def _gsm8k(row: dict[str, Any]) -> tuple[str, str, str]:
    question = _text(row.get("question"), "GSM8K question")
    answer = _text(row.get("answer"), "GSM8K answer")
    # Mirror GSM8KTemplate.format_answer: first match, preserving the exact
    # answer text. Comma/currency/decimal normalization is diagnostic only.
    matches = re.findall(r"#### (.*)", answer)
    if not matches or not re.fullmatch(r"[-+]?\$?[0-9][0-9,]*(?:\.[0-9]+)?", matches[0].strip()):
        raise ValueError("GSM8K answer requires the official '#### ' numeric annotation")
    return "gsm8k", question, matches[0]


def truthfulqa_choice_order(row: dict[str, Any]) -> list[int]:
    """Mirror the SDK's seed-42 permutation without changing global RNG state."""
    order = list(range(len(row["mc1_targets"]["choices"])))
    random.Random(42).shuffle(order)
    return order


def _truthfulqa(row: dict[str, Any]) -> tuple[str, str, str]:
    question = _text(row.get("question"), "TruthfulQA question")
    targets = row.get("mc1_targets")
    if not isinstance(targets, dict):
        raise ValueError("TruthfulQA Native requires mc1_targets choices/labels; generation answers are unsupported. " + _REPREPARE)
    choices = _choices(targets.get("choices"), "TruthfulQA mc1_targets.choices")
    labels = targets.get("labels")
    if (not isinstance(labels, list) or len(labels) != len(choices)
            or any(type(label) is not int or label not in (0, 1) for label in labels)
            or labels.count(1) != 1):
        raise ValueError("TruthfulQA MC1 requires aligned binary labels with exactly one correct answer")
    order = truthfulqa_choice_order(row)
    expected = str(order.index(labels.index(1)) + 1)
    task = _text(row.get("category", "truthfulqa_mc1"), "TruthfulQA category")
    return task, question, expected


def _hellaswag(row: dict[str, Any]) -> tuple[str, str, str]:
    context = _text(row.get("ctx"), "HellaSwag ctx")
    _choices(row.get("endings"), "HellaSwag endings", 4)
    label = row.get("label")
    if isinstance(label, bool) or not (
        type(label) is int or (isinstance(label, str) and re.fullmatch(r"\s*[0-3]\s*", label))
    ) or int(label) not in range(4):
        raise ValueError("HellaSwag label must be an index 0..3")
    task = _text(row.get("activity_label"), "HellaSwag activity_label")
    return task, context, "ABCD"[int(label)]


def _bbh(row: dict[str, Any]) -> tuple[str, str, str]:
    question = _text(row.get("input"), "BBH input")
    target = _text(row.get("target"), "BBH target")
    task = _text(row.get("task_name", row.get("task")), "BBH task_name")
    if task not in BBH_TASK_KINDS:
        raise ValueError(f"Unsupported BBH task: {task}; use an explicit SDK task name")
    kind = BBH_TASK_KINDS[task]
    if kind.startswith("choice_"):
        labels = [f"({letter})" for letter in "ABCDEFGHIJKLMNOPQR"[:int(kind.split("_")[1])]]
        valid = target in labels
    elif kind in {"boolean", "yes_no", "yes_no_lower", "validity"}:
        valid = target in {"boolean": ("True", "False"), "yes_no": ("Yes", "No"),
                           "yes_no_lower": ("yes", "no"), "validity": ("valid", "invalid")}[kind]
    elif kind == "numeric":
        valid = re.fullmatch(r"-?[0-9]+", target) is not None
    else:
        valid = True
    if not valid:
        raise ValueError(f"BBH target is incompatible with the official schema for {task}")
    return task, question, target


_ADAPTERS: dict[str, Callable[[dict[str, Any]], tuple[str, str, str]]] = {
    "mmlu": _mmlu, "gsm8k": _gsm8k, "truthfulqa": _truthfulqa,
    "hellaswag": _hellaswag, "bbh": _bbh,
}


def read_source(suite: str, path: str | Path, *, revision: str) -> list[dict[str, Any]]:
    """Read physical source lines and retain both byte and semantic hashes."""
    if suite not in _ADAPTERS:
        raise ValueError(f"Unknown expansion suite: {suite}")
    revision = _revision(revision)
    data = Path(path).read_bytes()
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
            "source_sha256": file_hash, "source_row_index": line_number,
            "raw_record": raw, "raw_record_sha256": fingerprint(raw),
            "source_line_sha256": hashlib.sha256(line.encode("utf-8")).hexdigest(),
        })
    if not records:
        raise ValueError("source JSONL is empty")
    return records


def make_expansion_case(suite: str, row: dict[str, Any], row_index: int, *,
                        revision: str, source_file_sha256: str,
                        source_line_sha256: str) -> dict[str, Any]:
    """Rebuild every executable field from one validated public source row."""
    if suite not in _ADAPTERS or type(row_index) is not int or row_index < 0:
        raise ValueError("Known expansion suite and nonnegative row index required")
    for name, value in (("source_file_sha256", source_file_sha256), ("source_line_sha256", source_line_sha256)):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError(f"{name} must be a SHA256")
    task, question, expected = _ADAPTERS[suite](row)
    info = EXPANSION_SUITES[suite]
    options = native_protocol(suite)
    notes = ["local source selection; no full-benchmark coverage claim"]
    if suite == "truthfulqa":
        notes.append("DeepEval MC1 prompted choice accuracy; not free-text semantic truthfulness or refusal grading")
        if "category" not in row:
            notes.append("category unavailable in local multiple-choice export")
    if suite == "gsm8k":
        notes.append("official exact answer text retained; numeric normalization is diagnostic only")
    return {
        "protocol_version": EXPANSION_VERSION, "suite": suite,
        "dataset": info["dataset"], "split": info["split"],
        "sample_id": f"{suite}-{row_index}", "case_id": f"{suite}-{row_index}",
        "task": task, "source_row_index": row_index,
        "raw_row_sha256": fingerprint(row), "raw_row": deepcopy(row),
        "source_line_sha256": source_line_sha256,
        "source_revision": _revision(revision), "source_file_sha256": source_file_sha256,
        "question": question, "passage": None,
        "references": [expected], "expected_answer": expected,
        "answer_type": BBH_TASK_KINDS[task] if suite == "bbh" else info["task_kind"],
        "n_shots": options["n_shots"], "native_protocol": options,
        "choice_order": truthfulqa_choice_order(row) if suite == "truthfulqa" else None,
        "scorer": info["scorer"], "limitations": notes,
        "product_review": {"status": "not_applicable", "reason": info["product_reason"],
                           "candidate": info["product_candidate"]},
    }


def select_expansion_cases(suite: str, path: str | Path, *, revision: str) -> tuple[list[dict], dict]:
    """Select all supplied rows in original order; record only supplied tasks."""
    records = read_source(suite, path, revision=revision)
    cases = [make_expansion_case(
        suite, record["raw_record"], record["source_row_index"],
        revision=record["source_revision"], source_file_sha256=record["source_file_sha256"],
        source_line_sha256=record["source_line_sha256"],
    ) for record in records]
    selection = {
        "method": "all supplied local rows in original source order",
        "requested_memberships": len(cases), "selected_memberships": len(cases),
        "distinct_questions": len({fingerprint(case["raw_row"]) for case in cases}),
        "selected_tasks": list(dict.fromkeys(case["task"] for case in cases)),
        "task_counts": dict(Counter(case["task"] for case in cases)),
        "answer_type_counts": dict(Counter(case["answer_type"] for case in cases)),
        "shortages": [], "coverage": "local_selection_only",
    }
    return cases, selection


def validate_expansion_source(source: dict, suite: str, raw_path: str | Path) -> None:
    info = EXPANSION_SUITES[suite]
    if not isinstance(source, dict):
        raise ValueError("Expansion source provenance must be an object")
    for field in ("dataset", "split", "revision", "source_url", "license", "license_url", "conversion"):
        _text(source.get(field), f"source.{field}")
    _revision(source["revision"])
    if source["dataset"] != info["dataset"] or source["split"] != info["split"]:
        raise ValueError("Dataset/split does not match the expansion suite")
    for field in ("origin_sha256", "export_sha256"):
        if not isinstance(source.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", source[field]):
            raise ValueError(f"source.{field} must be a SHA256")
    if digest(raw_path) != source["export_sha256"]:
        raise ValueError("Raw source differs from declared export hash")
    if source.get("original_order_preserved") is not True:
        raise ValueError("Export must preserve the original split order")


def expansion_manifest_fields(suite: str, cases: list[dict]) -> dict:
    """Protocol commitments reconstructed during preparation and bundle loading."""
    return {
        "protocol_version": EXPANSION_VERSION, "deepeval_version": SDK_VERSION,
        "native_protocol": native_protocol(suite),
        "n_shots": native_protocol(suite)["n_shots"], "scorer": EXPANSION_SUITES[suite]["scorer"],
        "applicability": deepcopy(EXPANSION_SUITES[suite]),
        "case_fingerprints": {case["case_id"]: fingerprint(case) for case in cases},
    }


def load_expansion_bundle(directory: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate bytes, SDK resources, and rebuild cases/selection from raw.jsonl."""
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Expansion manifest must be an object")
    suite = manifest.get("suite")
    if manifest.get("protocol_version") != EXPANSION_VERSION or suite not in EXPANSION_SUITES:
        raise ValueError("Incompatible expansion bundle; frozen Native SDK protocol required. " + _REPREPARE)
    if manifest.get("release_gate") is not False:
        raise ValueError("Expansion bundles cannot enable an uncalibrated release gate")
    artifacts = manifest.get("artifacts", {})
    required = {"raw.jsonl", "cases.jsonl", "instruction-audit.jsonl", "cards.md"}
    if not isinstance(artifacts, dict) or not required <= artifacts.keys():
        raise ValueError("Incomplete expansion artifact manifest")
    for relative, expected in artifacts.items():
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory) or not path.is_file() or digest(path) != expected:
            raise ValueError("Frozen artifact changed or invalid path: " + relative)
    validate_expansion_source(manifest.get("source"), suite, directory / "raw.jsonl")
    cases, selection = select_expansion_cases(suite, directory / "raw.jsonl", revision=manifest["source"]["revision"])
    try:
        saved = [json.loads(line) for line in (directory / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed expansion cases artifact") from exc
    if saved != cases or manifest.get("selection") != selection:
        raise ValueError("Frozen expansion cases/selection no longer reproduce raw source; " + _REPREPARE)
    fields = expansion_manifest_fields(suite, cases)
    if any(manifest.get(key) != value for key, value in fields.items()):
        raise ValueError("Expansion manifest protocol or case identities changed; " + _REPREPARE)
    from .public_expansion_native import validate_sdk_snapshot, validate_sdk_tasks
    sdk_root = (directory / "sdk-source").resolve()
    if not sdk_root.is_relative_to(directory):
        raise ValueError("Frozen SDK source directory is outside the expansion bundle")
    validate_sdk_snapshot(sdk_root, manifest, cases)
    validate_sdk_tasks(cases, sdk_root)
    return manifest, cases


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
