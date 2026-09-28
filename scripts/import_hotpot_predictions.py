#!/usr/bin/env python3
"""Import native HotpotQA answer/sp output into a frozen distractor submission."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rag_eval.hotpot_native_import import import_hotpot_predictions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--method', type=Path, required=True,
                        help='Explicit provenance; configuration.benchmark_setting must be distractor')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(import_hotpot_predictions(args.bundle, args.source, json.loads(args.method.read_text()), args.output))


if __name__ == '__main__':
    main()
