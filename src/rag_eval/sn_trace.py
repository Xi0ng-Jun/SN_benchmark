"""SN execution protocol helpers retained for historical trace diagnostics."""
from __future__ import annotations

import math
from typing import Any, Mapping

SCHEMA_VERSION = 'sn-execution-trace-v1'
CAPTURE_IDENTITY = {'schema_version': SCHEMA_VERSION, 'scope': 'native_sync_ask',
                    'capture': 'explicit_local', 'judge_during_capture': False}


def require_capture_api():
    """Fail before importing documents if the selected SN has no matching patch."""
    try:
        from app.core import evaluation_tracing
    except ImportError as exc:
        raise RuntimeError('SN execution tracing patch is required for --capture-agent-trace') from exc
    if evaluation_tracing.CAPTURE_SCHEMA_VERSION != SCHEMA_VERSION:
        raise RuntimeError('Unsupported SN execution trace schema')
    return evaluation_tracing


def audit_execution_trace(raw: Mapping[str, Any], *, mode: str, status: str) -> tuple[str, str]:
    """Check the declared instrumentation scope, independently of answer quality."""
    if raw.get('schema_version') != SCHEMA_VERSION:
        return 'partial', 'unsupported_execution_schema'
    if raw.get('closed') is not True:
        return 'partial', 'capture_not_closed'
    if raw.get('capture_errors') != []:
        return 'partial', 'capture_errors'
    if not isinstance(raw.get('trace_id'), str) or not raw['trace_id'].strip():
        return 'partial', 'trace_id_missing'
    spans = raw.get('spans')
    if not isinstance(spans, list) or not spans:
        return 'partial', 'execution_spans_missing'
    seen, roots, stages = {}, [], set()
    for span in spans:
        if not isinstance(span, Mapping):
            return 'partial', 'invalid_span'
        identity = span.get('span_id')
        if not isinstance(identity, str) or not identity or identity in seen:
            return 'partial', 'invalid_span_id'
        if not isinstance(span.get('name'), str) or not span['name'].strip():
            return 'partial', 'invalid_span_name'
        parent_id = span.get('parent_id')
        if 'parent_id' not in span or (parent_id is not None and (not isinstance(parent_id, str) or parent_id not in seen)):
            return 'partial', 'invalid_parent'
        if not isinstance(span.get('kind'), str) or span['kind'] not in {'agent', 'retriever', 'tool', 'llm'}:
            return 'partial', 'invalid_span_kind'
        if not isinstance(span.get('status'), str) or span['status'] not in {'completed', 'error', 'cancelled'}:
            return 'partial', 'unfinished_span'
        start, end = span.get('start_time'), span.get('end_time')
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end)) or end < start:
            return 'partial', 'invalid_timing'
        if parent_id is not None:
            parent = seen[parent_id]
            if start < parent['start_time'] or end > parent['end_time']:
                return 'partial', 'child_outside_parent'
        else:
            roots.append(span)
        metadata = span.get('metadata')
        if not isinstance(metadata, Mapping):
            return 'partial', 'span_metadata_missing'
        if span.get('error_type') is not None and not isinstance(span['error_type'], str):
            return 'partial', 'invalid_error_type'
        model_input = span.get('input')
        model = metadata.get('model') or (model_input.get('model') if isinstance(model_input, Mapping) else None)
        if span['kind'] == 'llm' and model is not None and not isinstance(model, str):
            return 'partial', 'invalid_model'
        stage = metadata.get('stage')
        if isinstance(stage, str):
            stages.add(stage)
        seen[identity] = span
    if len(roots) != 1 or roots[0].get('metadata', {}).get('stage') != 'request':
        return 'partial', 'request_root_missing'
    outcome = roots[0].get('output')
    if not isinstance(outcome, Mapping) or outcome.get('status') != status:
        return 'partial', 'request_outcome_missing'
    required = {'request'}
    if status == 'success':
        required |= {'ask', 'retrieve', 'synthesis', 'llm'}
    if mode == 'reasoning' and status in {'success', 'clarification', 'no_answer'}:
        required.add('intent')
    missing = required - stages
    if missing:
        return 'partial', 'missing_stage:' + sorted(missing)[0]
    return 'complete', 'complete_native_sync_ask'


def execution_steps(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Small diagnostic projection; full payloads stay in execution_trace once."""
    steps = []
    spans = raw.get('spans')
    if not isinstance(spans, list):
        return steps
    for index, span in enumerate(spans):
        if not isinstance(span, Mapping):
            continue
        metadata = span.get('metadata')
        metadata = metadata if isinstance(metadata, Mapping) else {}
        stage = metadata.get('stage', span.get('kind', 'unknown'))
        step_type = metadata.get('action', span.get('name', 'action')) if stage == 'action' else stage
        if not isinstance(step_type, str) or not step_type:
            step_type = 'unknown'
        start, end = span.get('start_time'), span.get('end_time')
        duration = (end - start) * 1000 if all(type(v) in (int, float) and math.isfinite(v)
                                             for v in (start, end)) and end >= start else None
        outcome = span.get('output')
        citation_keys = outcome.get('citation_keys', []) if isinstance(outcome, Mapping) else []
        steps.append(dict(index=index, type=step_type, status=span.get('status', 'unknown'),
                          summary=span.get('name', ''), duration_ms=duration,
                          detail={**{key: metadata[key] for key in ('stage', 'action', 'coverage',
                                   'downstream_internals_traced', 'origin', 'call_boundary') if key in metadata},
                                  'span_id': span.get('span_id'), 'parent_id': span.get('parent_id'),
                                  'output_present': span.get('output') is not None, 'citation_keys': citation_keys}))
    return steps
