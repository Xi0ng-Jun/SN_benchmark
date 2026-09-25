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
