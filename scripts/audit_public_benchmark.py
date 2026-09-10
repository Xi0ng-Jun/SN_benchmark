#!/usr/bin/env python3
"""Audit a completed public benchmark run without invoking product or judge models."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag_eval.artifacts import digest, save_json
from rag_eval.datasets import iter_jsonl
from rag_eval.protocol import capture_context


DATASETS = ("squad", "drop")
MODES = ("chunk", "reasoning")
PRIMARY = ("Answer Correctness", "Faithfulness", "Answer Relevancy")
DIAGNOSTIC = ("Contextual Recall", "Contextual Precision", "Contextual Relevancy")
# Native Ask postprocessing in project/backend/app/services/ask_service.py
# (`run_reasoning`, completeness_unavailable branch). Keep this versioned audit
# constant exact so an unrelated prefix cannot masquerade as synthesis ownership.
COMPLETENESS_WARNING = (
    "当前精确完整枚举支持 Knowhow 整表物理行清单与直接行计数，"
    "以及元素清单（公式/表格/图片/代码块）、知识对象清单与来源清单；"
    "条件筛选、去重、分组或其他集合请求本次仍来自相关性检索，"
    "不能视为全部结果。"
)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows(path):
    return list(iter_jsonl(Path(path)))


def add_error(errors, code, **detail):
    errors.append({"code": code, **detail})


def synthesis_owns_answer(answer, successful_captures, response=None):
    if not successful_captures:
        return False
    final = successful_captures[-1]
    if not final.get("sectioned"):
        body = str(final.get("answer") or "").strip()
        delivered = str(answer or "").strip()
        if body == delivered:
            return True
        intent = (response or {}).get("intent") or {}
        return (intent.get("completeness_required") is True
                and delivered == f"> {COMPLETENESS_WARNING}\n\n{body}")
    position = 0
    for capture in successful_captures:
        if not capture.get("sectioned"):
            return False
        body = str(capture.get("answer") or "").strip()
        found = str(answer or "").find(body, position)
        if not body or found < 0:
            return False
        position = found + len(body)
    return True


def cell_split_counts(questions, primary, repeats, scores):
    splits = sorted({str(row.get("split") or "unknown") for row in questions.values()})
    result = {}
    for split in splits:
        ids = {identity for identity, row in questions.items()
               if str(row.get("split") or "unknown") == split}
        primary_rows = [row for row in primary if row.get("id") in ids]
        result[split] = {
            "planned": len(ids),
            "actual_primary": len(primary_rows),
            "product_repeats": sum(row.get("id") in ids for row in repeats),
            "product_errors": sum(row.get("status") != "success" for row in primary_rows),
            "judge_errors": sum(row.get("id") in ids and row.get("status") == "error"
                                for row in scores),
        }
    return result


def expected_bundle_identity(bundle):
    hashes = {str(path.relative_to(bundle)): digest(path) for dataset in DATASETS
              for path in sorted((bundle / dataset).glob("*.jsonl"))}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(), hashes


def audit_cell(run, bundle, dataset, mode, expected_questions, outputs, repeats,
               errors, facts):
    cell = run / "cells" / dataset / mode
    state_path, mapping_path, db_path = cell / "state.json", cell / "document-map.json", cell / "runtime/database.db"
    for path in (state_path, mapping_path, db_path, cell / "identity.json"):
        if not path.exists():
            add_error(errors, "missing_cell_artifact", cell=f"{dataset}/{mode}", path=str(path.relative_to(run)))
    if not all(path.exists() for path in (state_path, mapping_path, db_path)):
        return
    state, mapping = read_json(state_path), read_json(mapping_path)
    identity = read_json(cell / "identity.json") if (cell / "identity.json").exists() else {}
    expected_input_hashes = {str(path.relative_to(bundle)): digest(path)
                             for path in sorted(bundle.rglob("*.json*")) if "raw" not in path.parts}
    if identity.get("mode") != mode or identity.get("input_hashes") != expected_input_hashes:
        add_error(errors, "cell_identity_mismatch", cell=f"{dataset}/{mode}")
    documents = {row["id"]: row for row in rows(bundle / dataset / "documents.jsonl")}
    if set(mapping) != set(documents):
        add_error(errors, "document_mapping_identity_mismatch", cell=f"{dataset}/{mode}",
                  missing=sorted(set(documents) - set(mapping)), unexpected=sorted(set(mapping) - set(documents)))
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        notebook = state.get("notebook_id")
        source_rows = connection.execute(
            "SELECT id, notebook_id, file_name, file_hash FROM sources WHERE notebook_id=?", (notebook,)).fetchall()
        sources = {str(row["id"]): row for row in source_rows}
        db_chunks = connection.execute(
            "SELECT id, source_id FROM chunks WHERE notebook_id=?", (notebook,)).fetchall()
        chunk_to_source = {str(row["id"]): str(row["source_id"]) for row in db_chunks}
        element_rows = connection.execute(
            "SELECT e.id, e.source_id FROM source_elements e JOIN sources s "
            "ON s.id=e.source_id WHERE s.notebook_id=?", (notebook,)).fetchall()
        object_to_source = {**chunk_to_source,
                            **{str(row["id"]): str(row["source_id"]) for row in element_rows}}
        embedded = connection.execute(
            "SELECT count(*) FROM chunk_embeddings WHERE notebook_id=?", (notebook,)).fetchone()[0]
        mapped_sources = {str(value.get("source_id")) for value in mapping.values()}
        if mapped_sources != set(sources):
            add_error(errors, "source_mapping_mismatch", cell=f"{dataset}/{mode}")
        for public_id, value in mapping.items():
            document = documents.get(public_id)
            if document and value.get("text_sha256") != document.get("text_sha256"):
                add_error(errors, "document_text_hash_mismatch", cell=f"{dataset}/{mode}", id=public_id)
            source_id = str(value.get("source_id"))
            if source_id not in sources:
                continue
            if sources[source_id]["file_hash"] and sources[source_id]["file_hash"] != value.get("text_sha256"):
                add_error(errors, "persisted_source_hash_mismatch", cell=f"{dataset}/{mode}", id=public_id)
            expected_chunks = {str(item) for item in value.get("chunk_ids", [])}
            actual_chunks = {chunk for chunk, owner in chunk_to_source.items() if owner == source_id}
            if not expected_chunks or expected_chunks != actual_chunks:
                add_error(errors, "chunk_mapping_mismatch", cell=f"{dataset}/{mode}", id=public_id)
        if embedded != len(db_chunks) or state.get("embedded_chunks") != embedded:
            add_error(errors, "embedding_coverage_mismatch", cell=f"{dataset}/{mode}",
                      chunks=len(db_chunks), embedded=embedded, state_embedded=state.get("embedded_chunks"))
        if state.get("documents") != len(documents) or state.get("chunks") != len(db_chunks):
            add_error(errors, "cell_state_count_mismatch", cell=f"{dataset}/{mode}")

        db_answers = connection.execute(
            "SELECT id, question, payload FROM answers WHERE notebook_id=?", (notebook,)).fetchall()
        answers_by_id = {str(row["id"]): row for row in db_answers}
        successful_answer_ids = set()
        for output in [*outputs, *repeats]:
            question = expected_questions[output["id"]]
            if output.get("question") != question["question"]:
                add_error(errors, "question_changed_or_context_injected", cell=f"{dataset}/{mode}", id=output["id"])
            if output.get("gold_document_ids") != question.get("gold_document_ids"):
                add_error(errors, "gold_identity_mismatch", cell=f"{dataset}/{mode}", id=output["id"])
            for gold in output.get("gold_document_ids", []):
                if gold not in mapping:
                    add_error(errors, "gold_source_not_mapped", cell=f"{dataset}/{mode}", id=output["id"], gold=gold)
            if output.get("retrieved_document_ids") != [
                    next((key for key, value in mapping.items() if value.get("source_id") == source), None)
                    for source in output.get("source_ids", [])]:
                add_error(errors, "retrieved_source_mapping_mismatch", cell=f"{dataset}/{mode}", id=output["id"])
            response = output.get("response") or {}
            answer_id = response.get("answer_id")
            if response and (response.get("mode") != mode
                             or response.get("answer") != output.get("answer")):
                add_error(errors, "native_response_identity_mismatch",
                          cell=f"{dataset}/{mode}", id=output["id"],
                          repeat=output.get("repeat", 0))
            if output.get("status") not in ("success", "product_error"):
                add_error(errors, "invalid_output_status", cell=f"{dataset}/{mode}", id=output["id"])
            if answer_id:
                if str(answer_id) not in answers_by_id:
                    add_error(errors, "persisted_answer_missing", cell=f"{dataset}/{mode}", id=output["id"])
                else:
                    successful_answer_ids.add(str(answer_id))
                    saved = answers_by_id[str(answer_id)]
                    try:
                        payload = json.loads(saved["payload"])
                    except Exception:
                        add_error(errors, "persisted_answer_payload_invalid", cell=f"{dataset}/{mode}", id=output["id"])
                    else:
                        if saved["question"] != output["question"].strip() or payload != response:
                            add_error(errors, "persisted_answer_payload_mismatch", cell=f"{dataset}/{mode}", id=output["id"])
            elif output.get("status") == "success":
                add_error(errors, "successful_output_without_answer_id", cell=f"{dataset}/{mode}", id=output["id"])
            captures = output.get("captures") or []
            for capture in captures:
                for target in (capture.get("id_map") or {}).values():
                    object_id = str(target.get("object_id") or "")
                    source_id = str(target.get("source_id") or "")
                    if source_id not in sources or object_to_source.get(object_id) != source_id:
                        add_error(errors, "capture_object_source_mismatch",
                                  cell=f"{dataset}/{mode}", id=output["id"],
                                  object_id=object_id, repeat=output.get("repeat", 0))
            successful = [capture for capture in captures if capture.get("succeeded") and str(capture.get("answer", "")).strip()]
            if output.get("status") == "success" and not synthesis_owns_answer(
                    output.get("answer"), successful, response):
                add_error(errors, "synthesis_answer_ownership_mismatch",
                          cell=f"{dataset}/{mode}", id=output["id"],
                          repeat=output.get("repeat", 0))
            supported = bool(output.get("context_supported"))
            if supported:
                if not successful or successful[-1].get("sectioned"):
                    add_error(errors, "unsupported_capture_marked_supported", cell=f"{dataset}/{mode}", id=output["id"])
                else:
                    capture = successful[-1]
                    try:
                        captured = capture_context(capture)
                    except Exception:
                        add_error(errors, "capture_record_invalid", cell=f"{dataset}/{mode}", id=output["id"])
                    else:
                        for field in ("context_block", "retrieval_context", "retrieved_ids", "source_ids", "handles"):
                            if output.get(field) != captured[field]:
                                add_error(errors, "capture_projection_mismatch", cell=f"{dataset}/{mode}", id=output["id"], field=field)
            elif output.get("retrieval_context") or output.get("context_block"):
                add_error(errors, "unsupported_context_not_empty", cell=f"{dataset}/{mode}", id=output["id"])
        unexpected_answers = set(answers_by_id) - successful_answer_ids
        if unexpected_answers:
            add_error(errors, "unexpected_persisted_answers", cell=f"{dataset}/{mode}", count=len(unexpected_answers))
        facts[f"{dataset}/{mode}"] = {"documents": len(source_rows), "chunks": len(db_chunks),
                                      "embedded_chunks": embedded, "persisted_answers": len(db_answers),
                                      "project_revision": identity.get("project_revision"),
                                      "model_config_sha256": identity.get("model_config_sha256")}
    finally:
        connection.close()


def audit_usage(run):
    counts, token_totals = Counter(), Counter()
    missing_usage = total = 0
    latency_ms = 0.0
    malformed = 0
    paths = sorted((run / "cells").glob("*/*/runtime/logs/**/llm*.jsonl"))
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    malformed += 1
                    continue
                total += 1
                counts[(str(row.get("kind", "unknown")), str(row.get("status", "unknown")),
                        str(row.get("model", "unknown")))] += 1
                latency_ms += float(row.get("latency_ms") or 0)
                usage = row.get("usage")
                if not isinstance(usage, dict):
                    missing_usage += 1
                else:
                    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        if usage.get(name) is not None:
                            token_totals[name] += int(usage[name])
    scheduler_groups = defaultdict(lambda: {"count": 0, "queue_ms": 0.0,
                                            "execution_ms": 0.0})
    scheduler_queue_ms = scheduler_execution_ms = 0.0
    scheduler_total = scheduler_malformed = 0
    scheduler_paths = sorted((run / "cells").glob("*/*/runtime/logs/**/events-*.jsonl"))
    for path in scheduler_paths:
        relative = path.relative_to(run / "cells").parts
        dataset, mode = relative[0], relative[1]
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    scheduler_malformed += 1
                    continue
                if row.get("kind") != "model_scheduler":
                    continue
                scheduler_total += 1
                key = (dataset, mode, str(row.get("workload_id", "unknown")),
                       str(row.get("model", "unknown")),
                       str(row.get("status", "unknown")))
                queue_ms = float(row.get("queue_latency_ms") or 0)
                execution_ms = float(row.get("execution_latency_ms") or 0)
                scheduler_groups[key]["count"] += 1
                scheduler_groups[key]["queue_ms"] += queue_ms
                scheduler_groups[key]["execution_ms"] += execution_ms
                scheduler_queue_ms += queue_ms
                scheduler_execution_ms += execution_ms
    scheduler = {
        "source": "isolated per-cell runtime/logs/**/events-*.jsonl model_scheduler rows",
        "accounting_relation": "separate scheduler observations; do not sum with raw provider calls or retries",
        "files": len(scheduler_paths),
        "total_events": scheduler_total,
        "malformed_log_lines": scheduler_malformed,
        "queue_latency_seconds_sum": scheduler_queue_ms / 1000,
        "execution_latency_seconds_sum": scheduler_execution_ms / 1000,
        "by_dataset_mode_workload_model_status": [
            {"dataset": key[0], "mode": key[1], "workload": key[2],
             "model": key[3], "status": key[4], "count": value["count"],
             "queue_latency_seconds_sum": value["queue_ms"] / 1000,
             "execution_latency_seconds_sum": value["execution_ms"] / 1000}
            for key, value in sorted(scheduler_groups.items())
        ],
        "embedding_rerank_usage": {
            "token_usage": None,
            "monetary_cost": None,
            "reason": "scheduler events expose calls and latency but no embedding/rerank tokens or verified pricing",
        },
    }
    return {"source": "isolated per-cell runtime/logs/**/llm*.jsonl",
            "privacy": "aggregate only; prompts, responses, support IDs, and user paths omitted",
            "verification_boundary": "log records are trusted observations; capture blocks are verified at the application synthesis boundary, not against a separate raw provider request",
            "files": len(paths), "total_calls": total, "malformed_log_lines": malformed,
            "calls_missing_usage": missing_usage, "token_totals": dict(token_totals),
            "latency_seconds_sum": latency_ms / 1000,
            "calls_by_kind_status_model": [
                {"kind": key[0], "status": key[1], "model": key[2], "count": value}
                for key, value in sorted(counts.items())],
            "monetary_cost": None, "cost_reason": "no verified dated provider pricing table",
            "scheduler_observations": scheduler}


def audit_run(run, bundle):
    errors, facts = [], {}
    manifest = read_json(run / "manifest.json")
    bundle_sha, input_hashes = expected_bundle_identity(bundle)
    identity = manifest.get("comparison_identity") or {}
    if identity.get("bundle_sha256") != bundle_sha:
        add_error(errors, "bundle_identity_mismatch", expected=bundle_sha, actual=identity.get("bundle_sha256"))
    if identity.get("protocol") != "public-notebook-v1" or identity.get("modes") != list(MODES):
        add_error(errors, "protocol_identity_mismatch")
    frozen_questions = {dataset: {row["id"]: row for row in rows(bundle / dataset / "questions.jsonl")}
                        for dataset in DATASETS}
    planned_rows = manifest.get("planned_output_keys")
    if isinstance(planned_rows, list):
        if not isinstance(manifest.get("planned_scope"), str):
            add_error(errors, "planned_scope_missing")
        expected_keys = {(str(row["dataset"]), str(row["id"]), str(row["mode"]), 0)
                         for row in planned_rows}
        if len(expected_keys) != len(planned_rows) or len(expected_keys) != manifest.get("planned_output_count"):
            add_error(errors, "planned_output_keys_invalid")
    else:
        expected_keys = {(dataset, identifier, mode, 0) for dataset in DATASETS
                         for identifier in frozen_questions[dataset] for mode in MODES}
        if manifest.get("planned_output_count") != len(expected_keys):
            add_error(errors, "legacy_planned_output_count_mismatch")
    selected_questions = {dataset: {identifier: question for identifier, question in questions.items()
                                    if any(key[:2] == (dataset, identifier) for key in expected_keys)}
                          for dataset, questions in frozen_questions.items()}
    aggregated_questions = rows(run / "questions.jsonl")
    expected_question_rows = [question for dataset in DATASETS for question in frozen_questions[dataset].values()]
    if aggregated_questions != expected_question_rows:
        add_error(errors, "aggregated_questions_not_frozen_bundle")
    outputs = rows(run / "outputs.jsonl")
    output_keys = [(row.get("dataset"), row.get("id"), row.get("mode"), row.get("repeat", 0)) for row in outputs]
    if len(output_keys) != len(set(output_keys)):
        add_error(errors, "duplicate_output_identity")
    actual_keys = set(output_keys)
    if actual_keys != expected_keys:
        add_error(errors, "output_identity_mismatch", missing_count=len(expected_keys - actual_keys),
                  unexpected_count=len(actual_keys - expected_keys))
    by_cell = defaultdict(list)
    for output in outputs:
        dataset, identifier, mode = output.get("dataset"), output.get("id"), output.get("mode")
        if dataset not in frozen_questions or identifier not in frozen_questions.get(dataset, {}) or mode not in MODES:
            continue
        by_cell[(dataset, mode)].append(output)
        frozen = frozen_questions[dataset][identifier]
        for field in ("split", "group", "answer_type", "references", "expected_answer", "calibration", "smoke", "diagnostic"):
            if output.get(field) != frozen.get(field):
                add_error(errors, "output_frozen_identity_mismatch", id=identifier, mode=mode, field=field)
    repeats_by_cell = defaultdict(list)
    repeat_keys = []
    for path in sorted((run / "cells").glob("*/*/product-repeat-*.jsonl")):
        for output in rows(path):
            key = (output.get("dataset"), output.get("id"), output.get("mode"),
                   output.get("repeat", 0))
            repeat_keys.append(key)
            if (key[0] not in frozen_questions
                    or key[1] not in frozen_questions.get(key[0], {})
                    or key[2] not in MODES or not isinstance(key[3], int)
                    or key[3] <= 0):
                add_error(errors, "unexpected_product_repeat_identity",
                          dataset=key[0], id=key[1], mode=key[2], repeat=key[3])
                continue
            repeats_by_cell[(key[0], key[2])].append(output)
    if len(repeat_keys) != len(set(repeat_keys)):
        add_error(errors, "duplicate_product_repeat_identity")
    scores = rows(run / "scores.jsonl")
    scores_by_cell = defaultdict(list)
    for score in scores:
        scores_by_cell[(score.get("dataset"), score.get("mode"))].append(score)
    planned_cells = sorted({(key[0], key[2]) for key in expected_keys})
    for dataset, mode in planned_cells:
        audit_cell(run, bundle, dataset, mode, selected_questions[dataset],
                   by_cell[(dataset, mode)], repeats_by_cell[(dataset, mode)],
                   errors, facts)
        facts.setdefault(f"{dataset}/{mode}", {})["splits"] = cell_split_counts(
            selected_questions[dataset], by_cell[(dataset, mode)],
            repeats_by_cell[(dataset, mode)], scores_by_cell[(dataset, mode)])

    score_keys = [(row.get("dataset"), row.get("id"), row.get("mode"), row.get("metric"), row.get("repeat", 0)) for row in scores]
    if len(score_keys) != len(set(score_keys)):
        add_error(errors, "duplicate_score_identity")
    output_by_key = {(row["dataset"], row["id"], row["mode"]): row for row in outputs}
    actual_score_keys = set(score_keys)
    for key, output in output_by_key.items():
        expected_metrics = set(PRIMARY) | (set(DIAGNOSTIC) if output.get("diagnostic") else set())
        for metric in expected_metrics:
            if (*key, metric, 0) not in actual_score_keys:
                add_error(errors, "missing_primary_score_denominator", id=key[1], mode=key[2], metric=metric)
    for row in scores:
        key = (row.get("dataset"), row.get("id"), row.get("mode"))
        output = output_by_key.get(key)
        if output is None:
            add_error(errors, "score_without_output", id=row.get("id"), mode=row.get("mode"))
            continue
        repeat = row.get("repeat", 0)
        allowed = set(PRIMARY) | (set(DIAGNOSTIC) if output.get("diagnostic") and repeat == 0 else set())
        if row.get("metric") not in allowed or not isinstance(repeat, int) or repeat < 0:
            add_error(errors, "unexpected_score_identity", id=row.get("id"), metric=row.get("metric"), repeat=repeat)
        if repeat > 0 and not output.get("calibration"):
            add_error(errors, "repeat_outside_calibration", id=row.get("id"), repeat=repeat)
        if row.get("status") not in ("valid", "error", "skipped"):
            add_error(errors, "invalid_score_status", id=row.get("id"), metric=row.get("metric"))
    repeat_groups = defaultdict(set)
    for dataset, identifier, mode, metric, repeat in score_keys:
        if isinstance(repeat, int) and repeat > 0:
            repeat_groups[(dataset, identifier, mode, repeat)].add(metric)
    for key, metrics in repeat_groups.items():
        if metrics != set(PRIMARY):
            add_error(errors, "incomplete_repeat_denominator", id=key[1], mode=key[2], repeat=key[3])
    revisions = {value.get("project_revision") for value in facts.values()}
    model_configs = {value.get("model_config_sha256") for value in facts.values()}
    if len(revisions) != 1 or None in revisions or len(model_configs) != 1 or None in model_configs:
        add_error(errors, "cross_cell_runtime_identity_mismatch")
    usage = audit_usage(run)
    judge_errors = sum(row.get("status") == "error" for row in scores)
    product_errors = sum(row.get("status") != "success" for row in outputs)
    audit = {"status": "failed" if errors else "passed", "integrity_errors": errors,
             "facts": facts, "input_hashes": input_hashes,
             "capture_verification": "exact against saved synthesis interceptor records; no raw-provider equivalence claim"}
    completion = "completed_with_product_errors" if product_errors else (
        "completed_with_metric_errors" if judge_errors else "completed")
    operational = {"completion_status": completion, "calibration_status": "pending",
                   "run_statuses": [completion, "calibration_pending"],
                   "outputs": len(outputs), "product_errors": product_errors,
                   "scores": len(scores), "judge_errors": judge_errors, "usage": usage,
                   "integrity_status": audit["status"]}
    return audit, operational


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--bundle", type=Path, default=ROOT / "data/public-benchmark-v1")
    args = parser.parse_args(argv)
    run, bundle = args.run_dir.resolve(), args.bundle.resolve()
    try:
        audit, operational = audit_run(run, bundle)
    except Exception as exc:
        audit = {"status": "failed", "integrity_errors": [
            {"code": "audit_input_or_schema_error", "error_type": type(exc).__name__, "message": str(exc)}]}
        operational = {"integrity_status": "failed", "calibration_status": "pending",
                       "run_statuses": ["calibration_pending"]}
    save_json(run / "audit.json", audit)
    save_json(run / "operational-summary.json", operational)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 2 if audit["status"] != "passed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
