import copy
import json

import pytest

from rag_eval.notebook_data import adapt
from rag_eval.notebook_bundle import prepare, load_bundle, partition_bundle


def paper():
    return {'p1': {'title': 'Paper', 'abstract': 'Abstract.',
        'full_text': [{'section_name': 'Results', 'paragraphs': ['Red birds fly.', 'Blue birds swim.']}],
        'qas': [{'question_id': 'q1', 'question': 'What flies?', 'answers': [{'answer': {
            'unanswerable': False, 'extractive_spans': ['Red birds'], 'free_form_answer': '',
            'yes_no': None, 'evidence': ['Red birds fly.']}}]}]}}


def source(suite):
    return dict(dataset=suite, split='test', revision='release-1', source_url='https://example.org/data', license='test fixture')


def test_qasper_gold_never_becomes_imported_material_and_float_is_excluded():
    raw = paper()
    raw['p1']['qas'][0]['answers'][0]['answer']['extractive_spans'] = ['SECRET-GOLD']
    other = copy.deepcopy(raw['p1']['qas'][0])
    other['question_id'] = 'q2'
    other['answers'][0]['answer']['evidence'] = ['FLOAT SELECTED: Figure 1']
    raw['p1']['qas'].append(other)
    result = adapt('qasper', raw)
    assert len(result['cases']) == 1
    assert result['cases'][0]['references'] == ['SECRET-GOLD']
    assert 'SECRET-GOLD' not in json.dumps(result['documents'])
    assert 'Blue birds swim.' in result['documents'][0]['text']
    assert result['decisions'][1]['status'] == 'excluded'
    assert result['cases'][0]['gold']['annotations'][0]['evidence'] == ['Red birds fly.']


def multihop():
    corpus = [dict(title='One', url='https://a', body='Alpha fact.'),
              dict(title='Two', url='https://b', body='Beta fact.'),
              dict(title='Other', url='https://c', body='Distractor.')]
    queries = [dict(query='Compare both.', answer='SECRET-GOLD', question_type='comparison_query',
                    evidence_list=[dict(title='One', url='https://a', fact='Alpha fact.'),
                                   dict(title='Two', url='https://b', fact='Beta fact.')])]
    return queries, corpus


def test_multihop_keeps_complete_corpus_not_gold_only():
    raw, corpus = multihop()
    result = adapt('multihop_rag', raw, corpus=corpus)
    case = result['cases'][0]
    assert len(case['gold_document_ids']) == 2
    assert len(case['material_document_ids']) == 3
    assert len(result['documents']) == 3
    assert 'SECRET-GOLD' not in json.dumps(result['documents'])
    raw[0]['evidence_list'][0]['url'] = 'https://missing'
    with pytest.raises(ValueError, match='evidence'):
        adapt('multihop_rag', raw, corpus=corpus)


def test_alce_candidate_order_and_separate_candidate_scopes():
    rows = [dict(question='First?', qa_pairs=[dict(short_answers=['SECRET'])],
                 docs=[dict(title='B', text='Second.'), dict(title='A', text='First.')]),
            dict(question='Second?', qa_pairs=[dict(short_answers=['Hidden'])],
                 docs=[dict(title='C', text='Other.')])]
    result = adapt('alce', rows, task='asqa')
    assert [d['title'] for d in result['cases'][0]['candidate_documents']] == ['B', 'A']
    assert result['cases'][0]['group_id'] != result['cases'][1]['group_id']
    assert 'SECRET' not in json.dumps(result['documents'])
    with pytest.raises(ValueError):
        adapt('alce', rows, task='eli5')


def test_qmsum_retains_speakers_all_turns_and_multiple_gold_spans():
    raw = [dict(meeting_transcripts=[dict(speaker='A', content='First.'), dict(speaker='B', content='Second.'),
                                   dict(speaker='A', content='Third.')],
                general_query_list=[dict(query='Summary?', answer='SECRET')],
                specific_query_list=[dict(query='Decisions?', answer='GOLD', relevant_text_span=[['0','0'],['2','2']])])]
    result = adapt('qmsum', raw)
    assert len(result['cases']) == 2
    assert result['cases'][1]['gold']['relevant_text_span'] == [[0,0],[2,2]]
    assert result['cases'][0]['gold']['relevant_text_span'] == []
    assert '[turn 1] B: Second.' in result['documents'][0]['text']
    assert 'SECRET' not in result['documents'][0]['text']
    raw[0]['specific_query_list'][0]['relevant_text_span'] = [['0','3']]
    with pytest.raises(ValueError, match='span'):
        adapt('qmsum', raw)


def test_frozen_bundle_rebuilds_partitions_and_rejects_tampering(tmp_path):
    raw, corpus = multihop()
    data_path, corpus_path = tmp_path/'data.json', tmp_path/'corpus.json'
    data_path.write_text(json.dumps(raw))
    corpus_path.write_text(json.dumps(corpus))
    meta = source('multihop_rag')
    meta['split'] = 'train'
    source_path = tmp_path/'source.json'
    source_path.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match='capacity'):
        prepare('multihop_rag', data_path, source_path, tmp_path/'too-small', corpus_path=corpus_path, max_documents=2)
    bundle = prepare('multihop_rag', data_path, source_path, tmp_path/'bundle', corpus_path=corpus_path, max_documents=3)
    assert len(bundle['partitions']) == 1
    loaded = load_bundle(tmp_path/'bundle')
    product = partition_bundle(loaded, loaded['partitions'][0]['partition_id'])
    assert len(product['documents']) == 3
    assert len(product['questions']) == 1
    assert product['questions'][0]['gold_document_ids'] == loaded['cases'][0]['gold_document_ids']
    (tmp_path/'bundle'/'cases.jsonl').write_text('{}\n')
    with pytest.raises(ValueError, match='hash'):
        load_bundle(tmp_path/'bundle')


def test_duplicate_question_ids_and_empty_documents_fail():
    raw = paper()
    raw['p1']['qas'] *= 2
    with pytest.raises(ValueError, match='Duplicate'):
        adapt('qasper', raw)
    raw, corpus = multihop()
    corpus[0]['body'] = ''
    with pytest.raises(ValueError):
        adapt('multihop_rag', raw, corpus=corpus)
