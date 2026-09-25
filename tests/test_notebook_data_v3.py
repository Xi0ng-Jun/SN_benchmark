"""The complete public inputs must be independent of private scoring labels."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from rag_eval.artifacts import digest
from rag_eval.notebook_bundle import load_bundle, partition_bundle, prepare
from rag_eval.notebook_data import adapt


DATA_V3 = 'notebook-data-v3'
REQUEST_V3 = 'notebook-request-v3'


def qasper_raw():
    # Original v0.3 JSON uses lists of objects. HF Sequence exports may instead
    # transpose these into columns; this adapter consumes the original release.
    answer = dict(unanswerable=False, extractive_spans=['SECRET-GOLD'],
                  free_form_answer='', yes_no=None, evidence=['Birds fly.'],
                  highlighted_evidence=['Birds fly.'])
    return {'p': dict(title='Paper title', abstract='Paper abstract.',
                     full_text=[dict(section_name='Results', paragraphs=['Birds fly.', 'Fish swim.'])],
                     figures_and_tables=[dict(caption='Figure 1: Bird counts.', file='figure.png'),
                                         dict(caption='Table 1: Fish counts.', file='table.png')],
                     qas=[dict(question_id='q', question='What flies?',
                               answers=[dict(answer=answer, annotation_id='a', worker_id='w')])])}


def news_raw():
    corpus = [dict(title='One', url='https://example.org/one', body='Alpha fact.', source='Daily News',
                   published_at='2023-09-26T19:11:30+00:00', author='Reporter', category='business'),
              dict(title='Two', url='https://example.org/two', body='Beta fact.',
                   published_at=None, author='')]
    raw = [dict(query='What fact was reported?', answer='SECRET-GOLD', question_type='inference_query',
                evidence_list=[dict(title='One', url=corpus[0]['url'], fact='Alpha fact.')])]
    return raw, corpus


def freeze(tmp_path, suite, raw, *, corpus=None, task=None, revision=DATA_V3):
    tmp_path.mkdir(parents=True, exist_ok=True)
    text = '\n'.join(json.dumps(row) for row in raw) if suite == 'qmsum' else json.dumps(raw)
    (tmp_path / 'raw').write_text(text)
    source = dict(dataset=suite, split='train' if suite == 'multihop_rag' else 'test',
                  revision='fixture', source_url='https://example.org/data', license='fixture')
    if task:
        source.update(task=task, retriever='gtr', variant='ordinary')
    (tmp_path / 'source').write_text(json.dumps(source))
    corpus_path = None
    if corpus is not None:
        corpus_path = tmp_path / 'corpus'
        corpus_path.write_text(json.dumps(corpus))
    return prepare(suite, tmp_path / 'raw', tmp_path / 'source', tmp_path / 'bundle',
                   corpus_path=corpus_path, adaptation_revision=revision)


def request(bundle):
    return partition_bundle(bundle, bundle['partitions'][0]['partition_id'], request_revision=REQUEST_V3)


def test_qasper_v3_keeps_float_unmapped_and_unanswerable_annotations():
    raw = qasper_raw()
    for sample, evidence in [('float', 'FLOAT SELECTED: Figure 1'), ('unmapped', 'Results ::: subsection')]:
        qa = copy.deepcopy(raw['p']['qas'][0])
        qa['question_id'] = sample
        qa['answers'][0]['answer']['evidence'] = [evidence]
        raw['p']['qas'].append(qa)
    unanswered = copy.deepcopy(raw['p']['qas'][0])
    unanswered['question_id'] = 'unanswerable'
    unanswered['answers'][0]['answer'].update(unanswerable=True, extractive_spans=[],
                                             evidence=['FLOAT SELECTED: Table 1'])
    raw['p']['qas'].append(unanswered)
    before = copy.deepcopy(raw)
    adapted = adapt('qasper', raw, adaptation_revision=DATA_V3)
    assert [case['sample_id'] for case in adapted['cases']] == ['q', 'float', 'unmapped', 'unanswerable']
    assert {row['status'] for row in adapted['decisions']} == {'selected'}
    assert {case['task'] for case in adapted['cases']} == {'qa'}
    assert adapted['cases'][-1]['references'] == ['Unanswerable']
    for case, qa in zip(adapted['cases'], raw['p']['qas']):
        assert case['gold']['raw_annotations'] == qa['answers']
    assert adapted['cases'][1]['gold']['annotations'][0]['evidence_mapping'][0]['match_method'] == 'unmapped'
    material = adapted['documents'][0]['text']
    for expected in ['Paper title', 'Paper abstract.', 'Results', 'Birds fly.', 'Fish swim.',
                     'Figure 1: Bird counts.', 'Table 1: Fish counts.']:
        assert expected in material
    assert 'SECRET-GOLD' not in material
    assert 'FLOAT SELECTED' not in material
    assert raw == before


def test_qasper_v3_gold_mutations_cannot_change_materials_or_generation_request(tmp_path):
    raw = qasper_raw()
    original = request(freeze(tmp_path / 'before', 'qasper', raw))
    raw['p']['qas'][0]['answers'][0]['answer'].update(
        unanswerable=True, extractive_spans=[], evidence=['FLOAT SELECTED: SECRET-EVIDENCE'])
    changed = request(freeze(tmp_path / 'after', 'qasper', raw))
    assert original['documents'] == changed['documents']
    assert original['questions'] == changed['questions']
    assert original['manifest']['source_manifest_fingerprint'] != changed['manifest']['source_manifest_fingerprint']
    question = original['questions'][0]
    assert question['task'] == 'qa'
    assert question['request_revision'] == REQUEST_V3
    assert 'Unanswerable' in question['question']
    assert 'short answer' in question['question'].lower()
    assert not {'references', 'expected_answer', 'gold_document_ids', 'gold'} & question.keys()
    assert 'SECRET' not in json.dumps(question)


def test_multihop_v3_imports_public_metadata_and_records_missing_values():
    raw, corpus = news_raw()
    adapted = adapt('multihop_rag', raw, corpus=corpus, adaptation_revision=DATA_V3)
    assert len(adapted['documents']) == 2
    assert len(adapted['cases'][0]['material_document_ids']) == 2
    first, second = adapted['documents']
    for value in corpus[0].values():
        assert value in first['text']
    assert second['data_observations']['missing_metadata'] == {
        'source': 'absent', 'published_at': 'null', 'author': 'empty', 'category': 'absent'}
    assert 'None' not in second['text']
    assert 'unknown' not in second['text'].lower()
    assert 'SECRET-GOLD' not in json.dumps(adapted['documents'])


def test_multihop_public_units_keep_metadata_body_boundary_with_internal_blank_lines():
    raw, corpus = news_raw()
    corpus[0].update(title='News\n\ncontinued title', source='Daily\n\nNews', body='Alpha fact.\n\nMore body.')
    raw[0]['evidence_list'][0]['title'] = corpus[0]['title']
    document = adapt('multihop_rag', raw, corpus=corpus, adaptation_revision=DATA_V3)['documents'][0]
    assert document['source_units'] == [
        dict(id='metadata', kind='metadata', text='Title: News\n\ncontinued title\nURL: https://example.org/one\n'
             'source: Daily\n\nNews\npublished_at: 2023-09-26T19:11:30+00:00\nauthor: Reporter\ncategory: business'),
        dict(id='body', kind='body', text='Alpha fact.\n\nMore body.'),
    ]
    assert document['text'] == document['source_units'][0]['text'] + '\n\n' + document['source_units'][1]['text']
    raw[0].update(answer='DIFFERENT-GOLD', question_type='null_query', evidence_list=[])
    changed = adapt('multihop_rag', raw, corpus=corpus, adaptation_revision=DATA_V3)['documents'][0]
    assert changed == document
    assert 'source_units' not in adapt('multihop_rag', raw, corpus=corpus)['documents'][0]


@pytest.mark.parametrize('field', ['source', 'published_at', 'author', 'category'])
def test_multihop_v3_rejects_nontext_metadata(field):
    raw, corpus = news_raw()
    corpus[0][field] = ['invalid']
    with pytest.raises(ValueError, match=field):
        adapt('multihop_rag', raw, corpus=corpus, adaptation_revision=DATA_V3)


@pytest.mark.parametrize('suite,task', [('multihop_rag', None), ('qmsum', None),
                                       ('alce', 'asqa'), ('alce', 'qampari'), ('alce', 'eli5')])
def test_v3_requests_only_use_public_inputs_and_preserve_complete_materials(tmp_path, suite, task):
    corpus = None
    if suite == 'multihop_rag':
        raw, corpus = news_raw()
        changed_raw = copy.deepcopy(raw)
        changed_raw[0]['answer'] = 'REPLACEMENT-GOLD'
        changed_raw[0].update(question_type='null_query', evidence_list=[])
    elif suite == 'qmsum':
        raw = [dict(meeting_transcripts=[dict(speaker='A', content='First.'), dict(speaker='B', content=''),
                                         dict(speaker='A', content='Last.')],
                    general_query_list=[dict(query='Summary?', answer='SECRET-GOLD')],
                    specific_query_list=[dict(query='Decisions?', answer='SECRET-GOLD',
                                              relevant_text_span=[['0', '0']])])]
        changed_raw = copy.deepcopy(raw)
        changed_raw[0]['general_query_list'][0]['answer'] = 'REPLACEMENT-GOLD'
        changed_raw[0]['specific_query_list'][0].update(answer='REPLACEMENT-GOLD', relevant_text_span=[['2', '2']])
    else:
        raw = [dict(question='Facts?', docs=[dict(title='First', text='First fact.'),
                                            dict(title='Second', text='Second fact.')],
                    qa_pairs=[dict(short_answers=['SECRET-GOLD'])],
                    answers=[['SECRET-GOLD']], claims=['SECRET-GOLD'])]
        changed_raw = copy.deepcopy(raw)
        changed_raw[0].update(qa_pairs=[dict(short_answers=['REPLACEMENT-GOLD'])],
                              answers=[['REPLACEMENT-GOLD']], claims=['REPLACEMENT-GOLD'])
    original = freeze(tmp_path / 'before', suite, raw, corpus=corpus, task=task)
    changed = freeze(tmp_path / 'after', suite, changed_raw, corpus=corpus, task=task)
    first, second = request(original), request(changed)
    assert first['questions'] == second['questions']
    assert first['documents'] == second['documents']
    assert len(first['questions']) == (2 if suite == 'qmsum' else 1)
    for question in first['questions']:
        assert not {'references', 'expected_answer', 'gold_document_ids', 'gold'} & question.keys()
        assert 'SECRET-GOLD' not in json.dumps(question)
    if suite == 'qmsum':
        assert '[turn 1] B: ' in first['documents'][0]['text']
        assert '[turn 2] A: Last.' in first['documents'][0]['text']
        assert 'query-focused summary' in first['questions'][0]['question']
    elif suite == 'alce':
        assert [doc['title'] for doc in first['documents']] == ['First', 'Second']
        instruction = first['questions'][0]['question'].lower()
        assert 'cit' in instruction
        if task == 'qampari':
            assert 'comma-separated list' in instruction
        else:
            assert 'one paragraph' in instruction
            assert 'single line' in instruction
    else:
        assert original['cases'][0]['task'] == 'inference_query'
        assert changed['cases'][0]['task'] == 'null_query'
        assert first['questions'][0]['task'] == 'qa'
        assert 'concise' in first['questions'][0]['question']
        assert 'insufficient' in first['questions'][0]['question']


def test_v3_request_hides_answer_type_even_when_reading_v2_data(tmp_path):
    bundle = freeze(tmp_path, 'qasper', qasper_raw(), revision='notebook-data-v2')
    assert bundle['cases'][0]['task'] == 'extractive'
    product = request(bundle)
    assert product['questions'][0]['task'] == 'qa'
    assert 'SECRET-GOLD' not in json.dumps(product['questions'])
    assert 'Figure 1: Bird counts.' not in product['documents'][0]['text']
    assert load_bundle(tmp_path / 'bundle')['manifest']['adaptation_revision'] == 'notebook-data-v2'


def test_qasper_public_units_preserve_original_boundaries_and_ignore_gold():
    raw = qasper_raw()
    raw['p']['abstract'] = 'An abstract.\n\nStill the same abstract.'
    raw['p']['full_text'][0]['paragraphs'] = ['First line.\n\nSame paragraph.', '', 'Last paragraph.']
    expected = [
        dict(id='abstract', text='An abstract.\n\nStill the same abstract.', kind='abstract'),
        dict(id='0:0', text='First line.\n\nSame paragraph.', kind='paragraph'),
        dict(id='0:1', text='', kind='paragraph'),
        dict(id='0:2', text='Last paragraph.', kind='paragraph'),
        dict(id='figure_or_table:0', text='Figure 1: Bird counts.', kind='caption'),
        dict(id='figure_or_table:1', text='Table 1: Fish counts.', kind='caption'),
    ]
    original = adapt('qasper', raw, adaptation_revision=DATA_V3)
    assert original['documents'][0]['source_units'] == expected
    raw['p']['qas'][0]['answers'][0]['answer'].update(
        unanswerable=True, extractive_spans=[], evidence=['FLOAT SELECTED: HIDDEN'])
    changed = adapt('qasper', raw, adaptation_revision=DATA_V3)
    assert changed['documents'] == original['documents']
    assert 'HIDDEN' not in json.dumps(changed['documents'])
    assert 'source_units' not in adapt('qasper', raw)['documents'][0]


def test_document_hash_includes_original_units_even_when_joined_text_is_identical():
    raw = qasper_raw()
    raw['p']['full_text'][0]['paragraphs'] = ['One.\n\nTwo.', 'Three.']
    original = adapt('qasper', raw, adaptation_revision=DATA_V3)['documents'][0]
    raw['p']['full_text'][0]['paragraphs'] = ['One.', 'Two.\n\nThree.']
    changed = adapt('qasper', raw, adaptation_revision=DATA_V3)['documents'][0]
    assert original['text'] == changed['text']
    assert original['text_sha256'] == changed['text_sha256']
    assert original['document_sha256'] != changed['document_sha256']


def test_qmsum_public_units_preserve_multiline_and_empty_turns_without_gold(tmp_path):
    raw = [dict(meeting_transcripts=[dict(speaker='A', content='First sentence.\n\nSame turn.'),
                                     dict(speaker='B', content=''), dict(speaker='A', content='Last.'),
                                     dict(speaker='B: lead', content=' \t ')],
                general_query_list=[], specific_query_list=[dict(query='Decisions?', answer='SECRET-GOLD',
                                                                  relevant_text_span=[['0', '0']])])]
    original = freeze(tmp_path / 'before', 'qmsum', raw)
    assert original['documents'][0]['source_units'] == [
        dict(id='turn:0', text='[turn 0] A: First sentence.\n\nSame turn.', kind='turn', is_empty=False),
        dict(id='turn:1', text='[turn 1] B: ', kind='turn', is_empty=True),
        dict(id='turn:2', text='[turn 2] A: Last.', kind='turn', is_empty=False),
        dict(id='turn:3', text='[turn 3] B: lead:  \t ', kind='turn', is_empty=True),
    ]
    assert load_bundle(tmp_path / 'before/bundle') == original
    raw[0]['specific_query_list'][0].update(answer='DIFFERENT-GOLD', relevant_text_span=[['2', '2']])
    changed = freeze(tmp_path / 'after', 'qmsum', raw)
    assert changed['documents'] == original['documents']
    assert 'GOLD' not in json.dumps(changed['documents'])
    assert 'source_units' not in adapt('qmsum', raw)['documents'][0]


def test_v3_bundle_rebuilds_and_cannot_be_relabeled_v2(tmp_path):
    bundle = freeze(tmp_path, 'qasper', qasper_raw())
    assert load_bundle(tmp_path / 'bundle') == bundle
    assert bundle['manifest']['adaptation_revision'] == DATA_V3
    assert bundle['cases'][0]['adaptation_revision'] == DATA_V3
    assert request(bundle)['manifest']['request_revision'] == REQUEST_V3
    bundle['manifest']['adaptation_revision'] = 'notebook-data-v2'
    (tmp_path / 'bundle/manifest.json').write_text(json.dumps(bundle['manifest']))
    with pytest.raises(ValueError, match='canonical source'):
        load_bundle(tmp_path / 'bundle')


def test_v3_rebuild_rejects_tampered_documents_even_with_updated_file_hash(tmp_path):
    bundle = freeze(tmp_path, 'qasper', qasper_raw())
    path = tmp_path / 'bundle/documents.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]['text'] += '\nInjected text.'
    path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
    bundle['manifest']['files']['documents.jsonl'] = digest(path)
    (tmp_path / 'bundle/manifest.json').write_text(json.dumps(bundle['manifest']))
    with pytest.raises(ValueError, match='canonical source'):
        load_bundle(tmp_path / 'bundle')


def test_v3_rebuild_rejects_tampered_public_units_with_updated_file_hash(tmp_path):
    bundle = freeze(tmp_path, 'qasper', qasper_raw())
    path = tmp_path / 'bundle/documents.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]['source_units'][0]['text'] = 'Invented abstract.'
    path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
    bundle['manifest']['files']['documents.jsonl'] = digest(path)
    (tmp_path / 'bundle/manifest.json').write_text(json.dumps(bundle['manifest']))
    with pytest.raises(ValueError, match='canonical source'):
        load_bundle(tmp_path / 'bundle')


@pytest.mark.parametrize('revision', [None, 'notebook-data-v2', DATA_V3])
def test_prepare_cli_defaults_to_v3_and_explicit_v2_still_rebuilds(tmp_path, revision):
    (tmp_path / 'raw').write_text(json.dumps(qasper_raw()))
    (tmp_path / 'source').write_text(json.dumps(dict(dataset='qasper', split='test', revision='fixture',
                                                   source_url='https://example.org', license='fixture')))
    script = Path(__file__).resolve().parents[1] / 'scripts/prepare_notebook_benchmarks.py'
    args = [sys.executable, str(script), '--suite', 'qasper', '--raw', str(tmp_path / 'raw'),
            '--source', str(tmp_path / 'source'), '--output', str(tmp_path / 'bundle')]
    if revision:
        args += ['--adaptation-revision', revision]
    completed = subprocess.run(args, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    bundle = load_bundle(tmp_path / 'bundle')
    assert bundle['manifest']['adaptation_revision'] == (revision or DATA_V3)
    assert ('Figure 1: Bird counts.' in bundle['documents'][0]['text']) == (revision != 'notebook-data-v2')
