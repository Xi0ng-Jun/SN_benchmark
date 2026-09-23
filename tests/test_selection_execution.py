"""Selection execution regressions; written without running in this phase."""
from copy import deepcopy

import pytest

from rag_eval import starter_runner
from rag_eval.selection_execution import execution_context, select_partition


def test_new_selection_cannot_run_r_without_explicit_partition(tmp_path, monkeypatch):
    source = {"suite": "gsm8k", "selection_protocol": "public-selection-v1",
              "selection_bundle_sha256": "a" * 64}
    monkeypatch.setattr(starter_runner, "load_bundle", lambda _: (source, [{"case_id": "c"}]))
    with pytest.raises(ValueError, match="partition"):
        starter_runner.execute(root=tmp_path / "root", project=tmp_path / "sn",
                               bundle_dir=tmp_path / "bundle", run=tmp_path / "run",
                               track="R", mode="chunk")
    assert not (tmp_path / "run").exists()


def test_partition_selects_exact_case_order_without_mutating_plan(monkeypatch):
    from rag_eval import selection_partitions
    source = {"selection_protocol": "public-selection-v1", "selection_bundle_sha256": "a" * 64}
    cases = [{"case_id": str(i)} for i in range(3)]
    product = {"questions": [{"case_id": "2"}, {"case_id": "0"}], "manifest": {}}
    plan = {"partitions": [{"partition_id": "gsm8k-0001", "case_ids": ["2", "0"],
                            "product_bundle": product}]}
    before = deepcopy(plan)
    monkeypatch.setattr(selection_partitions, "load_partition_plan", lambda *args: plan)
    selected, bundle, context, saved = select_partition(cases, source, "plan.json", "gsm8k-0001")
    assert [c["case_id"] for c in selected] == ["2", "0"]
    assert context["case_ids"] == ["2", "0"]
    assert context["selected_memberships"] == 3
    bundle["manifest"]["edited"] = True
    assert plan == before
    assert saved == before


def test_unknown_partition_rejected_without_fallback(monkeypatch):
    from rag_eval import selection_partitions
    monkeypatch.setattr(selection_partitions, "load_partition_plan", lambda *args: {"partitions": []})
    with pytest.raises(ValueError, match="partition"):
        select_partition([], {}, "plan.json", "missing")


def test_context_changes_when_plan_or_partition_changes():
    source = {"selection_protocol": "public-selection-v1", "selection_bundle_sha256": "a" * 64}
    first = {"partition_id": "p1", "case_ids": ["a"]}
    second = {"partition_id": "p2", "case_ids": ["b"]}
    plan = {"partitions": [first, second]}
    a = execution_context(source, 2, plan, first)
    assert a != execution_context(source, 2, plan, second)
    assert a != execution_context(source, 2, {**plan, "reviews": {"changed": True}}, first)
    assert execution_context(source, 2)["corpus_protocol"] is None
