#!/usr/bin/env python3
"""Prepare deterministic answer/evidence review packets; no models or rescoring."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rag_eval.benchmark_review import write_review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle', 'comparison', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--metric', action='append', required=True, help='Repeat for shared per-case metrics')
    parser.add_argument('--per-stratum', type=int, default=3)
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()
    try:
        review = write_review(args.bundle, args.comparison, args.output,
                              metrics=args.metric, per_stratum=args.per_stratum, seed=args.seed)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f'Prepared {len(review["assignments"])} review items over {len(review["cases"])} distinct cases')
    print(args.output / 'review.md')
    print(args.output / 'annotations.jsonl')


if __name__ == '__main__':
    main()
