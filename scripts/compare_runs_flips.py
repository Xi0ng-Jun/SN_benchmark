#!/usr/bin/env python3
"""Read two saved runs or two ledger rows; produce JSON and Markdown run flips."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from rag_eval.flip_report import write_flip_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--runs', nargs=2, type=Path, metavar=('BEFORE_RUN', 'AFTER_RUN'))
    source.add_argument('--ledger', type=Path)
    parser.add_argument('--rows', nargs=2, type=int, metavar=('BEFORE_ROW', 'AFTER_ROW'),
                        help='One-based ledger row numbers; required with --ledger')
    parser.add_argument('--scores', nargs=2, type=Path, metavar=('BEFORE_SCORES', 'AFTER_SCORES'))
    parser.add_argument('--submissions', nargs=2, type=Path, metavar=('BEFORE_SUBMISSION', 'AFTER_SUBMISSION'))
    parser.add_argument('--bundle', type=Path, help='Default: immutable input of before run')
    parser.add_argument('--output', '--output-dir', dest='output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.ledger:
            if args.rows is None:
                parser.error('--rows is required with --ledger')
            rows = [json.loads(line) for line in args.ledger.read_text(encoding='utf-8').splitlines() if line.strip()]
            if any(i < 1 or i > len(rows) for i in args.rows):
                parser.error('--rows must select existing one-based ledger rows')
            runs = [rows[i - 1] for i in args.rows]
        else:
            if args.rows is not None:
                parser.error('--rows requires --ledger')
            runs = args.runs
        report = write_flip_report(*runs, args.output, bundle_directory=args.bundle,
                  before_scores=args.scores[0] if args.scores else None,
                  after_scores=args.scores[1] if args.scores else None,
                  before_submission=args.submissions[0] if args.submissions else None,
                  after_submission=args.submissions[1] if args.submissions else None)
    except (OSError, ValueError, TypeError) as exc:
        # Keep input/configuration errors distinct from readable admission failure.
        parser.error(str(exc))
    print('Admission: ' + report['admission']['status'])
    print(json.dumps(report['summary'], ensure_ascii=False))
    print(args.output / 'report.json')
    print(args.output / 'report.md')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
