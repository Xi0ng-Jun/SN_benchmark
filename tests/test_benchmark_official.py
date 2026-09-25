import copy

import pytest

from rag_eval.benchmark_submission import build_submission
from rag_eval import benchmark_official as official


def fixture(suite='qasper', task='qa', status='success', answer='Falcon [k1]'):
    gold = {'annotations': [{'answer': 'Falcon', 'type': 'extractive', 'evidence': ['Source paragraph.']}],
            'answer': 'Falcon', 'qa_pairs': [{'short_answers': ['Falcon']}, {'short_answers': ['Owl']}]}
    case = {'case_id': suite + ':q1', 'sample_id': 'q1', 'suite': suite, 'task': task,
            'question': 'Which birds?', 'gold': gold, 'group_id': 'g1',
            'candidate_documents': [{'id': 'd1', 'title': 'Title', 'text': 'Source paragraph.'}]}
    bundle = {'manifest': {'suite': suite, 'adaptation_revision': 'notebook-data-v3'}, 'cases': [case]}
    method = {'name': 'sn-chunk', 'kind': 'sn', 'citation_style': 'sn',
              'model_identity': {'configuration': 'hash'}, 'input_policy': 'frozen-source-documents',
              'configuration': {}}
    record = {'status': status, 'answer': answer, 'source_to_document': {'s1': 'd1'},
              'response': {'anchors': [{'key': 'k1', 'source_id': 's1'}]}}
    row = {'case_id': case['case_id'], 'status': status, 'prediction': answer, 'record': record}
    return bundle, method, [row]


def test_qasper_evidence_requires_explicit_prediction_not_final_context():
    b, m, rows = fixture()
    rows[0]['record']['retrieval_context'] = ['Source paragraph.']
    prepared = official.prepare_inputs(b, build_submission(b, method=m, predictions=rows))
    assert prepared['predictions']['q1']['answer'] == 'Falcon '
    assert prepared['evidence_supported'] is False
    rows[0]['record']['predicted_evidence'] = ['Source paragraph.']
    with pytest.raises(ValueError, match='policy'):
        official.prepare_inputs(b, build_submission(b, method=m, predictions=rows))
    # External methods may submit explicit paragraphs. SN requires its frozen
    # final-citation projection; adding a bare list cannot bypass replay.
    m.update(kind='published', citation_style='none')
    prepared = official.prepare_inputs(b, build_submission(b, method=m, predictions=rows))
    assert prepared['evidence_supported'] is True
    assert prepared['predictions']['q1']['evidence'] == ['Source paragraph.']


def test_qasper_text_evidence_only_filters_float_without_dropping_question():
    b, m, rows = fixture()
    b['cases'][0]['gold']['annotations'][0]['evidence'].append('FLOAT SELECTED: fig1')
    p = official.prepare_inputs(b, build_submission(b, method=m, predictions=rows))
    assert list(p['gold']) == ['q1']
    assert p['gold']['q1'][0]['evidence'] == ['Source paragraph.']


def test_qasper_unanswerable_has_empty_evidence_even_if_source_annotation_does_not():
    b, m, rows = fixture()
    b['cases'][0]['gold']['annotations'][0].update(answer='Unanswerable', type='none')
    p = official.prepare_inputs(b, build_submission(b, method=m, predictions=rows))
    assert p['gold']['q1'][0]['evidence'] == []


def test_error_cannot_be_silently_scored_using_partial_answer():
    b, m, rows = fixture(status='error')
    p = official.prepare_inputs(b, build_submission(b, method=m, predictions=rows))
    assert p['predictions'] == {}
    assert p['coverage']['generation_complete'] is False


def test_alce_maps_observed_sn_anchor_and_preserves_raw_before_cli_processing():
    b, m, rows = fixture('alce', 'asqa', answer='Falcon [k1].\nOwl.')
    p = official.prepare_inputs(b, build_submission(b, method=m, predictions=rows))
    assert p['data'][0]['output'] == 'Falcon [1].\nOwl.'
    assert p['audit'][0]['raw_answer'] == 'Falcon [k1].\nOwl.'
    assert p['data'][0]['docs'][0]['text'] == 'Source paragraph.'


def test_alce_numeric_reference_citations_keep_original_candidate_indices():
    b, m, rows = fixture('alce', 'qampari', answer='Falcon [1], Owl [9]')
    b['cases'][0]['gold'] = {'answers': [['Falcon'], ['Owl']]}
    m.update(kind='reference', citation_style='numeric')
    rows[0]['record']['citation_index_to_document_id'] = {'1': 'd1'}
    p = official.prepare_inputs(b, build_submission(b, method=m, predictions=rows))
    assert p['data'][0]['output'] == 'Falcon [1], Owl [2]'
    assert p['audit'][0]['raw_answer'] == 'Falcon [1], Owl [9]'
    assert len(p['data'][0]['docs']) == 1


@pytest.mark.parametrize('answer', ['Claim [0', 'Claim [1', 'Claim [1,2]'])
def test_alce_malformed_numeric_prefix_cannot_cite_unseen_or_negative_document(answer):
    b, m, rows = fixture('alce', 'asqa', answer=answer)
    m.update(kind='reference', citation_style='numeric')
    rows[0]['record']['citation_index_to_document_id'] = {}
    prepared = official.prepare_inputs(b, build_submission(b, method=m, predictions=rows))
    assert prepared['data'][0]['output'].startswith('Claim [2')
    assert prepared['audit'][0]['conversions'][0]['valid'] is False


def test_new_official_scoring_rejects_gold_bearing_generation_records():
    b, m, rows = fixture()
    rows[0]['record']['expected_answer'] = 'Falcon'
    with pytest.raises(ValueError, match='gold|label'):
        official.prepare_inputs(b, build_submission(b, method=m, predictions=rows))


def test_official_comparison_rejects_old_gold_filtered_data_revision():
    b, m, rows = fixture()
    b['manifest']['adaptation_revision'] = 'notebook-data-v2'
    with pytest.raises(ValueError, match='v3'):
        official.prepare_inputs(b, build_submission(b, method=m, predictions=rows))


def test_alce_cannot_mix_subtasks_in_one_official_cli_batch():
    b, m, rows = fixture('alce', 'asqa')
    other = copy.deepcopy(b['cases'][0])
    other.update(case_id='alce:q2', sample_id='q2', task='eli5')
    b['cases'].append(other)
    with pytest.raises(ValueError, match='task'):
        official.prepare_inputs(b, build_submission(b, method=m, predictions=rows))


def test_numeric_results_reject_nan_and_out_of_range():
    for value in [float('nan'), float('inf'), -0.1, 1.1, True]:
        with pytest.raises(ValueError):
            official.validate_metrics({'answer_f1': value})
    assert official.validate_metrics({'answer_f1': 0.5}) == {'answer_f1': 0.5}
    assert official.validate_metrics({'upstream_map_at_10': 1.1}) == {'upstream_map_at_10': 1.1}


def test_scoring_rejects_prepared_gold_or_prediction_changes_before_writing(tmp_path):
    b, m, rows = fixture()
    submission = build_submission(b, method=m, predictions=rows)
    prepared = official.prepare_inputs(b, submission)
    prepared['gold']['q1'][0]['answer'] = 'Edited after preparation'
    with pytest.raises(ValueError, match='prepared|Prepared'):
        official.score_prepared(b, submission, prepared, source_directory=tmp_path,
                                output_dir=tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_scorer_identity_uses_rouge_content_and_library_order_not_absolute_paths():
    dependencies = {'rouge': {'rouge_home': '/first/rouge', 'files': {'data/a': {'sha256': 'a'}},
                              'files_sha256': 'files', 'perl_path': '/first/perl',
                              'perl_version': 'v5.38', 'perl_binary': {'sha256': 'b'},
                              'perl5lib': {'/first/lib': {'Module.pm': {'sha256': 'c'}}}},
                    'segmentation': 'hmnet_regex'}
    copied = copy.deepcopy(dependencies)
    copied['rouge'].update(rouge_home='/copy/rouge', perl_path='/copy/perl',
                           perl5lib={'/copy/lib': {'Module.pm': {'sha256': 'c'}}})
    assert official.scorer_identity('profile', dependencies) == official.scorer_identity('profile', copied)
    copied['rouge']['perl5lib']['/copy/lib']['Module.pm']['sha256'] = 'changed'
    assert official.scorer_identity('profile', dependencies) != official.scorer_identity('profile', copied)
