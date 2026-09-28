#!/usr/bin/env python3
"""Import the pinned 281-answer Socratic SegEnc QMSum release, without inference."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rag_eval.qmsum_socratic_import import import_socratic_predictions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True, help='Pinned Salesforce test.predictions file')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(import_socratic_predictions(args.bundle, args.source, args.output))


if __name__ == '__main__':
    main()
