"""Offline reporting contracts: denominators, provenance and untrusted text."""
import json
from pathlib import Path

import pytest

from rag_eval.artifacts import digest, save_json, save_jsonl
from rag_eval.starter_protocol import fingerprint
from rag_eval.starter_results import planned_result, result_record
from rag_eval.experiment_aggregation import aggregate_runs, discover_runs, write_dashboard

PRIMARY = "product.boolq.explicit_conclusion.v1"
CITATION = "product.citation_object.existence_ratio"


def make_run(root, name="chunk", mode="chunk", model="model-a", corpus="same"):
    run = root / name
    run.mkdir(parents=True)
    cases = [{"suite": "boolq", "task": "boolq", "case_id": key, "sample_id": key,
              "question": "Is this supported?", "references": ["Yes"],
              "raw_row": {"question": "Is this supported?", "passage": "A source.", "answer": True},
              "product_review": {"status": "approved"}} for key in ("q1", "q2")]
    save_jsonl(run / "input/cases.jsonl", cases)
    identity = {"source": {"suite": "boolq", "revision": "fixture-v1",
                           "artifacts": {"cases.jsonl": digest(run / "input/cases.jsonl")}},
                "models": {"judge": model}, "track": "R", "code": {"revision": "test"},
                "runtime_settings": "settings", "product_services": "services",
                "product_bundle": {"corpus": corpus}, "audits_sha256": "audit"}
    protocol_id = fingerprint({**identity, "mode": mode})
    planned = [planned_result(case, run_id=name, protocol_id=protocol_id, track="R", mode=mode, scorer=scorer)
               for case in cases for scorer in (PRIMARY, CITATION)]
    save_jsonl(run / "planned.jsonl", planned)
    save_jsonl(run / "outputs.jsonl", [{"case_id": "q1", "sample_id": "q1", "suite": "boolq", "task": "boolq",
        "status": "success", "output_available": True, "prediction": "Final answer: Yes",
        "product_record": {"question": "Is this supported?", "answer": "Final answer: Yes",
            "retrieval_context": ["A source."], "context_supported": True,
            "response": {"citations": [{"source_id": "source-1", "element_id": "chunk-1"}]}}}])
    save_jsonl(run / "scores.jsonl", [result_record(planned[0], status="scored", score=1.0,
        output_available=True, reason="matched", details={"scorer_result": {"label": "Yes"}})])
    save_jsonl(run / "model-events.jsonl", [
        {"case_id": "q1", "request_id": planned[0]["result_id"], "role": "judge", "call_id": "call-1",
         "event": "completed", "raw_client_response": {"answer": 1}, "api_key": "SECRET-TEST"},
        {"case_id": "not-a-selected-question", "call_id": "call-2", "event": "completed"}])
    save_json(run / "state.json", {"phase": "scoring"})
    save_json(run / "manifest.json", {"format": "public-starter-run-v1", "run_id": name,
        "suite": "boolq", "track": "R", "mode": mode, "product_protocol": "legacy",
        "protocol_id": protocol_id, "pairing_id": fingerprint(identity), "identity": identity,
        "planned_sha256": digest(run / "planned.jsonl"), "planned_predictions": 2, "planned_scores": 4})
    return run


def test_question_counts_do_not_multiply_by_number_of_metrics(tmp_path):
    data = aggregate_runs([make_run(tmp_path)])
    assert data["summary"]["planned_outputs"] == 2
    assert data["summary"]["planned_scores"] == 4
    assert data["summary"]["saved_outputs"] == 1
    assert "mean_scored" not in data["summary"]


def test_missing_outputs_and_scores_remain_selectable_entries(tmp_path):
    data = aggregate_runs([make_run(tmp_path)])
    assert len(data["entries"]) == 4
    assert len(data["observations"]) == 2
    assert sum(e["status"] == "missing" for e in data["entries"]) == 3
    assert sum(e["output_status"] == "missing" for e in data["entries"]) == 2
    assert all(e["score"] is None for e in data["entries"] if e["status"] == "missing")


def test_drilldown_preserves_source_response_score_and_bound_events(tmp_path):
    data = aggregate_runs([make_run(tmp_path)])
    entry = next(e for e in data["entries"] if e["status"] == "scored")
    detail = data["observations"][entry["observation_id"]]
    assert detail["case"]["raw_row"]["answer"] is True
    assert detail["output"]["product_record"]["retrieval_context"] == ["A source."]
    assert entry["result"]["details"]["scorer_result"]["label"] == "Yes"
    assert len(detail["events"]) == 1
    assert detail["events"][0]["request_id"] == entry["plan"]["result_id"]
    assert "SECRET-TEST" not in json.dumps(data)
    assert data["catalog"][PRIMARY]["formula"]


def test_mode_pair_keeps_identity_but_different_models_do_not(tmp_path):
    data = aggregate_runs([make_run(tmp_path), make_run(tmp_path, "reasoning", "reasoning"),
                           make_run(tmp_path, "other", "reasoning", model="model-b")])
    by_run = {e["run_id"]: e for e in data["entries"]}
    assert by_run["chunk"]["config_family"] == by_run["reasoning"]["config_family"]
    assert by_run["chunk"]["pairing_id"] == by_run["reasoning"]["pairing_id"]
    assert by_run["chunk"]["config_family"] != by_run["other"]["config_family"]


def test_duplicate_input_and_tampered_scores_are_rejected(tmp_path):
    run = make_run(tmp_path)
    with pytest.raises(ValueError, match="distinct|Duplicate"):
        aggregate_runs([run, run / "."])
    scores = json.loads((run / "scores.jsonl").read_text())
    scores["score"] = 3.0
    save_jsonl(run / "scores.jsonl", [scores])
    with pytest.raises(ValueError):
        aggregate_runs([run])


def test_different_candidate_corpora_are_not_pooled_into_one_mean(tmp_path):
    data = aggregate_runs([make_run(tmp_path), make_run(tmp_path, "different-corpus", corpus="other")])
    assert data["runs"][0]["config_family"] != data["runs"][1]["config_family"]


def test_discovery_ignores_source_manifests_and_includes_initialization(tmp_path):
    run = make_run(tmp_path)
    save_json(run / "input/manifest.json", {"protocol_version": "public-selection-v1"})
    save_json(tmp_path / "source/manifest.json", {"protocol_version": "public-starter-v1"})
    save_json(tmp_path / "initializing/state.json", {"phase": "initializing"})
    assert discover_runs(tmp_path) == sorted([run, tmp_path / "initializing"])


def test_html_data_cannot_break_out_of_script_and_report_cannot_overwrite_run(tmp_path):
    run = make_run(tmp_path)
    rows = json.loads((run / "outputs.jsonl").read_text())
    rows["prediction"] = '</script><img src=x onerror="window.PWNED=true">'
    save_jsonl(run / "outputs.jsonl", [rows])
    output = write_dashboard([run], tmp_path / "report")
    html = (output / "dashboard.html").read_text()
    assert rows["prediction"] not in html
    assert json.loads((output / "dashboard-data.json").read_text())["observations"]
    assert (output / "summary.json").exists() and (output / "audit.json").exists()
    with pytest.raises((ValueError, FileExistsError)):
        write_dashboard([run], run / "report")


def test_source_case_tampering_is_not_silently_shown_as_evidence(tmp_path):
    run = make_run(tmp_path)
    save_jsonl(run / "input/cases.jsonl", [{"case_id": "q1", "question": "changed"}])
    with pytest.raises(ValueError, match="case|artifact|source"):
        aggregate_runs([run])


def test_metric_explanations_cover_the_actual_default_plan():
    from rag_eval.metric_catalog import benchmark_rows, describe_metric
    from rag_eval.selection_execution import expected_scorers
    rows = benchmark_rows()
    suites = {row[0] for row in rows}
    assert len(suites) == 10
    for suite in suites:
        for track, label in (("N", "Native（N）"), ("R", "SN Product（R；chunk / reasoning）")):
            actual = {scorer for name, path, scorer in rows if name == suite and path == label}
            assert actual == set(expected_scorers(suite, track))
            assert all(describe_metric(scorer)["method"] != "未登记" for scorer in actual)
