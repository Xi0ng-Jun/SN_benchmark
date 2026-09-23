"""Selection contracts; authored while execution remains paused."""
import json
from pathlib import Path

import pytest

from rag_eval.artifacts import digest
from rag_eval.public_selection import select_public_rows
from rag_eval.selection_bundle import load_selection_bundle, prepare_selection
from rag_eval.starter_protocol import load_bundle


def rows_file(tmp_path, rows):
    path = tmp_path / "raw.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def select(tmp_path, suite, rows, **source):
    return select_public_rows(suite, rows_file(tmp_path, rows), {"revision": "fixed-v1", **source})


def boolq(index):
    return {"id": str(index), "question": "Question?", "passage": "Evidence.", "answer": index % 2 == 0}


def test_complete_boolq_and_ifeval_are_not_prefix_capped(tmp_path):
    result = select(tmp_path, "boolq", [boolq(i) for i in range(51)])
    assert len(result["cases"]) == 51
    assert result["summary"]["label_counts"] == {"Yes": 26, "No": 25}
    result = select(tmp_path, "ifeval", [
        {"key": i, "prompt": f"Task {i}", "instruction_id_list": ["unknown:rule"], "kwargs": [{}]}
        for i in range(29)
    ])
    assert len(result["cases"]) == 29
    assert result["summary"]["instruction_counts"] == {"unknown:rule": 29}


def test_squad_preserves_every_selected_topic_question(tmp_path):
    rows = [{"id": str(i), "title": "Normans", "question": "Who?", "context": "Normans",
             "answers": {"text": ["Normans"], "answer_start": [0]}} for i in range(24)]
    rows.append({**rows[0], "id": "other", "title": "Other"})
    result = select(tmp_path, "squad", rows)
    assert len(result["cases"]) == 24
    assert result["summary"]["out_of_scope_rows"] == 1
    assert result["summary"]["missing_expected_tasks"] == ["Steam_engine"]


def test_drop_includes_small_and_multiple_sections(tmp_path):
    rows = [{"query_id": str(i), "section_id": section, "question": "How many?", "passage": "Two.",
             "answers_spans": {"spans": ["2"], "types": ["number"]}}
            for i, section in enumerate(["history_a", "history_b", "nfl_a", "other_a"])]
    result = select(tmp_path, "drop", rows)
    assert [case["task"] for case in result["cases"]] == ["history_a", "history_b", "nfl_a"]
    assert result["summary"]["missing_expected_tasks"] == []


@pytest.mark.parametrize(("suite", "field", "tasks", "other", "base"), [
    ("mmlu", "subject", ["high_school_computer_science", "high_school_physics",
     "high_school_world_history", "high_school_macroeconomics"], "elementary_mathematics",
     {"question": "Q", "choices": ["a", "b", "c", "d"], "answer": 0}),
    ("bbh", "task_name", ["logical_deduction_three_objects", "date_understanding",
     "object_counting", "tracking_shuffled_objects_three_objects"], "boolean_expressions",
     {"input": "Q", "target": "(A)"}),
])
def test_task_scope_is_exact_and_missing_metadata_is_invalid(tmp_path, suite, field, tasks, other, base):
    rows = [{**base, field: task, **({"target": "3"} if task == "object_counting" else {})}
            for task in tasks]
    rows += [{**base, field: other}, dict(base)]
    result = select(tmp_path, suite, rows)
    assert [case["task"] for case in result["cases"]] == tasks
    assert [row["status"] for row in result["decisions"]] == ["selected"] * 4 + ["out_of_scope", "invalid"]
    assert result["summary"]["missing_expected_tasks"] == []


def test_logiqa_two_memberships_are_one_question(tmp_path):
    row = {"id": "one", "text": "Conditions", "question": "Q?", "options": ["a", "b", "c", "d"],
           "answer": 1, "type": {"Necessary Conditional Reasoning": True, "Sufficient Conditional Reasoning": True}}
    result = select(tmp_path, "logiqa", [row])
    assert len(result["cases"]) == 2
    assert result["summary"]["selected_rows"] == 1
    assert result["summary"]["distinct_questions"] == 1
    assert len(result["decisions"][0]["case_ids"]) == 2


def test_every_physical_row_has_a_decision_and_bytes_identity(tmp_path):
    path = tmp_path / "raw.jsonl"
    path.write_bytes((json.dumps(boolq(0)) + "\n\n{broken\n[]\n{}\n").encode() + b'\xff\n')
    result = select_public_rows("boolq", path, {"revision": "v1"})
    assert len(result["cases"]) == 1
    assert [row["source_row_index"] for row in result["decisions"]] == list(range(6))
    assert result["summary"]["invalid_rows"] == 5
    assert all(len(row["source_line_sha256"]) == 64 for row in result["decisions"])
    assert result["summary"]["upstream_coverage"]["status"] == "unknown"


def test_operator_coverage_is_not_verified_full_split(tmp_path):
    result = select(tmp_path, "gsm8k", [{"question": "Q", "answer": "work\n#### 1"}],
                    coverage_declaration={"status": "complete", "expected_rows": 1})
    assert result["summary"]["upstream_coverage"] == {
        "status": "operator_declared", "verified": False,
        "declaration": {"status": "complete", "expected_rows": 1},
    }
    assert result["summary"]["coverage"] == "local_selection_only"


def test_truthfulqa_unknown_topics_and_invalid_annotations_are_retained(tmp_path):
    good = {"question": "Q", "mc1_targets": {"choices": ["right", "wrong"], "labels": [1, 0]}}
    result = select(tmp_path, "truthfulqa", [good, {"question": "Bad"}])
    assert len(result["cases"]) == 1
    assert result["summary"]["topic_counts"] == {"unknown": 1}
    assert result["summary"]["invalid_rows"] == 1
    assert result["cases"][0]["choice_order"] == [1, 0]


def test_hellaswag_keeps_all_valid_activity_labels(tmp_path):
    result = select(tmp_path, "hellaswag", [
        {"ctx": "Q", "endings": ["a", "b", "c", "d"], "label": "0", "activity_label": "A"},
        {"ctx": "Q", "endings": ["a", "b", "c", "d"], "label": "", "activity_label": "B"},
    ])
    assert len(result["cases"]) == 1
    assert result["summary"]["invalid_rows"] == 1


def test_duplicate_text_different_ids_preserved_and_id_conflicts_quarantined(tmp_path):
    result = select(tmp_path, "boolq", [boolq(0), {**boolq(0), "id": "new"}, {**boolq(0), "answer": False}])
    assert len(result["cases"]) == 2
    assert result["decisions"][2]["status"] == "invalid"
    assert "identity" in result["decisions"][2]["reason"]
    assert result["summary"]["duplicate_groups"][0]["source_row_indices"] == [0, 1]


def source_manifest(path, suite="boolq"):
    from rag_eval.starter_protocol import SUITES
    from rag_eval.public_expansion_protocol import EXPANSION_SUITES
    info = {**SUITES, **EXPANSION_SUITES}[suite]
    return {"dataset": info["dataset"], "split": info["split"], "revision": "fixed-v1",
            "source_url": "https://example.test/source", "license": "fixture", "license_url": "https://example.test/license",
            "conversion": "fixture", "origin_sha256": "0" * 64, "export_sha256": digest(path),
            "original_order_preserved": True}


def prepare_fixture(tmp_path, monkeypatch):
    # Fake only installed distribution discovery; real freezing and reconstruction execute.
    import rag_eval.selection_bundle as module
    sdk = tmp_path / "sdk"
    for relative in ("utils.py", "benchmarks/__init__.py", "benchmarks/schema.py", "scorer/__init__.py",
                     "scorer/scorer.py", "benchmarks/bool_q/__init__.py", "benchmarks/bool_q/bool_q.py",
                     "benchmarks/bool_q/template.py"):
        target = sdk / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# fixture\n", encoding="utf-8")
    class Installed:
        version = "4.2.2"
        def locate_file(self, name):
            assert name == "deepeval"
            return sdk
    monkeypatch.setattr(module, "distribution", lambda name: Installed())
    raw = rows_file(tmp_path, [boolq(0), boolq(1)])
    source = tmp_path / "source.json"
    source.write_text(json.dumps(source_manifest(raw)), encoding="utf-8")
    output = tmp_path / "bundle"
    prepare_selection("boolq", raw, source, output)
    return output


def test_bundle_returns_canonical_native_source_and_outer_identity(tmp_path, monkeypatch):
    output = prepare_fixture(tmp_path, monkeypatch)
    bundle = load_selection_bundle(output)
    native, cases = load_bundle(output)
    assert native == bundle["native_source"]
    assert native["protocol_version"] == "public-starter-v1"
    assert native["selection_protocol"] == "public-selection-v1"
    assert native["selection_bundle_sha256"] == digest(output / "manifest.json")
    assert len(cases) == len(bundle["decisions"]) == 2


def test_bundle_reconstructs_decisions_even_if_hash_is_resealed(tmp_path, monkeypatch):
    output = prepare_fixture(tmp_path, monkeypatch)
    path = output / "decisions.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records[0]["status"] = "out_of_scope"
    path.write_text("".join(json.dumps(row) + "\n" for row in records))
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"]["decisions.jsonl"] = digest(path)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="reproduce"):
        load_selection_bundle(output)


def test_bundle_rejects_escape_artifacts_and_changed_sdk(tmp_path, monkeypatch):
    output = prepare_fixture(tmp_path, monkeypatch)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    external = tmp_path / "outside"
    external.write_text("outside")
    manifest["artifacts"]["../outside"] = digest(external)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="path"):
        load_selection_bundle(output)
    del manifest["artifacts"]["../outside"]
    manifest_path.write_text(json.dumps(manifest))
    (output / "sdk-source/utils.py").write_text("changed")
    with pytest.raises(ValueError, match="SDK"):
        load_selection_bundle(output)


@pytest.mark.parametrize("edit", ["policy", "native_version", "sdk_version", "gate", "missing_sdk"])
def test_bundle_rejects_changed_protocol_commitments(tmp_path, monkeypatch, edit):
    output = prepare_fixture(tmp_path, monkeypatch)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if edit == "policy":
        manifest["policy"]["expected_tasks"] = []
    elif edit == "native_version":
        manifest["native_source"]["protocol_version"] = "invented"
    elif edit == "sdk_version":
        manifest["native_source"]["deepeval_version"] = "0.0.0"
    elif edit == "gate":
        manifest["release_gate"] = True
    else:
        del manifest["native_source"]["sdk_source_hashes"]["benchmarks/bool_q/template.py"]
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_selection_bundle(output)


def test_expansion_keeps_duplicate_physical_records_with_distinct_case_ids(tmp_path):
    row = {"question": "Q", "answer": "work\n#### 1"}
    result = select(tmp_path, "gsm8k", [row, row])
    assert [case["case_id"] for case in result["cases"]] == ["gsm8k-0", "gsm8k-1"]
    assert result["summary"]["duplicate_groups"][0]["source_row_indices"] == [0, 1]


def test_invalid_unicode_and_nonfinite_json_are_quarantined(tmp_path):
    path = tmp_path / "raw.jsonl"
    path.write_bytes(b'{"question":"\\ud800"}\n{"answer":NaN}\n')
    result = select_public_rows("boolq", path, {"revision": "fixed-v1"})
    assert result["summary"]["invalid_rows"] == 2
    assert result["cases"] == []
