"""Native SDK contract tests; fixture model is the only judge boundary."""
from contextlib import contextmanager
import json
import socket
from types import SimpleNamespace

import pytest


@pytest.fixture
def sdk(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
    monkeypatch.setenv("DEEPEVAL_DISABLE_DOTENV", "1")
    monkeypatch.setenv("DEEPEVAL_NO_INSPECT_PROMPT", "1")
    monkeypatch.setenv("CONFIDENT_TRACE_FLUSH", "0")
    monkeypatch.delenv("CONFIDENT_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    def blocked(*args, **kwargs):
        raise AssertionError("Tests must not open network connections")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    pytest.importorskip("deepeval")
    from deepeval.models import DeepEvalBaseLLM
    from deepeval.tracing import observe, update_current_span

    class Judge(DeepEvalBaseLLM):
        def __init__(self, check, fail=False):
            self.check, self.fail, self.calls = check, fail, 0
            self.bindings = []
            self.prompts = []
            self.bound = False
            super().__init__("fixture-judge")
        def load_model(self):
            return self
        def get_model_name(self):
            return "fixture-judge"
        @contextmanager
        def for_case(self, case_id, request_id):
            self.bindings.append((case_id, request_id))
            self.bound = True
            try:
                yield self
            finally:
                self.bound = False
        def generate(self, prompt, schema=None):
            assert self.bound
            self.check()
            self.calls += 1
            self.prompts.append(prompt)
            if self.fail:
                raise RuntimeError("fixture judge failure")
            fields = schema.model_fields
            if "verdicts" in fields:
                return schema.model_validate({"verdicts": [{"verdict": "yes", "statement": "Ada wrote notes."}]})
            for key in ("truths", "claims", "statements"):
                if key in fields:
                    return schema.model_validate({key: ["Ada wrote notes."]})
            if "task" in fields:
                return schema.model_validate({"task": "Find the author", "outcome": "Ada wrote notes."})
            if "plan" in fields:
                return schema.model_validate({"plan": ["Find who wrote notes"]})
            if "score" in fields:
                return schema.model_validate({"score": 1, "reason": "Executed the observed plan."})
            if "verdict" in fields:
                return schema.model_validate({"verdict": 1, "reason": "Completed the observed task."})
            if "reason" in fields:
                return schema.model_validate({"reason": "Supported by the supplied text."})
            raise AssertionError(f"Unexpected schema: {schema}")
        async def a_generate(self, prompt, schema=None):
            raise AssertionError("Scoring must stay synchronous")

    class Session:
        active = False
        callback = None
        @contextmanager
        def factory(self, *, on_span):
            self.active, self.callback = True, on_span
            try:
                yield SimpleNamespace(errors=[])
            finally:
                self.active = False
        def emit(self, name, kind, stage, input, output, metadata=None):
            @observe(name=name, type=kind)
            def component():
                update_current_span(input=json.dumps(input), output=json.dumps(output), metadata=metadata)
                self.callback({"name": name, "kind": kind, "stage": stage,
                               "input": input, "output": output, "metadata": metadata or {}})
                # SN wrapper returns None so SDK cannot capture product objects.
            component()
            return output
    return SimpleNamespace(Judge=Judge, Session=Session)


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def runner_for(tmp_path, sdk, check, fail=False, **kwargs):
    from rag_eval.native_agent import NativeAgentEvaluation
    judge = sdk.Judge(check, fail=fail)
    runner = NativeAgentEvaluation(tmp_path / "native", judge=judge,
                                   judge_identity={"model_id": "fixture-judge", "config_sha256": "f" * 64},
                                   **kwargs)
    return runner, judge


def test_score_is_durable_before_next_metric_calls_judge(tmp_path, sdk):
    session = sdk.Session()
    runner, judge = runner_for(tmp_path, sdk, lambda: None)
    original = judge.generate
    def generate(prompt, schema=None):
        if schema.__module__.startswith('deepeval.metrics.answer_relevancy'):
            scores = rows(tmp_path / 'native/native-scores.jsonl')
            assert any(s['metric'] == 'Faithfulness' and s['status'] == 'scored' for s in scores)
            raise KeyboardInterrupt('stop during second metric')
        return original(prompt, schema)
    judge.generate = generate
    def product():
        session.emit('sn.synthesis.chunks', 'agent', 'synthesis', {'question': 'Who?'},
                     {'answer': 'Ada'}, {'question': 'Who?', 'context_block': 'Ada wrote notes.'})
        return {'status': 'success', 'answer': 'Ada'}
    with pytest.raises(KeyboardInterrupt):
        runner.run_case(case_id='durable', mode='chunk', question='Who?', invoke_and_persist=product,
                        session_factory=session.factory)
    scores = rows(tmp_path / 'native/native-scores.jsonl')
    faith = [s for s in scores if s['metric'] == 'Faithfulness']
    assert len(faith) == 1 and faith[0]['status'] == 'scored'
    assert faith[0]['elapsed_seconds'] >= 0
    error = next(s for s in scores if s['metric'] == 'Answer Relevancy')
    assert error['status'] == 'error' and error['score'] is None


def test_metric_selection_uses_native_trace_without_unselected_scores(tmp_path, sdk):
    runner, judge = runner_for(tmp_path, sdk, lambda: None, enable_whole_trace_metrics=True,
                               metrics=['step_efficiency'])
    session = sdk.Session()
    def product():
        session.emit('sn.synthesis.chunks', 'agent', 'synthesis', {'question': 'Who?'},
                     {'answer': 'Ada'}, {'question': 'Who?', 'context_block': 'Ada wrote notes.'})
        return {'status': 'success', 'answer': 'Ada'}
    runner.run_case(case_id='selected', mode='chunk', question='Who?', invoke_and_persist=product,
                    session_factory=session.factory)
    scores = rows(tmp_path / 'native/native-scores.jsonl')
    assert [(s['metric'], s['status']) for s in scores] == [('Step Efficiency', 'scored')]
    assert any('sn.synthesis.chunks' in p for p in judge.prompts)
    assert len([s for s in rows(tmp_path / 'native/components.jsonl') if s['record_type'] == 'sample']) == 1
    trace = rows(tmp_path / 'native/native-traces.jsonl')[-1]['trace']
    synthesis = trace['root_spans'][0]['children'][0]
    assert synthesis['input'] == 'Who?' and synthesis['output'] == 'Ada'
    assert synthesis['retrieval_context'] == ['Ada wrote notes.']


def test_real_metrics_score_after_durable_answer_and_native_trace(tmp_path, sdk):
    session = sdk.Session()
    answer = tmp_path / "answer.json"
    calls = []
    def check():
        assert answer.exists()
        assert rows(tmp_path / "native/native-traces.jsonl")[-1]["phase"] == "before_scoring"
        assert not session.active
    runner, judge = runner_for(tmp_path, sdk, check)
    def product():
        calls.append(1)
        session.emit("sn.retrieve.chunks", "retriever", "retrieve", {"query": "Who wrote notes?"},
                     [{"text": "Ada wrote notes."}])
        session.emit("sn.synthesis.chunks", "agent", "synthesis", {"question": "Who wrote notes?"},
                     {"answer": "Ada wrote notes.", "grounded": True},
                     {"question": "Who wrote notes?", "context_block": "[1] Ada wrote notes."})
        result = {"status": "ok", "answer": "Ada wrote notes."}
        answer.write_text(json.dumps(result))
        return result
    result = runner.run_case(case_id="case-1", mode="chunks", question="Who wrote notes?",
                             invoke_and_persist=product, session_factory=session.factory)
    assert result["answer"] == "Ada wrote notes."
    assert calls == [1] and judge.calls > 0
    scores = rows(tmp_path / "native/native-scores.jsonl")
    assert {s["metric"] for s in scores if s["status"] == "scored"} == {
        "Contextual Relevancy", "Faithfulness", "Answer Relevancy"}
    assert all(s["score"] == 1 for s in scores if s["status"] == "scored")
    assert not runner.has_errors
    reports = list((tmp_path / "native/sdk").rglob("test_run_*.json"))
    assert reports
    report = json.loads(reports[0].read_text())
    assert "Ada wrote notes." in json.dumps(report)
    exported_trace = report["testCases"][0]["trace"]
    exported_root = next(s for s in exported_trace["agentSpans"] if s["name"] == "benchmark.notebook_case")
    exported_synthesis = next(s for s in exported_trace["agentSpans"] if s["name"] == "sn.synthesis.chunks")
    exported_retrieval = exported_trace["retrieverSpans"][0]
    assert exported_synthesis["parentUuid"] == exported_retrieval["parentUuid"] == exported_root["uuid"]
    assert {m["name"] for m in exported_synthesis["metricsData"]} == {"Faithfulness", "Answer Relevancy"}
    assert exported_retrieval["metricsData"][0]["name"] == "Contextual Relevancy"
    trace = rows(tmp_path / "native/native-traces.jsonl")[-1]["trace"]
    def flatten(spans):
        return [node for span in spans for node in [span, *flatten(span["children"])]]
    spans = flatten(trace["root_spans"])
    synthesis = next(s for s in spans if s["name"] == "sn.synthesis.chunks")
    assert synthesis["parent_uuid"] in {s["uuid"] for s in spans}
    assert synthesis["output"] == "Ada wrote notes."


def test_failed_judge_preserves_answer_and_explicit_score_errors(tmp_path, sdk):
    runner, judge = runner_for(tmp_path, sdk, lambda: None, fail=True)
    session = sdk.Session()
    def product():
        session.emit("sn.synthesis.chunks", "agent", "synthesis", {"question": "Who?"},
                     {"answer": "Ada"}, {"question": "Who?", "context_block": "Ada wrote notes."})
        (tmp_path / "answer").write_text("Ada")
        return {"status": "ok", "answer": "Ada"}
    result = runner.run_case(case_id="error", mode="chunks", question="Who?", invoke_and_persist=product,
                             session_factory=session.factory)
    assert result["answer"] == (tmp_path / "answer").read_text()
    assert judge.calls >= 2  # independent metrics still run
    assert runner.has_errors
    failures = [s for s in rows(tmp_path / "native/native-scores.jsonl") if s["status"] == "error"]
    assert len(failures) == 2 and all(s["score"] is None for s in failures)
    assert list((tmp_path / "native/sdk").rglob("test_run_*.json"))


def test_empty_retrieval_and_no_plan_are_not_applicable_without_judge(tmp_path, sdk):
    runner, judge = runner_for(tmp_path, sdk, lambda: None)
    session = sdk.Session()
    def product():
        session.emit("sn.retrieve.chunks", "retriever", "retrieve", {"query": "Who?"}, [])
        return {"status": "clarification", "answer": "Please specify the source."}
    runner.run_case(case_id="empty", mode="chunks", question="Who?", invoke_and_persist=product,
                    session_factory=session.factory)
    scores = rows(tmp_path / "native/native-scores.jsonl")
    assert judge.calls == 0
    assert scores and all(s["status"] == "not_applicable" and s["score"] is None for s in scores)
    assert rows(tmp_path / "native/native-traces.jsonl")
    assert not runner.has_errors


def test_cancellation_keeps_completed_components_and_trace(tmp_path, sdk):
    runner, judge = runner_for(tmp_path, sdk, lambda: None)
    session = sdk.Session()
    def product():
        session.emit("sn.retrieve.chunks", "retriever", "retrieve", {"query": "Who?"}, [{"text": "Ada"}])
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        runner.run_case(case_id="cancel", mode="chunks", question="Who?", invoke_and_persist=product,
                        session_factory=session.factory)
    assert rows(tmp_path / "native/components.jsonl")
    assert rows(tmp_path / "native/native-traces.jsonl")[-1]["phase"] == "cancelled"
    assert judge.calls == 0 and runner.has_errors
    assert (tmp_path / "native/native-summary.json").exists()


def test_multi_query_samples_keep_individual_contexts_and_real_parent(tmp_path, sdk):
    session = sdk.Session()
    runner, judge = runner_for(tmp_path, sdk, lambda: None)
    def product():
        session.emit("sn.retrieve.chunks_multi", "retriever", "retrieve", {"queries": ["Who?", "When?", "Where?"]},
                     [{"text": "Ada wrote notes."}, {"text": "Yesterday."}],
                     {"per_query": [{"query": "Who?", "candidates": [{"text": "Ada wrote notes."}]},
                                    {"query": "When?", "candidates": [{"text": "Yesterday."}]},
                                    {"query": "Where?", "candidates": []}]})
        return {"status": "ok", "answer": "Ada wrote notes yesterday."}
    runner.run_case(case_id="multi", mode="mix", question="Who and when?", invoke_and_persist=product,
                    session_factory=session.factory)
    samples = [r for r in rows(tmp_path / "native/components.jsonl") if r["record_type"] == "sample"]
    assert [(s["input"], s["retrieval_context"]) for s in samples] == [
        ("Who?", ["Ada wrote notes."]), ("When?", ["Yesterday."]), ("Where?", [])]
    assert {s["grouping"] for s in samples} == {"multi_query_evaluate"}
    scores = [s for s in rows(tmp_path / "native/native-scores.jsonl") if s["metric"] == "Contextual Relevancy"]
    assert [s["status"] for s in sorted(scores, key=lambda s: s["query_index"])] == [
        "scored", "scored", "not_applicable"]
    assert len(list((tmp_path / "native/sdk").rglob("test_run_*.json"))) == 2
    assert not runner.has_errors


def test_answer_relevancy_does_not_require_retrieval_context(tmp_path, sdk):
    runner, judge = runner_for(tmp_path, sdk, lambda: None)
    session = sdk.Session()
    def product():
        session.emit("sn.synthesis.reasoning", "agent", "synthesis", {"question": "Who?"},
                     {"answer": "Ada"}, {"question": "Who?", "context_block": ""})
        return {"status": "ok", "answer": "Ada"}
    runner.run_case(case_id="contextless", mode="reasoning", question="Who?", invoke_and_persist=product,
                    session_factory=session.factory)
    scores = {s["metric"]: s for s in rows(tmp_path / "native/native-scores.jsonl")}
    assert scores["Faithfulness"]["status"] == "not_applicable"
    assert scores["Answer Relevancy"]["status"] == "scored"
    assert not runner.has_errors


@pytest.mark.parametrize("mode,plan,expected", [
    ("mix", True, {"Task Completion", "Step Efficiency"}),
    ("reasoning", False, {"Task Completion", "Step Efficiency"}),
    ("reasoning", True, {"Task Completion", "Step Efficiency", "Plan Quality", "Plan Adherence"}),
])
def test_whole_trace_uses_real_sdk_trace_and_only_explicit_reasoning_plan(tmp_path, sdk, mode, plan, expected):
    runner, judge = runner_for(tmp_path, sdk, lambda: None, enable_whole_trace_metrics=True)
    session = sdk.Session()
    def product():
        if plan:
            session.emit("sn.reasoning.initial_plan", "agent", "plan", {"question": "Who?"},
                         {"subqueries": ["Find who wrote notes"]})
        session.emit("sn.reasoning.reflect", "agent", "reflect", {"question": "Who?"},
                     {"next_action": "synthesize", "decision": "evidence ready"})
        return {"status": "ok", "answer": "Ada wrote notes."}
    runner.run_case(case_id="whole", mode=mode, question="Who?", invoke_and_persist=product,
                    session_factory=session.factory)
    scores = rows(tmp_path / "native/native-scores.jsonl")
    assert {s["metric"] for s in scores if s["status"] == "scored"} == expected
    assert not runner.has_errors
    assert any("sn.reasoning.reflect" in prompt for prompt in judge.prompts)
    assert all(s["score"] is None for s in scores if s["status"] == "not_applicable")


def test_two_cases_have_distinct_judge_bindings_and_no_trace_leakage(tmp_path, sdk):
    runner, judge = runner_for(tmp_path, sdk, lambda: None)
    session = sdk.Session()
    for case_id in ["one", "two"]:
        def product():
            session.emit("sn.synthesis.chunks", "agent", "synthesis", {"question": case_id},
                         {"answer": case_id}, {"question": case_id, "context_block": case_id})
            return {"status": "ok", "answer": case_id}
        runner.run_case(case_id=case_id, mode="chunks", question=case_id, invoke_and_persist=product,
                        session_factory=session.factory)
    assert len(judge.bindings) == 2 and judge.bindings[0][1] != judge.bindings[1][1]
    traces = [r for r in rows(tmp_path / "native/native-traces.jsonl") if r["phase"] == "before_scoring"]
    assert len(traces) == 2 and traces[0]["trace"]["uuid"] != traces[1]["trace"]["uuid"]
    assert "one" not in json.dumps(traces[1]["trace"])
    assert len(runner.finish()["cases"]) == 2


def test_incomplete_observation_disables_whole_trace_scores(tmp_path, sdk):
    runner, judge = runner_for(tmp_path, sdk, lambda: None, enable_whole_trace_metrics=True)
    session = sdk.Session()
    @contextmanager
    def broken_session(*, on_span):
        with session.factory(on_span=on_span):
            yield SimpleNamespace(errors=[{"code": "projection_output", "name": "sn.reasoning.initial_plan"}])
    def product():
        session.emit("sn.synthesis.chunks", "agent", "synthesis", {"question": "Who?"},
                     {"answer": "Ada"}, {"question": "Who?", "context_block": "Ada wrote notes."})
        return {"status": "ok", "answer": "Ada"}
    runner.run_case(case_id="incomplete", mode="reasoning", question="Who?", invoke_and_persist=product,
                    session_factory=broken_session)
    scores = rows(tmp_path / "native/native-scores.jsonl")
    assert {s["metric"] for s in scores if s["status"] == "scored"} == {"Faithfulness", "Answer Relevancy"}
    assert runner.has_errors
    assert all(s["status"] == "not_applicable" for s in scores if s.get("grouping") == "whole_trace")


def test_final_artifact_failure_preserves_original_cancellation(tmp_path, sdk, monkeypatch):
    runner, judge = runner_for(tmp_path, sdk, lambda: None)
    session = sdk.Session()
    def failed_summary():
        raise OSError("fixture disk full")
    monkeypatch.setattr(runner, "finish", failed_summary)
    def cancelled():
        raise KeyboardInterrupt("original cancellation")
    with pytest.raises(KeyboardInterrupt, match="original cancellation"):
        runner.run_case(case_id="cancel", mode="chunks", question="Who?", invoke_and_persist=cancelled,
                        session_factory=session.factory)
    assert runner.has_errors


def test_local_setup_clears_runtime_cloud_credentials_without_editing_saved_files(tmp_path, sdk, monkeypatch):
    import sys
    from pydantic import SecretStr
    from deepeval import get_settings
    from deepeval.confident.api import get_confident_api_key
    from rag_eval.native_agent import require_native_tracing
    settings = get_settings()
    monkeypatch.setattr(settings, "CONFIDENT_API_KEY", SecretStr("fixture-persisted-key"))
    monkeypatch.setattr(settings, "DEEPEVAL_DEFAULT_SAVE", "dotenv:" + str(tmp_path / "saved.env"))
    saved = tmp_path / "saved.env"
    saved.write_text("CONFIDENT_API_KEY=fixture-persisted-key\n")
    tracing = SimpleNamespace(NATIVE_TRACE_VERSION="sn-deepeval-native-v1", evaluation_session=lambda: None)
    monkeypatch.setitem(sys.modules, "app.core.evaluation_tracing", tracing)
    assert require_native_tracing() is tracing
    assert get_confident_api_key() is None
    assert saved.read_text() == "CONFIDENT_API_KEY=fixture-persisted-key\n"


def test_section_contexts_and_repeated_evidence_diagnostics_are_separate(tmp_path, sdk):
    runner, judge = runner_for(tmp_path, sdk, lambda: None)
    session = sdk.Session()
    def product():
        for query in ("Who?", "Who wrote the notes?"):
            session.emit("sn.retrieve.chunks", "retriever", "retrieve", {"query": query},
                         [{"text": "Ada wrote notes.", "chunk_id": "chunk-1", "source_id": "source-1"}])
        for question, context, answer in [("Who?", "[1] Ada wrote notes.", "Ada"),
                                          ("When?", "[2] The notes date from May.", "May")]:
            session.emit("sn.synthesis.reasoning", "agent", "synthesis", {"question": question},
                         {"answer": answer}, {"question": question, "context_block": context,
                                              "section_title": question})
        return {"status": "success", "answer": "Ada, in May."}
    runner.run_case(case_id="sections", mode="reasoning", question="Who and when?", invoke_and_persist=product,
                    session_factory=session.factory)
    samples = [r for r in rows(tmp_path / "native/components.jsonl") if r["record_type"] == "sample"]
    synthesis = [r for r in samples if r["span_name"] == "sn.synthesis.reasoning"]
    assert [(r["input"], r["actual_output"], r["retrieval_context"]) for r in synthesis] == [
        ("Who?", "Ada", ["[1] Ada wrote notes."]), ("When?", "May", ["[2] The notes date from May."])]
    retrieval = [r for r in samples if r["span_name"] == "sn.retrieve.chunks"]
    assert all(json.loads(r["actual_output"])[0]["chunk_id"] == "chunk-1" for r in retrieval)
    final_trace = rows(tmp_path / "native/native-traces.jsonl")[-1]["trace"]
    def walk(spans):
        return [node for span in spans for node in [span, *walk(span["children"])]]
    native_retrieval = [r for r in walk(final_trace["root_spans"]) if r["name"] == "sn.retrieve.chunks"]
    assert [json.loads(r["output"]) for r in native_retrieval] == [
        [{"text": "Ada wrote notes.", "chunk_id": "chunk-1", "source_id": "source-1"}],
        [{"text": "Ada wrote notes.", "chunk_id": "chunk-1", "source_id": "source-1"}]]
    diagnostic = rows(tmp_path / "native/native-diagnostics.jsonl")[0]
    assert diagnostic["callback_count"] == 4
    assert diagnostic["stage_counts"] == {"retrieve": 2, "synthesis": 2}
    assert diagnostic["evidence_repeat_count"] == 1
    assert diagnostic["sdk_span_count"] == 5  # SDK removes its internal wrapper on completion
    assert diagnostic["product_seconds"] >= 0 and diagnostic["judge_seconds"] >= 0


def test_error_record_write_failure_cannot_replace_product_cancellation(tmp_path, sdk, monkeypatch):
    runner, judge = runner_for(tmp_path, sdk, lambda: None)
    session = sdk.Session()
    original_append = runner._append
    def unavailable_error_file(filename, row):
        if filename == "native-errors.jsonl":
            raise OSError("fixture error sink unavailable")
        return original_append(filename, row)
    monkeypatch.setattr(runner, "_append", unavailable_error_file)
    def cancelled():
        raise KeyboardInterrupt("original cancellation")
    with pytest.raises(KeyboardInterrupt, match="original cancellation"):
        runner.run_case(case_id="cancel", mode="chunks", question="Who?", invoke_and_persist=cancelled,
                        session_factory=session.factory)


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, type("AskCancelled", (Exception,), {}),
    type("_StreamingAskCancelled", (type("AskCancelled", (Exception,), {}),), {})])
def test_sdk_error_trace_hides_exception_message_and_rethrows_same_object(tmp_path, sdk, exception_type):
    runner, judge = runner_for(tmp_path, sdk, lambda: None)
    session = sdk.Session()
    original = exception_type("private-service-sentinel")
    def cancelled():
        session.emit("sn.retrieve.chunks", "retriever", "retrieve", {"query": "Who?"}, [{"text": "Ada"}])
        raise original
    with pytest.raises(exception_type) as caught:
        runner.run_case(case_id="cancel", mode="reasoning", question="Who?", invoke_and_persist=cancelled,
                        session_factory=session.factory)
    assert caught.value is original
    raw = (tmp_path / "native/native-traces.jsonl").read_text()
    assert "private-service-sentinel" not in raw
    assert exception_type.__name__ in raw
    assert rows(tmp_path / "native/native-traces.jsonl")[-1]["phase"] == "cancelled"
