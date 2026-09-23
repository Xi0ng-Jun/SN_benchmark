"""Prepare and reconstruct local public-selection-v1 containers.

SDK files are inspected as bytes/AST only. No dataset acquisition, SDK import,
model construction or runtime execution occurs here.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from importlib.metadata import distribution
import json
from pathlib import Path
import re
import shutil

from .artifacts import digest, save_json, save_jsonl
from .public_expansion_native import sdk_source_files, validate_sdk_snapshot, validate_sdk_tasks
from .public_expansion_protocol import EXPANSION_SUITES
from .public_expansion_sources import _revision, expansion_manifest_fields
from .public_selection import SELECTION_VERSION, select_public_rows, selection_policy
from .starter_protocol import SDK_VERSION, SUITES, VERSION

_ARTIFACTS = {"raw.jsonl", "cases.jsonl", "decisions.jsonl", "instruction-audit.jsonl", "cards.md"}
_STARTER_MODULES = {"squad": "squad", "drop": "drop", "boolq": "bool_q", "logiqa": "logi_qa", "ifeval": "ifeval"}


def _safe_path(directory, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("Invalid bundle artifact path")
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        raise ValueError("Missing bundle artifact or path outside bundle: " + relative)
    return path


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _validate_source(source, suite, raw_path):
    policy = selection_policy(suite)
    if not isinstance(source, dict):
        raise ValueError("Source must be an object")
    for key in ("dataset", "split", "revision", "source_url", "license", "license_url", "conversion"):
        if not isinstance(source.get(key), str) or not source[key].strip():
            raise ValueError("source." + key + " must be nonempty text")
    _revision(source["revision"])
    if source["dataset"] != policy["dataset"] or source["split"] != policy["split"]:
        raise ValueError("Source dataset/split differs from fixed selection policy")
    for key in ("origin_sha256", "export_sha256"):
        if not _sha(source.get(key)):
            raise ValueError("source." + key + " must be a SHA256")
    if digest(raw_path) != source["export_sha256"]:
        raise ValueError("Source export hash changed")
    if source.get("original_order_preserved") is not True:
        raise ValueError("Export must preserve original source order")


def _validate_sdk(root, native, cases):
    if native["suite"] in EXPANSION_SUITES:
        validate_sdk_snapshot(root, native, cases)
        validate_sdk_tasks(cases, root)
        return
    if native.get("deepeval_version") != SDK_VERSION:
        raise ValueError("Frozen SDK version differs from required version")
    hashes = native.get("sdk_source_hashes")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("Missing frozen SDK source hashes")
    module = _STARTER_MODULES[native["suite"]]
    required = {"utils.py", "benchmarks/__init__.py", "benchmarks/schema.py",
                "scorer/__init__.py", "scorer/scorer.py",
                f"benchmarks/{module}/__init__.py", f"benchmarks/{module}/{module}.py"}
    if native["suite"] != "ifeval":
        required.add(f"benchmarks/{module}/template.py")
    if not required <= hashes.keys():
        raise ValueError("Frozen SDK manifest omits required sources")
    inventory = {str(path.relative_to(root)) for path in sdk_source_files(root)}
    if inventory != set(hashes):
        raise ValueError("Frozen SDK inventory differs from manifest")
    for relative, expected in hashes.items():
        if not _sha(expected) or digest(_safe_path(root, relative)) != expected:
            raise ValueError("Frozen SDK source changed: " + relative)


# Frozen v1 inventory only; pending fixtures never gate current IFEval scoring.
def _instruction_inventory(cases, sdk_root, suite):
    if suite != "ifeval":
        return []
    path = sdk_root / "benchmarks/ifeval/ifeval.py"
    branches = {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if (isinstance(child, ast.Compare) and isinstance(child.left, ast.Name)
                    and child.left.id == "instruction_id" and len(child.ops) == 1
                    and isinstance(child.ops[0], ast.Eq) and isinstance(child.comparators[0], ast.Constant)):
                branches[child.comparators[0].value] = node.name
    verifier_hash = digest(path)
    return [{"case_id": case["case_id"], "position": position,
             "instruction_id": instruction, "kwargs": case["raw_row"]["kwargs"][position],
             "explicit_branch": branches.get(instruction), "audit_status": "pending",
             "positive_fixture": None, "negative_fixture": None, "verifier_sha256": verifier_hash}
            for case in cases for position, instruction in enumerate(case["raw_row"]["instruction_id_list"])]


def _cards(cases):
    cards = ["# 公开选题题卡（未运行）", "参考与标签仅供评测；本地完整读取不证明上游完整覆盖。"]
    for case in cases:
        cards.extend([f"\n## {case['case_id']}", f"任务：{case['task']}",
                      f"原始行号（从 0 起）：{case['source_row_index']}",
                      "纳入理由：属于冻结任务范围且原始字段有效。",
                      "```json\n" + json.dumps(case["raw_row"], ensure_ascii=False, indent=2) + "\n```",
                      "评分：" + case["scorer"], "限制：" + "; ".join(case["limitations"])])
    return "\n\n".join(cards) + "\n"


def _native_source(suite, source, selected, source_hashes, artifacts):
    native = {
        "protocol_version": VERSION, "status": "prepared_not_validated", "suite": suite,
        "source": deepcopy(source), "source_provenance_status": "operator_declared; export hash checked",
        "selection": deepcopy(selected["summary"]), "n_shots": None if suite == "ifeval" else 0,
        "scorer": {**SUITES, **EXPANSION_SUITES}[suite]["scorer"],
        "deepeval_version": SDK_VERSION, "sdk_source_hashes": source_hashes,
        "artifacts": artifacts, "model_predictions": 0, "human_review": "pending",
        "verifier_audit": "pending" if suite == "ifeval" else "not_applicable", "release_gate": False,
    }
    if suite in EXPANSION_SUITES:
        native.update(expansion_manifest_fields(suite, selected["cases"]))
    return native


def prepare_selection(suite, raw_path, source_path, output):
    """Freeze an uncapped local selection in a new directory; write manifest last."""
    raw_path, source_path, output = Path(raw_path), Path(source_path), Path(output)
    source = json.loads(source_path.read_text(encoding="utf-8"))
    _validate_source(source, suite, raw_path)
    selected = select_public_rows(suite, raw_path, source)
    dist = distribution("deepeval")
    if dist.version != SDK_VERSION:
        raise ValueError(f"Selection requires DeepEval {SDK_VERSION}, found {dist.version}")
    sdk_root = Path(dist.locate_file("deepeval")).resolve()
    files = sdk_source_files(sdk_root)
    source_hashes = {str(path.relative_to(sdk_root)): digest(path) for path in files}
    native = _native_source(suite, source, selected, source_hashes, {})
    _validate_sdk(sdk_root, native, selected["cases"])
    inventory = _instruction_inventory(selected["cases"], sdk_root, suite)
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(raw_path, output / "raw.jsonl")
    _validate_source(source, suite, output / "raw.jsonl")
    if select_public_rows(suite, output / "raw.jsonl", source) != selected:
        raise ValueError("Source changed between selection and freezing")
    for path in files:
        relative = path.relative_to(sdk_root)
        target = output / "sdk-source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    _validate_sdk((output / "sdk-source").resolve(), native, selected["cases"])
    if _instruction_inventory(selected["cases"], output / "sdk-source", suite) != inventory:
        raise ValueError("SDK instruction verifier changed during preparation")
    save_jsonl(output / "cases.jsonl", selected["cases"])
    save_jsonl(output / "decisions.jsonl", selected["decisions"])
    save_jsonl(output / "instruction-audit.jsonl", inventory)
    (output / "cards.md").write_text(_cards(selected["cases"]), encoding="utf-8")
    artifacts = {name: digest(output / name) for name in sorted(_ARTIFACTS)}
    native = _native_source(suite, source, selected, source_hashes, artifacts)
    save_json(output / "manifest.json", {
        "protocol_version": SELECTION_VERSION, "suite": suite, "status": "prepared_not_validated",
        "policy": selection_policy(suite), "source": source,
        "selection": selected["summary"], "native_source": native, "artifacts": artifacts,
        "release_gate": False,
    })


def _read_records(path):
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed selection artifact") from exc


def load_selection_bundle(directory):
    """Verify bytes and reconstruct selection, canonical cases and Native settings."""
    directory = Path(directory).resolve()
    manifest_path = _safe_path(directory, "manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("protocol_version") != SELECTION_VERSION:
        raise ValueError("Unsupported selection manifest")
    suite = manifest.get("suite")
    if manifest.get("policy") != selection_policy(suite):
        raise ValueError("Selection policy no longer matches this version")
    if manifest.get("release_gate") is not False or manifest.get("status") != "prepared_not_validated":
        raise ValueError("Selection bundle cannot declare a validated release gate")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not _ARTIFACTS <= artifacts.keys():
        raise ValueError("Incomplete selection artifact manifest")
    for relative, expected in artifacts.items():
        path = _safe_path(directory, relative)
        if not _sha(expected) or digest(path) != expected:
            raise ValueError("Frozen selection artifact changed: " + relative)
    source = manifest.get("source")
    _validate_source(source, suite, directory / "raw.jsonl")
    selected = select_public_rows(suite, directory / "raw.jsonl", source)
    if (selected["cases"] != _read_records(directory / "cases.jsonl")
            or selected["decisions"] != _read_records(directory / "decisions.jsonl")
            or selected["summary"] != manifest.get("selection")):
        raise ValueError("Frozen cases/decisions/summary no longer reproduce source selection")
    recorded_native = manifest.get("native_source")
    if not isinstance(recorded_native, dict):
        raise ValueError("Selection bundle requires a canonical Native source")
    native = _native_source(suite, source, selected, recorded_native.get("sdk_source_hashes"), artifacts)
    if native != recorded_native:
        raise ValueError("Frozen Native protocol no longer reproduces source selection")
    sdk_root = (directory / "sdk-source").resolve()
    if not sdk_root.is_relative_to(directory):
        raise ValueError("SDK snapshot path is outside bundle")
    _validate_sdk(sdk_root, native, selected["cases"])
    if _read_records(directory / "instruction-audit.jsonl") != _instruction_inventory(selected["cases"], sdk_root, suite):
        raise ValueError("Frozen instruction inventory no longer reproduces SDK source")
    if (directory / "cards.md").read_text(encoding="utf-8") != _cards(selected["cases"]):
        raise ValueError("Frozen cards no longer reproduce cases")
    native = deepcopy(native)
    native.update(selection_protocol=SELECTION_VERSION, selection_bundle_sha256=digest(manifest_path),
                  selection_summary=deepcopy(selected["summary"]))
    return {"manifest": manifest, "native_source": native, "cases": selected["cases"],
            "decisions": selected["decisions"]}
