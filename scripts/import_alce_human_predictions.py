#!/usr/bin/env python3
"""Import real answers from the fixed ALCE human-evaluation release, without inference."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rag_eval.alce_human_import import import_alce_human


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True, help='Ordinary ASQA or ELI5 v3 bundle')
    parser.add_argument('--source', type=Path, required=True, help='human_eval_citations_completed.json at fixed revision')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    for path in import_alce_human(args.bundle, args.source, args.output):
        print(path)


if __name__ == '__main__':
    main()
