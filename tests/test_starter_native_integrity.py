"""Offline regression with synthetic rows and the real pinned SDK; no models."""
from copy import deepcopy
from importlib.metadata import PackageNotFoundError, distribution
import shutil

import pytest

from rag_eval.artifacts import digest, save_json, save_jsonl
from rag_eval.public_expansion_protocol import EXPANSION_SUITES, EXPANSION_VERSION
from rag_eval.public_expansion_sources import load_expansion_bundle
from rag_eval.starter_protocol import SDK_VERSION, SUITES, fingerprint, load_bundle


ROWS = {
    "mmlu": {"question": "2+2?", "choices": ["3", "4", "5", "6"], "answer": 1,
             "subject": "elementary_mathematics"},
    "gsm8k": {"question": "How many?", "answer": "Work\n#### 1,200"},
    "truthfulqa": {"question": "Q", "mc1_targets": {
        "choices": ["right", "wrong1", "wrong2", "wrong3"], "labels": [1, 0, 0, 0]}},
    "hellaswag": {"ctx": "C", "endings": ["a", "b", "c", "d"], "label": "2",
                  "activity_label": "Applying sunscreen"},
    "bbh": {"input": "I", "target": "True", "task_name": "boolean_expressions"},
    "logiqa": {"id": "logic", "text": "All robins are birds.", "question": "What follows?",
               "options": ["A robin is a bird", "No birds", "No robins", "Nothing"], "answer": 0,
               "type": {"Necessary Conditional Reasoning": True}},
    "ifeval": {"key": 1, "prompt": "  Write without commas.\r\n",
               "instruction_id_list": ["punctuation:no_comma"], "kwargs": [{}]},
    "squad": {"id": "s1", "title": "Normans", "context": "Ada wrote notes.",
              "question": "Who wrote notes?", "answers": {"text": ["Ada"], "answer_start": [0]}},
    "drop": {"query_id": "d1", "section_id": "history_fixture", "passage": "Ada has two apples.",
             "question": "How many apples?", "answers_spans": {"spans": ["2"], "types": ["number"]},
             "_annotations": [{"number": "2"}]},
    "boolq": {"passage": "Ada wrote notes.", "question": "Did Ada write notes?", "answer": True},
}


@pytest.fixture
def prepared_bundle(tmp_path):
    """Synthetic local data with source bytes from the installed pinned SDK."""
    try:
        dist = distribution("deepeval")
    except PackageNotFoundError:
        pytest.skip("DeepEval 4.2.2 required for future Native interface checks")
    if dist.version != SDK_VERSION:
        pytest.skip("DeepEval version is not the pinned 4.2.2")
    from scripts.prepare_public_starter import prepare

    def make(suite="mmlu", row=None):
        base = tmp_path / suite
        base.mkdir()
        raw, source_path, bundle = base / "source.jsonl", base / "source.json", base / "bundle"
        original = deepcopy(ROWS[suite] if row is None else row)
        # The legacy DROP selector requires ten rows in an eligible section.
        rows = [{**original, "query_id": f"d{i}"} for i in range(10)] if suite == "drop" else [original]
        save_jsonl(raw, rows)
        info = {**SUITES, **EXPANSION_SUITES}[suite]
        source = {
            "dataset": info["dataset"], "split": info["split"], "revision": "fixture-v1",
            "source_url": "https://example.invalid/fixture", "license": "fixture",
            "license_url": "https://example.invalid/license", "conversion": "synthetic local fixture",
            "origin_sha256": digest(raw), "export_sha256": digest(raw),
            "original_order_preserved": True,
        }
        save_json(source_path, source)
        prepare(suite, raw, source_path, bundle)
        manifest, cases = load_bundle(bundle)
        return bundle, manifest, cases

    return make


def test_expansion_request_cannot_override_frozen_expected_output(prepared_bundle):
    from rag_eval.starter_native import build_request, score_prediction
    _, manifest, cases = prepared_bundle()
    request, _ = build_request(cases[0], manifest)
    request["expected_output"] = "D"
    with pytest.raises(ValueError, match="request does not match"):
        score_prediction(cases[0], request, "D", manifest)


@pytest.mark.parametrize("field,value", [
    ("references", ["D"]), ("task", "college_biology"), ("raw_row_sha256", "0" * 64),
    ("expected_answer", "D"), ("n_shots", 5), ("scorer", "local.exact"),
])
def test_rehashed_case_cannot_override_raw_source_fields(prepared_bundle, field, value):
    from rag_eval.starter_native import build_request
    _, manifest, cases = prepared_bundle()
    case = cases[0]
    case[field] = value
    manifest["case_fingerprints"][case["case_id"]] = fingerprint(case)
    with pytest.raises(ValueError, match="case fields or references"):
        build_request(case, manifest)


def test_bundle_rebuild_rejects_edited_references_even_after_hashes_updated(prepared_bundle):
    directory, manifest, cases = prepared_bundle()
    cases[0]["references"] = ["D"]
    cases[0]["expected_answer"] = "D"
    save_jsonl(directory / "cases.jsonl", cases)
    manifest["artifacts"]["cases.jsonl"] = digest(directory / "cases.jsonl")
    manifest["case_fingerprints"][cases[0]["case_id"]] = fingerprint(cases[0])
    save_json(directory / "manifest.json", manifest)
    with pytest.raises(ValueError, match="no longer reproduce raw source"):
        load_expansion_bundle(directory)


def test_bundle_rebuild_rejects_edited_selection(prepared_bundle):
    directory, manifest, _ = prepared_bundle()
    manifest["selection"]["selected_memberships"] = 50
    save_json(directory / "manifest.json", manifest)
    with pytest.raises(ValueError, match="selection no longer reproduce"):
        load_bundle(directory)


def test_old_expansion_bundle_requires_repreparation(tmp_path):
    save_json(tmp_path / "manifest.json", {"suite": "mmlu", "protocol_version": "public-starter-v1"})
    with pytest.raises(ValueError, match="Reprepare.*prepare_public_starter"):
        load_bundle(tmp_path)


def test_expansion_cannot_enable_release_gate(prepared_bundle):
    directory, manifest, _ = prepared_bundle()
    manifest["release_gate"] = True
    save_json(directory / "manifest.json", manifest)
    with pytest.raises(ValueError, match="release gate"):
        load_bundle(directory)


def test_empty_sdk_identity_is_rejected(prepared_bundle):
    directory, manifest, _ = prepared_bundle()
    manifest["sdk_source_hashes"] = {}
    save_json(directory / "manifest.json", manifest)
    with pytest.raises(ValueError, match="nonempty frozen SDK"):
        load_bundle(directory)


def test_bbh_prompt_resource_is_frozen_and_required(prepared_bundle):
    directory, manifest, _ = prepared_bundle("bbh")
    relative = "benchmarks/big_bench_hard/shot_prompts/boolean_expressions.txt"
    assert relative in manifest["sdk_source_hashes"]
    (directory / "sdk-source" / relative).unlink()
    del manifest["sdk_source_hashes"][relative]
    save_json(directory / "manifest.json", manifest)
    with pytest.raises(ValueError, match="omits required"):
        load_bundle(directory)


def test_bbh_prompt_resource_changed_bytes_are_rejected(prepared_bundle):
    directory, _, _ = prepared_bundle("bbh")
    path = directory / "sdk-source/benchmarks/big_bench_hard/shot_prompts/boolean_expressions.txt"
    path.write_text("edited task instruction", encoding="utf-8")
    with pytest.raises(ValueError, match="source/resource changed"):
        load_bundle(directory)


def test_sdk_snapshot_root_cannot_escape_bundle(prepared_bundle):
    directory, _, _ = prepared_bundle()
    outside = directory.parent / "outside-sdk"
    shutil.move(str(directory / "sdk-source"), outside)
    (directory / "sdk-source").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="outside the expansion bundle"):
        load_bundle(directory)


@pytest.mark.parametrize("suite,schema_name,answer", [
    ("mmlu", "MultipleChoiceSchema", "B"),
    ("gsm8k", "NumberSchema", "1,200"),
    ("truthfulqa", "NumberSchema", "4"),
    ("hellaswag", "MultipleChoiceSchema", "C"),
    ("bbh", "BooleanSchema", "True"),
])
def test_all_expansion_native_requests_bind_official_schema_and_scorer(prepared_bundle, suite, schema_name, answer):
    from rag_eval.starter_native import build_request, score_prediction
    _, manifest, cases = prepared_bundle(suite)
    request, schema = build_request(cases[0], manifest)
    assert request["protocol_version"] == EXPANSION_VERSION
    assert schema.__module__ == "deepeval.benchmarks.schema"
    assert schema.__name__ == schema_name
    assert request["expected_output"] == answer
    # This exercises the scorer on saved text only; no tested/judge model exists.
    result = score_prediction(cases[0], request, answer, manifest)
    assert result["score"] == 1.0
    assert result["score_kind"] == "official_native"
    assert result["normalization"]["used_for_official_score"] is False


def test_gsm8k_official_score_does_not_use_numeric_diagnostic(prepared_bundle):
    from rag_eval.starter_native import build_request, score_prediction
    _, manifest, cases = prepared_bundle("gsm8k")
    request, schema = build_request(cases[0], manifest)
    answer = str(schema(answer=1200).answer)
    result = score_prediction(cases[0], request, answer, manifest)
    assert request["expected_output"] == "1,200"
    assert result["score"] == 0.0
    assert result["normalization"]["normalized_answer"] == "1200"


def test_truthfulqa_uses_mc1_shuffle_and_official_fixed_examples(prepared_bundle):
    from rag_eval.starter_native import build_request
    from deepeval.benchmarks.truthful_qa.template import TruthfulQATemplate
    from deepeval.benchmarks.truthful_qa.mode import TruthfulQAMode
    _, manifest, cases = prepared_bundle("truthfulqa")
    request, _ = build_request(cases[0], manifest)
    assert cases[0]["choice_order"] == [2, 1, 3, 0]
    assert "4. right" in request["native_input"]
    assert request["prompt"] == TruthfulQATemplate.generate_output(request["native_input"], TruthfulQAMode.MC1)
    assert request["native_protocol"]["builtin_examples"] == 6
    assert request["n_shots"] is None


@pytest.mark.parametrize("task,target,schema_name", [
    ("date_understanding", "(F)", "BBHMultipleChoice6Schema"),
    ("reasoning_about_colored_objects", "(R)", "BBHMultipleChoice18Schema"),
    ("formal_fallacies", "valid", "ValidSchema"),
    ("sports_understanding", "yes", "AffirmationLowerSchema"),
    ("object_counting", "12", "NumberSchema"),
    ("word_sorting", "apple pear", "StringSchema"),
])
def test_bbh_uses_selected_task_schema_and_resource(prepared_bundle, task, target, schema_name):
    from rag_eval.starter_native import build_request
    _, manifest, cases = prepared_bundle("bbh", {"input": "I", "target": target, "task_name": task})
    request, schema = build_request(cases[0], manifest)
    assert schema.__name__ == schema_name
    assert request["expected_output"] == target
    assert request["prompt"].startswith("Task description: ")
    assert f"benchmarks/big_bench_hard/shot_prompts/{task}.txt" in manifest["sdk_source_hashes"]


@pytest.mark.parametrize("suite,correct,wrong", [
    ("logiqa", "A", "B"), ("gsm8k", "1,200", "1200"), ("bbh", "True", "False"),
    ("mmlu", "B", "A"), ("truthfulqa", "4", "1"), ("hellaswag", "C", "D"),
])
def test_system_materials_to_real_sdk_score(prepared_bundle, suite, correct, wrong):
    from rag_eval.system_product import build_system_bundle
    from rag_eval.system_scoring import score_system_answer

    _, manifest, cases = prepared_bundle(suite)
    product = build_system_bundle(cases, manifest)
    assert len(product["questions"]) == len(cases) == 1
    assert product["questions"][0]["case_id"] == cases[0]["case_id"]
    body = f"Explanation with a source [1].\nFinal answer: {correct}"
    result = score_system_answer(cases[0], body, manifest)
    assert result["status"] == "scored"
    assert result["score"] == 1.0
    assert result["normalized_answer"] == correct
    assert result["details"]["official_native"] is False
    assert score_system_answer(cases[0], f"Final answer: {wrong}", manifest)["score"] == 0.0


@pytest.mark.parametrize("body,expected", [
    ("  A short response\r\n[1] Source\n", 1.0),
    ("  A short response\r\n[1] Source\n[2] Author, title\n", 0.0),
])
def test_ifeval_direct_sdk_scoring_without_audits_preserves_full_body(prepared_bundle, body, expected):
    from rag_eval.starter_native import build_request, score_prediction
    from rag_eval.system_product import build_system_bundle
    from rag_eval.system_scoring import score_system_answer
    bundle, manifest, cases = prepared_bundle("ifeval")
    before = {p.name: p.read_bytes() for p in bundle.iterdir() if p.is_file()}
    case = cases[0]
    product = build_system_bundle(cases, manifest)
    assert product["questions"][0]["question"] == ROWS["ifeval"]["prompt"]
    request, _ = build_request(case, manifest)
    native = score_prediction(case, request, body, manifest)
    result = score_system_answer(case, body, manifest)
    assert native["status"] == result["status"] == "scored"
    assert native["score"] == result["score"] == expected
    assert result["normalized_answer"] == body
    assert result["details"]["instruction_results"] == native["details"]
    assert native["details"][0]["kwargs"] == {}
    assert native["details"][0]["position"] == 0
    assert score_system_answer(case, body, manifest, [{"invalid": True}])["score"] == expected
    assert load_bundle(bundle) == (manifest, cases)
    assert before == {p.name: p.read_bytes() for p in bundle.iterdir() if p.is_file()}


@pytest.mark.parametrize("body", ["hello, world", "hello world"])
def test_ifeval_matches_sdk_predict_including_duplicates_and_default_branch(prepared_bundle, body):
    from types import SimpleNamespace
    from deepeval.benchmarks.ifeval.ifeval import IFEval
    from deepeval.dataset import Golden
    from rag_eval.starter_native import build_request, score_prediction
    row = {**ROWS["ifeval"], "instruction_id_list": [
        "punctuation:no_comma", "length_constraints:number_words",
        "length_constraints:number_words", "combination:two_responses"],
        "kwargs": [{}, {"relation": "at least", "num_words": 50},
                   {"relation": "at least", "num_words": 1}, {}]}
    _, manifest, cases = prepared_bundle("ifeval", row)
    request, _ = build_request(cases[0], manifest)
    model = SimpleNamespace(generate=lambda **_: SimpleNamespace(answer=body))
    golden = Golden(input=row["prompt"], additional_metadata={
        "instruction_ids": row["instruction_id_list"], "kwargs_list": row["kwargs"]})
    prediction, overall, breakdown = IFEval.predict(None, model, golden)
    actual = score_prediction(cases[0], request, prediction, manifest)
    assert actual["status"] == "scored"
    assert actual["score"] == float(overall) == 0.0
    assert [d["position"] for d in actual["details"]] == list(range(4))
    assert [d["score"] for d in actual["details"]][1:] == [0, 1, 1]
    assert bool(actual["details"][-1]["score"]) == breakdown["combination:two_responses"]


def test_ifeval_native_generation_is_attempted_without_audits(prepared_bundle, tmp_path, monkeypatch):
    import sys
    from contextlib import nullcontext
    from types import ModuleType, SimpleNamespace
    from rag_eval import starter_runner, usage_capture
    _, source, cases = prepared_bundle("ifeval")
    logging = ModuleType("app.core.llm_logging")
    logging.LLMInteractionLogger = object
    monkeypatch.setitem(sys.modules, "app.core.llm_logging", logging)
    monkeypatch.setattr(usage_capture, "capture_usage", lambda _: nullcontext({}))
    calls = []
    def generate(prompt, schema):
        calls.append(prompt)
        return schema(answer="hello world")
    model = SimpleNamespace(generate=generate, for_case=lambda *args: nullcontext())
    outputs = []
    starter_runner.native_predictions(tmp_path, source, cases, model, [], outputs.append)
    assert calls == [ROWS["ifeval"]["prompt"]]
    assert outputs[0]["status"] == "success"
    assert outputs[0]["prediction"] == "hello world"


@pytest.mark.parametrize("suite,expected", [("squad", "Ada"), ("drop", "2"), ("boolq", "Yes")])
def test_legacy_product_reviews_and_native_scoring_remain_separate(prepared_bundle, suite, expected):
    from rag_eval.starter_native import build_request, score_prediction
    from rag_eval.starter_product import product_bundle, check_boolq

    _, manifest, cases = prepared_bundle(suite)
    case = cases[0]
    pending = product_bundle([case], {}, [])
    assert pending["questions"] == []
    assert pending["decisions"][0]["status"] == "pending"
    # Fictional review of a synthetic row, used only to exercise the approved branch.
    reviews = {case["sample_id"]: {"status": "approved", "reason": "synthetic unit-test fixture",
                                  "reviewer": "test fixture only", "raw_row_sha256": case["raw_row_sha256"]}}
    product = product_bundle([case], reviews, [])
    assert len(product["questions"]) == len(product["documents"]) == 1
    assert product["questions"][0]["references"] == [expected]
    assert product["questions"][0]["gold_document_ids"] == [product["documents"][0]["id"]]
    assert product["documents"][0]["text"] == case["passage"]
    request, _ = build_request(case, manifest)
    if suite == "squad":
        with pytest.raises(ValueError, match="explicit judge-role"):
            score_prediction(case, request, expected, manifest)
    else:
        assert score_prediction(case, request, expected, manifest)["score"] == 1.0
        assert score_prediction(case, request, "incorrect", manifest)["score"] == 0.0
    if suite == "boolq":
        assert check_boolq("Final answer: Yes\nSee [1].", expected)["score"] == 1.0
        assert check_boolq("Final answer: No\nSee [1].", expected)["score"] == 0.0
        assert check_boolq("Yes or No", expected)["status"] == "unparsed"
    if suite == "drop":
        from rag_eval.starter_protocol import make_case
        incomplete = deepcopy(case["raw_row"])
        del incomplete["_annotations"]
        incomplete_case = make_case(suite, incomplete, case["source_row_index"], case["task"])
        reviews[case["sample_id"]]["raw_row_sha256"] = incomplete_case["raw_row_sha256"]
        rejected = product_bundle([incomplete_case], reviews, [])
        assert rejected["questions"] == []
        assert rejected["decisions"][0]["status"] == "not_applicable"


@pytest.mark.parametrize("selection", [False, True])
@pytest.mark.parametrize("track", ["N", "R"])
def test_ifeval_runner_and_reports_use_direct_policy_with_existing_bundles(
        prepared_bundle, tmp_path, monkeypatch, selection, track):
    import json
    import sys
    from contextlib import nullcontext
    from types import ModuleType, SimpleNamespace
    from rag_eval import starter_runner, usage_capture
    from rag_eval.ifeval_protocol import DIRECT_POLICY, scorer_for
    from rag_eval.selection_bundle import prepare_selection
    from rag_eval.selection_partitions import build_partition_plan
    from rag_eval.starter_report import load_run
    from rag_eval.starter_results import planned_result, result_record
    from rag_eval.experiment_aggregation import aggregate_runs

    directory, source, cases = prepared_bundle("ifeval")
    if selection:
        selected = tmp_path / "selection"
        prepare_selection("ifeval", directory / "raw.jsonl", directory.parent / "source.json", selected)
        directory = selected
        source, cases = load_bundle(directory)
    before = {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    options = {}
    if selection and track == "R":
        plan = build_partition_plan(cases, source)
        plan_path = tmp_path / "partitions.json"
        save_json(plan_path, plan)
        options = dict(partition_plan_path=plan_path, partition_id=plan["partitions"][0]["partition_id"])
    logging = ModuleType("app.core.llm_logging")
    logging.LLMInteractionLogger = object
    monkeypatch.setitem(sys.modules, "app.core.llm_logging", logging)
    monkeypatch.setattr(usage_capture, "capture_usage", lambda _: nullcontext({}))
    runtime = ModuleType("rag_eval.starter_runtime")
    runtime.resolve_models = lambda *args: ({"tested": {}}, {"tested": {"fixture": True}})
    model = SimpleNamespace(generate=lambda prompt, schema: schema(answer="hello world"),
        for_case=lambda *args: nullcontext(), client=SimpleNamespace(close=lambda: None))
    runtime.make_adapter = lambda *args: model
    runtime.snapshot_sources = lambda *args: {"fixture": True}
    runtime.configure_environment = lambda *args, **kwargs: (None, {
        "comparable_settings_sha256": "settings", "service_config_sha256": "services"})
    monkeypatch.setitem(sys.modules, "rag_eval.starter_runtime", runtime)
    run = tmp_path / "direct"
    repository = ModuleType("app.services.sqlite_repository")
    repository.SQLiteRepository = lambda *args: SimpleNamespace(db_path=run / "runtime/database.db", close=lambda: None)
    monkeypatch.setitem(sys.modules, "app.services.sqlite_repository", repository)
    def predict(run, cases, bundle, mode, repo, sink):
        for question in bundle["questions"]:
            sink({**question, "status": "success", "output_available": True, "prediction": "hello world",
                  "product_record": {"answer": "hello world", "citation_count": 0},
                  "answer_extraction": {"status": "not_applicable", "value": "hello world"}})
    monkeypatch.setattr(starter_runner, "system_predictions", predict)
    starter_runner.execute(root=tmp_path / "repo", project=tmp_path / "sn", bundle_dir=directory,
        run=run, track=track, mode="chunk" if track == "R" else None,
        models_path=tmp_path / "models.json" if track == "N" else None,
        audits_path=tmp_path / "nonexistent-audits.jsonl", **options)
    loaded = load_run(run)
    assert not loaded["warnings"]
    assert loaded["manifest"]["identity"]["ifeval_scoring"] == DIRECT_POLICY
    primary = next(row for row in loaded["scores"] if row["scorer"] == scorer_for(track))
    assert (primary["status"], primary["score"]) == ("scored", 1.0)
    assert source["verifier_audit"] == "pending"
    assert before == {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    # Reconstruct a synthetic legacy run to exercise old policy/scorer readers.
    old = tmp_path / "legacy"
    shutil.copytree(run, old)
    manifest = deepcopy(loaded["manifest"])
    manifest["identity"].pop("ifeval_scoring")
    manifest["run_id"] = old.name
    manifest["protocol_id"] = fingerprint({**manifest["identity"], "mode": manifest["mode"]})
    manifest["pairing_id"] = fingerprint(manifest["identity"]) if track == "R" else None
    planned = [planned_result(p, run_id=old.name, protocol_id=manifest["protocol_id"],
        track=track, mode=manifest["mode"], scorer=scorer_for(track, None)
        if p["scorer"] == scorer_for(track) else p["scorer"]) for p in loaded["planned"]]
    save_jsonl(old / "planned.jsonl", planned)
    save_jsonl(old / "scores.jsonl", [result_record(p, status="not_applicable", output_available=True,
        reason="historical fixture: missing audit or diagnostic unavailable") for p in planned])
    manifest["planned_sha256"] = digest(old / "planned.jsonl")
    save_json(old / "manifest.json", manifest)
    legacy = load_run(old)
    assert not legacy["warnings"]
    assert all(r["status"] == "not_applicable" and r["score"] is None for r in legacy["scores"])
    data = aggregate_runs([old, run])
    assert scorer_for(track) in data["catalog"]
    assert scorer_for(track, None) in data["catalog"]
    assert "历史" in data["catalog"][scorer_for(track, None)]["name"]
    # A declared direct policy must not accept a legacy planned scorer.
    manifest["identity"]["ifeval_scoring"] = DIRECT_POLICY
    manifest["protocol_id"] = fingerprint({**manifest["identity"], "mode": manifest["mode"]})
    manifest["pairing_id"] = fingerprint(manifest["identity"]) if track == "R" else None
    save_json(old / "manifest.json", manifest)
    with pytest.raises(ValueError, match="scorers|scoring policy"):
        load_run(old)
