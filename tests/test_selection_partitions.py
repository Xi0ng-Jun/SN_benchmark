"""Partition regression specifications; this implementation session did not run them."""
from copy import deepcopy
import json

import pytest

from rag_eval.public_expansion_protocol import EXPANSION_SUITES, EXPANSION_VERSION, native_protocol
from rag_eval.public_expansion_sources import make_expansion_case
from rag_eval.selection_partitions import PARTITION_VERSION, build_partition_plan, load_partition_plan
from rag_eval.starter_protocol import VERSION, fingerprint, make_case


def source_for(cases):
    suite = cases[0]["suite"]
    source = {"protocol_version": VERSION, "suite": suite,
              "selection_protocol": "public-selection-v1", "selection_bundle_sha256": "c" * 64}
    if suite in EXPANSION_SUITES:
        source.update(protocol_version=EXPANSION_VERSION,
                      source={"revision": "fixture-v1", "export_sha256": "a" * 64},
                      native_protocol=native_protocol(suite), n_shots=cases[0]["n_shots"],
                      scorer=EXPANSION_SUITES[suite]["scorer"], applicability=EXPANSION_SUITES[suite],
                      release_gate=False, case_fingerprints={c["case_id"]: fingerprint(c) for c in cases})
    return source


def logic(index, passage=None, task="Necessary Conditional Reasoning"):
    row = {"id": index, "text": passage or f"Original premises {index}",
           "question": f"Which conclusion {index}?", "options": ["one", "two", "three", "four"],
           "answer": 0, "explanation": "SECRET_SOLUTION"}
    return make_case("logiqa", row, index, task)


def boolq(index, passage=None):
    row = {"id": index, "question": f"Is statement {index} supported?",
           "passage": passage or f"Original passage {index}", "answer": bool(index % 2)}
    return make_case("boolq", row, index, "boolq")


def approved(case):
    return {"status": "approved", "reason": "Fixture represents an explicit human verdict",
            "reviewer": "fixture reviewer", "raw_row_sha256": case["raw_row_sha256"]}


def write_plan(tmp_path, plan):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def rehash(plan):
    plan["manifest"].pop("plan_sha256", None)
    plan["manifest"]["plan_sha256"] = fingerprint(plan)


def test_more_than_forty_documents_partitions_without_losing_memberships(tmp_path):
    cases = [logic(i) for i in range(83)]
    source = source_for(cases)
    original = deepcopy(cases)
    plan = build_partition_plan(cases, source)
    assert [len(p["product_bundle"]["documents"]) for p in plan["partitions"]] == [40, 40, 3]
    assert [cid for p in plan["partitions"] for cid in p["case_ids"]] == [c["case_id"] for c in cases]
    assert plan["manifest"]["applicable_case_count"] == 83
    assert cases == original
    assert build_partition_plan(cases, source) == plan
    assert load_partition_plan(write_plan(tmp_path, plan), cases, source) == plan
    for part in plan["partitions"]:
        bundle = part["product_bundle"]
        manifest = bundle["manifest"]
        assert manifest["corpus_protocol"] == PARTITION_VERSION
        assert manifest["partition_id"] == part["partition_id"]
        assert manifest["source_manifest_sha256"] == source["selection_bundle_sha256"]
        assert "partition" in manifest["source_scope"]
        assert manifest["questions_sha256"] == fingerprint(bundle["questions"])
        assert manifest["documents_sha256"] == fingerprint(bundle["documents"])
        assert manifest["decisions_sha256"] == fingerprint(bundle["decisions"])
        assert "SECRET_SOLUTION" not in json.dumps(bundle["documents"])


def test_shared_passage_and_logiqa_memberships_stay_together_above_question_cap():
    cases = [logic(i, "One shared passage") for i in range(51)]
    other_task = make_case("logiqa", cases[0]["raw_row"], 0, "Sufficient Conditional Reasoning")
    cases.append(other_task)
    plan = build_partition_plan(cases, source_for(cases), max_documents=1)
    assert len(plan["partitions"]) == 1
    bundle = plan["partitions"][0]["product_bundle"]
    assert len(bundle["documents"]) == 1
    assert len(bundle["questions"]) == 52
    assert len({q["case_id"] for q in bundle["questions"]}) == 52


@pytest.mark.parametrize("suite,tasks", [
    ("mmlu", ["high_school_physics", "high_school_world_history"]),
    ("bbh", ["object_counting", "date_understanding"]),
])
def test_task_groups_are_separate_even_with_spare_capacity(suite, tasks):
    cases = []
    for index, task in enumerate([tasks[0], tasks[1], tasks[0], tasks[1]]):
        row = ({"question": f"Question {index}", "choices": ["a", "b", "c", "d"],
                "answer": 0, "subject": task} if suite == "mmlu" else
               {"input": f"Original task {index}", "target": "3" if task == "object_counting" else "(A)",
                "task_name": task})
        cases.append(make_expansion_case(suite, row, index, revision="fixture-v1",
                                        source_file_sha256="a" * 64, source_line_sha256=fingerprint(row)))
    plan = build_partition_plan(cases, source_for(cases))
    assert [part["group"] for part in plan["partitions"]] == tasks
    assert [part["case_ids"] for part in plan["partitions"]] == [
        [cases[0]["case_id"], cases[2]["case_id"]], [cases[1]["case_id"], cases[3]["case_id"]]]


def test_same_material_crossing_subject_groups_remains_one_unit():
    cases = []
    for index, task in enumerate(["high_school_physics", "high_school_world_history"]):
        row = {"question": "Identical task text", "choices": ["a", "b", "c", "d"], "answer": 0, "subject": task}
        cases.append(make_expansion_case("mmlu", row, index, revision="fixture-v1",
                                        source_file_sha256="a" * 64, source_line_sha256=fingerprint(row)))
    plan = build_partition_plan(cases, source_for(cases), max_documents=1)
    assert len(plan["partitions"]) == 1
    assert len(plan["partitions"][0]["case_ids"]) == 2


def test_squad_title_groups_and_same_source_distractors_preserve_source_order():
    cases = []
    for index, title in enumerate(["Normans", "Steam_engine", "Normans"]):
        row = {"id": index, "title": title, "question": f"Who {index}?", "context": f"Norman {index}",
               "answers": {"text": ["Norman"], "answer_start": [0]}}
        cases.append(make_case("squad", row, index, title))
    reviews = {case["sample_id"]: approved(case) for case in cases}
    plan = build_partition_plan(cases, source_for(cases), reviews, max_documents=3)
    assert [part["group"] for part in plan["partitions"]] == ["Normans", "Steam_engine"]
    second = plan["partitions"][1]["product_bundle"]
    assert [doc["text"] for doc in second["documents"]] == ["Norman 1", "Norman 0", "Norman 2"]
    assert second["manifest"]["gold_document_count"] == 1
    assert second["manifest"]["distractor_document_count"] == 2
    assert "selected cases only" in second["manifest"]["distractor_provenance"]["scope"]


def test_pending_rejected_and_approved_remain_in_full_denominator():
    cases = [boolq(i) for i in range(3)]
    rejection = {**approved(cases[1]), "status": "rejected", "reason": "Pronoun lacks context"}
    reviews = {cases[1]["sample_id"]: rejection, cases[2]["sample_id"]: approved(cases[2])}
    plan = build_partition_plan(cases, source_for(cases), reviews)
    assert [d["status"] for d in plan["decisions"]] == ["pending", "rejected", "applicable"]
    assert plan["reviews"] == reviews
    assert plan["decisions"][1]["human_review"] == rejection
    assert plan["partitions"][0]["case_ids"] == [cases[2]["case_id"]]
    assert len(plan["partitions"][0]["product_bundle"]["documents"]) == 3
    assert plan["manifest"]["planned_case_count"] == 3


def test_all_pending_does_not_create_empty_notebook():
    cases = [boolq(0)]
    plan = build_partition_plan(cases, source_for(cases))
    assert plan["partitions"] == []
    assert plan["decisions"][0]["partition_id"] is None
    assert plan["decisions"][0]["human_review"]["status"] == "pending"


def test_drop_without_complete_annotations_retains_not_applicable_case():
    row = {"query_id": "q", "section_id": "history_1", "question": "How many?", "passage": "There were three.",
           "answers_spans": {"spans": ["3"], "types": ["number"]}}
    case = make_case("drop", row, 0, "history_1")
    review = {case["sample_id"]: approved(case)}
    plan = build_partition_plan([case], source_for([case]), review)
    assert plan["partitions"] == []
    assert plan["decisions"][0]["status"] == "not_applicable"
    assert "annotations" in plan["decisions"][0]["reason"]
    assert plan["decisions"][0]["human_review"]["status"] == "approved"


@pytest.mark.parametrize("cap", [0, 41, True, 1.5])
def test_invalid_document_caps_rejected(cap):
    cases = [logic(0)]
    with pytest.raises(ValueError, match="cap"):
        build_partition_plan(cases, source_for(cases), max_documents=cap)


def test_unknown_or_stale_reviews_and_modified_cases_are_rejected():
    cases = [boolq(0)]
    source = source_for(cases)
    with pytest.raises(ValueError, match="outside"):
        build_partition_plan(cases, source, {"unknown": approved(cases[0])})
    stale = {cases[0]["sample_id"]: {**approved(cases[0]), "raw_row_sha256": "f" * 64}}
    with pytest.raises(ValueError, match="different source"):
        build_partition_plan(cases, source, stale)
    cases[0]["references"] = ["invented answer"]
    with pytest.raises(ValueError, match="canonical"):
        build_partition_plan(cases, source)


@pytest.mark.parametrize("tamper", ["documents", "case_ids", "reviews", "decisions"])
@pytest.mark.parametrize("rehash_outer", [False, True])
def test_plan_tampering_rejected_even_with_recomputed_outer_hash(tmp_path, tamper, rehash_outer):
    cases = [boolq(0), boolq(1)]
    source = source_for(cases)
    plan = build_partition_plan(cases, source, {case["sample_id"]: approved(case) for case in cases})
    if tamper == "documents":
        plan["partitions"][0]["product_bundle"]["documents"][0]["text"] = "forged"
    elif tamper == "case_ids":
        plan["partitions"][0]["case_ids"].reverse()
    elif tamper == "reviews":
        plan["reviews"][cases[0]["sample_id"]]["reason"] = "different review"
    else:
        plan["decisions"][0]["status"] = "pending"
    if rehash_outer:
        rehash(plan)
    with pytest.raises(ValueError, match="hash|reproduce"):
        load_partition_plan(write_plan(tmp_path, plan), cases, source)


def test_plan_rejects_other_selection_source_and_missing_membership(tmp_path):
    cases = [logic(0), logic(1)]
    source = source_for(cases)
    path = write_plan(tmp_path, build_partition_plan(cases, source))
    changed_source = {**source, "selection_bundle_sha256": "d" * 64}
    with pytest.raises(ValueError, match="source cases"):
        load_partition_plan(path, cases, changed_source)
    with pytest.raises(ValueError, match="source cases"):
        load_partition_plan(path, cases[:1], source)


def test_empty_or_duplicate_selection_rejected():
    with pytest.raises(ValueError, match="Nonempty"):
        build_partition_plan([], {})
    cases = [logic(0)]
    with pytest.raises(ValueError, match="Duplicate"):
        build_partition_plan(cases * 2, source_for(cases))
