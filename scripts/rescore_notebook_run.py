#!/usr/bin/env python3
"""Score saved notebook answers without calling Silicon Notebook or a generator."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True,
                        help="Completed or interrupted notebook run containing outputs.jsonl")
    parser.add_argument("--output", type=Path, required=True,
                        help="New derived scoring run directory")
    parser.add_argument("--metric", dest="metrics", action="append", default=None,
                        help="Scorer ID to include; repeat this option for several metrics")
    parser.add_argument("--case-id", dest="case_ids", action="append", default=None,
                        help="Case ID to include; repeat this option for several cases")
    parser.add_argument("--all", action="store_true", dest="all_scores",
                        help="Recompute every selected cell, including successful old scores")
    args = parser.parse_args(argv)
    from rag_eval.notebook_rescoring import rescore_run
    print(rescore_run(args.source_run, args.output, metrics=args.metrics,
                       case_ids=args.case_ids, all_scores=args.all_scores))


if __name__ == "__main__":
    main()
