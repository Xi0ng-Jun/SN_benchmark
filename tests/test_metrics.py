from rag_eval.metrics import (
    evidence_recall_at_k,
    hit_at_k,
    mean_reciprocal_rank,
    ndcg_at_k,
)


def test_retrieval_metrics_use_ranked_ids_and_gold_ids():
    ranked = ["wrong", "gold-2", "gold-1"]
    gold = {"gold-1", "gold-2"}

    assert hit_at_k(ranked, gold, 2) == 1.0
    assert evidence_recall_at_k(ranked, gold, 2) == 0.5
    assert mean_reciprocal_rank(ranked, gold, 3) == 0.5


def test_ndcg_is_zero_when_no_gold_is_retrieved():
    assert ndcg_at_k(["wrong"], {"gold"}, 1) == 0.0


def test_empty_inputs_are_safe():
    assert hit_at_k([], set(), 5) == 0.0
    assert evidence_recall_at_k([], {"gold"}, 5) == 0.0
    assert mean_reciprocal_rank([], {"gold"}, 5) == 0.0
    assert ndcg_at_k([], {"gold"}, 5) == 0.0
