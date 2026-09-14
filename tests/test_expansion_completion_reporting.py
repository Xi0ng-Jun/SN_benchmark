"""Regression cases for the completion patch. Written but not run this phase."""
from copy import deepcopy
import json
import sys

import pytest

from rag_eval.starter_report import load_run, write_report
from rag_eval.starter_results import planned_result, result_record, summarize
from rag_eval.public_expansion_protocol import EXPANSION_VERSION


def test_legacy_summary_preserves_records_and_still_rejects_tampering():
    case = {"suite": "boolq", "case_id": "b1", "sample_id": "b1", "scorer": "exact"}
    plan = planned_result(case, run_id="legacy", protocol_id="p", track="N")
    del plan["task"], plan["applicability"]
    score = {**plan, "status": "scored", "score": 1.0, "output_available": True}
    before = deepcopy((plan, score))
    groups = summarize([plan], [score])
    assert groups[0]["mean_over_scored"] == 1.0
    assert (plan, score) == before
    with pytest.raises(ValueError, match="identity"):
        summarize([plan], [{**score, "scorer": "tampered"}])


@pytest.mark.parametrize("suite", ["mmlu", "gsm8k", "truthfulqa", "hellaswag", "bbh"])
def test_unsupported_product_records_every_case_without_runtime(tmp_path, monkeypatch, suite):
    from rag_eval import starter_runner, starter_not_applicable

    # Emulate a previously validated bundle; this test exercises orchestration,
    # not SDK/data preparation (tested separately).
    source = {"suite": suite}
    cases = [{"protocol_version": EXPANSION_VERSION,
              "suite": suite, "case_id": f"{suite}-{i}", "sample_id": f"{suite}-{i}",
              "task": "task", "scorer": "unused"} for i in range(2)]
    for module in (starter_runner, starter_not_applicable):
        monkeypatch.setattr(module, "load_bundle", lambda path: (source, cases))
    monkeypatch.setitem(sys.modules, "rag_eval.starter_runtime", None)
    monkeypatch.setitem(sys.modules, "deepeval", None)
    monkeypatch.setitem(sys.modules, "app", None)
    root = tmp_path / "repo"
    root.mkdir()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_text(json.dumps(source))
    run = tmp_path / "run"
    starter_runner.execute(root=root, project=tmp_path / "nonexistent-product",
                           bundle_dir=bundle, run=run, track="R", mode="chunk",
                           models_path=None, product_protocol="legacy")
    loaded = load_run(run)
    assert loaded["state"]["phase"] == "not_applicable"
    assert len(loaded["planned"]) == len(loaded["scores"]) == len(cases)
    assert all(p["protocol_version"] == EXPANSION_VERSION for p in loaded["planned"])
    assert all(r["score"] is None and r["status"] == "not_applicable" for r in loaded["scores"])
    assert loaded["manifest"]["models"] == {}
    assert not (run / "runtime").exists()
    assert not (run / "model-events.jsonl").read_text()
    assert not loaded["warnings"]
    report = write_report([run], tmp_path / "report")
    assert report["paired_modes"] == []
    assert report["runs"][0]["groups"][0]["mean_over_scored"] is None


def test_new_result_cannot_omit_applicability_identity():
    case = {"suite": "mmlu", "case_id": "m1", "sample_id": "m1", "scorer": "exact"}
    plan = planned_result(case, run_id="new", protocol_id="p", track="N")
    score = result_record(plan, status="scored", score=1, output_available=True)
    del score["applicability"]
    with pytest.raises(ValueError, match="identity"):
        summarize([plan], [score])


def test_complete_trace_still_does_not_enable_unimplemented_agent_metrics():
    case = {"suite": "mmlu", "case_id": "m1", "sample_id": "m1", "scorer": "exact"}
    plan = planned_result(case, run_id="new", protocol_id="p", track="N")
    score = result_record(plan, status="scored", score=1, output_available=True,
                          trace={"trace_id": "observed", "completeness": "complete",
                                 "spans": [{"type": "answer_generation"}]})
    group = summarize([plan], [score])[0]
    assert group["complete_trace_records"] == 1
    assert group["agent_metrics_suppressed"] is True
    assert group["agent_metrics_status"] == "not_implemented"
