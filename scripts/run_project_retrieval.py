#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rag_eval.artifacts import save_jsonl, save_json
from rag_eval.project import open_repository, retrieve, answer


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--project-root', default=str(ROOT / 'project'))
    p.add_argument('--notebook-id', required=True)
    p.add_argument('--input', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--top-k', type=int, default=10)
    p.add_argument('--limit', type=int)
    args = p.parse_args()
    questions = [json.loads(x) for x in args.input.read_text(encoding='utf-8').splitlines() if x.strip()]
    if args.limit:
        questions = questions[:args.limit]
    repo = open_repository(args.project_root)
    records = []
    try:
        for i, row in enumerate(questions, 1):
            hits = retrieve(repo, args.notebook_id, row['question'], args.top_k)
            texts = [h['text'] for h in hits]
            actual, grounded = answer(repo, row['question'], texts)
            records.append({**row, 'answer': actual, 'grounded': grounded,
                            'retrieval_context': texts, 'retrieved_ids': [h['id'] for h in hits],
                            'retrieved_source_ids': [h['source_id'] for h in hits]})
            save_jsonl(args.output, records)
            print(f'{i}/{len(questions)} retrieved={len(hits)}', flush=True)
    finally:
        repo.close()
    save_json(args.output.with_suffix('.meta.json'), {'notebook_id': args.notebook_id, 'top_k': args.top_k, 'count': len(records)})


if __name__ == '__main__':
    main()
