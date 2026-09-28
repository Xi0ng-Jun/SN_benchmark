from copy import deepcopy
import importlib.util


def adapter():
    assert importlib.util.find_spec('rag_eval.lab_qasper'), 'LAB QASPER adapter missing'
    from rag_eval import lab_qasper
    return lab_qasper


def document():
    return {'prefix': 'paper', 'meta': {'arxiv_id': 'paper', 'ix_counter': 3,
                                      'qas': [{'answers': ['secret']}]},
            'nodes': [{'ix': 'paper_0', 'ntype': 'article-title', 'content': 'Title', 'meta': {}},
                      {'ix': 'paper_1', 'ntype': 'p', 'content': 'Abstract',
                       'meta': {'is_evidence_for': ['secret']}},
                      {'ix': 'paper_2', 'ntype': 'p', 'content': 'Paragraph', 'meta': {}}],
            'span_nodes': [], 'edges': []}


def test_public_graph_strips_labels_and_ordered_mapping_restores_original_whitespace():
    m = adapter()
    doc = document()
    clean = m.public_document(doc)
    assert clean['meta'] == {'arxiv_id': 'paper', 'ix_counter': 3}
    assert all(n['meta'] == {} for n in clean['nodes'])
    raw = {'abstract': ' Abstract ', 'full_text': [{'paragraphs': ['Paragraph\n']}],
           'figures_and_tables': []}
    mapping = m.evidence_text_map(doc, raw)
    assert mapping['paper_1'] == ' Abstract '
    assert mapping['paper_2'] == 'Paragraph\n'
    changed = deepcopy(doc)
    changed['meta']['qas'] = [{'answers': ['other']}]
    changed['nodes'][1]['meta']['is_evidence_for'] = []
    assert m.public_document(changed) == clean


def test_prediction_uses_author_answer_and_explicit_nodes_only():
    m = adapter()
    mapping = {'paper_1': ' Abstract ', 'paper_2': 'Paragraph\n'}
    pred = dict(task_name='qasper', example_id='q', free_text_answer='Answer',
                extraction_nodes=['paper_2'], raw_generation='Answer [2]')
    row = m.prediction_row(pred, 'q', 'qasper:q', mapping)
    assert row['record']['predicted_evidence'] == ['Paragraph\n']
    assert row['prediction'] == 'Answer'
    assert row['record']['raw_generation'] == 'Answer [2]'
    import pytest
    pred['extraction_nodes'] = ['another-paper_2']
    with pytest.raises(ValueError, match='node'):
        m.prediction_row(pred, 'q', 'qasper:q', mapping)


def test_test_instance_has_no_gold_and_training_answers_stay_separate():
    import json
    m = adapter()
    instance = m.instance_json(m.public_document(document()), 'q', 'Question?')
    assert instance['free_text_answer'] == []
    assert instance['answer_type'] == []
    assert instance['extraction_nodes'] == []
    assert instance['extraction_candidates'] == ['paper_1', 'paper_2']
    assert 'qas' not in json.loads(instance['document'])['meta']


def test_runtime_keeps_abstention_scored_and_generation_failure_separate(tmp_path):
    from types import SimpleNamespace as NS
    assert importlib.util.find_spec('rag_eval.lab_qasper_runtime'), 'LAB runtime missing'
    from rag_eval.lab_qasper_runtime import generate_rows
    m = adapter()
    doc = m.public_document(document())
    class Model:
        tokenizer = NS(decode=lambda *a, **kw: 'fixture input')
        def validation_collate_fn(self, instances):
            assert instances[0]['free_text_answer'] == []
            return {'instances': instances, 'input_texts': ['fixture input'],
                    'tokenized_input': {'input_ids': NS(tolist=lambda: [[1, 2]])}}
        def validation_step(self, batch, index):
            qid = batch['instances'][0]['example_id']
            if qid == 'fail':
                raise RuntimeError('fake transport failure')
            native = dict(task_name='qasper', example_id=qid, free_text_answer='unanswerable',
                          extraction_nodes=[], raw_generation='unknown')
            return [NS(to_json_dict=lambda: native)]
    questions = [dict(question_id=q, case_id='qasper:'+q, paper_id='paper', question='Question?')
                 for q in ['ok', 'fail']]
    rows = generate_rows(questions, {'paper': doc}, {'paper': {}}, lambda x: x, Model(), tmp_path)
    assert [r['status'] for r in rows] == ['success', 'error']
    assert rows[0]['prediction'] == 'unanswerable'
    assert rows[0]['record']['predicted_evidence'] == []
    assert rows[1]['record']['error']['stage'] == 'generation'
