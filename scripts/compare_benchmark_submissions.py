#!/usr/bin/env python3
"""Compare validated local submissions without downloading data or running models."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from rag_eval.benchmark_comparison import write_comparison


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--entry', nargs=2, action='append', required=True,
                        metavar=('SUBMISSION_JSON', 'SCORES_JSON'),
                        help='Repeat for each distinct method; all must use the same frozen scope and scorer')
    parser.add_argument('--output', '--output-dir', dest='output', type=Path, required=True)
    args = parser.parse_args()
    entries = [{'submission': submission, 'scores': scores} for submission, scores in args.entry]
    try:
        report = write_comparison(args.bundle, entries, args.output)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f'Compared {len(report["methods"])} methods on {report["case_count"]} {report["suite"]} cases')
    print(args.output / 'report.json')
    print(args.output / 'report.md')


if __name__ == '__main__':
    main()
