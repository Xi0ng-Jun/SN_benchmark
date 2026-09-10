#!/usr/bin/env python3
"""Freeze one starter suite from local JSONL. Never downloads or calls models."""
from __future__ import annotations

import argparse
import ast
from importlib.metadata import distribution
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag_eval.artifacts import digest, save_json, save_jsonl
from rag_eval.starter_protocol import SDK_VERSION, SUITES, VERSION, select_cases


def validate_source(source, suite, raw_path):
    info = SUITES[suite]
    for field in ("dataset", "split", "revision", "source_url", "license", "license_url", "conversion"):
        if not isinstance(source.get(field), str) or not source[field].strip():
            raise ValueError(f"source.{field} is required")
    if source["dataset"] != info["dataset"] or source["split"] != info["split"]:
        raise ValueError("Dataset/split does not match the starter suite")
    if source["revision"].lower() in {"main", "master", "latest", "unknown", "pending"}:
        raise ValueError("Use a fixed upstream revision/release, not a moving branch")
    for field in ("origin_sha256", "export_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", source.get(field, "")):
            raise ValueError(f"source.{field} must be a SHA256")
    if digest(raw_path) != source["export_sha256"]:
        raise ValueError("Local JSONL hash differs from declared export hash")
    if source.get("original_order_preserved") is not True:
        raise ValueError("Export must preserve the original split order")


def sdk_files():
    """Read distribution files without importing deepeval (and its integrations)."""
    dist = distribution("deepeval")
    if dist.version != SDK_VERSION:
        raise ValueError(f"Starter protocol requires DeepEval {SDK_VERSION}, found {dist.version}")
    root = Path(dist.locate_file("deepeval"))
    files = (list((root / "benchmarks").rglob("*.py"))
             + list((root / "scorer").rglob("*.py")) + [root / "utils.py"])
    if not files:
        raise ValueError("DeepEval benchmark/scorer source files unavailable")
    return root, sorted(files)


def instruction_inventory(cases, verifier_path):
    """Static evidence only: an explicit branch is not proof that a rule works."""
    tree = ast.parse(verifier_path.read_text(encoding="utf-8"))
    branches = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if (isinstance(child, ast.Compare) and isinstance(child.left, ast.Name)
                    and child.left.id == "instruction_id" and len(child.ops) == 1
                    and isinstance(child.ops[0], ast.Eq) and isinstance(child.comparators[0], ast.Constant)):
                branches[child.comparators[0].value] = node.name
    return [
        {"case_id": case["case_id"], "position": i, "instruction_id": instruction,
         "kwargs": case["raw_row"]["kwargs"][i], "explicit_branch": branches.get(instruction),
         "audit_status": "pending", "positive_fixture": None, "negative_fixture": None,
         "verifier_sha256": digest(verifier_path)}
        for case in cases for i, instruction in enumerate(case["raw_row"]["instruction_id_list"])
    ]


def prepare(suite, raw_path, source_path, output):
    raw_path, source_path, output = Path(raw_path), Path(source_path), Path(output)
    source = json.loads(source_path.read_text(encoding="utf-8"))
    validate_source(source, suite, raw_path)
    # Blank lines are rejected so source_row_index always identifies a physical line.
    rows = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]
    cases, selection = select_cases(suite, rows)
    sdk_root, files = sdk_files()
    inventory = instruction_inventory(cases, sdk_root / "benchmarks/ifeval/ifeval.py") if suite == "ifeval" else []
    # A failed preparation remains visibly incomplete; never overwrite or reuse it.
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(raw_path, output / "raw.jsonl")
    if digest(output / "raw.jsonl") != source["export_sha256"]:
        raise ValueError("Source changed during preparation")
    source_hashes = {}
    for path in files:
        relative = path.relative_to(sdk_root)
        target = output / "sdk-source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        source_hashes[str(relative)] = digest(target)
    save_jsonl(output / "cases.jsonl", cases)
    save_jsonl(output / "instruction-audit.jsonl", inventory)
    cards = ["# 公开题卡（未运行）", "公开参考仅供评测侧使用；产品适用性尚未人审。"]
    for case in cases:
        cards.extend([f"\n## {case['case_id']}", f"任务：{case['task']}",
                      f"原始行号（从 0 起）：{case['source_row_index']}",
                      "```json\n" + json.dumps(case["raw_row"], ensure_ascii=False, indent=2) + "\n```",
                      "限制：" + "; ".join(case["limitations"])])
    (output / "cards.md").write_text("\n\n".join(cards) + "\n", encoding="utf-8")
    # The manifest is written last: its absence means the bundle is incomplete.
    save_json(output / "manifest.json", {
        "protocol_version": VERSION, "status": "prepared_not_validated", "suite": suite,
        "source": source, "source_provenance_status": "operator_declared; export hash checked",
        "selection": selection, "n_shots": None if suite == "ifeval" else 0,
        "scorer": SUITES[suite]["scorer"], "deepeval_version": SDK_VERSION,
        "sdk_source_hashes": source_hashes,
        "artifacts": {name: digest(output / name) for name in ("raw.jsonl", "cases.jsonl", "instruction-audit.jsonl", "cards.md")},
        "model_predictions": 0, "human_review": "pending", "verifier_audit": "pending" if suite == "ifeval" else "not_applicable",
        "release_gate": False,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=SUITES, required=True)
    parser.add_argument("--raw-jsonl", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True, help="Operator-provided source provenance JSON")
    parser.add_argument("--output", type=Path, required=True, help="New directory; existing directories rejected")
    args = parser.parse_args()
    prepare(args.suite, args.raw_jsonl, args.source, args.output)


if __name__ == "__main__":
    main()
