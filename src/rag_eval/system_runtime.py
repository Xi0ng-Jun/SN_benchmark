"""SN public-task execution. Product imports occur only during explicit runs."""
from __future__ import annotations

import json
import re
import time
from uuid import uuid4

from .public_expansion_protocol import TraceEnvelope


def submit_system_question(repo, notebook, question, mode):
    """Follow the native clear-intent confirmation path with a fresh conversation."""
    from app.models.ask import AskRequest, AskIntentConfirmation

    record = {"status": "error", "answer": "", "response": {},
              "intent_preview": None, "request": {"question": question, "mode": mode,
                                                  "conversation_id": None}}
    phase = "request_validation"
    try:
        if mode not in {"chunk", "reasoning"}:
            raise ValueError("System run requires a fixed chunk/reasoning mode")
        # Validate the full question before preview. Never truncate to fit SN.
        payload = AskRequest(question=question, mode=mode, conversation_id=None)
        if mode == "reasoning":
            phase = "intent_preview"
            contract = repo.preview_reasoning_intent(notebook, question.strip(), "")
            record["intent_preview"] = contract.model_dump(mode="json")
            if contract.needs_clarification:
                record.update(status="clarification", reason="native intent requires clarification; no answers supplied")
                return record
            payload.intent = AskIntentConfirmation(contract=contract,
                resolved_question=contract.resolved_question, answers=[])
        phase = "ask"
        response = repo.ask(notebook, payload)
        record.update(answer=response.answer, response=response.model_dump(mode="json"))
        phase = "persistence"
        if response.mode != mode:
            raise ValueError("Native mode differs from planned mode")
        stored = repo._connect().execute(
            "SELECT question, payload FROM answers WHERE id=? AND notebook_id=?",
            (response.answer_id, notebook)).fetchone()
        record["persistence_verified"] = bool(stored and stored[0] == question.strip()
            and json.loads(stored[1]).get("answer") == response.answer)
        if not record["persistence_verified"]:
            raise ValueError("Native saved answer differs from the observed response")
        if record["response"].get("llm_mode") == "synthesis_failed":
            record.update(status="error", reason="native answer synthesis failed")
        elif not response.answer.strip():
            record.update(status="no_answer", reason="native response has no answer body")
        else:
            record.update(status="success", reason=None)
    except Exception as exc:
        # Preserve already observed response, never print provider error bodies.
        record.update(status="error", reason="native request, intent, Ask or persistence error",
                      error_phase=phase, error_type=type(exc).__name__,
                      error_support_id=str(getattr(exc, "support_id", "")))
    return record


def behavior_observation(record):
    """Only explicit protocol states are authoritative; textual refusal is a hint."""
    if record["status"] != "success":
        return {"kind": record["status"], "method": "runtime_state", "human_label": None}
    text = record.get("answer", "")
    candidate = bool(re.search(
        r"(?i)(?:cannot answer|unable to answer|insufficient (?:information|evidence)|"
        r"无法回答|不能回答|资料不足|证据不足|无法确定)", text))
    return {"kind": "refusal_candidate" if candidate else "answer_returned",
            "method": "text_heuristic" if candidate else "runtime_state", "human_label": None}


def run_system_question(repo, notebook, question, mode, mapping):
    """Capture the actual product response, final context and citation objects."""
    from app.core.llm_logging import LLMInteractionLogger
    from .benchmark_runtime import evidence_checks
    from .system_capture import capture_synthesis, final_context
    from .usage_capture import capture_usage

    started = time.monotonic()
    with capture_usage(LLMInteractionLogger) as usage, capture_synthesis(repo._runtime.ask_component) as calls:
        observed = submit_system_question(repo, notebook, question["question"], mode)
    record = {**question, **observed, "mode": mode, "attempt_id": uuid4().hex,
              "captures": calls, "usage": usage, "latency_seconds": time.monotonic() - started}
    try:
        record.update(final_context(calls))
    except Exception as exc:
        record.update(context_supported=False, retrieval_context=[], source_ids=[],
                      retrieved_ids=[], context_block="", handles=[],
                      context_unavailable_reason="context_capture_error", context_capture_error=type(exc).__name__)
    try:
        evidence_checks(repo, record, mapping)
    except Exception as exc:
        # Capture failure cannot erase a saved answer or imply a passing citation check.
        record["evidence_check_error"] = type(exc).__name__
    trace = record.get("response", {}).get("reasoning_trace") or []
    envelope = TraceEnvelope.missing()
    if isinstance(trace, list) and trace and all(isinstance(step, dict) for step in trace):
        envelope = TraceEnvelope(trace_id=record["response"].get("answer_id") or record["attempt_id"],
                                 completeness="partial", spans=trace)
    record["trace"] = envelope.to_dict()
    record["behavior"] = behavior_observation(record)
    return record
