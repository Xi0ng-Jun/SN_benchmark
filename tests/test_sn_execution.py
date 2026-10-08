"""Shared SN execution contracts, exercised without product services."""
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace
import json
import sys

import pytest

from rag_eval.run_results import planned_result, result_record, summarize


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


def test_streaming_cancellation_is_propagated_unchanged(request_types):
    from rag_eval.system_runtime import submit_system_question
    class AskCancelled(Exception):
        pass
    class StreamingCancelled(AskCancelled):
        pass
    original = StreamingCancelled('private cancellation detail')
    class Repo:
        def ask(self, *args):
            raise original
    with pytest.raises(StreamingCancelled) as caught:
        submit_system_question(Repo(), 'notebook', 'Who?', 'chunk')
    assert caught.value is original


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


def test_chunk_prompt_and_body_are_not_reformatted(request_types):
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
    case = {"suite": "multihop_rag", "case_id": "1", "sample_id": "1",
            "scorer": "product.notebook.multihop_official_weak_match_body_v1", "product_protocol": "sn-notebook-benchmarks-v1",
            "material_role": "source_documents", "metric_role": "primary", "score_kind": "binary"}
    a = planned_result(case, run_id="r", protocol_id="p", track="R", mode="chunk", scorer=case["scorer"])
    b = planned_result({**case, "case_id": "2", "sample_id": "2"},
                       run_id="r", protocol_id="p", track="R", mode="chunk", scorer=case["scorer"])
    group = summarize([a, b], [result_record(a, status="scored", score=1, output_available=True),
                               result_record(b, status="unscored", reason="clarification")])[0]
    assert group["mean_over_scored"] == 1
    assert group["correct_over_planned"] == 0.5
    assert group["scored"] == 1
    assert group["agent_metrics_suppressed"] is True






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
