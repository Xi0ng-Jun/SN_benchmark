"""SN public-task execution. Product imports occur only during explicit runs."""
from __future__ import annotations

import json
import re
import time
from asyncio import CancelledError
from uuid import uuid4

from .trace_contract import TraceEnvelope
from .execution_diagnostics import exception_details, sensitive_strings


def is_cancellation(error):
    """Recognize optional SN cancellation, including streaming subclasses.

    Offline consumers do not import the SN package; the exception's base-class
    contract is enough to propagate it unchanged at this runtime boundary.
    """
    return isinstance(error, (KeyboardInterrupt, SystemExit, CancelledError)) or any(
        base.__name__ == 'AskCancelled' for base in type(error).__mro__)


def submit_system_question(repo, notebook, question, mode, *, diagnostic_directory=None,
                           diagnostic_sensitive_values=(), protocol="reasoning-v1"):
    """Follow the native clear-intent confirmation path with a fresh conversation."""
    from app.models.ask import AskRequest, AskIntentConfirmation

    from .reasoning_protocol import (
        AUTO_CONFIRM_POLICY, NO_CLARIFICATION_PROTOCOL, STANDARD_REASONING_PROTOCOL,
        auto_confirm_contract,
    )
    if protocol not in {STANDARD_REASONING_PROTOCOL, NO_CLARIFICATION_PROTOCOL}:
        raise ValueError("Unknown reasoning protocol: " + str(protocol))
    if mode != "reasoning" and protocol != STANDARD_REASONING_PROTOCOL:
        raise ValueError("Reasoning protocol variants require reasoning mode")
    record = {"status": "error", "answer": "", "response": {},
              "intent_preview": None, "request": {"question": question, "mode": mode,
                                                  "conversation_id": None},
              "reasoning_protocol": protocol,
              "preview_needs_clarification": None,
              "auto_confirmed_ambiguities": [],
              "auto_confirm_policy": (dict(AUTO_CONFIRM_POLICY)
                                      if protocol == NO_CLARIFICATION_PROTOCOL else None)}
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
            record["preview_needs_clarification"] = bool(contract.needs_clarification)
            if contract.needs_clarification:
                if protocol == STANDARD_REASONING_PROTOCOL:
                    record.update(status="clarification", reason="native intent requires clarification; no answers supplied")
                    return record
                auto = auto_confirm_contract(contract)
                record["auto_confirmed_ambiguities"] = auto.decisions
                record["auto_confirm_policy"] = auto.policy
                payload.intent = AskIntentConfirmation(contract=contract,
                    resolved_question=contract.resolved_question, answers=auto.answers)
            else:
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
        if is_cancellation(exc):
            raise
        # Preserve the observed answer separately from sanitized failure evidence.
        details = exception_details(exc, phase=phase, directory=diagnostic_directory,
            sensitive_values=(*diagnostic_sensitive_values, question,
                              *sensitive_strings(record['response']), record['answer']))
        record.update(status="error", reason="native request, intent, Ask or persistence error",
                      error_phase=phase, **details,
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


def complete_evidence_checks(repo, record, mapping, *, gold_document_ids=None):
    """Join scoring-only document labels after generation, without retaining them.

    Legacy requests already contain the labels and retain their old behavior.
    A public request defers this check until its caller supplies frozen labels.
    """
    from .benchmark_runtime import evidence_checks

    if gold_document_ids is None:
        if 'gold_document_ids' not in record:
            record['evidence_check_status'] = 'deferred'
            return
        gold_document_ids = record['gold_document_ids']
    checked = {**record, 'gold_document_ids': gold_document_ids}
    try:
        evidence_checks(repo, checked, mapping)
        record.update(retrieved_document_ids=checked['retrieved_document_ids'],
                      deterministic=checked['deterministic'], evidence_check_status='completed')
        record.pop('evidence_check_error', None)
    except Exception as exc:
        # A diagnostic failure cannot erase an answer or preserve stale success.
        record.pop('retrieved_document_ids', None)
        record.pop('deterministic', None)
        record.update(evidence_check_status='error', evidence_check_error=type(exc).__name__)


def run_system_question(repo, notebook, question, mode, mapping, *, diagnostic_directory=None,
                        diagnostic_sensitive_values=(), protocol="reasoning-v1"):
    """Capture the actual product response, final context and citation objects."""
    from app.core.llm_logging import LLMInteractionLogger
    from .system_capture import capture_synthesis, final_context
    from .usage_capture import capture_usage

    started = time.monotonic()
    with capture_usage(LLMInteractionLogger) as usage, capture_synthesis(repo._runtime.ask_component) as calls:
        observed = submit_system_question(repo, notebook, question["question"], mode,
            diagnostic_directory=diagnostic_directory, diagnostic_sensitive_values=diagnostic_sensitive_values,
            protocol=protocol)
    record = {**question, **observed, "mode": mode, "attempt_id": uuid4().hex,
              "captures": calls, "usage": usage, "latency_seconds": time.monotonic() - started}
    try:
        record.update(final_context(calls))
    except Exception as exc:
        record.update(context_supported=False, retrieval_context=[], source_ids=[],
                      retrieved_ids=[], context_block="", handles=[],
                      context_unavailable_reason="context_capture_error", context_capture_error=type(exc).__name__)
    complete_evidence_checks(repo, record, mapping)
    trace = record.get("response", {}).get("reasoning_trace") or []
    envelope = TraceEnvelope.missing()
    if isinstance(trace, list) and trace and all(isinstance(step, dict) for step in trace):
        envelope = TraceEnvelope(trace_id=record["response"].get("answer_id") or record["attempt_id"],
                                 completeness="partial", spans=trace)
    record["trace"] = envelope.to_dict()
    record["behavior"] = behavior_observation(record)
    return record
