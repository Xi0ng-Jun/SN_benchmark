import copy
import json

import pytest

from rag_eval import benchmark_submission as submissions


def bundle():
    return {'manifest': {'suite': 'qasper', 'adaptation_revision': 'notebook-data-v3',
                         'source': {'split': 'test'}, 'files': {'raw-data': 'abc'}},
            'cases': [{'case_id': 'qasper:q1', 'sample_id': 'q1', 'suite': 'qasper', 'task': 'qa',
                       'group_id': 'paper-1'},
                      {'case_id': 'qasper:q2', 'sample_id': 'q2', 'suite': 'qasper', 'task': 'qa',
                       'group_id': 'paper-1'}]}


def method():
    return {'name': 'reference-bm25', 'kind': 'reference', 'citation_style': 'numeric',
            'model_identity': {'model_id': 'test-model', 'parameters': {'temperature': 0}},
            'input_policy': 'frozen-source-documents', 'configuration': {'top_k': 3}}


def rows():
    return [{'case_id': 'qasper:q1', 'status': 'success', 'prediction': 'Falcon'},
            {'case_id': 'qasper:q2', 'status': 'no_answer', 'prediction': ''}]


def test_submission_keeps_whole_frozen_scope_and_missing_status():
    result = submissions.build_submission(bundle(), method=method(), predictions=rows()[:1])
    assert result['case_ids'] == ['qasper:q1', 'qasper:q2']
    assert result['coverage'] == {'planned': 2, 'success': 1, 'no_answer': 0,
                                 'clarification': 0, 'error': 0, 'missing': 1,
                                 'generation_complete': False}
    assert result['predictions'][1]['status'] == 'missing'
    assert result['predictions'][1]['prediction'] == ''


@pytest.mark.parametrize('extra', [
    {'case_id': 'qasper:q1', 'status': 'success', 'prediction': 'Duplicate'},
    {'case_id': 'qasper:unknown', 'status': 'success', 'prediction': 'Unknown'},
])
def test_duplicate_or_unknown_predictions_cannot_change_denominator(extra):
    with pytest.raises(ValueError):
        submissions.build_submission(bundle(), method=method(), predictions=rows() + [extra])


def test_unanswered_is_observed_but_not_success_and_error_is_incomplete():
    result = submissions.build_submission(bundle(), method=method(), predictions=rows())
    assert result['coverage']['generation_complete'] is True
    assert result['coverage']['no_answer'] == 1
    changed = rows()
    changed[1]['status'] = 'error'
    assert submissions.build_submission(bundle(), method=method(), predictions=changed)['coverage']['generation_complete'] is False


def test_validate_rejects_bundle_scope_and_saved_coverage_tampering():
    result = submissions.build_submission(bundle(), method=method(), predictions=rows())
    other = bundle()
    other['manifest']['files']['raw-data'] = 'different'
    with pytest.raises(ValueError, match='bundle'):
        submissions.validate_submission(other, result)
    altered = copy.deepcopy(result)
    altered['coverage']['planned'] = 1
    with pytest.raises(ValueError, match='coverage'):
        submissions.validate_submission(bundle(), altered)


def test_subset_is_explicit_and_cannot_be_relabelled_full():
    result = submissions.build_submission(bundle(), method=method(), predictions=rows()[:1],
                                          case_ids=['qasper:q1'])
    assert result['scope'] == 'subset'
    assert result['coverage']['planned'] == 1
    result['scope'] = 'full'
    with pytest.raises(ValueError, match='scope'):
        submissions.validate_submission(bundle(), result)


@pytest.mark.parametrize('mutation', ['secret', 'nonfinite', 'empty_success', 'bad_status'])
def test_invalid_method_or_prediction_is_rejected_before_write(mutation):
    m, p = method(), rows()
    if mutation == 'secret':
        m['model_identity']['api_key'] = 'must-not-be-saved'
    elif mutation == 'nonfinite':
        m['configuration']['budget'] = float('nan')
    elif mutation == 'empty_success':
        p[0]['prediction'] = ' '
    else:
        p[0]['status'] = 'finished'
    with pytest.raises(ValueError):
        submissions.build_submission(bundle(), method=m, predictions=p)


def test_submission_is_independent_of_input_order_and_does_not_mutate_input():
    original = rows()
    result = submissions.build_submission(bundle(), method=method(), predictions=list(reversed(original)))
    assert [r['case_id'] for r in result['predictions']] == ['qasper:q1', 'qasper:q2']
    assert original == rows()
    assert submissions.validate_submission(bundle(), json.loads(json.dumps(result))) == result


@pytest.mark.parametrize('field', ['gold', 'gold_document_ids', 'expected_answer', 'references'])
def test_submission_rejects_original_gold_record_before_any_projection(field):
    predictions = rows()
    predictions[0]['record'] = dict(status='success', answer='Falcon', **{field: ['secret annotation']})
    with pytest.raises(ValueError, match='gold'):
        submissions.build_submission(bundle(), method=method(), predictions=predictions)


def test_write_submission_reuses_validated_context(tmp_path):
    class Context:
        artifact_roots = ()
        def __init__(self):
            self.paths = []

        def bundle(self, path):
            self.paths.append(path)
            return bundle()

    context = Context()
    source = tmp_path / 'bundle'
    result = submissions.write_submission(source, tmp_path / 'submission', method=method(),
                                          predictions=rows(), context=context)
    saved = json.loads(result.read_text())
    assert saved['coverage']['planned'] == 2
    assert context.paths == [source.resolve()]


def export_fixture(tmp_path, monkeypatch, *, discarded=None):
    from rag_eval import run_reader, run_report
    frozen = bundle()
    frozen['manifest']['suite'] = 'qmsum'
    frozen['documents'] = []
    contexts = []

    class Context:
        artifact_roots = ()
        def bundle(self, path):
            contexts.append(self)
            return frozen

    monkeypatch.setattr(run_reader, 'RunReadContext', Context)
    run = tmp_path / 'run'
    run.mkdir()
    record = dict(status='success', answer='Falcon', retrieval_context=['Large transcript'] * 100,
                  runtime_debug=discarded or {'trace': ['Audit only']})
    outputs = [dict(case_id='qasper:q1', status='success', prediction='Falcon', product_record=record),
               dict(case_id='qasper:q2', status='error', prediction='Partial observed answer',
                    product_record=dict(status='error', answer='Partial observed answer', reason='request failed'))]
    (run / 'outputs.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in outputs))
    identity = dict(source=frozen['manifest'], notebook_context=dict(request_revision='notebook-request-v3',
                    partition_id='p1', case_ids=['qasper:q1', 'qasper:q2']),
                    product_services='services', runtime_settings='settings')

    def load(path, *, context=None, include_model_events=True):
        assert include_model_events is False
        contexts.append(context)
        return dict(manifest=dict(mode='chunk', identity=identity, run_id='run'), outputs=outputs)

    monkeypatch.setattr(run_report, 'load_run', load)
    return run, outputs, contexts


def test_sn_export_uses_one_context_compact_projection_and_output_provenance(tmp_path, monkeypatch):
    from rag_eval.artifacts import digest
    from rag_eval.scoring_projection import PROTOCOL
    run, outputs, contexts = export_fixture(tmp_path, monkeypatch)
    original = copy.deepcopy(outputs)
    path = submissions.export_sn_runs(tmp_path / 'bundle', [run], tmp_path / 'submission')
    saved = json.loads(path.read_text())
    assert saved['format'] == 'benchmark-submission-v1'
    assert saved['method']['configuration']['scoring_projection'] == PROTOCOL
    assert saved['predictions'][0]['record'] == dict(status='success', answer='Falcon')
    assert saved['predictions'][1]['status'] == 'error'
    assert saved['predictions'][1]['record']['reason'] == 'request failed'
    assert all(row['run_id'] == 'run' and row['outputs_sha256'] == digest(run / 'outputs.jsonl')
               for row in saved['predictions'])
    assert all(context is contexts[0] for context in contexts)
    assert outputs == original


@pytest.mark.parametrize('discarded', [{'api_key': 'secret'}, {'value': float('nan')}])
def test_sn_export_checks_full_observation_before_discarding_debug_fields(tmp_path, monkeypatch, discarded):
    run, _, _ = export_fixture(tmp_path, monkeypatch, discarded=discarded)
    with pytest.raises(ValueError):
        submissions.export_sn_runs(tmp_path / 'bundle', [run], tmp_path / 'submission')
    assert not (tmp_path / 'submission').exists()


def test_sn_export_rejects_original_gold_even_when_projection_would_drop_it(tmp_path, monkeypatch):
    run, outputs, _ = export_fixture(tmp_path, monkeypatch)
    outputs[0]['product_record']['expected_answer'] = 'Secret annotation'
    with pytest.raises(ValueError, match='gold'):
        submissions.export_sn_runs(tmp_path / 'bundle', [run], tmp_path / 'submission')


def test_sn_export_does_not_copy_discarded_full_audit_values(tmp_path, monkeypatch):
    class AuditContext(dict):
        def __deepcopy__(self, memo):
            raise AssertionError('Full audit context cannot be copied')

    run, _, _ = export_fixture(tmp_path, monkeypatch, discarded=AuditContext(trace=['large audit']))
    result = submissions.export_sn_runs(tmp_path / 'bundle', [run], tmp_path / 'submission')
    assert json.loads(result.read_text())['predictions'][0]['record'] == dict(status='success', answer='Falcon')


def continuation_runs(tmp_path, monkeypatch):
    from rag_eval import run_report
    first, outputs, _ = export_fixture(tmp_path, monkeypatch)
    template = run_report.load_run(first, include_model_events=False)
    second = tmp_path / 'continued'
    second.mkdir()
    runs = {}
    for index, path in enumerate((first, second)):
        run = copy.deepcopy(template)
        run['outputs'] = [copy.deepcopy(outputs[index])]
        run['manifest']['run_id'] = path.name
        identity = run['manifest']['identity']
        identity['notebook_context'].update(partition_id=path.name, case_ids=[outputs[index]['case_id']])
        identity['code'] = dict(product_archive_sha256='same-sn', versions={'package': '1.0'},
                                benchmark_source_hashes={'src/rag_eval/notebook_runner.py': str(index)})
        identity['runtime_dependencies'] = dict(
            format='sn-startup-binding-v1', requirements_sha256='same-requirements',
            observation_sha256=f'observation-{index}', environment_sha256=f'checkout-{index}',
            run_coordinate=str(path))
        if index:
            identity['preparation_policy'] = 'deferred-notebook-metadata-v1'
            run['outputs'][0].update(prediction='', product_record=dict(status='error', answer=''))
        # The old run intentionally has no preparation_policy field.
        (path / 'outputs.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in run['outputs']))
        runs[path] = run
    monkeypatch.setattr(run_report, 'load_run', lambda path, **kwargs: runs[path])
    return runs


def test_sn_export_combines_completed_and_optimized_remaining_cases(tmp_path, monkeypatch):
    from rag_eval.artifacts import digest
    from rag_eval.identity import fingerprint
    runs = continuation_runs(tmp_path, monkeypatch)
    original = copy.deepcopy(runs)
    before = {path: digest(path / 'outputs.jsonl') for path in runs}
    result = submissions.export_sn_runs(tmp_path / 'bundle', list(runs), tmp_path / 'submission')
    saved = json.loads(result.read_text())
    assert saved['scope'] == 'full'
    assert saved['coverage']['planned'] == 2
    assert saved['coverage']['success'] == saved['coverage']['error'] == 1
    assert saved['coverage']['missing'] == 0
    assert saved['predictions'][1]['prediction'] == ''
    assert saved['method']['configuration']['preparation_policies'] == [
        'deferred-notebook-metadata-v1', 'per-source-metadata-v1']
    for row, (path, run) in zip(saved['predictions'], runs.items()):
        identity = run['manifest']['identity']
        assert row['run_id'] == path.name
        assert row['outputs_sha256'] == before[path]
        assert row['run_provenance'] == dict(
            identity_sha256=fingerprint(identity),
            preparation_policy=identity.get('preparation_policy', 'per-source-metadata-v1'),
            evaluator_source_sha256=fingerprint(identity['code']['benchmark_source_hashes']))
    reversed_result = submissions.export_sn_runs(tmp_path / 'bundle', list(reversed(runs)), tmp_path / 'reversed')
    assert json.loads(reversed_result.read_text()) == saved
    assert runs == original
    assert {path: digest(path / 'outputs.jsonl') for path in runs} == before


@pytest.mark.parametrize('difference', ['mode', 'model', 'settings', 'sn', 'dependencies', 'request'])
def test_continuation_still_rejects_changed_answer_conditions(tmp_path, monkeypatch, difference):
    runs = continuation_runs(tmp_path, monkeypatch)
    manifest = list(runs.values())[1]['manifest']
    identity = manifest['identity']
    if difference == 'mode':
        manifest['mode'] = 'reasoning'
    elif difference == 'model':
        identity['product_services'] = 'other-model'
    elif difference == 'settings':
        identity['runtime_settings'] = 'other-settings'
    elif difference == 'sn':
        identity['code']['product_archive_sha256'] = 'other-sn'
    elif difference == 'dependencies':
        identity['code']['versions']['package'] = '2.0'
    else:
        identity['notebook_context']['request_revision'] = 'notebook-request-v2'
    with pytest.raises(ValueError, match='cannot mix|gold-free'):
        submissions.export_sn_runs(tmp_path / 'bundle', list(runs), tmp_path / 'submission')
