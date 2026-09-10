import json
import math

import pytest

from rag_eval.datasets import load_dataset, load_jsonl
from rag_eval.metrics import score_ranking
from rag_eval.protocol import capture_context, validate_result
from rag_eval.synthetic import validate_golden


def test_unicode_line_separator_is_not_a_jsonl_record_boundary(tmp_path):
    p = tmp_path / 'rows.jsonl'
    p.write_text(json.dumps({'text': 'one\u2028two'}, ensure_ascii=False) + '\n')
    assert load_jsonl(p) == [{'text': 'one\u2028two'}]


def test_duplicate_retrievals_do_not_inflate_ndcg():
    scores = score_ranking(['a', 'a', 'b'], {'a': 2, 'b': 1}, 3)
    assert scores['ndcg'] == pytest.approx(1)
    assert scores['recall'] == 1


def test_missing_gold_is_not_zero_recall():
    assert score_ranking(['a'], {}, 5) is None


def test_graded_ndcg_penalizes_reversed_order():
    s = score_ranking(['b', 'a'], {'a': 2, 'b': 1}, 2)
    assert s['ndcg'] == pytest.approx((1 + 3 / math.log2(3)) / (3 + 1 / math.log2(3)))


def test_context_preserves_exact_delivered_text_and_order():
    block = 'k1: Alpha\nsecond line\nk2: Beta'
    mapped = {'k1': {'object_id': 'c1', 'source_id': 's1'}, 'k2': {'object_id': 'c2', 'source_id': 's2'}}
    context = capture_context({'context_block': block, 'id_map': mapped})
    assert context['context_block'] == block
    assert context['retrieval_context'] == ['k1: Alpha\nsecond line', 'k2: Beta']
    assert context['retrieved_ids'] == ['c1', 'c2']


def test_result_requires_real_list_context():
    with pytest.raises(ValueError):
        validate_result({'id': 'q', 'question': 'q', 'answer': 'a', 'retrieval_context': 'wrong'})


def test_gold_quote_must_exist_in_original_evidence():
    item = {'question': 'Q', 'expected_answer': 'A', 'evidence': [{'id': 'c1', 'quote': 'invented'}]}
    with pytest.raises(ValueError):
        validate_golden(item, {'c1': 'real evidence'})


def test_gold_is_labeled_model_generated_pending_human_review():
    item = {'question': 'Q', 'expected_answer': 'A', 'evidence': [{'id': 'c1', 'quote': 'evidence'}]}
    result = validate_golden(item, {'c1': 'real evidence'})
    assert result['review_status'] == 'model_verified_pending_human'


def test_dataset_rejects_duplicate_ids(tmp_path):
    root = tmp_path / 'scifact' / 'full'
    root.mkdir(parents=True)
    (root / 'questions.jsonl').write_text('{"id":"q","question":"Q"}\n{"id":"q","question":"Q2"}\n')
    (root / 'annotations.jsonl').write_text('{"question_id":"q","relevance":{}}\n')
    (root / 'documents.jsonl').write_text('')
    with pytest.raises(ValueError, match='duplicate'):
        load_dataset(root)
