#!/usr/bin/env python3
"""Compare saved QMSum BM25 and SN scores, without model calls or rescoring."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-runs', type=Path, nargs='+', required=True)
    parser.add_argument('--sn-runs', type=Path, nargs='+', required=True,
                        help='One SN mode/configuration, at most one attempt per partition')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    from rag_eval.notebook_baseline_comparison import write_comparison
    print(write_comparison(args.baseline_runs, args.sn_runs, args.output))


if __name__ == '__main__':
    main()
