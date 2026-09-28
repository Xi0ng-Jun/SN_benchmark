import json

import pytest

from rag_eval.notebook_data import adapt
from rag_eval.notebook_bundle import prepare, partition_bundle, request_question
from rag_eval.benchmark_submission import build_submission
from rag_eval import benchmark_official as official
from rag_eval import benchmark_reference as reference
from rag_eval.notebook_data import OFFICIAL_ADAPTATION_REVISION


def hotpot_row():
    return {
        "_id": "hp-1",
        "question": "Which city is older?",
        "answer": "Alpha City",
        "type": "comparison",
        "level": "medium",
        "supporting_facts": [["Alpha", 0], ["Beta", 0]],
        "context": [
            ["Alpha", ["Alpha City was founded in 1800.", "It has a river."]],
            ["Beta", ["Beta City was founded in 1900."]],
            ["Distractor", ["This paragraph contains unrelated facts."]],
        ],
    }


def hotpot_source():
    return {
        "dataset": "hotpotqa/hotpot_qa",
        "split": "validation",
        "revision": "distractor-v1",
        "source_url": "https://hotpotqa.github.io/",
        "license": "CC BY-SA 4.0",
        "setting": "distractor",
    }


def hotpot_hf_row():
    row = hotpot_row()
    row.pop('_id')
    row['id'] = 'hp-1'
    row['context'] = {
        'title': [entry[0] for entry in row['context']],
        'sentences': [entry[1] for entry in row['context']],
    }
    row['supporting_facts'] = {
        'title': [entry[0] for entry in hotpot_row()['supporting_facts']],
        'sent_id': [entry[1] for entry in hotpot_row()['supporting_facts']],
    }
    return row


def test_hotpot_adaptation_preserves_context_and_maps_sentence_facts():
    result = adapt("hotpotqa", [hotpot_row()])
    assert len(result["cases"]) == 1
    case = result["cases"][0]
    assert case["sample_id"] == "hp-1"
    assert case["references"] == ["Alpha City"]
    assert case["gold"]["answer"] == "Alpha City"
    assert case["gold"]["supporting_facts"] == [
        {"title": "Alpha", "sent_id": 0, "document_id": case["material_document_ids"][0], "source_unit_id": "sentence:0"},
        {"title": "Beta", "sent_id": 0, "document_id": case["material_document_ids"][1], "source_unit_id": "sentence:0"},
    ]
    assert len(case["material_document_ids"]) == 3
    assert "Alpha City" in result["documents"][0]["text"]
    assert all("source_units" in document for document in result["documents"])


def test_hotpot_adaptation_accepts_huggingface_columnar_shape():
    original = adapt("hotpotqa", [hotpot_row()])
    columnar = adapt("hotpotqa", [hotpot_hf_row()])
    assert columnar["cases"] == original["cases"]
    assert columnar["documents"] == original["documents"]


def test_hotpot_preserves_empty_sentence_slots_and_unresolvable_official_gold(tmp_path):
    # The published validation set has 49 empty sentence slots and a gold
    # sentence 902 in a five-sentence Jimmy Butler paragraph. Do not repair gold.
    raw = hotpot_row()
    raw['context'][0][1].insert(1, '')
    raw['supporting_facts'] = [['Alpha', 2], ['Beta', 902]]
    data = adapt('hotpotqa', [raw], adaptation_revision='notebook-data-v3')
    case = data['cases'][0]
    assert [(u['sent_id'], u['text']) for u in data['documents'][0]['source_units']] == [
        (0, 'Alpha City was founded in 1800.'), (1, ''), (2, 'It has a river.')]
    assert case['gold']['official_supporting_facts'] == [['Alpha', 2], ['Beta', 902]]
    assert [f['source_unit_id'] for f in case['gold']['supporting_facts']] == ['sentence:2']
    assert case['gold']['unmapped_supporting_facts'] == [
        {'title': 'Beta', 'sent_id': 902, 'reason': 'sentence_id_outside_context'}]
    bundle = {'manifest': {'suite': 'hotpotqa', 'adaptation_revision': 'notebook-data-v3'},
              'cases': data['cases']}
    method = {'name': 'external', 'kind': 'reference', 'citation_style': 'none',
              'model_identity': {'model': 'fixture'}, 'input_policy': 'distractor', 'configuration': {}}
    submission = build_submission(bundle, method=method, predictions=[{
        'case_id': case['case_id'], 'status': 'success', 'prediction': 'Alpha City',
        'record': {'predicted_supporting_facts': [['Alpha', 2]]}}])
    result = official.score_prepared(bundle, submission, official.prepare_inputs(bundle, submission),
                                     source_directory=tmp_path, output_dir=tmp_path / 'score')
    assert result['metrics']['supporting_fact_f1'] == pytest.approx(2 / 3)
    assert result['metric_denominators']['joint_f1'] == 1


def test_hotpot_rejects_missing_or_ambiguous_supporting_fact():
    raw = [hotpot_row()]
    raw[0]["supporting_facts"] = [["Missing", 0]]
    with pytest.raises(ValueError, match="supporting"):
        adapt("hotpotqa", raw)
    raw = [hotpot_row()]
    raw[0]["context"].append(["Alpha", ["Duplicate title."]])
    with pytest.raises(ValueError, match="title"):
        adapt("hotpotqa", raw)


def test_hotpot_bundle_and_v3_request_hide_private_labels(tmp_path):
    raw_path = tmp_path / "hotpot.json"
    source_path = tmp_path / "source.json"
    raw_path.write_text(json.dumps([hotpot_row()]), encoding="utf-8")
    source_path.write_text(json.dumps(hotpot_source()), encoding="utf-8")
    bundle = prepare("hotpotqa", raw_path, source_path, tmp_path / "bundle", max_documents=3,
                     adaptation_revision=OFFICIAL_ADAPTATION_REVISION)
    loaded_case = bundle["cases"][0]
    question = request_question(loaded_case, request_revision="notebook-request-v3")
    assert question["task"] == "qa"
    assert "references" not in question
    assert "supporting_facts" not in json.dumps(question)
    product = partition_bundle(bundle, bundle["partitions"][0]["partition_id"], request_revision="notebook-request-v3")
    assert len(product["documents"]) == 3
    assert bundle["manifest"]["scope"] == "per question distractor context"


def test_hotpot_reference_request_exposes_sentence_units_without_gold(tmp_path):
    raw_path = tmp_path / "hotpot.json"
    source_path = tmp_path / "source.json"
    raw_path.write_text(json.dumps([hotpot_row()]), encoding="utf-8")
    source_path.write_text(json.dumps(hotpot_source()), encoding="utf-8")
    bundle = prepare("hotpotqa", raw_path, source_path, tmp_path / "bundle", max_documents=3,
                     adaptation_revision=OFFICIAL_ADAPTATION_REVISION)
    request = reference.plan_reference(bundle, configuration=reference.reference_config(top_k=3))[0]
    assert request["suite"] == "hotpotqa"
    assert request["supporting_fact_unit_map"]
    assert "official_supporting_facts" not in request["prompt"]
    assert "gold_supporting_facts" not in request["prompt"]


def test_hotpot_reference_run_projects_supporting_facts_and_scores_officially(tmp_path):
    raw_path = tmp_path / "hotpot.json"
    source_path = tmp_path / "source.json"
    raw_path.write_text(json.dumps([hotpot_row()]), encoding="utf-8")
    source_path.write_text(json.dumps(hotpot_source()), encoding="utf-8")
    bundle = prepare("hotpotqa", raw_path, source_path, tmp_path / "bundle", max_documents=3,
                     adaptation_revision=OFFICIAL_ADAPTATION_REVISION)
    configuration = reference.reference_config("full-context", max_context_chars=10000)

    def generate(prompt, schema, *, case_id):
        return {"answer": "Alpha City", "evidence_unit_ids": ["u0", "u2"]}

    run = reference.run_reference(bundle, tmp_path / "reference-run", configuration=configuration,
                                  model_identity={"model_id": "fixture"}, generate=generate,
                                  implementation_identity={"source": "test"})
    submission = json.loads((run / "submission.json").read_text(encoding="utf-8"))
    prepared = official.prepare_inputs(bundle, submission)
    assert prepared["supporting_fact_supported"] is True
    output = official.score_prepared(bundle, submission, prepared, source_directory=tmp_path,
                                     output_dir=tmp_path / "reference-scores")
    assert output["metrics"]["answer_f1"] == 1
    assert output["metrics"]["supporting_fact_f1"] == 1
    assert output["metrics"]["joint_f1"] == 1


def test_hotpot_official_answer_support_and_joint_metrics(tmp_path):
    adapted = adapt("hotpotqa", [hotpot_row()])
    case = adapted["cases"][0]
    manifest = {
        "suite": "hotpotqa", "adaptation_revision": "notebook-data-v3",
        "source": hotpot_source(), "files": {"raw-data": "fixture"},
    }
    bundle = {"manifest": manifest, "cases": adapted["cases"], "documents": adapted["documents"]}
    method = {"name": "hotpot-explicit", "kind": "published", "citation_style": "none",
              "model_identity": {"source": "fixture"}, "input_policy": "frozen-source-documents",
              "configuration": {"setting": "distractor"}}
    record = {"status": "success", "answer": "Alpha City", "predicted_supporting_facts": [["Alpha", 0], ["Beta", 0]]}
    row = {"case_id": case["case_id"], "status": "success", "prediction": "Alpha City", "record": record}
    submission = build_submission(bundle, method=method, predictions=[row])
    prepared = official.prepare_inputs(bundle, submission)
    assert prepared["supporting_fact_supported"] is True
    output = official.score_prepared(bundle, submission, prepared, source_directory=tmp_path,
                                     output_dir=tmp_path / "scores")
    assert output["metrics"]["answer_em"] == 1
    assert output["metrics"]["supporting_fact_f1"] == 1
    assert output["metrics"]["joint_f1"] == 1


def test_hotpot_official_answer_score_remains_available_when_support_is_missing(tmp_path):
    adapted = adapt("hotpotqa", [hotpot_row()])
    case = adapted["cases"][0]
    bundle = {"manifest": {"suite": "hotpotqa", "adaptation_revision": "notebook-data-v3",
                            "source": hotpot_source(), "files": {"raw-data": "fixture"}},
              "cases": adapted["cases"], "documents": adapted["documents"]}
    method = {"name": "hotpot-answer-only", "kind": "sn", "citation_style": "sn",
              "model_identity": {"source": "fixture"}, "input_policy": "frozen-source-documents",
              "configuration": {}}
    row = {"case_id": case["case_id"], "status": "success", "prediction": "Alpha City",
           "record": {"status": "success", "answer": "Alpha City"}}
    submission = build_submission(bundle, method=method, predictions=[row])
    prepared = official.prepare_inputs(bundle, submission)
    assert prepared["supporting_fact_supported"] is False
    output = official.score_prepared(bundle, submission, prepared, source_directory=tmp_path,
                                     output_dir=tmp_path / "scores")
    assert output["metrics"]["answer_f1"] == 1
    assert "supporting_fact_f1" in output["pending_metrics"]
