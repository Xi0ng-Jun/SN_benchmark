from types import ModuleType, SimpleNamespace
import sys
from pathlib import Path

import pytest


@pytest.fixture
def request_types(monkeypatch):
    module = ModuleType("app.models.ask")
    module.AskRequest = lambda **values: SimpleNamespace(**values)
    module.AskIntentConfirmation = lambda **values: SimpleNamespace(
        **{**values, "answers": [SimpleNamespace(**item) for item in values.get("answers", [])]}
    )
    monkeypatch.setitem(sys.modules, "app.models.ask", module)


@pytest.fixture
def sn_backend():
    backend = Path("/home/wabiwabi/silicon-notebook/project/backend")
    if not backend.is_dir():
        pytest.skip("SN checkout is unavailable")
    before_modules = set(sys.modules)
    sys.path.insert(0, str(backend))
    try:
        yield
    finally:
        sys.path.remove(str(backend))
        for name in list(sys.modules):
            if name not in before_modules and (name == "app" or name.startswith("app.")):
                del sys.modules[name]


def contract(*, needs=True, ambiguities=()):
    return SimpleNamespace(
        needs_clarification=needs,
        resolved_question="Which interpretation applies?",
        ambiguities=list(ambiguities),
        model_dump=lambda **_: {
            "objective": "Which interpretation applies?",
            "resolved_question": "Which interpretation applies?",
            "needs_clarification": needs,
            "ambiguities": list(ambiguities),
        },
    )


def test_cover_all_options_policy_is_deterministic_and_records_sources():
    from rag_eval.reasoning_protocol import auto_confirm_contract

    value = auto_confirm_contract(contract(ambiguities=(
        {"id": "scope", "question": "Which scope?", "required": True,
         "options": ["A", "", "B"]},
        {"id": "missing", "question": "What wording?", "required": True,
         "options": []},
        {"id": "optional", "question": "Optional?", "required": False,
         "options": ["C"]},
    )))

    assert value.policy == {
        "name": "cover-all-options",
        "version": "v1",
    }
    assert value.answers == [
        {"id": "scope", "answer": "同时考虑以下所有可能：A；B"},
        {"id": "missing", "answer": "Which interpretation applies?"},
    ]
    assert value.decisions == [
        {"id": "scope", "question": "Which scope?", "options": ["A", "", "B"],
         "selected_answer": "同时考虑以下所有可能：A；B", "answer_source": "all_options"},
        {"id": "missing", "question": "What wording?", "options": [],
         "selected_answer": "Which interpretation applies?", "answer_source": "resolved_question_fallback"},
    ]


def test_standard_reasoning_keeps_clarification_stop(request_types):
    from rag_eval.system_runtime import submit_system_question

    class Repo:
        def preview_reasoning_intent(self, notebook, question, history):
            return contract(ambiguities=({"id": "x", "question": "Choose", "required": True, "options": ["A"]},))

        def ask(self, *args):
            pytest.fail("standard reasoning must still stop on clarification")

    row = submit_system_question(Repo(), "n", "Which?", "reasoning")
    assert row["status"] == "clarification"
    assert row["preview_needs_clarification"] is True


def test_no_clarification_calls_existing_confirmation_and_records_contract(request_types):
    from rag_eval.system_runtime import submit_system_question

    seen = {}

    class Repo:
        def preview_reasoning_intent(self, notebook, question, history):
            return contract(ambiguities=({"id": "x", "question": "Choose", "required": True, "options": ["A", "B"]},))

        def ask(self, notebook, payload):
            seen["payload"] = payload
            return SimpleNamespace(answer="answer", mode="reasoning", answer_id="a",
                                   model_dump=lambda **_: {"answer": "answer"})

        def _connect(self):
            return SimpleNamespace(execute=lambda *args: SimpleNamespace(
                fetchone=lambda: ("Which?", '{"answer":"answer"}')))

    row = submit_system_question(Repo(), "n", "Which?", "reasoning",
                                 protocol="reasoning-no-clarification-v1")
    assert row["status"] == "success"
    assert row["preview_needs_clarification"] is True
    assert row["auto_confirm_policy"] == {"name": "cover-all-options", "version": "v1"}
    assert row["auto_confirmed_ambiguities"][0]["answer_source"] == "all_options"
    assert seen["payload"].intent.answers[0].answer == "同时考虑以下所有可能：A；B"


def test_protocol_variant_changes_run_identity_and_submission_name():
    from rag_eval.reasoning_protocol import (
        ensure_comparable_reasoning_protocols, protocol_identity,
    )

    standard = protocol_identity("reasoning-v1")
    variant = protocol_identity("reasoning-no-clarification-v1")
    assert standard != variant
    assert variant["name"] == "reasoning-no-clarification-v1"
    assert variant["comparison_category"] == "controlled-rerun"
    with pytest.raises(ValueError, match="cannot enter one comparison"):
        ensure_comparable_reasoning_protocols([
            {"kind": "sn", "configuration": {"mode": "reasoning"}},
            {"kind": "sn", "configuration": {"mode": "reasoning",
                                                  "reasoning_protocol": variant}},
        ])


def test_sn_validator_accepts_cover_all_options_free_text(sn_backend):
    from app.services.query_intent import validate_confirmed_intent
    from app.services.query_intent import finalize_query_intent

    seed = {
        "objective": "Which interpretation applies?",
        "resolved_question": "Which interpretation applies?",
        "ambiguities": [{"id": "scope", "question": "Which scope?", "required": True,
                         "options": ["A", "B"]}],
        "needs_clarification": True,
        "confirmed": False,
    }
    answer = "同时考虑以下所有可能：A；B"
    final = validate_confirmed_intent(
        seed["objective"], seed, resolved_question=seed["resolved_question"],
        answers=[{"id": "scope", "answer": answer}],
    )
    assert final["clarification_answers"][0]["answer"] == answer
    assert final["confirmed"] is True
    finalized = finalize_query_intent(
        seed, resolved_question=seed["resolved_question"],
        answers=[{"id": "scope", "answer": answer}],
    )
    assert finalized["clarification_answers"][0]["answer"] == answer
    assert finalized["confirmed"] is True


def test_protocol_is_recorded_in_feature_identity():
    from rag_eval.feature_identity import build_feature_identity, validate_feature_identity
    from rag_eval.reasoning_protocol import protocol_identity

    runtime = {"reasoning_protocol": protocol_identity("reasoning-no-clarification-v1")}
    identity = build_feature_identity(runtime)
    assert identity["reasoning_protocol"]["name"] == "reasoning-no-clarification-v1"
    assert validate_feature_identity(identity) == identity
