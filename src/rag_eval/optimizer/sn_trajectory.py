"""SN R-track diagnostic trajectory views and predicate features (method-v3 §1, §2.3).

Reads one run's ``outputs.jsonl`` in a single streaming pass and projects each
selected row through a fixed whitelist into two separate objects:

- ``diagnostic_view``: what the analyst Agent reads (step chain, intent stage,
  final behaviour). Display-only; may be truncated by ``render_views``.
- ``observation``: what predicates are evaluated on (v2 behaviour fields plus
  ``trace.*`` scalar features). Computed from the complete row, never from the
  truncated display, so truncation cannot change a predicate's value.

Gold-derived and scorer-derived fields (``qasper_evidence``, ``predicted_evidence``,
``deterministic.evidence_hit``/``evidence_coverage``, ``anchor_documents``,
``material_document_ids``, scores) are never copied. Missing observations are
``None`` ("not computable"), never zero or False.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional

VIEW_SCHEMA_VERSION = "sn-r-diagnostic-v1"

# Trace detail keys whose list values are system-produced text worth showing.
_TEXT_LIST_KEYS = {"sub_queries", "entities", "constraints", "excluded_topics",
                   "assumptions", "mandatory_topics", "quota"}
_TEXT_KEYS = {"query", "keywords", "resolved_question", "next_action", "phase",
              "reason", "source", "retrieval_effort", "result_scope", "evidence_level"}
_CASE_ID = re.compile(r'"case_id":\s*"([^"]+)"')

# Scalar trace features a predicate may reference as ``trace.<name>``.
TRACE_FEATURES = ("n_steps", "n_retrieval_calls", "n_empty_retrievals", "n_reflect",
                  "n_reflect_no_progress", "final_sufficient", "n_sub_queries",
                  "synthesis_citations", "synthesis_anchors", "kg_unavailable",
                  "clarification_requested", "intent_confidence", "n_ambiguities")
_RETRIEVAL_STEPS = {"retrieve", "ppr", "search_chunks"}


def read_rows(run_dir: str | Path, case_ids: Iterable[str]) -> dict[str, dict]:
    """One streaming pass over ``run_dir/outputs.jsonl``; parse only wanted rows.

    The case_id is found with a regex before ``json.loads`` so unrelated rows
    (~80 KB each, mostly product bulk) are skipped without parsing. If a case
    appears more than once the last row wins (latest attempt).
    """
    wanted = set(case_ids)
    rows: dict[str, dict] = {}
    path = Path(run_dir) / "outputs.jsonl"
    if not wanted or not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            match = _CASE_ID.search(line)
            if match is None or match.group(1) not in wanted:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("case_id") in wanted:
                rows[row["case_id"]] = row
    return rows


def run_is_final(run_dir: str | Path) -> bool:
    """True when the run finished; only final runs are safe to cache."""
    try:
        state = json.loads((Path(run_dir) / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return state.get("phase") in ("finished", "finished_with_errors")


def _clip(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


def _project_detail(detail: dict) -> dict:
    out = {}
    for key, value in (detail or {}).items():
        if isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
            out[key] = value
        elif isinstance(value, str):
            if key in _TEXT_KEYS:
                out[key] = _clip(value, 160)
        elif isinstance(value, list):
            if key.endswith("ids"):
                out["n_" + key] = len(value)
            elif key in _TEXT_LIST_KEYS:
                items = [v if isinstance(v, str) else (v.get("title") or v.get("question"))
                         for v in value[:6] if isinstance(v, (str, dict))]
                out[key] = [_clip(v, 120) for v in items if isinstance(v, str)]
            else:
                out["n_" + key] = len(value)
    return out


def _intent(record: dict) -> dict:
    preview = record.get("intent_preview") or {}
    return {
        "needs_clarification": preview.get("needs_clarification"),
        "confidence": preview.get("confidence"),
        "intent_type": preview.get("intent_type"),
        "ambiguities": [_clip(a.get("question"), 160) for a in preview.get("ambiguities") or []
                        if isinstance(a, dict)][:4],
        "planned_queries": [_clip(q, 120) for t in preview.get("mandatory_topics") or []
                            if isinstance(t, dict) for q in (t.get("retrieval_queries") or [])][:6],
    }


def diagnostic_view(row: dict) -> dict:
    """Whitelist projection shown to the analyst Agent."""
    record = row.get("product_record") or {}
    response = record.get("response") or {}
    deterministic = record.get("deterministic") or {}
    trace = row.get("trace") or record.get("trace") or {}
    spans = trace.get("spans") or []
    return {
        "schema_version": VIEW_SCHEMA_VERSION,
        "track": "R",
        "observation_boundary": "SN step trace; no raw LLM messages are recorded",
        "completeness": trace.get("completeness", "none"),
        "question": _clip(record.get("question") or "", 300),
        "status": row.get("status"),
        "behavior": (row.get("behavior") or record.get("behavior") or {}).get("kind"),
        "intent": _intent(record),
        "steps": [{"step": s.get("step_type"), "summary": _clip(s.get("summary") or "", 80),
                   **_project_detail(s.get("detail") or {})} for s in spans],
        "answer": _clip(row.get("prediction") or "", 400),
        "grounded": response.get("grounded"),
        "evidence_level": response.get("evidence_level"),
        "citations": {k: deterministic.get(k) for k in
                      ("citation_count", "citation_valid_count", "citation_invalid_count")},
    }


def trace_features(row: dict) -> dict[str, Optional[float]]:
    """Scalar features from the complete row; None when not observable."""
    record = row.get("product_record") or {}
    trace = row.get("trace") or record.get("trace") or {}
    spans = trace.get("spans") or []
    preview = record.get("intent_preview") or {}
    features: dict[str, Optional[float]] = {name: None for name in TRACE_FEATURES}
    if preview:
        features["clarification_requested"] = bool(preview.get("needs_clarification"))
        conf = preview.get("confidence")
        features["intent_confidence"] = float(conf) if isinstance(conf, (int, float)) else None
        features["n_ambiguities"] = len(preview.get("ambiguities") or [])
    if trace.get("completeness", "none") == "none" or not spans:
        return features
    details = [(s.get("step_type"), s.get("detail") or {}) for s in spans]
    retrieval = [d for t, d in details if t in _RETRIEVAL_STEPS]
    reflects = [d for t, d in details if t == "reflect"]
    plans = [d for t, d in details if t == "plan"]
    synth = [d for t, d in details if t == "synthesis"]
    found = lambda d: d.get("found", d.get("count"))  # noqa: E731
    features.update(
        n_steps=len(spans),
        n_retrieval_calls=len(retrieval),
        n_empty_retrievals=sum(1 for d in retrieval if found(d) == 0),
        n_reflect=len(reflects),
        n_reflect_no_progress=sum(1 for d in reflects if d.get("no_progress") is True),
        final_sufficient=(bool(reflects[-1].get("sufficient")) if reflects
                          and "sufficient" in reflects[-1] else None),
        n_sub_queries=(len(plans[-1].get("sub_queries") or []) if plans else None),
        synthesis_citations=(synth[-1].get("citations") if synth else None),
        synthesis_anchors=(synth[-1].get("anchors") if synth else None),
        kg_unavailable=any(t == "skip" and d.get("reason") == "kg_unavailable" for t, d in details),
    )
    return features


def observation(row: dict) -> dict:
    """v2-shaped behaviour observation plus ``trace`` features, for predicates.

    Fields mirror ``rag_eval.workbench.clusters.FIELDS`` minus gold/scorer-derived
    ones, so v2 signatures can be matched with ``clusters._matches``.
    """
    record = row.get("product_record") or {}
    response = record.get("response") or {}
    deterministic = record.get("deterministic") or {}
    usage = record.get("usage") or {}
    contexts = record.get("retrieval_context") or []
    return {
        "status": row.get("status"),
        "prediction": row.get("prediction"),
        "response": {k: response.get(k) for k in ("answer", "grounded", "evidence_level")},
        "intent_preview": {"needs_clarification": (record.get("intent_preview") or {}).get("needs_clarification")},
        "deterministic": {k: deterministic.get(k) for k in ("citation_valid_count", "citation_invalid_count")},
        "usage": {"total_tokens": usage.get("total_tokens") if isinstance(usage, dict) else None},
        "retrieval_context": [{"text": c if isinstance(c, str) else (c or {}).get("text", "")} for c in contexts],
        "trace": trace_features(row),
    }


def _render_one(ref: str, role: str, view: dict) -> str:
    intent = view["intent"]
    steps = " > ".join(
        s["step"] + "(" + ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in s.items()
                                    if k not in ("step", "summary")) + ")"
        for s in view["steps"]) or "(no step trace recorded)"
    return (f"### {ref} [{role}] status={view['status']} behavior={view['behavior']} "
            f"trace={view['completeness']} grounded={view['grounded']} "
            f"evidence={view['evidence_level']} citations={json.dumps(view['citations'])}\n"
            f"Q: {view['question']}\n"
            f"intent: clarify={intent['needs_clarification']} conf={intent['confidence']} "
            f"ambiguities={json.dumps(intent['ambiguities'], ensure_ascii=False)} "
            f"planned_queries={json.dumps(intent['planned_queries'], ensure_ascii=False)}\n"
            f"steps: {steps}\n"
            f"A: {view['answer']}\n")


def render_views(items: list[tuple[str, str, dict]], budget_chars: int) -> tuple[str, int, int]:
    """Render (ref, role, view) items in order until the character budget is hit.

    Returns (text, n_included, n_omitted). Whole cases are kept or dropped; a
    case is never cut mid-way, and the omission count is reported to the Agent.
    """
    parts, used = [], 0
    for index, (ref, role, view) in enumerate(items):
        block = _render_one(ref, role, view)
        if used + len(block) > budget_chars:
            omitted = len(items) - index
            parts.append(f"[{omitted} selected case(s) omitted: diagnostic budget reached]\n")
            return "".join(parts), index, omitted
        parts.append(block)
        used += len(block)
    return "".join(parts), len(items), 0
