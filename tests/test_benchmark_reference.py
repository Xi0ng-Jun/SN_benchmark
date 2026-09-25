"""Reference methods use public inputs, whole units and durable observations."""
import copy
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

from rag_eval.notebook_bundle import prepare


def frozen(tmp_path, suite='qmsum', *, questions=1, multihop_source='Public News'):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = dict(dataset=suite, split='train' if suite == 'multihop_rag' else 'test',
                  revision='fixture', source_url='https://example.org/data', license='fixture')
    corpus = None
    if suite == 'qmsum':
        raw = [dict(meeting_transcripts=[dict(speaker='A', content='apple'),
                    dict(speaker='B: editor', content=' \n '), dict(speaker='C', content='pear\nSecond line.')],
                    general_query_list=[dict(query='apple', answer='GOLD_SECRET') for _ in range(questions)],
                    specific_query_list=[])]
    elif suite == 'qasper':
        raw = {'p': dict(title='Paper title', abstract='Introduction.',
               full_text=[dict(section_name='Results', paragraphs=['apple\nSecond sentence.', 'pear'])],
               qas=[dict(question_id='q1', question='apple', answers=[dict(answer=dict(
                   unanswerable=False, extractive_spans=['GOLD_SECRET'], free_form_answer='',
                   yes_no=None, evidence=['apple\nSecond sentence.']))])])}
    elif suite == 'multihop_rag':
        raw = [dict(query='December apple', answer='GOLD_SECRET', question_type='inference_query',
                    evidence_list=[dict(title='News', fact='one two three')])]
        corpus = [dict(title='News', url='https://example.org/news', source=multihop_source,
                       published_at='December 3, 2023', body='one two three four five six seven')]
    else:
        source.update(task='asqa', retriever='gtr', variant='ordinary')
        raw = [dict(question='apple', qa_pairs=[dict(short_answers=['GOLD_SECRET'])],
                    docs=[dict(title='First', text='pear'), dict(title='Second', text='apple'),
                          dict(title='First', text='pear')])]
    raw_path, source_path = tmp_path/'raw', tmp_path/'source.json'
    raw_path.write_text('\n'.join(json.dumps(x) for x in raw) if suite == 'qmsum' else json.dumps(raw))
    source_path.write_text(json.dumps(source))
    corpus_path = tmp_path/'corpus.json'
    if corpus is not None:
        corpus_path.write_text(json.dumps(corpus))
    bundle = prepare(suite, raw_path, source_path, tmp_path/'bundle',
                     corpus_path=corpus_path if corpus is not None else None,
                     adaptation_revision='notebook-data-v3')
    return bundle


def api():
    # Missing module is represented as a feature failure during the first RED run.
    import importlib.util
    assert importlib.util.find_spec('rag_eval.benchmark_reference') is not None, 'reference runner is missing'
    from rag_eval import benchmark_reference
    return benchmark_reference


def test_bm25_score_has_independent_hand_calculated_reference():
    reference = api()
    units = [dict(unit_id='u0', document_id='a', text='apple', ordinal=0),
             dict(unit_id='u1', document_id='b', text='pear', ordinal=1)]
    rows = reference.rank_bm25('apple', units)
    assert rows[0]['unit_id'] == 'u0'
    assert rows[0]['score'] == pytest.approx(math.log(2))
    assert rows[1]['score'] == 0


def test_reusable_bm25_index_scores_tf_length_and_unique_query_terms():
    reference = api()
    assert hasattr(reference, 'BM25Index'), 'shared corpora need a reusable public-text index'
    units = [dict(unit_id='u0', document_id='a', text='apple apple pear', ordinal=0),
             dict(unit_id='u1', document_id='b', text='apple', ordinal=1),
             dict(unit_id='u2', document_id='c', text='pear pear', ordinal=2)]
    original = copy.deepcopy(units)
    index = reference.BM25Index(units)
    ranked = index.rank('apple apple')
    assert [row['unit_id'] for row in ranked] == ['u1', 'u0', 'u2']
    assert ranked[0]['score'] == pytest.approx(math.log(1.6) * 2.5 / 1.9375)
    assert ranked[1]['score'] == pytest.approx(math.log(1.6) * 5 / 4.0625)
    assert index.rank('apple') == ranked
    assert index.rank('pear')[0]['unit_id'] == 'u2'
    assert units == original


@pytest.mark.parametrize('suite', ['qasper', 'qmsum', 'multihop_rag', 'alce'])
def test_plan_does_not_depend_on_scoring_labels(tmp_path, suite):
    reference = api()
    bundle = frozen(tmp_path, suite)
    configuration = reference.reference_config(top_k=2, max_context_chars=10000)
    first = reference.plan_reference(bundle, configuration=configuration)
    changed = copy.deepcopy(bundle)
    for case in changed['cases']:
        case.update(gold={'DO_NOT_READ': 'REPLACED'}, references=['REPLACED'], gold_document_ids=['REPLACED'])
        if suite in {'qasper', 'multihop_rag'}:
            case['task'] = 'null_query'
    second = reference.plan_reference(changed, configuration=configuration)
    assert first == second
    assert 'GOLD_SECRET' not in json.dumps(first)
    assert first[0]['original_question'] == bundle['cases'][0]['question']


def test_qmsum_keeps_complete_turn_and_omits_empty_turn(tmp_path):
    reference = api()
    request = reference.plan_reference(frozen(tmp_path), configuration=reference.reference_config(top_k=10))[0]
    assert [u['source_unit_id'] for u in request['retrieval']['ranked']] == ['turn:0', 'turn:2']
    assert 'pear\nSecond line.' in request['context']
    assert '[turn 1]' not in request['context']


def test_multihop_chunks_repeat_public_metadata_and_record_exact_spans(tmp_path):
    reference = api()
    request = reference.plan_reference(frozen(tmp_path, 'multihop_rag'), configuration=
        reference.reference_config(top_k=10, chunk_window=3, chunk_overlap=1))[0]
    rows = request['retrieval']['ranked']
    assert [r['body_text'] for r in rows] == ['one two three', 'three four five', 'five six seven']
    assert all('Public News' in r['text'] and 'December 3, 2023' in r['text'] for r in rows)
    assert [r['token_span'] for r in rows] == [[0, 3], [2, 5], [4, 7]]


def test_multihop_metadata_internal_blank_lines_cannot_change_body_boundary(tmp_path):
    reference = api()
    bundle = frozen(tmp_path, 'multihop_rag', multihop_source='Public\n\nNews')
    request = reference.plan_reference(bundle, configuration=reference.reference_config(
        top_k=10, chunk_window=3, chunk_overlap=1))[0]
    assert [row['body_text'] for row in request['retrieval']['ranked']] == [
        'one two three', 'three four five', 'five six seven']
    assert all('source: Public\n\nNews\npublished_at: December 3, 2023' in row['text']
               for row in request['retrieval']['ranked'])


def test_alce_numeric_labels_keep_original_candidate_positions_and_duplicates(tmp_path):
    reference = api()
    bundle = frozen(tmp_path, 'alce')
    request = reference.plan_reference(bundle, configuration=reference.reference_config(top_k=1))[0]
    assert request['retrieval']['selected'][0]['citation_index'] == 2
    assert '[2]' in request['context'] and '[1]' not in request['context']
    assert request['citation_index_to_document_id']['2'] == bundle['cases'][0]['candidate_documents'][1]['id']
    all_candidates = reference.plan_reference(bundle, configuration=
        reference.reference_config('candidate-topk', top_k=3))[0]
    assert [r['citation_index'] for r in all_candidates['retrieval']['selected']] == [1, 2, 3]


def test_budget_never_truncates_a_unit_and_full_context_refuses_overflow(tmp_path):
    reference = api()
    bundle = frozen(tmp_path)
    bm25 = reference.plan_reference(bundle, configuration=reference.reference_config(max_context_chars=1))[0]
    assert bm25['status'] == 'error' and bm25['retrieval']['selected'] == []
    assert bm25['reason'] == 'no_unit_within_context_budget'
    full = reference.plan_reference(bundle, configuration=
        reference.reference_config('full-context', max_context_chars=1))[0]
    assert full['status'] == 'error' and full['reason'] == 'full_context_exceeds_budget'
    assert full['context'] is None and full['retrieval']['context_chars'] > 1
    complete = reference.plan_reference(bundle, configuration=
        reference.reference_config('full-context', max_context_chars=10000))[0]
    assert 'pear\nSecond line.' in complete['context'] and '[turn 1]' in complete['context']


def test_qasper_evidence_ids_must_be_selected_by_model_and_actually_sent(tmp_path):
    reference = api()
    bundle = frozen(tmp_path/'data', 'qasper')
    def generate(prompt, schema, *, case_id):
        assert 'evidence_unit_ids' in schema.model_json_schema()['properties']
        return {'answer': 'apple', 'evidence_unit_ids': ['u1', 'u999']}
    run = reference.run_reference(bundle, tmp_path/'run', generate=generate,
        model_identity={'model_id': 'fake-transport'}, configuration=reference.reference_config(top_k=1))
    submission = json.loads((run/'submission.json').read_text())
    row = submission['predictions'][0]
    evidence = row['record']['predicted_evidence']
    assert evidence[0] == 'apple\nSecond sentence.' and len(evidence) == 2
    assert evidence[1].startswith('\x00invalid-unit:')
    assert row['record']['valid_evidence_unit_ids'] == ['u1']
    assert row['record']['invalid_evidence_unit_ids'] == ['u999']
    assert row['record']['prompt'] and row['record']['context']


@pytest.mark.parametrize('ids,gold,expected', [
    (['u1', 'u999'], ['apple\nSecond sentence.'], 2/3),
    (['u999'], [], 0),
    (['u1', 'u1'], ['apple\nSecond sentence.'], 2/3),
])
def test_qasper_bad_and_repeated_evidence_remain_false_positive_slots(tmp_path, ids, gold, expected):
    reference = api()
    bundle = frozen(tmp_path/'data', 'qasper')
    run = reference.run_reference(bundle, tmp_path/'run',
        generate=lambda *a, **k: dict(answer='answer', evidence_unit_ids=ids),
        model_identity={'model_id': 'fake'}, configuration=reference.reference_config(top_k=1))
    record = json.loads((run/'submission.json').read_text())['predictions'][0]['record']
    predicted = record['predicted_evidence']
    # Algebraic form of QASPER's exact-set-overlap numerator and list-length denominator.
    actual = 2 * len(set(predicted) & set(gold)) / (len(predicted) + len(gold)) if predicted or gold else 1
    assert actual == pytest.approx(expected)
    assert record['model_response']['evidence_unit_ids'] == ids
    assert len(predicted) == len(ids)


def test_invalid_evidence_namespace_avoids_even_unselected_public_paragraphs(tmp_path):
    reference = api()
    frozen(tmp_path/'data', 'qasper')
    raw_path = tmp_path/'data/raw'
    raw = json.loads(raw_path.read_text())
    raw['p']['full_text'][0]['paragraphs'][1] = '\x00invalid-unit:public-paragraph'
    raw_path.write_text(json.dumps(raw))
    bundle = prepare('qasper', raw_path, tmp_path/'data/source.json', tmp_path/'new-bundle',
                     adaptation_revision='notebook-data-v3')
    configuration = reference.reference_config(top_k=1)
    request = reference.plan_reference(bundle, configuration=configuration)[0]
    assert request['invalid_evidence_prefix'] == '\x00invalid-unit::'
    run = reference.run_reference(bundle, tmp_path/'run', configuration=configuration,
        model_identity={'model_id': 'fake'}, generate=lambda *a, **k: dict(answer='answer', evidence_unit_ids=['public-paragraph']))
    evidence = json.loads((run/'submission.json').read_text())['predictions'][0]['record']['predicted_evidence'][0]
    assert all(evidence != unit['text'] for document in bundle['documents'] for unit in document['source_units'])


def test_model_failure_and_empty_answer_are_distinct_and_do_not_leak_exception(tmp_path):
    reference = api()
    bundle = frozen(tmp_path/'data', questions=2)
    calls = []
    def generate(prompt, schema, *, case_id):
        calls.append(case_id)
        if len(calls) == 1:
            raise RuntimeError('SECRET_ENDPOINT')
        return {'answer': ' '}
    run = reference.run_reference(bundle, tmp_path/'run', generate=generate,
        model_identity={'model_id': 'fake'}, configuration=reference.reference_config())
    saved = (run/'submission.json').read_text()
    assert [x['status'] for x in json.loads(saved)['predictions']] == ['error', 'no_answer']
    assert 'SECRET_ENDPOINT' not in saved


def test_interrupt_preserves_completed_outputs_and_marks_remaining_missing(tmp_path):
    reference = api()
    bundle = frozen(tmp_path/'data', questions=2)
    calls = []
    def generate(prompt, schema, *, case_id):
        assert (tmp_path/'run/planned.jsonl').exists()
        assert (tmp_path/'run/method.json').exists()
        calls.append(case_id)
        if len(calls) == 2:
            raise KeyboardInterrupt()
        return {'answer': 'observed answer'}
    with pytest.raises(KeyboardInterrupt):
        reference.run_reference(bundle, tmp_path/'run', generate=generate,
            model_identity={'model_id': 'fake'}, configuration=reference.reference_config())
    submission = json.loads((tmp_path/'run/submission.json').read_text())
    assert [x['status'] for x in submission['predictions']] == ['success', 'missing']
    assert len((tmp_path/'run/outputs.jsonl').read_text().splitlines()) == 1
    assert json.loads((tmp_path/'run/state.json').read_text())['phase'] == 'interrupted'


def test_run_refuses_legacy_bundle_existing_output_and_bad_selection(tmp_path):
    reference = api()
    bundle = frozen(tmp_path/'data')
    config = reference.reference_config()
    old = copy.deepcopy(bundle)
    old['manifest']['adaptation_revision'] = 'notebook-data-v2'
    with pytest.raises(ValueError, match='v3'):
        reference.plan_reference(old, configuration=config)
    with pytest.raises(ValueError, match='case'):
        reference.plan_reference(bundle, configuration=config, case_ids=['unknown'])
    target = tmp_path/'exists'
    target.mkdir()
    with pytest.raises(ValueError, match='new'):
        reference.run_reference(bundle, target, configuration=config, model_identity={'model_id': 'fake'},
                                generate=lambda *a, **k: {'answer': 'x'})


def test_subset_stays_explicit_and_configuration_changes_method_identity(tmp_path):
    reference = api()
    bundle = frozen(tmp_path/'data', questions=2)
    selected = [bundle['cases'][1]['case_id']]
    method_ids = []
    for top_k in (1, 2):
        run = reference.run_reference(bundle, tmp_path/str(top_k),
            configuration=reference.reference_config(top_k=top_k), model_identity={'model_id': 'fake'},
            case_ids=selected, generate=lambda *a, **k: {'answer': 'answer'})
        submission = json.loads((run/'submission.json').read_text())
        assert submission['scope'] == 'subset' and submission['case_ids'] == selected
        assert submission['coverage']['planned'] == 1
        method_ids.append(submission['method_id'])
    assert len(set(method_ids)) == 2


def test_injected_callback_is_explicit_and_identified_implementations_change_method(tmp_path):
    reference = api()
    bundle = frozen(tmp_path/'data')
    identities = []
    for number, implementation in enumerate([None, {'code_sha256': '1'*64}, {'code_sha256': '2'*64}]):
        run = reference.run_reference(bundle, tmp_path/f'run{number}',
            generate=lambda *a, **k: {'answer': 'answer'}, implementation_identity=implementation,
            model_identity={'model_id': 'fake'}, configuration=reference.reference_config())
        saved = json.loads((run/'submission.json').read_text())
        identities.append(saved['method_id'])
        observed = saved['method']['configuration']['implementation']
        assert observed['execution'].startswith('injected-callback')
        assert len(observed['reference_sha256']) == 64
        assert observed['identity'] == implementation
    assert len(set(identities)) == 3


def test_failed_runtime_initialization_cannot_claim_a_final_implementation_identity(tmp_path):
    reference = api()
    bundle = frozen(tmp_path/'data')
    def fail(destination):
        assert (destination/'planned.jsonl').exists()
        raise RuntimeError('PRIVATE_RUNTIME_ADDRESS')
    with pytest.raises(RuntimeError):
        reference.run_reference(bundle, tmp_path/'run', initialize=fail,
            model_identity={'model_id': 'fake'}, configuration=reference.reference_config())
    saved = (tmp_path/'run/submission.json').read_text()
    result = json.loads(saved)
    assert result['method']['configuration']['implementation']['execution'] == 'initialization-incomplete'
    assert result['coverage']['missing'] == 1
    assert 'PRIVATE_RUNTIME_ADDRESS' not in saved
    assert json.loads((tmp_path/'run/state.json').read_text())['phase'] == 'failed'


def test_refuses_oracle_candidates_and_non_alce_candidate_topk(tmp_path):
    reference = api()
    bundle = frozen(tmp_path/'alce', 'alce')
    bundle['manifest']['source']['variant'] = 'oracle'
    with pytest.raises(ValueError, match='ordinary'):
        reference.plan_reference(bundle, configuration=reference.reference_config())
    with pytest.raises(ValueError, match='ALCE'):
        reference.plan_reference(frozen(tmp_path/'qmsum'), configuration=reference.reference_config('candidate-topk'))


def test_cli_requires_explicit_model_and_product_configuration():
    script = Path(__file__).parents[1]/'scripts/run_benchmark_reference.py'
    assert script.exists(), 'reference CLI is missing'
    result = subprocess.run([sys.executable, str(script), '--bundle', 'unused', '--run-dir', 'unused'],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert '--model-config' in result.stderr and '--project-root' in result.stderr


def test_full_context_success_keeps_original_bytes_with_only_evidence_markers_added(tmp_path):
    reference = api()
    bundle = frozen(tmp_path, 'qasper')
    request = reference.plan_reference(bundle, configuration=
        reference.reference_config('full-context', max_context_chars=10000))[0]
    import re
    unmarked = re.sub(r'\[unit u\d+\]\n', '', request['context'])
    assert bundle['documents'][0]['text'] in unmarked
    assert request['evidence_unit_text'] == {'u0': 'Introduction.', 'u1': 'apple\nSecond sentence.', 'u2': 'pear'}


def test_oversized_complete_context_is_not_copied_into_every_rejected_request(tmp_path):
    reference = api()
    bundle = frozen(tmp_path/'data', questions=2)
    def never_generate(*args, **kwargs):
        pytest.fail('an oversized full-context request must never call the model')
    run = reference.run_reference(bundle, tmp_path/'run', generate=never_generate,
        model_identity={'model_id': 'fake'}, configuration=reference.reference_config('full-context', max_context_chars=1))
    requests = [json.loads(line) for line in (run/'requests.jsonl').read_text().splitlines()]
    assert all(row['prompt'] is None and row['context'] is None for row in requests)
    assert all(row['retrieval']['context_chars'] > 1 for row in requests)
    assert (run/'public-documents.jsonl').exists()
    saved_documents = [json.loads(line) for line in (run/'public-documents.jsonl').read_text().splitlines()]
    assert saved_documents == bundle['documents']
    assert 'Second line.' not in (run/'requests.jsonl').read_text()


@pytest.mark.parametrize('response', [{'answer': 4}, {'answer': 'x', 'unexpected': 'value'}])
def test_strict_output_schema_rejects_malformed_model_answers(tmp_path, response):
    reference = api()
    run = reference.run_reference(frozen(tmp_path/'data'), tmp_path/'run',
        generate=lambda *args, **kwargs: response, model_identity={'model_id': 'fake'},
        configuration=reference.reference_config())
    row = json.loads((run/'submission.json').read_text())['predictions'][0]
    assert row['status'] == 'error' and row['prediction'] == ''
    assert json.loads(row['record']['raw_model_response_json']) == response


def test_cli_runs_actual_adapter_with_only_external_client_replaced(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from rag_eval import starter_runtime
    from rag_eval.starter_model import ExplicitBenchmarkModel
    from rag_eval.benchmark_submission import validate_submission
    root = Path(__file__).parents[1]
    bundle = frozen(tmp_path/'data', 'alce')
    product = tmp_path/'product'
    product.mkdir()
    subprocess.run(['git', 'init', str(product)], check=True, capture_output=True)
    (product/'fixture').write_text('test product source')
    subprocess.run(['git', '-C', str(product), 'add', 'fixture'], check=True)
    subprocess.run(['git', '-C', str(product), '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.org',
                    'commit', '-m', 'fixture'], check=True, capture_output=True)
    monkeypatch.setenv('REF_MODEL_URL', 'https://private.invalid/v1')
    monkeypatch.setenv('REF_MODEL_KEY', 'SECRET')
    config = tmp_path/'models.json'
    config.write_text(json.dumps({'tested': dict(model_id='test-model', base_url_env='REF_MODEL_URL',
        api_key_env='REF_MODEL_KEY', parameters=dict(temperature=0, top_p=1, max_tokens=100, max_retries=0, timeout=30))}))
    runtime_comparable = {'hash': 'c'*64}
    def configure(project, destination, **kwargs):
        return SimpleNamespace(), dict(settings_sha256=str(destination),
            comparable_settings_sha256=runtime_comparable['hash'], service_config_sha256=None,
            overrides={'DATABASE_URL': str(destination/'private')})
    monkeypatch.setattr(starter_runtime, 'configure_environment', configure)
    client_response = {'value': {'answer': 'apple [2]'}}
    class Client:
        def chat_json(self, messages, schema_hint, **kwargs):
            assert '[2]' in messages[0]['content'] and 'GOLD_SECRET' not in messages[0]['content']
            assert kwargs['max_tokens'] == 100 and kwargs['bypass_cache'] is True
            return client_response['value']
    monkeypatch.setattr(starter_runtime, 'make_adapter', lambda spec, role, settings, sink:
        ExplicitBenchmarkModel(Client(), model_id=spec['model_id'], role=role, parameters=spec['parameters'],
                               config_sha256=spec['config_sha256'], sink=sink))
    monkeypatch.syspath_prepend(str(root))
    from scripts.run_benchmark_reference import main
    run = main(['--bundle', str(tmp_path/'data/bundle'), '--run-dir', str(tmp_path/'run'),
                '--model-config', str(config), '--project-root', str(product), '--top-k', '1'])
    submission = json.loads((run/'submission.json').read_text())
    assert validate_submission(bundle, submission)['predictions'][0]['prediction'] == 'apple [2]'
    assert (run/'product-source.tar').exists() and (run/'source-identity.json').exists()
    events = [json.loads(line) for line in (run/'model-events.jsonl').read_text().splitlines()]
    assert [event['event'] for event in events] == ['started', 'completed']
    assert events[0]['schema']['properties']['answer']['type'] == 'string'
    assert 'SECRET' not in (run/'method.json').read_text()
    assert submission['method']['configuration']['request_revision'] == 'notebook-request-v3'
    implementation = submission['method']['configuration']['implementation']
    assert implementation['execution'] == 'cli-snapshot'
    assert implementation['identity']['sources'] == json.loads((run/'source-identity.json').read_text())
    assert implementation['identity']['runtime']['comparable_settings_sha256'] == 'c'*64
    assert str(tmp_path) not in json.dumps(implementation)
    method_ids = [submission['method_id']]
    for number in (2, 3, 4):
        if number == 3:
            (product/'fixture').write_text('changed actual product client source')
            subprocess.run(['git', '-C', str(product), 'add', 'fixture'], check=True)
            subprocess.run(['git', '-C', str(product), '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.org',
                            'commit', '-m', 'second implementation'], check=True, capture_output=True)
        if number == 4:
            runtime_comparable['hash'] = 'd'*64
        other = main(['--bundle', str(tmp_path/'data/bundle'), '--run-dir', str(tmp_path/f'run{number}'),
                      '--model-config', str(config), '--project-root', str(product), '--top-k', '1'])
        method_ids.append(json.loads((other/'submission.json').read_text())['method_id'])
    assert method_ids[0] == method_ids[1]
    assert method_ids[1] != method_ids[2] != method_ids[3]
    client_response['value'] = {'answer': 'visible but invalid answer', 'unexpected': 'field'}
    failed = main(['--bundle', str(tmp_path/'data/bundle'), '--run-dir', str(tmp_path/'invalid-output'),
                   '--model-config', str(config), '--project-root', str(product), '--top-k', '1'])
    observed = json.loads((failed/'submission.json').read_text())['predictions'][0]
    assert observed['status'] == 'error' and observed['prediction'] == ''
    failed_event = json.loads((failed/'model-events.jsonl').read_text().splitlines()[-1])
    assert failed_event['event'] == 'failed' and failed_event['phase'] == 'schema_parse'
    assert failed_event['raw_client_response'] == client_response['value']
