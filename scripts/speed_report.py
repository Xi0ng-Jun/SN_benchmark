#!/usr/bin/env python3
"""Merge the speed diagnostics of partition runs into one summary (no model calls).

Each argument is a workbench execution directory (its partitions and retry runs
are read) or a single partition run. Executions that ran at the same time on one
server shared its model gate; merge them together to see whether it was full.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    from rag_eval.speed_diagnostics import FILENAME, load, summarize
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='+', type=Path)
    args = parser.parse_args()
    observed = {}
    for path in args.paths:
        for pattern in (FILENAME, 'partitions/*/' + FILENAME, 'retry-*/*/*/' + FILENAME):
            for found in sorted(path.glob(pattern)):
                observed[str(found.parent)] = load(found.parent)
    if not any(observed.values()):
        parser.error('No ' + FILENAME + ' found under the given paths')
    print(json.dumps(summarize(observed), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
