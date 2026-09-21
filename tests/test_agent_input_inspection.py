"""Judge-input diagnostics must match SDK requests without modifying evidence."""
from copy import deepcopy
import hashlib
import json
import socket

import pytest

pytest.importorskip('deepeval')

from rag_eval.agent_trace import AgentTraceEnvelope
from rag_eval.artifacts import save_jsonl
from test_execution_trace import execution_trace


def test_payload_accounting_does_not_count_child_content_twice():
    from rag_eval.agent_input_inspection import inspect_tree

    # One 300-character value at each level. JSON escapes each 中 as six bytes.
    evidence = '中' * 300
    tree = {'input': evidence, 'children': [{'output': evidence, 'children': []}]}
    before = deepcopy(tree)
    report = inspect_tree(tree)
    assert report['string_json_bytes'] == 3604
    assert report['spans'][0]['own_string_json_bytes'] == 1802
    assert report['spans'][0]['subtree_string_json_bytes'] == 3604
    assert report['spans'][1]['own_string_json_bytes'] == 1802
    assert report['trace']['bytes'] == report['string_json_bytes'] + report['other_json_bytes']
    assert report['repeated_strings'][0]['occurrences'] == 2
    assert report['repeated_strings'][0]['repeated_json_bytes'] == 1802
    assert report['repeated_strings'][0]['locations'] == ['$.input', '$.children[0].output']
    assert evidence not in json.dumps(report, ensure_ascii=False)
    assert tree == before


def test_inspection_matches_actual_sdk_static_prompts_without_a_provider(monkeypatch):
    from deepeval.models import DeepEvalBaseLLM
    from rag_eval.agent_deepeval import build_test_case, evaluate_trajectory
    from rag_eval.agent_input_inspection import inspect_envelope

    def reject_network(*args, **kwargs):
        pytest.fail('offline inspection attempted network access')

    monkeypatch.setattr(socket.socket, 'connect', reject_network)
    raw = execution_trace()
    # Give the real evaluator an explicit observed plan so all four metrics apply.
    raw['spans'].append(dict(span_id='plan', parent_id='ask', name='sn.plan', kind='agent',
        metadata={'stage': 'plan'}, status='completed', input='Summarize decisions',
        output={'plan': ['Retrieve evidence', 'Summarize decisions']},
        start_time=1.5, end_time=1.6, duration_ms=100))
    raw['spans'][3]['metadata']['context_block'] = 'NOT_TRANSMITTED' * 1000
    envelope = AgentTraceEnvelope.from_record(
        {'execution_trace': raw, 'status': 'success', 'answer': 'A decision'},
        case_id='c1', mode='reasoning')
    report = inspect_envelope(envelope)

    captured = []

    class RecordingJudge(DeepEvalBaseLLM):
        def load_model(self):
            return self

        def get_model_name(self):
            return 'offline-recording-judge'

        def generate(self, prompt, schema=None, **kwargs):
            captured.append(prompt)
            values = {
                'TaskAndOutcome': {'task': 'Summarize decisions', 'outcome': 'A decision'},
                'TaskCompletionVerdict': {'verdict': 1.0, 'reason': 'Synthetic response'},
                'Task': {'task': 'Summarize decisions'},
                'EfficiencyVerdict': {'score': 0.75, 'reason': 'Synthetic response'},
                'AgentPlan': {'plan': ['Retrieve evidence', 'Summarize decisions']},
                'PlanQualityScore': {'score': 0.8, 'reason': 'Synthetic response'},
                'PlanAdherenceScore': {'score': 0.8, 'reason': 'Synthetic response'},
            }
            return schema(**values[schema.__name__])

        async def a_generate(self, prompt, schema=None, **kwargs):
            return self.generate(prompt, schema=schema, **kwargs)

    case = build_test_case({'question': 'Summarize decisions'}, {'answer': 'A decision'}, envelope, {})
    for result in evaluate_trajectory(case, envelope, model=RecordingJudge()):
        assert result['status'] == 'scored', result
    observed = {hashlib.sha256(p.encode()).hexdigest(): p for p in captured}
    assert len(report['static_prompts']) == 3
    for prompt in report['static_prompts']:
        actual = observed[prompt['sha256']]
        assert prompt['chars'] == len(actual)
        assert prompt['bytes'] == len(actual.encode('utf-8'))
        assert 'NOT_TRANSMITTED' not in actual
    assert report['context_fit'] == 'unknown'
    assert report['token_count'] is None
    assert report['dynamic_prompts_measured'] is False


def test_inspect_run_preserves_sources_and_keeps_missing_scores_unknown(tmp_path, monkeypatch):
    from rag_eval.agent_input_inspection import inspect_run
    import rag_eval.agent_deepeval as scoring

    monkeypatch.setattr(scoring, '_metric', lambda *a, **k: pytest.fail('constructed a judge'))
    run = tmp_path / 'run'
    run.mkdir()
    save_jsonl(run / 'outputs.jsonl', [
        dict(case_id='ok', mode='chunk', status='success', prediction='A decision',
             product_record={'execution_trace': execution_trace()}),
        dict(case_id='clarify', mode='reasoning', status='clarification',
             product_record={'execution_trace': execution_trace(clarification=True)}),
        dict(case_id='legacy', mode='reasoning', status='success', prediction='Old answer'),
    ])
    before = (run / 'outputs.jsonl').read_bytes()
    output = tmp_path / 'inspection'
    summary = inspect_run(run, output, max_prompt_bytes=1)
    rows = [json.loads(line) for line in (output / 'agent-inputs.jsonl').read_text().splitlines()]
    assert summary['record_count'] == 3
    assert summary['judge_enabled'] is False
    assert summary['over_byte_budget_count'] == 1
    assert rows[0]['byte_budget_status'] == 'over_byte_budget'
    assert rows[0]['metrics']['plan_quality']['reason'] == 'explicit_plan_missing'
    assert rows[1]['metrics']['task_completion']['reason'] == 'final_output_missing'
    assert rows[1]['static_prompts'] == []
    assert rows[2]['status'] == 'not_applicable'
    assert (run / 'outputs.jsonl').read_bytes() == before
    assert not (output / 'agent-scores.jsonl').exists()
    assert (output / 'agent-input-report.md').is_file()
    with pytest.raises(FileExistsError):
        inspect_run(run, output)
    with pytest.raises(ValueError):
        inspect_run(run, run / 'inspection')
    with pytest.raises(ValueError):
        inspect_run(run, tmp_path / 'invalid-budget', max_prompt_bytes=0)
