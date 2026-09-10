#!/usr/bin/env python3
"""Run a bounded, local BM25 retrieval baseline on the downloaded datasets."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from rag_eval.artifacts import save_json
from rag_eval.datasets import iter_jsonl, load_dataset
from rag_eval.metrics import score_ranking


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--datasets-root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--limit', type=int, default=50)
    args = p.parse_args()
    from rank_bm25 import BM25Okapi
    report = {'limit': args.limit, 'datasets': {}}
    for name in ('crud-rag', 'multihop-rag', 'scifact'):
        root = args.datasets_root / name / 'full'
        documents = list(iter_jsonl(root / 'documents.jsonl'))
        ids = [str(d['id']) for d in documents]
        texts = [str(d.get('text') or d.get('contents') or d.get('title') or '') for d in documents]
        tokenized = [text.lower().split() for text in texts]
        bm25 = BM25Okapi(tokenized)
        rows, scores = load_dataset(root), []
        for row in rows[:args.limit]:
            ranked = [str(item) for item in bm25.get_top_n(row['question'].lower().split(), ids, n=10)]
            score = score_ranking(ranked, row['relevance'], 10)
            if score is not None:
                scores.append(score)
        aggregate = {}
        for key in ('hit', 'recall', 'mrr', 'ndcg', 'complete_evidence'):
            aggregate[key] = sum(s[key] for s in scores) / len(scores) if scores else None
        report['datasets'][name] = {'questions_seen': min(args.limit, len(rows)),
                                    'questions_with_gold': len(scores), 'metrics_at_10': aggregate}
        print(name, report['datasets'][name], flush=True)
    save_json(args.output, report)


if __name__ == '__main__':
    main()
