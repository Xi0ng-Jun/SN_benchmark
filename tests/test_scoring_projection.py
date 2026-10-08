import importlib.util
import json

import pytest

from rag_eval.benchmark_official import prepare_inputs
from rag_eval.benchmark_submission import build_submission
from test_benchmark_official import fixture as official_fixture


def api():
    assert importlib.util.find_spec('rag_eval.scoring_projection'), 'Scoring projection is missing'
    from rag_eval import scoring_projection
    return scoring_projection


def scored_fixture(tmp_path, suite):
    bundle, method, rows = official_fixture(suite, 'asqa' if suite == 'alce' else 'qa')
    if suite == 'qasper':
        from rag_eval.notebook_data import adapt
        from rag_eval import qasper_evidence
        from test_qasper_evidence import fixture
        raw, document, repo, mapping, record = fixture(tmp_path)
        bundle = adapt('qasper', raw, adaptation_revision='notebook-data-v3')
        bundle['manifest'] = dict(suite=suite, adaptation_revision='notebook-data-v3')
        catalogues = qasper_evidence.public_catalogues(raw, [document])
        bundle['qasper_evidence_catalogues'] = catalogues
        snapshot = qasper_evidence.capture_evidence(repo, record, document, catalogues[document['id']], mapping)
        record.update(qasper_evidence=snapshot, predicted_evidence=snapshot['projection']['predicted_evidence'])
        method['configuration']['qasper_evidence'] = qasper_evidence.identity()
        rows = [dict(case_id=bundle['cases'][0]['case_id'], status='success', prediction=record['answer'], record=record)]
    elif suite == 'hotpotqa':
        from rag_eval import hotpot_evidence
        from test_hotpot_evidence import fixture
        bundle, repo, mapping, record = fixture(tmp_path)
        bundle['manifest'] = dict(suite=suite, adaptation_revision='notebook-data-v3')
        snapshot = hotpot_evidence.capture_evidence(repo, record, bundle['documents'], mapping)
        record.update(hotpot_evidence=snapshot, predicted_supporting_facts=snapshot['projection']['predicted_supporting_facts'])
        method['configuration']['hotpot_evidence'] = hotpot_evidence.identity()
        rows = [dict(case_id=bundle['cases'][0]['case_id'], status='success', prediction=record['answer'], record=record)]
    elif suite == 'multihop_rag':
        from rag_eval import sn_retrieval
        from rag_eval.notebook_bundle import request_question
        from test_sn_retrieval import fixture
        bundle, repo, mapping, chunks = fixture(tmp_path)
        question = request_question(bundle['cases'][0], request_revision='notebook-request-v3')
        with sn_retrieval.capture_chunk_ranking(repo, 'notebook', question, bundle['documents'], mapping) as retrieval:
            repo._runtime.ask_component._activate_selected_source_graph('notebook', chunks)
        assert retrieval['status'] == 'complete'
        method['configuration'].update(mode='chunk', retrieval_contract=sn_retrieval.POLICY,
                                        multihop_retrieval=sn_retrieval.identity())
        rows = [dict(case_id=bundle['cases'][0]['case_id'], status='success', prediction='yes',
                     record=dict(status='success', answer='yes', retrieval=retrieval))]
    elif suite == 'alce':
        rows[0]['record'].update(answer='Falcon [k1] [k2] [k9]', anchor_documents={'k1': 'd1', 'k2': 'wrong'},
                                 mapping_errors=[dict(key='k2', reason='conflicting_source')])
        rows[0]['prediction'] = rows[0]['record']['answer']
        rows[0]['record']['response']['anchors'].append(dict(key='k2', source_id='s1'))
    for row in rows:
        row['record'].update(retrieval_context=['irrelevant audit text' * 100],
                             runtime_debug={'events': ['large debug observation'] * 100})
    return bundle, method, rows


@pytest.mark.parametrize('suite', ['qasper', 'hotpotqa', 'multihop_rag', 'alce', 'qmsum'])
def test_five_suite_projection_keeps_prepared_inputs_identical(tmp_path, suite):
    bundle, method, rows = scored_fixture(tmp_path, suite)
    full = build_submission(bundle, method=method, predictions=rows)
    compact_rows = [dict(row, record=api().project_record(suite, row['record'])) for row in rows]
    compact = build_submission(bundle, method=method, predictions=compact_rows)
    assert prepare_inputs(bundle, compact) == prepare_inputs(bundle, full)
    if suite == 'multihop_rag':
        from rag_eval.multihop_official import prepare_ranked_inputs
        assert prepare_ranked_inputs(bundle, compact) == prepare_ranked_inputs(bundle, full)
        assert compact_rows[0]['record']['retrieval'] == rows[0]['record']['retrieval']
    if suite == 'alce':
        assert [item['key'] for item in prepare_inputs(bundle, compact)['audit'][0]['mapping_errors']] == ['k2', 'k9']
        assert compact_rows[0]['record']['mapping_errors'] == rows[0]['record']['mapping_errors']
    assert all('retrieval_context' not in row['record'] and 'runtime_debug' not in row['record'] for row in compact_rows)


@pytest.mark.parametrize('suite', ['qasper', 'hotpotqa'])
@pytest.mark.parametrize('field', ['answer', 'response', 'captures', 'snapshot'])
def test_compact_evidence_replay_rejects_tampered_whole_observation(tmp_path, suite, field):
    bundle, method, rows = scored_fixture(tmp_path, suite)
    record = api().project_record(suite, rows[0]['record'])
    if field == 'answer':
        record['answer'] += ' tampered'
        rows[0]['prediction'] = record['answer']
    elif field == 'response':
        record['response']['unrelated_observation'] = 'tampered'
    elif field == 'captures':
        record['captures'][0]['unrelated_observation'] = 'tampered'
    else:
        record['qasper_evidence' if suite == 'qasper' else 'hotpot_evidence']['snapshot']['tampered'] = True
    rows[0]['record'] = record
    with pytest.raises(ValueError):
        prepare_inputs(bundle, build_submission(bundle, method=method, predictions=rows))


@pytest.mark.parametrize('suite', ['qasper', 'hotpotqa'])
def test_complete_response_and_captures_are_preserved_without_aliasing(tmp_path, suite):
    _, _, rows = scored_fixture(tmp_path, suite)
    original = rows[0]['record']
    original['response']['extra'] = dict(trace=['entire observed response'])
    original['captures'][0]['extra'] = dict(trace=['entire observed capture'])
    compact = api().project_record(suite, original)
    assert compact['response'] == original['response']
    assert compact['captures'] == original['captures']
    compact['response']['extra']['trace'].append('changed')
    compact['captures'][0]['extra']['trace'].append('changed')
    assert original['response']['extra']['trace'] == ['entire observed response']
    assert original['captures'][0]['extra']['trace'] == ['entire observed capture']


@pytest.mark.parametrize('suite', ['qasper', 'hotpotqa', 'multihop_rag', 'alce', 'qmsum'])
@pytest.mark.parametrize('status', ['error', 'missing', 'no_answer', 'clarification'])
def test_projection_preserves_unsuccessful_status_and_reason(suite, status):
    original = dict(status=status, answer='', reason='observed failure', error=dict(type='ObservedError'),
                    retrieval_context=['audit context'])
    assert api().project_record(suite, original) == {key: original[key] for key in ('status', 'answer', 'reason', 'error')}


@pytest.mark.parametrize('field,value,match', [
    ('gold', {'answer': 'secret annotation'}, 'gold'),
    ('gold_document_ids', ['secret'], 'gold'),
    ('expected_answer', 'secret', 'gold'),
    ('references', ['secret'], 'gold'),
    ('runtime_debug', {'api_key': 'secret'}, 'credentials'),
    ('runtime_debug', {'score': float('nan')}, 'finite'),
])
def test_discarded_original_fields_are_validated_before_allowlist(field, value, match):
    with pytest.raises(ValueError, match=match):
        api().project_record('qmsum', dict(status='success', answer='Summary', **{field: value}))


def test_projection_does_not_deepcopy_discarded_values():
    class AuditContext(dict):
        def __deepcopy__(self, memo):
            raise AssertionError('Discarded audit context must not be copied')
    record = dict(status='success', answer='Summary', runtime_debug=AuditContext(events=['large audit']))
    assert api().project_record('qmsum', record) == dict(status='success', answer='Summary')


def test_qmsum_compact_record_drops_large_context_and_keeps_whole_answer():
    original = dict(status='success', answer='Summary sentence.\n' * 1000,
                    retrieval_context=['Transcript context. ' * 1000] * 100)
    compact = api().project_record('qmsum', original)
    assert compact == dict(status='success', answer=original['answer'])
    assert len(json.dumps(compact)) < len(json.dumps(original)) / 50


def test_projection_rejects_unknown_suite_instead_of_guessing_fields():
    with pytest.raises(ValueError, match='suite'):
        api().project_record('unknown', dict(status='success', answer='Text'))


@pytest.mark.parametrize('suite', ['qasper', 'hotpotqa'])
def test_evidence_mapping_failure_retains_pending_inputs_and_answer(tmp_path, suite):
    bundle, method, rows = scored_fixture(tmp_path, suite)
    name = 'qasper_evidence' if suite == 'qasper' else 'hotpot_evidence'
    prediction = 'predicted_evidence' if suite == 'qasper' else 'predicted_supporting_facts'
    record = rows[0]['record']
    record[name].update(status='error', reason='missing_reference_observation')
    record[name].pop('projection', None)
    record.pop(prediction)
    full = build_submission(bundle, method=method, predictions=rows)
    rows[0]['record'] = api().project_record(suite, record)
    compact = build_submission(bundle, method=method, predictions=rows)
    assert prepare_inputs(bundle, compact) == prepare_inputs(bundle, full)
    assert rows[0]['record']['answer'] == record['answer']
    assert rows[0]['record'][name]['reason'] == 'missing_reference_observation'


def test_multihop_compact_ranking_snapshot_tampering_still_rejected(tmp_path):
    from rag_eval.multihop_official import prepare_ranked_inputs
    bundle, method, rows = scored_fixture(tmp_path, 'multihop_rag')
    compact = api().project_record('multihop_rag', rows[0]['record'])
    compact['retrieval']['snapshot']['chunks']['first']['text'] = 'Changed ranking passage'
    rows[0]['record'] = compact
    with pytest.raises(ValueError, match='hash|snapshot'):
        prepare_ranked_inputs(bundle, build_submission(bundle, method=method, predictions=rows))


def test_multihop_reasoning_missing_ranking_keeps_not_applicable_inputs(tmp_path):
    from rag_eval.multihop_official import prepare_ranked_inputs
    bundle, method, rows = scored_fixture(tmp_path, 'multihop_rag')
    method['configuration'] = dict(mode='reasoning')
    rows[0]['record']['retrieval'] = dict(status='not_applicable', reason='no_native_chunk_ranking')
    full = build_submission(bundle, method=method, predictions=rows)
    rows[0]['record'] = api().project_record('multihop_rag', rows[0]['record'])
    compact = build_submission(bundle, method=method, predictions=rows)
    assert prepare_ranked_inputs(bundle, compact) == prepare_ranked_inputs(bundle, full)
    assert prepare_ranked_inputs(bundle, compact)['coverage']['scored'] == 0
    assert rows[0]['record']['retrieval']['status'] == 'not_applicable'


@pytest.mark.parametrize('suite', ['qasper', 'hotpotqa', 'multihop_rag', 'alce', 'qmsum'])
@pytest.mark.parametrize('status', ['error', 'missing'])
def test_error_and_missing_prepared_inputs_and_denominators_are_unchanged(tmp_path, suite, status):
    bundle, method, rows = scored_fixture(tmp_path, suite)
    rows[0].update(status=status, prediction='')
    record = rows[0]['record']
    record.update(status=status, answer='', reason='failed request' if status == 'error' else 'absent observation')
    record.pop('predicted_evidence', None)
    record.pop('predicted_supporting_facts', None)
    full = build_submission(bundle, method=method, predictions=rows)
    rows[0]['record'] = api().project_record(suite, record)
    compact = build_submission(bundle, method=method, predictions=rows)
    assert prepare_inputs(bundle, compact) == prepare_inputs(bundle, full)
    assert compact['coverage'][status] == (len(bundle['cases']) if status == 'missing' else 1)
    assert compact['coverage']['generation_complete'] is False


@pytest.mark.parametrize('suite', ['qasper', 'hotpotqa', 'multihop_rag', 'alce', 'qmsum'])
@pytest.mark.parametrize('status', ['no_answer', 'clarification'])
def test_observed_unanswered_states_keep_prepared_inputs_and_complete_scope(tmp_path, suite, status):
    bundle, method, rows = scored_fixture(tmp_path, suite)
    rows[0].update(status=status, prediction='')
    record = rows[0]['record']
    record.update(status=status, reason='observed unanswered response')
    for evidence in ('qasper_evidence', 'hotpot_evidence'):
        if evidence in record:
            record[evidence].update(status='error', reason='no_reference_observation')
            record[evidence].pop('projection', None)
    record.pop('predicted_evidence', None)
    record.pop('predicted_supporting_facts', None)
    selected = [rows[0]['case_id']]
    full = build_submission(bundle, method=method, predictions=rows, case_ids=selected)
    rows[0]['record'] = api().project_record(suite, record)
    compact = build_submission(bundle, method=method, predictions=rows, case_ids=selected)
    assert prepare_inputs(bundle, compact) == prepare_inputs(bundle, full)
    assert compact['coverage'][status] == 1
    assert compact['coverage']['generation_complete'] is True
