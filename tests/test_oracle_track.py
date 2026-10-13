"""Oracle isolation and persistence contracts, with an offline generator only."""
import importlib
import json
from copy import deepcopy

import pytest

from rag_eval.notebook_data import adapt
from test_notebook_data_v3 import DATA_V3, qasper_raw


def fixture():
    data = adapt('qasper', qasper_raw(), adaptation_revision=DATA_V3)
    case = data['cases'][0]
    document = data['documents'][0]
    product = {'manifest': {'request_revision': 'notebook-request-v3'},
               'documents': [document],
               'questions': [{'case_id': case['case_id'], 'question': case['question'],
                              'material_document_ids': [document['id']]}]}
    evidence = {case['case_id']: [{'document_id': document['id'], 'source_unit_id': '0:0'}]}
    return product, evidence, case


def api():
    return importlib.import_module('rag_eval.oracle_track')


def test_only_selected_public_gold_units_reach_generation():
    product, evidence, _ = fixture()
    before = deepcopy(product)
    prepared = api().prepare_oracle_inputs(product, evidence)
    request, = prepared['requests']
    assert request['retrieval_context'] == ['Birds fly.']
    assert 'Fish swim.' not in json.dumps(request)
    assert 'gold' not in request['question'] and 'references' not in request['question']
    assert product == before
    assert prepared['kind'] == 'diagnostic/oracle'


def test_empty_evidence_retains_case_and_never_falls_back_to_full_corpus():
    product, evidence, case = fixture()
    evidence[case['case_id']] = []
    request, = api().prepare_oracle_inputs(product, evidence)['requests']
    assert request['case_id'] == case['case_id']
    assert request['retrieval_context'] == []


@pytest.mark.parametrize('mutation', ['unknown_unit', 'answer_field', 'missing_case', 'legacy'])
def test_invalid_or_leaky_input_fails_before_generation(mutation):
    product, evidence, case = fixture()
    if mutation == 'unknown_unit':
        evidence[case['case_id']][0]['source_unit_id'] = 'unknown'
    elif mutation == 'answer_field':
        product['questions'][0]['expected_answer'] = 'SECRET ANSWER'
    elif mutation == 'missing_case':
        evidence.clear()
    else:
        product['manifest']['request_revision'] = 'notebook-request-v1'
    with pytest.raises(ValueError):
        api().prepare_oracle_inputs(product, evidence)


def test_diagnostic_manifest_and_answers_exist_before_next_generation(tmp_path):
    product, evidence, case = fixture()
    other = dict(product['questions'][0], case_id='qasper:second')
    product['questions'].append(other)
    evidence[other['case_id']] = []
    source = tmp_path / 'source'; source.mkdir()
    (source / 'product-bundle.json').write_text(json.dumps(product))
    destination = tmp_path / 'oracle'
    observed = []

    def generate(request):
        manifest = json.loads((destination / 'manifest.json').read_text())
        assert manifest['diagnostic'] is True
        assert manifest['kind'] == 'diagnostic/oracle'
        if observed:
            saved = [json.loads(x) for x in (destination / 'outputs.jsonl').read_text().splitlines()]
            assert saved[0]['prediction'] == 'Birds'
            raise RuntimeError('SECRET provider address must never be copied')
        observed.append(request)
        return {'status': 'success', 'prediction': 'Birds', 'captures': [{'actual': True}]}

    result = api().execute_oracle_track(source, evidence, destination, generate=generate)
    outputs = [json.loads(x) for x in (destination / 'outputs.jsonl').read_text().splitlines()]
    assert [x['status'] for x in outputs] == ['success', 'error']
    assert outputs[0]['captures'] == [{'actual': True}]
    assert 'SECRET' not in (destination / 'outputs.jsonl').read_text()
    assert not (destination / 'scores.json').exists()
    assert result['phase'] == 'finished'
    with pytest.raises(ValueError, match='diagnostic'):
        api().require_formal_run(json.loads((destination / 'manifest.json').read_text()))
    with pytest.raises(FileExistsError):
        api().execute_oracle_track(source, evidence, destination, generate=generate)


def test_evaluator_adapter_uses_evidence_not_reference_answer():
    product, _, case = fixture()
    case['references'] = ['SECRET ANSWER']
    evidence = api().gold_evidence_from_cases(product, [case])
    prepared = api().prepare_oracle_inputs(product, evidence)
    assert 'SECRET ANSWER' not in json.dumps(prepared)


def test_export_guard_rejects_each_diagnostic_marker():
    for manifest in ({'diagnostic': True}, {'kind': 'diagnostic/oracle'}, {'track': 'oracle'},
                     {'identity': {'diagnostic': True}}):
        with pytest.raises(ValueError, match='diagnostic'):
            api().require_formal_run(manifest)
    api().require_formal_run({'track': 'R', 'mode': 'chunk'})


@pytest.mark.parametrize('marker', [{'diagnostic': True}, {'kind': 'diagnostic/oracle'},
                                  {'format': 'sn-oracle-run-v1'}, {'format': 'sn-oracle-inputs-v1'}])
def test_formal_manifest_cannot_hide_oracle_marker(marker):
    formal = {'format': 'public-starter-run-v1', 'track': 'R', 'mode': 'chunk',
              'diagnostic': False, 'identity': {'notebook_context': {'request_revision': 'notebook-request-v3'}}}
    formal.update(marker)
    with pytest.raises(ValueError, match='diagnostic'):
        api().require_formal_run(formal)


def test_cancellation_preserves_completed_answer_and_pending_plan(tmp_path):
    product, evidence, _ = fixture()
    product['questions'].append(dict(product['questions'][0], case_id='qasper:later'))
    evidence['qasper:later'] = []
    source = tmp_path / 'source'; source.mkdir()
    (source / 'product-bundle.json').write_text(json.dumps(product))
    destination = tmp_path / 'oracle'
    calls = []

    def generate(request):
        if calls:
            raise KeyboardInterrupt()
        calls.append(request)
        return {'status': 'no_answer', 'prediction': ''}

    with pytest.raises(KeyboardInterrupt):
        api().execute_oracle_track(source, evidence, destination, generate=generate)
    state = json.loads((destination / 'state.json').read_text())
    assert state['phase'] == 'failed' and state['completed'] == 1 and state['planned'] == 2
    assert len((destination / 'planned.jsonl').read_text().splitlines()) == 2
    assert json.loads((destination / 'outputs.jsonl').read_text())['status'] == 'no_answer'
    readback = api().read_oracle_run(destination)
    assert readback['metric_role'] == 'diagnostic'
    assert [c['status'] for c in readback['cases']] == ['no_answer', 'missing']
    assert readback['cases'][1]['prediction'] is None


def test_multihop_fact_selects_source_paragraph_without_annotation_text():
    product = {'documents': [{'id': 'd', 'text': 'Heading\n\nThe birds fly over water.\n\nFish swim.',
                              'source_units': [{'id': 'body', 'kind': 'body',
                                                'text': 'The birds fly over water.\n\nFish swim.'}]}],
               'questions': [{'case_id': 'c', 'question': 'Where?', 'material_document_ids': ['d']}],
               'manifest': {'request_revision': 'notebook-request-v3'}}
    case = {'case_id': 'c', 'suite': 'multihop_rag',
            'gold': {'evidence': [{'document_id': 'd', 'fact': 'birds fly'}], 'answer': 'SECRET'}}
    prepared = api().prepare_oracle_inputs(product, api().gold_evidence_from_cases(product, [case]))
    assert prepared['requests'][0]['retrieval_context'] == ['The birds fly over water.']
    assert 'SECRET' not in json.dumps(prepared)


def test_qmsum_spans_are_inclusive_and_overlap_does_not_repeat_units():
    product = {'documents': [{'id': 'd', 'text': '[turn 0] A: start\n\n[turn 1] B: middle\n\n[turn 2] A: end',
                              'source_units': [{'id': f'turn:{i}', 'text': text} for i, text in enumerate(
                                  ['[turn 0] A: start', '[turn 1] B: middle', '[turn 2] A: end'])]}],
               'questions': [{'case_id': 'c', 'question': 'Summarize.', 'material_document_ids': ['d']}],
               'manifest': {'request_revision': 'notebook-request-v3'}}
    case = {'case_id': 'c', 'suite': 'qmsum', 'task': 'specific',
            'material_document_ids': ['d'], 'gold': {'relevant_text_span': [[0, 1], [1, 2]]}}
    prepared = api().prepare_oracle_inputs(product, api().gold_evidence_from_cases(product, [case]))
    assert prepared['requests'][0]['retrieval_context'] == ['[turn 0] A: start', '[turn 1] B: middle', '[turn 2] A: end']


def test_unmappable_gold_fails_before_creating_artifact_directory(tmp_path):
    product, evidence, case = fixture()
    evidence[case['case_id']][0]['source_unit_id'] = 'absent'
    source = tmp_path / 'source'; source.mkdir()
    (source / 'product-bundle.json').write_text(json.dumps(product))
    calls = []
    with pytest.raises(ValueError):
        api().execute_oracle_track(source, evidence, tmp_path / 'oracle', generate=calls.append)
    assert calls == [] and not (tmp_path / 'oracle').exists()


def test_oracle_output_cannot_be_written_into_immutable_store(tmp_path):
    product, evidence, _ = fixture()
    source = tmp_path / 'source'; source.mkdir()
    (source / 'product-bundle.json').write_text(json.dumps(product))
    store = tmp_path / 'store'
    (store / 'objects' / 'bundle').mkdir(parents=True)
    destination = store / 'new-oracle'
    with pytest.raises(ValueError, match='shared artifact store'):
        api().execute_oracle_track(source, evidence, destination, generate=lambda _: {})
    assert not destination.exists()


def test_oracle_defaults_to_original_run_case_scope_not_whole_partition(tmp_path):
    product, evidence, case = fixture()
    product['questions'].append(dict(product['questions'][0], case_id='qasper:not-in-run'))
    source = tmp_path / 'source'; source.mkdir()
    (source / 'product-bundle.json').write_text(json.dumps(product))
    (source / 'manifest.json').write_text(json.dumps({'run_id': 'original', 'identity': {
        'notebook_context': {'case_ids': [case['case_id']]}}}))
    calls = []

    def generate(request):
        calls.append(request['case_id'])
        return {'status': 'success', 'prediction': 'Birds'}

    api().execute_oracle_track(source, evidence, tmp_path / 'oracle', generate=generate)
    assert calls == [case['case_id']]
    assert json.loads((tmp_path / 'oracle/manifest.json').read_text())['source_run_id'] == 'original'


def test_oracle_readback_refuses_to_invent_actual_evidence_from_intended_context(tmp_path):
    product, evidence, _ = fixture()
    source = tmp_path / 'source'; source.mkdir()
    (source / 'product-bundle.json').write_text(json.dumps(product))
    destination = tmp_path / 'oracle'
    api().execute_oracle_track(source, evidence, destination,
                              generate=lambda _: {'status': 'success', 'prediction': 'Birds'})
    before = {p.name: p.read_bytes() for p in destination.iterdir()}
    readback = api().read_oracle_run(destination)
    assert readback['cases'][0]['prediction'] == 'Birds'
    assert 'retrieval_context' not in readback['outputs'][0]
    assert readback['inputs']['requests'][0]['retrieval_context'] == ['Birds fly.']
    assert {p.name: p.read_bytes() for p in destination.iterdir()} == before
    manifest_path = destination / 'manifest.json'
    manifest = json.loads(manifest_path.read_text()); manifest['format'] = 'public-starter-run-v1'
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        api().read_oracle_run(destination)
