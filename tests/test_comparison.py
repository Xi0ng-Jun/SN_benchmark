import json
import importlib.util
from pathlib import Path

import pytest

from rag_eval.comparison import compare_runs, summarize_run


def load_report_script():
    path = Path(__file__).resolve().parents[1] / "scripts/report_public_benchmark.py"
    spec = importlib.util.spec_from_file_location("report_public_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def make_run(tmp_path, name, identity, outputs, scores):
    run = tmp_path / name
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"comparison_identity": identity}), encoding="utf-8")
    write_jsonl(run / "outputs.jsonl", outputs)
    write_jsonl(run / "scores.jsonl", scores)
    return run


def output(identifier, mode="chunk", group="article-1", latency=1.0, status="success"):
    return {"id": identifier, "dataset": "squad", "mode": mode, "split": "regression",
            "group": group, "latency_seconds": latency, "status": status,
            "deterministic": {"evidence_hit": True, "citation_valid": 1, "citation_total": 1}}


def score(identifier, value, mode="chunk", status="valid"):
    return {"id": identifier, "dataset": "squad", "mode": mode,
            "metric": "Answer Correctness", "score": value if status == "valid" else None,
            "reason": None, "status": status, "seconds": 0.2, "repeat": 0}


def test_summary_keeps_attempted_valid_error_and_skipped_denominators(tmp_path):
    run = make_run(tmp_path, "run", {"protocol": 1}, [output("q1"), output("q2"), output("q3")],
                   [score("q1", .8), score("q2", 0, status="error"), score("q3", 0, status="skipped")])

    summary = summarize_run(run)

    metric = summary["groups"]["squad|chunk|regression"]["metrics"]["Answer Correctness"]
    assert metric == {"attempted": 3, "valid": 1, "error": 1, "skipped": 1, "missing": 0, "mean": .8}
    assert summary["groups"]["squad|chunk|regression"]["latency_seconds"]["p50"] == 1.0


def test_comparison_rejects_identity_mismatch_and_primary_sample_loss(tmp_path):
    baseline = make_run(tmp_path, "base", {"protocol": 1}, [output("q1")], [score("q1", .4)])
    incompatible = make_run(tmp_path, "bad", {"protocol": 2}, [output("q1")], [score("q1", .6)])
    lost = make_run(tmp_path, "lost", {"protocol": 1}, [], [])

    with pytest.raises(ValueError, match="comparison_identity"):
        compare_runs(incompatible, baseline)
    with pytest.raises(ValueError, match="sample loss"):
        compare_runs(lost, baseline)


def test_comparison_pairs_scores_and_reports_missing_metric_rows(tmp_path):
    outputs = [output("q1", group="a"), output("q2", group="b")]
    baseline = make_run(tmp_path, "base", {"protocol": 1}, outputs, [score("q1", .4), score("q2", .8)])
    current = make_run(tmp_path, "new", {"protocol": 1}, outputs, [score("q1", .7)])

    result = compare_runs(current, baseline)

    row = result["metrics"][0]
    assert row["metric"] == "Answer Correctness"
    assert row["comparable"] == 1
    assert row["mean_delta"] == pytest.approx(.3)
    assert row["missing_current"] == 1
    assert row["missing_baseline"] == 0
    assert row["interval"] is None


def test_comparison_projects_superset_baseline_to_frozen_candidate_plan(tmp_path):
    baseline_outputs = [output("q1"), output("q2")]
    baseline = make_run(tmp_path, "base", {"protocol": 1}, baseline_outputs,
                        [score("q1", .4), score("q2", .9)])
    current = make_run(tmp_path, "new", {"protocol": 1}, [output("q1")], [score("q1", .7)])
    manifest = json.loads((current / "manifest.json").read_text())
    manifest["planned_output_keys"] = [{"dataset": "squad", "id": "q1", "mode": "chunk"}]
    manifest["planned_output_count"] = 1
    manifest["planned_scope"] = "regression"
    (current / "manifest.json").write_text(json.dumps(manifest))

    result = compare_runs(current, baseline)

    assert result["samples"] == 1
    assert result["metrics"][0]["dataset"] == "squad"
    assert result["metrics"][0]["mean_delta"] == pytest.approx(.3)


def test_summary_pairs_modes_and_keeps_usage_and_span_observations_separate(tmp_path):
    chunk = output("q1", mode="chunk", latency=1.0)
    reasoning = output("q1", mode="reasoning", latency=3.0)
    chunk.update(usage={"call_count": 2, "calls_with_usage": 1, "total_tokens": 100},
                 squad_span_evidence={"status": "supported", "span_supported": True})
    reasoning.update(usage={"call_count": 3, "calls_with_usage": 3, "total_tokens": 180},
                     squad_span_evidence={"status": "unsupported", "span_supported": False})
    run = make_run(tmp_path, "paired", {"protocol": 1}, [chunk, reasoning],
                   [score("q1", .4, mode="chunk"), score("q1", .7, mode="reasoning")])

    summary = summarize_run(run)

    paired = summary["paired_modes"][0]
    assert paired["dataset"] == "squad" and paired["split"] == "regression"
    assert paired["metrics"]["Answer Correctness"]["valid_both"] == 1
    assert paired["metrics"]["Answer Correctness"]["reasoning_minus_chunk"] == pytest.approx(.3)
    assert paired["latency_seconds"]["reasoning_minus_chunk_mean"] == 2.0
    assert summary["usage"]["squad|chunk"]["calls_with_usage"] == 1
    assert summary["usage"]["squad|reasoning"]["total_tokens"] == 180
    assert summary["squad_span_support"]["squad|chunk"] == {"applicable": 1, "supported": 1, "rate": 1.0}


def test_product_repeat_does_not_count_failed_empty_outputs_as_agreement(tmp_path):
    report = load_report_script()
    primary = [{"dataset": "squad", "id": "q1", "mode": "chunk", "repeat": 0,
                "status": "product_error", "answer": "", "context_supported": False,
                "retrieval_context": []}]
    write_jsonl(tmp_path / "product-repeats.jsonl", [{**primary[0], "repeat": 1}])

    summary = report.repeat_summaries(tmp_path, primary)["product_repeat_agreement"]

    assert summary["attempted"] == 1
    assert summary["rows"][0]["answer_exact_match"] is None
    assert summary["rows"][0]["context_exact_match"] is None
    assert summary["groups"][0]["answer_comparable"] == 0
