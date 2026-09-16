"""Coverage regressions for full selections; not executed in this phase."""
from copy import deepcopy

import pytest

from rag_eval.selection_execution import execution_context, expected_scorers
from rag_eval.selection_report import summarize_selection


def inputs():
    cases = [{"case_id": str(i), "sample_id": str(i), "suite": "gsm8k", "task": "gsm8k"}
             for i in range(3)]
    source = {"suite": "gsm8k", "selection_protocol": "public-selection-v1",
              "selection_bundle_sha256": "a" * 64, "selection_summary": {"fixture": True}}
    partitions = [{"partition_id": "p1", "case_ids": ["0", "1"]},
                  {"partition_id": "p2", "case_ids": ["2"]}]
    plan = {"partitions": partitions, "decisions": [
        {**case, "status": "applicable", "partition_id": "p1" if i < 2 else "p2"}
        for i, case in enumerate(cases)]}
    return {"native_source": source, "cases": cases, "manifest": {}}, plan


def saved_run(selection, plan, mode="chunk"):
    source = selection["native_source"]
    context = execution_context(source, 3, plan, plan["partitions"][0])
    scorer = expected_scorers("gsm8k", "R")[0]
    return {"path": "run-" + mode, "manifest": {
        "suite": "gsm8k", "track": "R", "mode": mode, "selection_context": context,
        "identity": {"source": source, "selection_context": context, "code": {"fixture": True}},
        "pairing_id": "pair1"},
        "state": {"phase": "finished"}, "warnings": [],
        "planned": [{"case_id": cid, "scorer": sid} for cid in ["0", "1"]
                    for sid in expected_scorers("gsm8k", "R")],
        "outputs": [{"case_id": "0", "output_available": True, "status": "success",
                     "answer_extraction": {"status": "parsed"}},
                    {"case_id": "1", "output_available": False, "status": "clarification"}],
        "scores": [{"case_id": "0", "scorer": scorer, "status": "scored", "score": 1.0}]}


def test_no_runs_still_reports_all_partitions_and_denominators():
    selection, plan = inputs()
    report = summarize_selection(selection, plan, [])
    assert report["selected_memberships"] == 3
    assert len(report["partitions"]) == 4  # Two modes times two partitions.
    assert all(p["state"] == "not_run" for p in report["partitions"])
    assert all(g["missing_predictions"] == 3 for g in report["groups"])
    assert not report["all_partition_ledgers_complete"]


def test_partial_run_preserves_unrun_and_clarification_denominators():
    selection, plan = inputs()
    report = summarize_selection(selection, plan, [saved_run(selection, plan)])
    group = next(g for g in report["groups"] if g["mode"] == "chunk" and g["metric_role"] == "primary")
    assert group["mean_over_scored"] == 1.0
    assert group["correct_over_planned"] == pytest.approx(1 / 3)
    assert group["saved_outputs"] == 1
    assert group["missing_predictions"] == 1
    assert group["prediction_status_counts"]["clarification"] == 1
    assert group["missing_scores"] == 2
    assert not report["all_partition_ledgers_complete"]


def test_duplicate_partition_runs_are_not_implicitly_retried_or_averaged():
    selection, plan = inputs()
    run = saved_run(selection, plan)
    with pytest.raises(ValueError, match="duplicate|Duplicate"):
        summarize_selection(selection, plan, [run, deepcopy(run)])


def test_partition_from_different_plan_is_rejected():
    selection, plan = inputs()
    run = saved_run(selection, plan)
    run["manifest"]["selection_context"]["partition_plan_sha256"] = "edited"
    with pytest.raises(ValueError, match="context|plan"):
        summarize_selection(selection, plan, [run])


def test_pending_cases_stay_in_selection_coverage():
    selection, plan = inputs()
    plan["partitions"] = plan["partitions"][:1]
    plan["decisions"][2].update(status="pending", partition_id=None)
    report = summarize_selection(selection, plan, [], modes=("chunk",))
    assert report["product_applicability"]["pending"] == 1
    assert report["executable_memberships"] == 2
    assert report["execution_plan_coverage"] == pytest.approx(2 / 3)
    assert report["full_upstream_evaluation_complete"] is False


def test_mixed_runtime_settings_cannot_be_aggregated():
    selection, plan = inputs()
    a = saved_run(selection, plan, "chunk")
    b = saved_run(selection, plan, "reasoning")
    b["manifest"]["identity"]["code"] = {"different": True}
    with pytest.raises(ValueError, match="configuration"):
        summarize_selection(selection, plan, [a, b])


@pytest.mark.parametrize("policy,scorer", [
    (None, "product.ifeval.audited_all_instructions.full_body.v1"),
    ("deepeval-ifeval-direct-v1", "product.ifeval.all_instructions.full_body.v2"),
])
def test_ifeval_reports_preserve_saved_scoring_policy(policy, scorer):
    selection, plan = inputs()
    selection["native_source"]["suite"] = "ifeval"
    for case in selection["cases"]:
        case.update(suite="ifeval", task="ifeval")
    run = saved_run(selection, plan)
    run["manifest"]["suite"] = "ifeval"
    if policy is not None:
        run["manifest"]["identity"]["ifeval_scoring"] = policy
    for row in run["planned"] + run["scores"]:
        if row["scorer"] == expected_scorers("gsm8k", "R")[0]:
            row["scorer"] = scorer
    report = summarize_selection(selection, plan, [run])
    primary = [g for g in report["groups"] if g["metric_role"] == "primary"]
    assert all(g["scorer"] == scorer for g in primary)
    assert primary[0]["mean_over_scored"] == 1
    mixed = deepcopy(run)
    mixed["manifest"]["mode"] = "reasoning"
    mixed["manifest"]["identity"]["ifeval_scoring"] = (
        "deepeval-ifeval-direct-v1" if policy is None else None)
    with pytest.raises(ValueError, match="configuration|scoring polic"):
        summarize_selection(selection, plan, [run, mixed])


def test_new_ifeval_plan_uses_direct_scorers_and_rejects_unknown_policy():
    from rag_eval.selection_execution import expected_scorers
    assert expected_scorers("ifeval", "N") == ["deepeval.ifeval.all_instructions.v1"]
    assert expected_scorers("ifeval", "R")[0] == "product.ifeval.all_instructions.full_body.v2"
    assert expected_scorers("ifeval", "N", ifeval_scoring=None) == [
        "deepeval.ifeval.audited_all_instructions"]
    with pytest.raises(ValueError, match="scoring policy"):
        expected_scorers("ifeval", "N", ifeval_scoring="unknown")
