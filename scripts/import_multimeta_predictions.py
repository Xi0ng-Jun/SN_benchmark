#!/usr/bin/env python3
"""Import fixed complete Multi-Meta-RAG answers and rankings, without model calls."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rag_eval.multimeta_import import import_multimeta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True, help='Directory containing qa_output/ and output/')
    parser.add_argument('--model', choices=['gpt4', 'palm'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(import_multimeta(args.bundle, args.source, args.output, model=args.model))


if __name__ == '__main__':
    main()
