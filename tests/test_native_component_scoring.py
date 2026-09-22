"""Saved-component recovery uses full inputs, public metrics and no product call."""
import json
import sys

import pytest

from test_native_agent import sdk, rows  # fixture uses the installed SDK; network is blocked


def source_fixture(tmp_path):
    source = tmp_path / 'source'
    (source / 'agent').mkdir(parents=True)
    common = dict(schema_version='sn-deepeval-native-v1', record_type='sample', case_id='case-1',
                  mode='reasoning', request_id='request-1', judge={'model_id': 'old-judge'},
                  grouping='native_span', component_status='completed')
    samples = [dict(common, sample_id='retrieve-1', span_id='span-1', span_name='sn.retrieve.chunks',
                    input='Who wrote notes?', actual_output='[{"text":"Ada wrote notes."}]',
                    retrieval_context=['Ada wrote notes.', 'The meeting continued.']),
               dict(common, sample_id='synthesis-1', span_id='span-2', span_name='sn.synthesis.reasoning',
                    input='Who wrote notes?', actual_output='Ada wrote notes.',
                    retrieval_context=['[1] Ada wrote notes.'])]
    path = source / 'agent/components.jsonl'
    path.write_text(''.join(json.dumps(s) + '\n' for s in samples))
    return source, samples


def test_saved_sample_is_scored_without_product_and_full_input_preserved(tmp_path, sdk, monkeypatch):
    from rag_eval.native_component_scoring import rescore_components
    source, samples = source_fixture(tmp_path)
    before = (source / 'agent/components.jsonl').read_bytes()
    for name in ('app.services.sqlite_repository', 'rag_eval.benchmark_runtime', 'rag_eval.system_runtime'):
        monkeypatch.setitem(sys.modules, name, None)
    judge = sdk.Judge(lambda: None)
    output = tmp_path / 'scored'
    result = rescore_components(source, output, sample_ids=['retrieve-1'], metrics=['contextual_relevancy'],
                               judge_factory=lambda sink: judge, judge_identity={'model_id': 'fixture-judge'})
    scores = rows(output / 'component-scores.jsonl')
    assert [(s['metric'], s['status'], s['score']) for s in scores] == [('Contextual Relevancy', 'scored', 1)]
    assert scores[0]['sample_id'] == 'retrieve-1' and scores[0]['request_id'] == 'request-1'
    assert scores[0]['judge']['model_id'] == 'fixture-judge'
    assert rows(output / 'components.jsonl') == [samples[0]]
    assert any('The meeting continued.' in p for p in judge.prompts)
    assert (source / 'agent/components.jsonl').read_bytes() == before
    assert result['status'] == 'finished'
    assert list((output / 'sdk').rglob('test_run_*.json'))


def test_saved_components_cancel_after_completed_metric_keeps_first_score(tmp_path, sdk):
    from rag_eval.native_component_scoring import rescore_components
    source, _ = source_fixture(tmp_path)
    judge = sdk.Judge(lambda: None)
    output = tmp_path / 'scored'
    original = judge.generate
    def generate(prompt, schema=None):
        if schema.__module__.startswith('deepeval.metrics.answer_relevancy'):
            assert rows(output / 'component-scores.jsonl')[0]['status'] == 'scored'
            raise KeyboardInterrupt()
        return original(prompt, schema)
    judge.generate = generate
    with pytest.raises(KeyboardInterrupt):
        rescore_components(source, output, sample_ids=['synthesis-1'], metrics=['faithfulness', 'answer_relevancy'],
                           judge_factory=lambda sink: judge, judge_identity={'model_id': 'fixture-judge'})
    assert [s['status'] for s in rows(output / 'component-scores.jsonl')] == ['scored', 'error']
    assert json.loads((output / 'component-score-summary.json').read_text())['status'] == 'interrupted'


@pytest.mark.parametrize('sample_ids,metrics', [(['unknown'], ['faithfulness']),
    (['retrieve-1'], ['task_completion']), (['retrieve-1'], ['faithfulness']),
    (['retrieve-1', 'retrieve-1'], ['contextual_relevancy'])])
def test_invalid_selection_fails_before_judge_and_creating_output(tmp_path, sdk, sample_ids, metrics):
    from rag_eval.native_component_scoring import rescore_components
    source, _ = source_fixture(tmp_path)
    with pytest.raises(ValueError):
        rescore_components(source, tmp_path / 'out', sample_ids=sample_ids, metrics=metrics,
                           judge_factory=lambda sink: pytest.fail('must not create judge'),
                           judge_identity={'model_id': 'fixture-judge'})
    assert not (tmp_path / 'out').exists()


def test_judge_timing_and_metric_identity_and_http_status_do_not_leak_credentials(tmp_path, sdk):
    from rag_eval.starter_model import ExplicitBenchmarkModel, ModelCallError
    from pydantic import BaseModel
    class Response(BaseModel):
        answer: str
    class GatewayFailure(Exception):
        status_code = 504
    class Client:
        def chat_json(self, *args, **kwargs):
            raise GatewayFailure('private-service-credential')
    events = []
    judge = ExplicitBenchmarkModel(Client(), model_id='fixture', role='judge', parameters={'max_retries': 0},
                                   config_sha256='f' * 64, sink=events.append)
    with judge.for_case('case-1', 'request-1'), judge.for_metric(metric='Faithfulness', score_id='score-1', sample_id='synth'):
        with pytest.raises(ModelCallError):
            judge.generate('证据', Response)
    assert events[-1]['http_status'] == 504
    assert events[-1]['elapsed_seconds'] >= 0
    assert events[0]['prompt_utf8_bytes'] == 6
    assert events[-1]['metric'] == 'Faithfulness' and events[-1]['sample_id'] == 'synth'
    assert 'private-service-credential' not in json.dumps(events)
    with judge.for_case('case-2', 'request-2'), pytest.raises(ModelCallError):
        judge.generate('Another', Response)
    assert 'sample_id' not in events[-1] and events[-1]['case_id'] == 'case-2'


def test_existing_native_sample_uses_recorded_span_status(tmp_path, sdk):
    from rag_eval.native_component_scoring import rescore_components
    source, samples = source_fixture(tmp_path)
    sample = samples[0]
    sample.pop('component_status')
    span = dict(sample, record_type='span', metadata={'status': 'completed'})
    (source / 'agent/components.jsonl').write_text(json.dumps(span) + '\n' + json.dumps(sample) + '\n')
    result = rescore_components(source, tmp_path / 'out', sample_ids=['retrieve-1'], metrics=['contextual_relevancy'],
                               judge_factory=lambda sink: sdk.Judge(lambda: None), judge_identity={'model_id': 'fixture'})
    assert result['score_status_counts']['scored'] == 1


def test_finalized_metric_blocks_late_calls_and_writes(tmp_path, sdk):
    from rag_eval.starter_model import ExplicitBenchmarkModel, ModelCallError
    from pydantic import BaseModel
    class Response(BaseModel):
        answer: str
    active, calls, events = [True], [], []
    class Client:
        def chat_json(self, *args, **kwargs):
            calls.append(1)
            active[0] = False  # outer task finalized while this request was in flight
            return {'answer': 'late response'}
    judge = ExplicitBenchmarkModel(Client(), model_id='fixture', role='judge', parameters={},
                                   config_sha256='f' * 64, sink=events.append)
    with judge.for_case('case', 'request'), judge.for_metric(metric='Faithfulness', score_id='score',
                                                           is_active=lambda: active[0]):
        with pytest.raises(ModelCallError):
            judge.generate('first', Response)
        with pytest.raises(ModelCallError):
            judge.generate('must not send', Response)
    assert len(calls) == 1
    assert [e['event'] for e in events] == ['started']


def test_sdk_timeout_cannot_be_replaced_by_late_metric_success(tmp_path, sdk):
    from deepeval.metrics import BaseMetric
    from rag_eval.native_metrics import CheckpointMetric
    scores = []
    class LateMetric(BaseMetric):
        threshold = .5
        def measure(self, test_case):
            metric.error = 'Timed out while evaluating metric'  # SDK outer deadline fires
            metric.success = False
            self.score, self.success = 1., True  # already running provider returns late
            return self.score
    metric = CheckpointMetric(LateMetric(), link={}, on_start=lambda row: None,
                              on_result=lambda metric, row: scores.append(row))
    metric.measure(None)
    assert scores[0]['status'] == 'error' and scores[0]['score'] is None
    assert scores[0]['reason'] == 'Timed out while evaluating metric'


def test_interrupt_first_selected_metric_records_remaining_jobs_as_not_started(tmp_path, sdk):
    from rag_eval.native_component_scoring import rescore_components
    source, _ = source_fixture(tmp_path)
    def cancelled():
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        rescore_components(source, tmp_path / 'out', sample_ids=['synthesis-1'],
                           metrics=['faithfulness', 'answer_relevancy'],
                           judge_factory=lambda sink: sdk.Judge(cancelled), judge_identity={'model_id': 'fixture'})
    scores = rows(tmp_path / 'out/component-scores.jsonl')
    assert [(s['metric'], s['execution_status']) for s in scores] == [
        ('Faithfulness', 'failed'), ('Answer Relevancy', 'not_started')]
    assert all(s['score'] is None and s['status'] == 'error' for s in scores)
