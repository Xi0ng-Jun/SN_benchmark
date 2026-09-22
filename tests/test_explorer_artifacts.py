"""Offline boundaries for lazy artifacts, provenance and native identities."""
import json
import shutil

import pytest

from rag_eval.artifacts import digest, save_json, save_jsonl
from rag_eval.explorer_artifacts import write_explorer_data
from rag_eval.starter_protocol import fingerprint
from test_experiment_dashboard import make_run


def export(tmp_path, runs, name="report"):
    directory = tmp_path / name
    directory.mkdir()
    return write_explorer_data(runs, directory), directory


def detail(data, directory, case_id="q1", run_id=None):
    selected = next(o for o in data["observations"].values() if o["case_id"] == case_id and
                    (run_id is None or o["run_key"] == next(r["key"] for r in data["runs"] if r["run_id"] == run_id)))
    script = (directory / selected["detail_file"]).read_text()
    return json.loads(json.loads(script.split(", JSON.parse(", 1)[1].removesuffix("));\n")))


def reidentify(run, identity):
    manifest = json.loads((run / "manifest.json").read_text())
    manifest.update(identity=identity, protocol_id=fingerprint({**identity, "mode": manifest["mode"]}), pairing_id=fingerprint(identity))
    plans = [json.loads(row) for row in (run / "planned.jsonl").read_text().splitlines()]
    ids = {}
    for row in plans:
        old = row["result_id"]
        row.update(run_id=manifest["run_id"], protocol_id=manifest["protocol_id"])
        row["result_id"] = fingerprint({k: v for k, v in row.items() if k != "result_id"})
        ids[old] = row["result_id"]
    scores = [json.loads(row) for row in (run / "scores.jsonl").read_text().splitlines()]
    for row in scores:
        row.update(run_id=manifest["run_id"], protocol_id=manifest["protocol_id"], result_id=ids[row["result_id"]])
    save_jsonl(run / "planned.jsonl", plans)
    save_jsonl(run / "scores.jsonl", scores)
    manifest["planned_sha256"] = digest(run / "planned.jsonl")
    save_json(run / "manifest.json", manifest)
    return manifest


def rescored(tmp_path, original):
    run = tmp_path / "rescored"
    shutil.copytree(original, run)
    for name in ("manifest.json", "planned.jsonl", "scores.jsonl"):
        shutil.copyfile(original / name, run / ("base-" + name))
    origin = json.loads((original / "manifest.json").read_text())
    batch = {"version": "notebook-rescoring-v1", "origin_run_id": origin["run_id"],
             "origin_manifest_sha256": digest(original / "manifest.json"),
             "origin_artifacts": {name: digest(original / name) for name in
                                   ("manifest.json", "planned.jsonl", "outputs.jsonl", "scores.jsonl")}}
    manifest = json.loads((run / "manifest.json").read_text())
    manifest.update(run_id="rescored", scoring_batch=batch)
    save_json(run / "manifest.json", manifest)
    reidentify(run, {**origin["identity"], "scoring_batch": batch})
    return run


def with_native(run):
    judge = {"model_id": "synthetic-judge", "parameters": {"temperature": 0}}
    config = {"protocol": "sn-deepeval-native-v1", "sdk_version": "4.2.2", "judge": judge, "trajectory": True}
    manifest = json.loads((run / "manifest.json").read_text())
    reidentify(run, {**manifest["identity"], "agent_evaluation": config})
    common = {"schema_version": "sn-deepeval-native-v1", "case_id": "q1", "mode": "chunk", "judge": judge, "request_id": "request-1"}
    save_json(run / "agent/native-manifest.json", {"schema_version": config["protocol"], "sdk_version": "4.2.2", "judge": judge})
    components = [{**common, "record_type": "span", "span_id": "span-1", "name": "sn.retrieve.chunks_multi",
                   "input": {"queries": ["Who?", "When?"]}, "output": [], "metadata": {"status": "success"}}]
    samples = [{**common, "record_type": "sample", "sample_id": f"sample-{i}", "span_id": "span-1",
                "span_name": "sn.retrieve.chunks_multi", "grouping": "multi_query_evaluate", "query_index": i,
                "input": question, "retrieval_context": ["PRIVATE_LONG_EVIDENCE" * 100], "actual_output": "[]"}
               for i, question in enumerate(("Who?", "When?"))]
    save_jsonl(run / "agent/components.jsonl", components + samples)
    scores = [{**s, "metric": "Contextual Relevancy", "status": "scored", "score": i / 2, "reason": "native score"}
              for i, s in enumerate(samples)]
    save_jsonl(run / "agent/native-scores.jsonl", scores)
    traces = [{**common, "phase": phase, "trace": {"uuid": "trace-1", "metadata": {"request_id": "request-1"},
               "root_spans": [{"uuid": "span-1", "name": "sn.retrieve.chunks_multi", "children": [], "output": "FINAL" if phase == "completed" else "BEFORE"}]}}
              for phase in ("before_scoring", "completed")]
    save_jsonl(run / "agent/native-traces.jsonl", traces)
    save_jsonl(run / "agent/native-diagnostics.jsonl", [{**common, "status": "completed"}])
    save_jsonl(run / "agent/native-errors.jsonl", [])
    save_jsonl(run / "agent/native-outputs.jsonl", [])
    save_jsonl(run / "judge-events.jsonl", [{"case_id": "q1", "request_id": "request-1", "event": "completed"},
                                           {"case_id": "q1", "request_id": "unknown", "event": "completed"}])
    return run


def test_index_is_lightweight_full_detail_redacted_and_safe(tmp_path):
    run = with_native(make_run(tmp_path))
    rows = [json.loads(line) for line in (run / "outputs.jsonl").read_text().splitlines()]
    injected = '</script><img src=x onerror="window.PWNED=true">'
    rows[0]["prediction"] = injected
    rows[0]["product_record"]["retrieval_context"] = ["SAVED_EVIDENCE" * 2000]
    save_jsonl(run / "outputs.jsonl", rows)
    data, folder = export(tmp_path, [run])
    encoded = json.dumps(data)
    assert "SAVED_EVIDENCE" not in encoded and "PRIVATE_LONG_EVIDENCE" not in encoded
    assert "plan" not in data["entries"][0] and "result" not in data["entries"][0]
    saved = detail(data, folder)
    assert saved["output"]["prediction"] == injected
    assert saved["output"]["product_record"]["retrieval_context"] == ["SAVED_EVIDENCE" * 2000]
    assert "SECRET-TEST" not in json.dumps(saved)
    assert injected not in (folder / data["observations"][saved["id"]]["detail_file"]).read_text()
    native = [e for e in saved["entries"] if e["scope"] == "retrieval"]
    assert [e["sample_id"] for e in native] == ["sample-0", "sample-1"]
    assert [e["score"] for e in native] == [0, .5]
    assert [r["phase"] for r in saved["native"]["traces"] if r["selected_for_display"]] == ["completed"]
    assert len(saved["native"]["judge_events"]) == 1
    assert data["summary"]["planned_outputs"] == 2 and data["summary"]["planned_scores"] == 6
    assert [s["id"] for s in saved["steps"]] == ["source", "normalize", "partition", "import", "retrieve", "synthesize", "answer", "score"]
    assert next(s for s in saved["steps"] if s["id"] == "synthesize")["status"] == "missing"


def test_rescoring_links_verified_source_without_recounting_answers(tmp_path):
    run = make_run(tmp_path)
    rescore = rescored(tmp_path, run)
    data, _ = export(tmp_path, [rescore, run])
    assert data["summary"]["planned_outputs"] == 2
    assert data["summary"]["saved_outputs"] == 1
    assert data["summary"]["planned_scores"] == 8
    assert data["summary"]["unique_questions"] == 2
    assert len([n for n in data["graph"]["nodes"] if n["kind"] == "answers"]) == 1
    assert any(e["kind"] == "rescore" for e in data["graph"]["edges"])
    external, _ = export(tmp_path, [rescore], "external-report")
    assert external["summary"]["planned_outputs"] == 0
    assert any(n["kind"] == "external" for n in external["graph"]["nodes"])


@pytest.mark.parametrize("artifact", ["base-manifest.json", "outputs.jsonl"])
def test_tampered_rescoring_hash_is_rejected(tmp_path, artifact):
    rescore = rescored(tmp_path, make_run(tmp_path))
    with (rescore / artifact).open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="hash"):
        export(tmp_path, [rescore])


def test_same_case_id_different_frozen_sources_are_distinct(tmp_path):
    first = make_run(tmp_path)
    other = make_run(tmp_path, "other")
    manifest = json.loads((other / "manifest.json").read_text())
    identity = manifest["identity"]
    identity["source"]["revision"] = "another-source"
    reidentify(other, identity)
    data, _ = export(tmp_path, [first, other])
    assert data["summary"]["unique_questions"] == 4
    assert len([n for n in data["graph"]["nodes"] if n["kind"] == "dataset"]) == 2


@pytest.mark.parametrize("kind", ["sample", "request", "duplicate", "score"])
def test_native_unknown_or_duplicate_identity_and_invalid_score_rejected(tmp_path, kind):
    run = with_native(make_run(tmp_path))
    path = run / "agent/native-scores.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if kind == "sample": rows[0]["sample_id"] = "unknown"
    elif kind == "request": rows[0]["request_id"] = "other-request"
    elif kind == "duplicate": rows.append(rows[0])
    else: rows[0].update(status="not_applicable", score=0)
    save_jsonl(path, rows)
    with pytest.raises(ValueError, match="sample|Duplicate|Non-scored"):
        export(tmp_path, [run])


def test_missing_trace_retains_components_and_incomplete_run(tmp_path):
    run = with_native(make_run(tmp_path))
    (run / "agent/native-traces.jsonl").unlink()
    data, folder = export(tmp_path, [run])
    saved = detail(data, folder)
    assert saved["native"]["traces"] == []
    assert saved["native"]["components"]
    assert any("trace" in warning for warning in data["runs"][0]["warnings"])
    missing = detail(data, folder, "q2")
    assert missing["output"] is None
    assert next(s for s in missing["steps"] if s["id"] == "answer")["status"] == "missing"
    assert data["summary"]["prediction_status"] == {"success": 1, "missing": 1}


@pytest.mark.parametrize("escape", ["symlink", "source"])
def test_artifact_and_code_paths_cannot_escape_run(tmp_path, escape):
    run = make_run(tmp_path)
    if escape == "symlink":
        outside = tmp_path / "outside"
        outside.write_text("PRIVATE")
        (run / "scores.jsonl").unlink()
        (run / "scores.jsonl").symlink_to(outside)
    else:
        manifest = json.loads((run / "manifest.json").read_text())
        manifest["identity"]["code"]["benchmark_source_hashes"] = {"../../outside": "fake"}
        reidentify(run, manifest["identity"])
    with pytest.raises(ValueError, match="outside"):
        export(tmp_path, [run])


def test_request_identity_and_synthesis_sections_do_not_collapse(tmp_path):
    run = with_native(make_run(tmp_path))
    components = [json.loads(line) for line in (run / "agent/components.jsonl").read_text().splitlines()]
    common = {k: components[0][k] for k in ("schema_version", "case_id", "mode", "judge")}
    scores = [json.loads(line) for line in (run / "agent/native-scores.jsonl").read_text().splitlines()]
    traces = [json.loads(line) for line in (run / "agent/native-traces.jsonl").read_text().splitlines()]
    second_spans = []
    for index in range(2):
        span_id = f"synthesis-{index}"
        link = {**common, "request_id": "request-2", "span_id": span_id}
        components.append({**link, "record_type": "span", "name": "sn.synthesis.reasoning", "stage": "synthesis",
                           "input": {"question": f"Section {index}"}, "output": {"answer": f"Answer {index}"}})
        sample = {**link, "record_type": "sample", "sample_id": f"section-{index}", "span_name": "sn.synthesis.reasoning",
                  "grouping": "native_span", "query_index": None, "input": f"Section {index}",
                  "actual_output": f"Answer {index}", "retrieval_context": [f"Context {index}"]}
        components.append(sample)
        scores.append({**sample, "metric": "Faithfulness", "status": "scored", "score": 1, "reason": "supported"})
        second_spans.append({"uuid": span_id, "children": [], "input": sample["input"], "output": sample["actual_output"]})
    # Appended before-scoring snapshot for another request must not displace
    # the first request's completed snapshot.
    traces.append({**common, "request_id": "request-2", "phase": "before_scoring",
                   "trace": {"uuid": "trace-2", "metadata": {"request_id": "request-2"}, "root_spans": second_spans}})
    save_jsonl(run / "agent/components.jsonl", components)
    save_jsonl(run / "agent/native-scores.jsonl", scores)
    save_jsonl(run / "agent/native-traces.jsonl", traces)
    data, folder = export(tmp_path, [run])
    saved = detail(data, folder)
    selected = [(t["request_id"], t["phase"]) for t in saved["native"]["traces"] if t["selected_for_display"]]
    assert selected == [("request-1", "completed"), ("request-2", "before_scoring")]
    section_entries = [e for e in saved["entries"] if e["scope"] == "synthesis"]
    assert [e["sample_id"] for e in section_entries] == ["section-0", "section-1"]
    assert next(s for s in saved["steps"] if s["id"] == "synthesize")["status"] == "observed"


def test_real_baseline_protocol_exposes_comparison_identity_and_saved_prompt(tmp_path, monkeypatch):
    from test_notebook_baseline import run_fixture
    run = run_fixture(tmp_path, monkeypatch)
    data, folder = export(tmp_path, [run])
    observation = next(iter(data["observations"].values()))
    saved = detail(data, folder, observation["case_id"])
    assert observation["corpus_id"] and observation["question_id"]
    assert all(e["corpus_id"] == observation["corpus_id"] for e in saved["entries"])
    stages = {s["id"]: s for s in saved["steps"]}
    assert stages["retrieve"]["output"]["saved_context"]["retrieval"] == saved["output"]["retrieval"]
    assert stages["synthesize"]["output"]["saved_invocation"]["prompt"] == saved["output"]["prompt"]
    assert "GOLD_SENTINEL" not in json.dumps(stages["import"])
    assert "GOLD_SENTINEL" not in json.dumps(stages["retrieve"])


def test_unknown_native_provenance_rejected_and_native_warnings_reach_audit(tmp_path):
    run = with_native(make_run(tmp_path))
    (run / "agent/native-traces.jsonl").unlink()
    data, _ = export(tmp_path, [run])
    assert any("trace" in warning for warning in data["audit"][0]["warnings"])
    manifest = json.loads((run / "agent/native-manifest.json").read_text())
    manifest["schema_version"] = "unknown-version"
    save_json(run / "agent/native-manifest.json", manifest)
    with pytest.raises(ValueError, match="provenance"):
        export(tmp_path, [run], "unknown")
