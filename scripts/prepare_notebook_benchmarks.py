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
    parser.add_argument('--suite', choices=tuple(SUITES))
    parser.add_argument('--raw', type=Path)
    parser.add_argument('--source', type=Path, help='Local provenance JSON')
    parser.add_argument('--corpus', type=Path, help='MultiHop-RAG corpus.json only')
    parser.add_argument('--max-documents', type=int, default=40, help='Per-notebook capacity; never a question limit')
    parser.add_argument('--adaptation-revision', choices=(ADAPTATION_REVISION, OFFICIAL_ADAPTATION_REVISION),
                        default=OFFICIAL_ADAPTATION_REVISION, help='Data contract to freeze (default: notebook-data-v3)')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--install-bundle', type=Path, help='Install an existing frozen bundle without changing or re-preparing it')
    parser.add_argument('--artifact-root', type=Path, help='Install immutable shared bundle and partition index in this campaign store')
    args = parser.parse_args()
    if args.install_bundle is not None:
        if args.artifact_root is None or any(v is not None for v in (args.suite, args.raw, args.source, args.output, args.corpus)):
            parser.error('--install-bundle requires --artifact-root and cannot be mixed with preparation inputs')
        from rag_eval.bundle_index import install_bundle
        refs = install_bundle(args.install_bundle, args.artifact_root)
        print('Shared bundle: ' + refs['bundle']['id'])
        print('Shared partition index: ' + refs['index']['id'])
        return
    if any(v is None for v in (args.suite, args.raw, args.source, args.output)):
        parser.error('Preparation requires --suite, --raw, --source and --output')
    data = prepare(args.suite, args.raw, args.source, args.output, corpus_path=args.corpus,
                   max_documents=args.max_documents, adaptation_revision=args.adaptation_revision)
    if args.artifact_root is not None:
        from rag_eval.bundle_index import install_bundle
        refs = install_bundle(args.output, args.artifact_root)
        print('Shared bundle: ' + refs['bundle']['id'])
        print('Shared partition index: ' + refs['index']['id'])
    print(f"Selected {len(data['cases'])} cases in {len(data['partitions'])} partitions")
    print(args.output / 'partitions.jsonl')


if __name__ == '__main__':
    main()
