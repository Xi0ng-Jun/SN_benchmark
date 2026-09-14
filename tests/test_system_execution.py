"""System execution regression specifications; not executed in this phase."""
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace
import json
import sys

import pytest

from rag_eval.starter_results import planned_result, result_record, summarize


@pytest.fixture
def request_types(monkeypatch):
    # No product imports: exercise the orchestration contract with simple DTOs.
    module = ModuleType("app.models.ask")
    module.AskRequest = lambda **values: SimpleNamespace(**values)
    module.AskIntentConfirmation = lambda **values: SimpleNamespace(**values)
    monkeypatch.setitem(sys.modules, "app.models.ask", module)


def test_reasoning_clarification_stops_before_ask(request_types):
    from rag_eval.system_runtime import submit_system_question
    contract = SimpleNamespace(needs_clarification=True,
                               model_dump=lambda **_: {"needs_clarification": True})
    class Repo:
        def preview_reasoning_intent(self, notebook, question, history):
            assert history == ""
            return contract
        def ask(self, *args):
            pytest.fail("Clarification must not submit an Ask")
    row = submit_system_question(Repo(), "n", "Which object?", "reasoning")
    assert row["status"] == "clarification"
    assert row["answer"] == ""


def test_reasoning_confirms_exact_preview_and_never_supplies_gold(request_types):
    from rag_eval.system_runtime import submit_system_question
    prompt = "Q\nFinal answer: requested"
    contract = SimpleNamespace(needs_clarification=False, resolved_question=prompt,
                               model_dump=lambda **_: {"needs_clarification": False})
    class Repo:
        def preview_reasoning_intent(self, notebook, question, history):
            assert question == prompt and history == ""
            return contract
        def ask(self, notebook, payload):
            assert payload.question == prompt
            assert payload.conversation_id is None
            assert payload.intent.contract is contract
            assert payload.intent.answers == []
            assert not hasattr(payload, "expected_answer")
            return SimpleNamespace(answer="Final answer: B", mode="reasoning", answer_id="a",
                                   model_dump=lambda **_: {"answer": "Final answer: B", "citations": [{"id": "k1"}]})
        def _connect(self):
            return SimpleNamespace(execute=lambda *args: SimpleNamespace(
                fetchone=lambda: (prompt, json.dumps({"answer": "Final answer: B"}))))
    row = submit_system_question(Repo(), "n", prompt, "reasoning")
    assert row["status"] == "success"
    assert row["response"]["citations"] == [{"id": "k1"}]
    assert row["persistence_verified"] is True


def test_chunk_ifeval_prompt_and_body_are_not_reformatted(request_types):
    from rag_eval.system_runtime import submit_system_question
    prompt, body = "  Reply with three words.\n", "One two three [k1]\n"
    class Repo:
        def ask(self, notebook, payload):
            assert payload.question == prompt
            assert payload.conversation_id is None
            return SimpleNamespace(answer=body, mode="chunk", answer_id="a",
                                   model_dump=lambda **_: {"answer": body})
        def _connect(self):
            return SimpleNamespace(execute=lambda *args: SimpleNamespace(
                fetchone=lambda: (prompt.strip(), json.dumps({"answer": body}))))
    row = submit_system_question(Repo(), "n", prompt, "chunk")
    assert row["answer"] == body


def test_primary_coverage_preserves_unscored_denominator():
    from rag_eval.system_scoring import primary_scorer
    case = {"suite": "gsm8k", "case_id": "1", "sample_id": "1",
            "scorer": primary_scorer("gsm8k"), "product_protocol": "sn-public-system-v1",
            "material_role": "task_input", "metric_role": "primary"}
    a = planned_result(case, run_id="r", protocol_id="p", track="R", mode="chunk", scorer=case["scorer"])
    b = planned_result({**case, "case_id": "2", "sample_id": "2"},
                       run_id="r", protocol_id="p", track="R", mode="chunk", scorer=case["scorer"])
    group = summarize([a, b], [result_record(a, status="scored", score=1, output_available=True),
                               result_record(b, status="unscored", reason="clarification")])[0]
    assert group["mean_over_scored"] == 1
    assert group["correct_over_planned"] == 0.5
    assert group["scored"] == 1
    assert group["agent_metrics_suppressed"] is True


@pytest.mark.parametrize("suite", ["logiqa", "gsm8k", "bbh", "mmlu", "truthfulqa", "hellaswag", "ifeval"])
def test_new_system_suites_do_not_take_legacy_na_branch(tmp_path, monkeypatch, suite):
    from rag_eval import starter_runner, system_product
    sentinel = RuntimeError("reached system preparation")
    def prepare(*args, **kwargs):
        raise sentinel
    monkeypatch.setattr(system_product, "build_system_bundle", prepare)
    monkeypatch.setattr(starter_runner, "load_bundle", lambda _: ({"suite": suite}, [{"case_id": "1"}]))
    with pytest.raises(RuntimeError) as caught:
        starter_runner.execute(root=tmp_path / "repo", project=tmp_path / "product",
                               bundle_dir=tmp_path / "bundle", run=tmp_path / "run",
                               track="R", mode="chunk", models_path=None)
    assert caught.value is sentinel


def test_system_runner_saves_all_cases_and_report_without_judge(tmp_path, monkeypatch):
    from rag_eval import starter_runner, starter_native, system_product
    from rag_eval.artifacts import save_json
    from rag_eval.starter_protocol import fingerprint
    from rag_eval.starter_report import load_run, write_report
    from rag_eval.system_scoring import primary_scorer
    version = system_product.SYSTEM_VERSION
    source = {"suite": "gsm8k"}
    cases = [{"case_id": str(i), "sample_id": str(i), "suite": "gsm8k", "task": "gsm8k",
              "scorer": "native"} for i in range(2)]
    questions = [{**case, "product_protocol": version, "material_role": "task_input"} for case in cases]
    bundle = {"questions": questions, "documents": [],
              "decisions": [{"status": "applicable"} for _ in cases],
              "manifest": {"suite": "gsm8k", "protocol_version": version,
                           "questions_sha256": fingerprint(questions), "documents_sha256": fingerprint([]),
                           "decisions_sha256": fingerprint([{"status": "applicable"} for _ in cases])}}
    monkeypatch.setattr(system_product, "build_system_bundle", lambda *args: bundle)
    monkeypatch.setattr(starter_runner, "load_bundle", lambda *args: (source, cases))
    monkeypatch.setattr(starter_native, "check_sdk", lambda *args: None)
    runtime = ModuleType("rag_eval.starter_runtime")
    runtime.resolve_models = runtime.make_adapter = lambda *args: pytest.fail("No judge/tested adapter in system track")
    runtime.snapshot_sources = lambda *args: {"test": "stubbed sources"}
    runtime.configure_environment = lambda *args, **kwargs: (None, {
        "comparable_settings_sha256": "settings", "service_config_sha256": "sn-services"})
    monkeypatch.setitem(sys.modules, "rag_eval.starter_runtime", runtime)
    run = tmp_path / "run"
    repository = ModuleType("app.services.sqlite_repository")
    repository.SQLiteRepository = lambda *args: SimpleNamespace(db_path=run / "runtime/database.db", close=lambda: None)
    monkeypatch.setitem(sys.modules, "app.services.sqlite_repository", repository)
    def predict(run, cases, bundle, mode, repo, sink):
        for i, question in enumerate(bundle["questions"]):
            sink({**question, "status": "success" if i == 0 else "clarification",
                  "output_available": i == 0, "prediction": "Final answer: 2" if i == 0 else "",
                  "answer_extraction": {"status": "parsed" if i == 0 else "not_attempted", "value": "2" if i == 0 else None},
                  "behavior": {"kind": "answer_returned" if i == 0 else "clarification"}})
    monkeypatch.setattr(starter_runner, "system_predictions", predict)
    def score(run, source, cases, planned, judge, audits, sink):
        assert judge is None
        for item in planned:
            if item["case_id"] == "0" and item["metric_role"] == "primary":
                sink(result_record(item, status="scored", score=1, output_available=True))
            else:
                sink(result_record(item, status="unscored", reason="diagnostic unavailable or clarification"))
    monkeypatch.setattr(starter_runner, "score_outputs", score)
    directory = tmp_path / "input"
    directory.mkdir()
    save_json(directory / "manifest.json", source)
    starter_runner.execute(root=tmp_path / "repo", project=tmp_path / "production",
                           bundle_dir=directory, run=run, track="R", mode="reasoning")
    loaded = load_run(run)
    assert not loaded["warnings"]
    assert loaded["manifest"]["planned_predictions"] == 2
    assert loaded["manifest"]["models"] == {}
    assert {p["applicability"]["status"] for p in loaded["planned"]} == {"applicable"}
    group = next(g for g in loaded["groups"] if g["scorer"] == primary_scorer("gsm8k"))
    assert group["correct_over_planned"] == 0.5
    assert group["prediction_status_counts"]["clarification"] == 1
    assert group["answer_parse_coverage_over_saved_outputs"] == 1.0
    report = write_report([run], tmp_path / "report")
    assert report["runs"][0]["manifest"]["product_protocol"] == version
    product_path = run / "product-bundle.json"
    original_product = product_path.read_text(encoding="utf-8")
    product = json.loads(original_product)
    product["decisions"][0]["status"] = "not_applicable"
    save_json(product_path, product)
    with pytest.raises(ValueError, match="decisions changed"):
        load_run(run)
    product_path.write_text(original_product, encoding="utf-8")
    manifest = json.loads((run / "manifest.json").read_text())
    manifest["product_protocol"] = "legacy"
    save_json(run / "manifest.json", manifest)
    with pytest.raises(ValueError, match="protocol declaration"):
        load_run(run)


def test_context_capture_failure_does_not_erase_persisted_answer(monkeypatch):
    from rag_eval import system_runtime, system_capture, usage_capture, benchmark_runtime
    logging = ModuleType("app.core.llm_logging")
    logging.LLMInteractionLogger = object
    monkeypatch.setitem(sys.modules, "app.core.llm_logging", logging)
    monkeypatch.setattr(usage_capture, "capture_usage", lambda _: nullcontext({}))
    monkeypatch.setattr(system_capture, "capture_synthesis", lambda _: nullcontext([]))
    def broken_context(_):
        raise KeyError("id_map")
    monkeypatch.setattr(system_capture, "final_context", broken_context)
    monkeypatch.setattr(benchmark_runtime, "evidence_checks", lambda *args: None)
    monkeypatch.setattr(system_runtime, "submit_system_question", lambda *args: {
        "status": "success", "answer": "Final answer: B", "persistence_verified": True,
        "response": {"answer": "Final answer: B", "citations": [{"id": "k1"}]}})
    repo = SimpleNamespace(_runtime=SimpleNamespace(ask_component=object()))
    row = system_runtime.run_system_question(repo, "n", {"question": "Q"}, "chunk", {})
    assert row["answer"] == "Final answer: B"
    assert row["response"]["citations"] == [{"id": "k1"}]
    assert row["status"] == "success"
    assert row["context_supported"] is False
    assert row["context_capture_error"] == "KeyError"
