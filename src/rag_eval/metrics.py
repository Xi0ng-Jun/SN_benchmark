"""Document-level IR metrics; duplicate chunks from one document count once."""
import math


def score_ranking(ranked_ids, relevance, k):
    if k < 1:
        raise ValueError('k must be positive')
    grades = {str(i): float(g) for i, g in relevance.items() if g > 0}
    if not grades:
        return None
    ranked = list(dict.fromkeys(map(str, ranked_ids)))[:k]
    found = set(ranked) & set(grades)
    dcg = sum((2 ** grades.get(i, 0) - 1) / math.log2(n + 2) for n, i in enumerate(ranked))
    ideal = sum((2 ** g - 1) / math.log2(n + 2) for n, g in enumerate(sorted(grades.values(), reverse=True)[:k]))
    return dict(hit=float(bool(found)), recall=len(found) / len(grades),
                mrr=next((1 / n for n, i in enumerate(ranked, 1) if i in grades), 0.0),
                ndcg=dcg / ideal, complete_evidence=float(set(grades) <= found))


def _binary(ranked, gold, k, metric):
    scores = score_ranking(ranked, dict.fromkeys(gold, 1), k)
    return scores[metric] if scores else 0.0


def hit_at_k(ranked_ids, gold_ids, k):
    return _binary(ranked_ids, gold_ids, k, 'hit')


def evidence_recall_at_k(ranked_ids, gold_ids, k):
    return _binary(ranked_ids, gold_ids, k, 'recall')


def mean_reciprocal_rank(ranked_ids, gold_ids, k):
    return _binary(ranked_ids, gold_ids, k, 'mrr')


def ndcg_at_k(ranked_ids, gold_ids, k):
    return _binary(ranked_ids, gold_ids, k, 'ndcg')
