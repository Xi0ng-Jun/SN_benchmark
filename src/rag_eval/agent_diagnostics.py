"""Deterministic diagnostics for normalized Silicon Notebook trajectories."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

from .agent_trace import AgentTraceEnvelope


NON_ACTION_TYPES = frozenset({
    "answer", "experience", "intent", "plan", "profile", "reflect",
    "rerank", "skip", "synthesis",
})
RETRIEVAL_TYPES = frozenset({
    "retrieve", "search_chunks", "ppr", "expand", "follow_chain", "expand_community",
})


def _count_anchors(detail: Mapping[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("anchors", "anchor_evidence_ids", "citation_keys", "citation_ids"):
        raw = detail.get(key)
        if isinstance(raw, (list, tuple, set)):
            values.update(str(item) for item in raw if isinstance(item, (str, int)) and str(item))
        elif isinstance(raw, Mapping):
            values.update(str(item) for item in raw if str(item))
    return values


def _termination_reason(steps: list[dict[str, Any]]) -> str | None:
    for step in reversed(steps):
        detail = step.get("detail")
        if not isinstance(detail, Mapping):
            continue
        for key in ("termination_reason", "termination", "stop_reason"):
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if step.get("type") in {"reflect", "fallback", "answer"}:
            value = detail.get("reason")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def diagnose_trace(envelope: AgentTraceEnvelope) -> dict[str, Any]:
    """Project a trace into stable, non-LLM diagnostic fields."""
    steps = envelope.steps
    action_seq = [step["type"] for step in steps if step["type"] not in NON_ACTION_TYPES]
    action_counts = Counter(action_seq)
    repeated_types = sorted(name for name, count in action_counts.items() if count > 1)
    durations = [step.get("duration_ms") for step in steps if isinstance(step.get("duration_ms"), (int, float))]
    durations_by_type: Counter[str] = Counter()
    anchors: set[str] = set()
    for step in steps:
        value = step.get("duration_ms")
        if isinstance(value, (int, float)):
            durations_by_type[step["type"]] += value
        detail = step.get("detail")
        if isinstance(detail, Mapping):
            anchors.update(_count_anchors(detail))
    return {
        "trace_id": envelope.trace_id,
        "case_id": envelope.case_id,
        "mode": envelope.mode,
        "status": envelope.status,
        "completeness": envelope.completeness,
        "completeness_reason": envelope.completeness_reason,
        "step_count": len(steps),
        "action_seq": action_seq,
        "action_counts": dict(sorted(action_counts.items())),
        "retrieval_count": sum(action_counts.get(name, 0) for name in RETRIEVAL_TYPES),
        "repeated_action_count": sum(max(0, count - 1) for count in action_counts.values()),
        "repeated_action_types": repeated_types,
        "reflect_turns": sum(step["type"] == "reflect" for step in steps),
        "fallback_count": sum(step["type"] == "fallback" for step in steps),
        "plan_present": any(step["type"] == "plan" for step in steps),
        "synthesis_present": any(step["type"] == "synthesis" for step in steps),
        "answer_present": any(step["type"] == "answer" for step in steps),
        "termination_reason": _termination_reason(steps),
        "anchor_count": len(anchors),
        "total_duration_ms": sum(durations) if steps and len(durations) == len(steps) else None,
        "duration_by_type_ms": dict(sorted(durations_by_type.items())),
        "final_output_available": envelope.final_output_available,
        "context_available": envelope.context_available,
        "citations_available": envelope.citations_available,
    }


def diagnose_execution(record: Mapping[str, Any], envelope: AgentTraceEnvelope) -> dict[str, Any]:
    """Describe observed request stages without manufacturing trajectory spans.

    Intent preview lives outside Ask.  A missing trace therefore cannot erase
    an explicitly saved clarification, nor prove that an unknown error was
    raised before Ask.  ``ask_entered=None`` preserves that distinction.
    """
    preview = record.get("intent_preview")
    preview = preview if isinstance(preview, Mapping) else {}
    response = record.get("response")
    response = response if isinstance(response, Mapping) else {}
    request = record.get("request")
    request = request if isinstance(request, Mapping) else {}
    ambiguities = preview.get("ambiguities")
    ambiguities = [dict(item) for item in ambiguities if isinstance(item, Mapping)] if isinstance(ambiguities, list) else []
    needs = preview.get("needs_clarification")
    needs = needs if isinstance(needs, bool) else None
    error_phase = record.get("error_phase")
    phase, entered, evidence = "unknown", None, []
    # Positive Ask observations take precedence over a saved earlier preview.
    if response.get("llm_mode") == "synthesis_failed":
        phase, entered, evidence = "answer_synthesis", True, ["response.llm_mode"]
    elif error_phase in {"ask", "persistence"}:
        phase, entered, evidence = error_phase, True, ["error_phase"]
    elif envelope.status == "success" and envelope.final_output_available:
        phase, entered, evidence = "answer_returned", True, ["status", "answer"]
    elif response:
        phase, entered, evidence = "ask_response", True, ["response"]
    elif error_phase in {"request_validation", "intent_preview"}:
        phase, entered, evidence = error_phase, False, ["error_phase"]
    elif (envelope.status == "clarification" and needs is True
          and record.get("reason") == "native intent requires clarification; no answers supplied"):
        phase, entered, evidence = "intent_preview", False, ["status", "intent_preview.needs_clarification", "reason"]
    elif envelope.steps:
        entered, evidence = True, ["trace.steps"]
    return {
        "termination_phase": phase,
        "ask_entered": entered,
        "phase_evidence": evidence,
        "intent_preview_available": bool(preview),
        "intent_needs_clarification": needs,
        "intent_type": preview.get("intent_type"),
        "clarification_reasons": list(dict.fromkeys(
            item["reason"] for item in ambiguities if isinstance(item.get("reason"), str) and item["reason"].strip()
        )),
        "ambiguities": ambiguities,
        "original_question": record.get("original_question"),
        "submitted_question": request.get("question") or record.get("question"),
        "request_revision": record.get("request_revision"),
    }


def _delta(left: object, right: object) -> object:
    if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
        return right - left
    return None


def compare_modes(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Pair chunk/reasoning diagnostics without treating missing values as zero."""
    grouped: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        case_id = row.get("case_id")
        mode = row.get("mode")
        if not isinstance(case_id, str) or not case_id or mode not in {"chunk", "reasoning"}:
            continue
        grouped[case_id][mode] = row
    result = []
    for case_id in sorted(grouped):
        pair = grouped[case_id]
        if "chunk" not in pair or "reasoning" not in pair:
            result.append({"case_id": case_id, "status": "unpaired", "modes": sorted(pair), "delta": None})
            continue
        chunk = pair["chunk"].get("diagnostics") or {}
        reasoning = pair["reasoning"].get("diagnostics") or {}
        result.append({
            "case_id": case_id,
            "status": "paired",
            "modes": ["chunk", "reasoning"],
            "delta": {
                "step_count": _delta(chunk.get("step_count"), reasoning.get("step_count")),
                "retrieval_count": _delta(chunk.get("retrieval_count"), reasoning.get("retrieval_count")),
                "reflect_turns": _delta(chunk.get("reflect_turns"), reasoning.get("reflect_turns")),
                "fallback_count": _delta(chunk.get("fallback_count"), reasoning.get("fallback_count")),
                "total_duration_ms": _delta(chunk.get("total_duration_ms"), reasoning.get("total_duration_ms")),
            },
        })
    return result
