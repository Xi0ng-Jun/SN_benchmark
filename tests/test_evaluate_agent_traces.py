import json

from rag_eval.agent_evaluator import evaluate_run
from rag_eval.artifacts import save_jsonl
from rag_eval.starter_runner import read_rows


def partial_trace():
    return {
        "trace_id": "trace-1",
        "completeness": "partial",
        "spans": [{"step_type": "retrieve", "summary": "retrieve"}],
    }


def test_evaluate_run_writes_separate_diagnostic_artifacts_without_judging(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    save_jsonl(run / "outputs.jsonl", [
        {
            "case_id": "case-1", "sample_id": "case-1", "suite": "qasper", "task": "qa",
            "mode": "reasoning", "status": "success", "output_available": True,
            "prediction": "An event", "trace": partial_trace(),
            "product_record": {
                "question": "What happened?", "answer": "An event",
                "context_available": True, "citations_available": True,
                "retrieval_context": ["The event happened."],
            },
        },
        {
            "case_id": "case-2", "sample_id": "case-2", "suite": "qasper", "task": "qa",
            "mode": "chunk", "status": "success", "output_available": True,
            "prediction": "A result", "trace": {"completeness": "none", "spans": []},
            "product_record": {"question": "What result?", "answer": "A result"},
        },
    ])
    output = tmp_path / "agent-report"

    summary = evaluate_run(run, output)

    assert summary["format"] == "sn-agent-evaluation-v1"
    assert summary["judge_enabled"] is False
    assert summary["completeness_counts"] == {"none": 1, "partial": 1, "complete": 0}
    assert len(read_rows(output / "agent-traces.jsonl")) == 2
    assert len(read_rows(output / "agent-diagnostics.jsonl")) == 2
    assert read_rows(output / "agent-scores.jsonl") == []
    assert summary["mode_comparison"][0]["status"] == "unpaired"
    assert not (run / "agent-scores.jsonl").exists()
    json.loads((output / "agent-summary.json").read_text(encoding="utf-8"))
