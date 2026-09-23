"""Protocol tests use observed-style trees, including overlapping children."""

import pytest

from rag_eval.agent_trace import AgentTraceEnvelope
from rag_eval.sn_trace import audit_execution_trace


def execution_trace(*, clarification=False):
    def span(identity, parent, stage, start, end, *, output=None, kind='agent'):
        return dict(span_id=identity, parent_id=parent, name='sn.' + stage, kind=kind,
                    metadata={'stage': stage}, status='completed', input={'question': 'Summarize decisions'},
                    output=output, start_time=start, end_time=end, duration_ms=(end-start)*1000)
    root = span('root', None, 'request', 1, 6, output={'status': 'clarification' if clarification else 'success'})
    intent = span('intent', 'root', 'intent', 1.1, 1.4, output={'needs_clarification': clarification})
    spans = [root, intent]
    if not clarification:
        spans.extend([
            span('ask', 'root', 'ask', 1.5, 5.9, output={'answer': 'A decision'}),
            span('retrieve', 'ask', 'retrieve', 1.6, 3, kind='retriever', output=['Evidence']),
            span('synthesis', 'ask', 'synthesis', 3.1, 5.8, output='A decision'),
            span('llm', 'synthesis', 'llm', 3.2, 5.7, kind='llm', output='A decision'),
        ])
    return dict(schema_version='sn-execution-trace-v1', trace_id='t1', closed=True,
                spans=spans, capture_errors=[], duration_ms=5000)


def test_closed_clarification_is_a_complete_observed_path_without_an_answer():
    raw = execution_trace(clarification=True)
    envelope = AgentTraceEnvelope.from_record(
        {'execution_trace': raw, 'status': 'clarification'}, case_id='c1', mode='reasoning')
    assert envelope.completeness == 'complete'
    assert envelope.final_output_available is False
    assert envelope.execution_trace == raw


@pytest.mark.parametrize('damage, reason', [
    (lambda t: t.update(closed=False), 'capture_not_closed'),
    (lambda t: t['capture_errors'].append('projection_error'), 'capture_errors'),
    (lambda t: t['spans'][2].update(parent_id='missing'), 'invalid_parent'),
    (lambda t: t['spans'][2].update(parent_id='llm'), 'invalid_parent'),
    (lambda t: t['spans'][2].update(end_time=7), 'child_outside_parent'),
    (lambda t: t['spans'][2].update(start_time=float('nan')), 'invalid_timing'),
    (lambda t: t['spans'][0].pop('name'), 'invalid_span_name'),
    (lambda t: t['spans'][0].pop('parent_id'), 'invalid_parent'),
    (lambda t: t['spans'][0].update(kind=[]), 'invalid_span_kind'),
    (lambda t: t['spans'][0].update(status={}), 'unfinished_span'),
    (lambda t: t['spans'][3].update(metadata={'stage': 'other'}), 'missing_stage:retrieve'),
])
def test_broken_or_incomplete_execution_is_downgraded(damage, reason):
    raw = execution_trace()
    damage(raw)
    assert audit_execution_trace(raw, mode='reasoning', status='success') == ('partial', reason)


def test_nested_time_is_wall_clock_not_sum_of_nested_spans():
    from rag_eval.agent_diagnostics import diagnose_trace
    raw = execution_trace()
    envelope = AgentTraceEnvelope.from_record(
        {'execution_trace': raw, 'status': 'success', 'answer': 'A decision'}, case_id='c1', mode='reasoning')
    diagnostics = diagnose_trace(envelope)
    assert diagnostics['total_duration_ms'] == 5000
    assert diagnostics['retrieval_count'] == 1
    assert diagnostics['action_seq'] == []
    assert diagnostics['answer_present'] is True


def test_offline_evaluator_prefers_new_tree_over_legacy_projection(tmp_path):
    import json
    from rag_eval.agent_evaluator import evaluate_run
    run = tmp_path / 'run'
    run.mkdir()
    row = dict(case_id='c1', mode='reasoning', status='clarification', output_available=False,
               trace={'trace_id': None, 'completeness': 'none', 'spans': []},
               product_record={'execution_trace': execution_trace(clarification=True),
                               'intent_preview': {'needs_clarification': True}})
    (run / 'outputs.jsonl').write_text(json.dumps(row) + '\n')
    summary = evaluate_run(run, tmp_path / 'report')
    assert summary['completeness_counts']['complete'] == 1
    assert summary['format'] == 'sn-agent-diagnostics-v1'
    saved = json.loads((tmp_path / 'report/agent-traces.jsonl').read_text())
    assert saved['trace']['execution_trace']['spans'][1]['name'] == 'sn.intent'
