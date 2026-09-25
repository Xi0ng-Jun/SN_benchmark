#!/usr/bin/env python3
"""Freeze local official notebook benchmark data. Never downloads or runs models."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from rag_eval.notebook_bundle import prepare
from rag_eval.notebook_data import ADAPTATION_REVISION, OFFICIAL_ADAPTATION_REVISION, SUITES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=tuple(SUITES), required=True)
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True, help='Local provenance JSON')
    parser.add_argument('--corpus', type=Path, help='MultiHop-RAG corpus.json only')
    parser.add_argument('--max-documents', type=int, default=40, help='Per-notebook capacity; never a question limit')
    parser.add_argument('--adaptation-revision', choices=(ADAPTATION_REVISION, OFFICIAL_ADAPTATION_REVISION),
                        default=OFFICIAL_ADAPTATION_REVISION, help='Data contract to freeze (default: notebook-data-v3)')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = prepare(args.suite, args.raw, args.source, args.output, corpus_path=args.corpus,
                   max_documents=args.max_documents, adaptation_revision=args.adaptation_revision)
    print(f"Selected {len(data['cases'])} cases in {len(data['partitions'])} partitions")
    print(args.output / 'partitions.jsonl')


if __name__ == '__main__':
    main()
