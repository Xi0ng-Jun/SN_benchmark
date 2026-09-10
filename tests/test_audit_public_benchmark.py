import importlib.util
import json
from pathlib import Path


def load_audit():
    path = Path(__file__).resolve().parents[1] / "scripts/audit_public_benchmark.py"
    spec = importlib.util.spec_from_file_location("audit_public_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_synthesis_answer_ownership_handles_native_single_and_sectioned_answers():
    audit = load_audit()

    assert audit.synthesis_owns_answer(
        "blue", [{"answer": "blue", "succeeded": True, "sectioned": False}]
    )
    assert not audit.synthesis_owns_answer(
        "blue", [{"answer": "red", "succeeded": True, "sectioned": False}]
    )
    warning = audit.COMPLETENESS_WARNING
    response = {"intent": {"completeness_required": True}}
    assert audit.synthesis_owns_answer(
        f"> {warning}\n\nblue",
        [{"answer": "blue", "succeeded": True, "sectioned": False}],
        response,
    )
    assert not audit.synthesis_owns_answer(
        "> arbitrary prefix\n\nblue",
        [{"answer": "blue", "succeeded": True, "sectioned": False}],
        response,
    )
    assert audit.synthesis_owns_answer(
        "## First\n\nalpha\n\n## Second\n\nbeta",
        [{"answer": "alpha", "succeeded": True, "sectioned": True},
         {"answer": "beta", "succeeded": True, "sectioned": True}],
    )
    assert not audit.synthesis_owns_answer(
        "## Second\n\nbeta\n\n## First\n\nalpha",
        [{"answer": "alpha", "succeeded": True, "sectioned": True},
         {"answer": "beta", "succeeded": True, "sectioned": True}],
    )


def test_cell_split_counts_separate_primary_repeats_and_errors():
    audit = load_audit()
    questions = {"q1": {"split": "debug"}, "q2": {"split": "regression"}}
    primary = [{"id": "q1", "status": "success"},
               {"id": "q2", "status": "product_error"}]
    repeats = [{"id": "q1", "status": "success", "repeat": 1}]
    scores = [{"id": "q1", "status": "error"}, {"id": "q2", "status": "skipped"}]

    assert audit.cell_split_counts(questions, primary, repeats, scores) == {
        "debug": {"planned": 1, "actual_primary": 1, "product_repeats": 1,
                  "product_errors": 0, "judge_errors": 1},
        "regression": {"planned": 1, "actual_primary": 1, "product_repeats": 0,
                       "product_errors": 1, "judge_errors": 0},
    }


def test_usage_keeps_scheduler_observations_separate_from_provider_logs(tmp_path):
    audit = load_audit()
    logs = tmp_path / "cells/squad/chunk/runtime/logs/user-local"
    logs.mkdir(parents=True)
    (logs / "llm-2026.jsonl").write_text(json.dumps({
        "kind": "embedding", "status": "ok", "model": "text-embedding-v4",
        "latency_ms": 90,
    }) + "\n")
    (logs / "events-2026.jsonl").write_text(json.dumps({
        "kind": "model_scheduler", "status": "ok", "workload_id": "chunk_embedding",
        "model": "text-embedding-v4", "queue_latency_ms": 4,
        "execution_latency_ms": 80,
    }) + "\n")

    usage = audit.audit_usage(tmp_path)

    assert usage["total_calls"] == 1
    scheduler = usage["scheduler_observations"]
    assert scheduler["total_events"] == 1
    assert scheduler["queue_latency_seconds_sum"] == .004
    assert scheduler["execution_latency_seconds_sum"] == .08
    assert scheduler["by_dataset_mode_workload_model_status"] == [{
        "dataset": "squad", "mode": "chunk", "workload": "chunk_embedding",
        "model": "text-embedding-v4", "status": "ok", "count": 1,
        "queue_latency_seconds_sum": .004, "execution_latency_seconds_sum": .08,
    }]
    assert scheduler["embedding_rerank_usage"]["token_usage"] is None
    assert scheduler["embedding_rerank_usage"]["monetary_cost"] is None
