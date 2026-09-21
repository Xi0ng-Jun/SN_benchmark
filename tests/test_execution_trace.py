"""Protocol tests use observed-style trees, including overlapping children."""
from copy import deepcopy

import pytest

from rag_eval.agent_trace import AgentTraceEnvelope
from rag_eval.sn_trace import audit_execution_trace, deepeval_tree


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
def test_broken_or_incomplete_execution_cannot_enable_trajectory_judges(damage, reason):
    raw = execution_trace()
    damage(raw)
    assert audit_execution_trace(raw, mode='reasoning', status='success') == ('partial', reason)


def test_native_tree_uses_sdk_types_and_official_projection_without_upload(monkeypatch):
    from deepeval.tracing import trace_manager
    monkeypatch.setattr(trace_manager, 'post_trace', lambda *a, **k: pytest.fail('trace upload'))
    monkeypatch.setattr(trace_manager, 'start_new_trace', lambda *a, **k: pytest.fail('live trace registration'))
    raw = execution_trace()
    before = deepcopy(raw)
    tree = deepeval_tree(raw)
    assert tree['name'] == 'sn.request'
    assert tree['children'][1]['children'][0]['type'] == 'retriever'
    assert tree['children'][1]['children'][1]['children'][0]['type'] == 'llm'
    assert tree['output']['status'] == 'success'
    assert raw == before


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


def test_complete_clarification_is_not_a_judge_transport_error():
    from rag_eval.agent_deepeval import build_test_case, evaluate_trajectory
    envelope = AgentTraceEnvelope.from_record(
        {'execution_trace': execution_trace(clarification=True), 'status': 'clarification'},
        case_id='c1', mode='reasoning')
    test_case = build_test_case({'question': 'Summarize decisions'}, {}, envelope, {})
    rows = evaluate_trajectory(test_case, envelope, model=object())
    assert all(row['status'] == 'not_applicable' and row['reason'] == 'final_output_missing' for row in rows)


def test_actual_sdk_metrics_consume_tree_with_fake_judge_and_no_product_call(monkeypatch):
    from deepeval.models import DeepEvalBaseLLM
    from rag_eval.agent_deepeval import build_test_case, evaluate_trajectory
    from rag_eval.agent_diagnostics import diagnose_trace

    class FakeJudge(DeepEvalBaseLLM):
        def load_model(self):
            return self

        def get_model_name(self):
            return 'offline-test-judge'

        def generate(self, prompt, schema=None, **kwargs):
            prompts.append(prompt)
            values = {
                'TaskAndOutcome': {'task': 'Summarize decisions', 'outcome': 'A decision'},
                'TaskCompletionVerdict': {'verdict': 1.0, 'reason': 'Synthetic response'},
                'Task': {'task': 'Summarize decisions'},
                'EfficiencyVerdict': {'score': 0.75, 'reason': 'Synthetic response'},
            }
            return schema(**values[schema.__name__])

        async def a_generate(self, prompt, schema=None, **kwargs):
            return self.generate(prompt, schema=schema, **kwargs)

    prompts = []
    envelope = AgentTraceEnvelope.from_record(
        {'execution_trace': execution_trace(), 'status': 'success', 'answer': 'A decision'},
        case_id='c1', mode='reasoning')
    case = build_test_case({'question': 'Summarize decisions'}, {'answer': 'A decision'}, envelope,
                           diagnose_trace(envelope))
    scores = evaluate_trajectory(case, envelope, model=FakeJudge())
    assert [(s['metric'], s['status']) for s in scores] == [
        ('task_completion', 'scored'), ('step_efficiency', 'scored'),
        ('plan_quality', 'not_applicable'), ('plan_adherence', 'not_applicable')]
    assert any('sn.retrieve' in p and 'Evidence' in p and 'children' in p for p in prompts)
    assert scores[0]['score'] == 1.0
    assert scores[1]['score'] == 0.75
    assert scores[-1]['reason'] == 'explicit_plan_missing'


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
    assert summary['judge_enabled'] is False
    saved = json.loads((tmp_path / 'report/agent-traces.jsonl').read_text())
    assert saved['trace']['execution_trace']['spans'][1]['name'] == 'sn.intent'
