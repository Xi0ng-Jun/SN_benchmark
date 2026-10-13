import json
from pathlib import Path

import pytest

from rag_eval.run_ledger import append_run, query_runs, render_table


def _run(tmp_path: Path, *, phase="finished"):
    run = tmp_path / "runs" / "qasper--p1--attempt-1"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({
        "suite": "qasper",
        "mode": "chunk",
        "created_at": "2026-10-13T03:15:00+00:00",
        "identity": {
            "product_bundle": {
                "source_manifest_fingerprint": "b" * 64,
                "partition_id": "p1",
            },
            "notebook_context": {"partition_id": "p1", "case_ids": ["qasper:c1", "qasper:c2"]},
            "product_services": "c" * 64,
            "runtime_settings": "d" * 64,
            "code": {"source_manifest_sha256": "e" * 64},
        },
    }))
    (run / "state.json").write_text(json.dumps({"phase": phase}))
    (run / "artifact-refs.json").write_text(json.dumps({
        "sources": {"sn": {"id": "sn-ref-1"}},
    }))
    return run


def test_append_derives_identity_and_keeps_optional_values_null(tmp_path):
    run = _run(tmp_path)
    ledger = tmp_path / "runs.jsonl"

    row = append_run(ledger, run)

    assert row == {
        "run_dir": str(run.resolve()),
        "suite": "qasper",
        "bundle_id": "b" * 64,
        "partition_id": "p1",
        "case_ids_count": 2,
        "mode": "chunk",
        "sn_source_ref": "sn-ref-1",
        "service_config_sha256": "c" * 64,
        "runtime_settings_sha256": "d" * 64,
        "state": "finished",
        "exit_code": None,
        "stderr_summary": None,
        "official_score": None,
        "official_scores_path": None,
        "task_worktree": None,
        "stage_metrics": None,
        "created_at": "2026-10-13T03:15:00+00:00",
    }
    assert json.loads(ledger.read_text()) == row


def test_append_reads_explicit_official_score_and_rejects_duplicate(tmp_path):
    run = _run(tmp_path)
    ledger = tmp_path / "runs.jsonl"
    scores = tmp_path / "scores.json"
    scores.write_text(json.dumps({"metrics": {"answer_f1": 2 / 3, "evidence_f1": 1.0}}))

    row = append_run(ledger, run, scores_path=scores)
    assert row["official_score"] == {"answer_f1": 2 / 3, "evidence_f1": 1.0}
    with pytest.raises(ValueError, match="duplicate run_dir"):
        append_run(ledger, run, scores_path=scores)


def test_append_reads_optional_stage_summary_without_filling_missing_values(tmp_path):
    run = _run(tmp_path)
    (run / "stage-metrics.json").write_text(json.dumps({"summary": {"status_counts": {"success": 2}}}))

    row = append_run(tmp_path / "runs.jsonl", run)

    assert row["stage_metrics"] == {"status_counts": {"success": 2}}
    assert row["official_score"] is None


def test_query_filters_and_table_are_stable(tmp_path):
    ledger = tmp_path / "runs.jsonl"
    first = _run(tmp_path)
    second = _run(tmp_path / "other", phase="failed")
    append_run(ledger, first)
    append_run(ledger, second)

    rows = query_runs(ledger, suite="qasper", mode="chunk", state="finished")
    assert [row["run_dir"] for row in rows] == [str(first.resolve())]
    table = render_table(rows)
    assert "suite" in table and "qasper" in table and "finished" in table
