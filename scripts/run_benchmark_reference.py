#!/usr/bin/env python3
"""Generate a BM25, full-context or ALCE candidate-topk reference submission.

Consumes a frozen notebook-data-v3 bundle. Does not download data or models.
Full-context inputs exceeding --max-context-chars are recorded as errors without
calling a model; no source unit is truncated to satisfy a character budget.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--model-config', type=Path, required=True)
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--strategy', choices=['bm25', 'full-context', 'candidate-topk'], default='bm25')
    parser.add_argument('--top-k', type=int, default=8)
    parser.add_argument('--max-context-chars', type=int, default=12000)
    parser.add_argument('--chunk-window', type=int, default=256)
    parser.add_argument('--chunk-overlap', type=int, default=32)
    parser.add_argument('--case-id', action='append', dest='case_ids', help='Explicit subset; repeat for several cases')
    args = parser.parse_args(argv)
    from rag_eval.benchmark_reference import execute, reference_config
    configuration = reference_config(args.strategy, top_k=args.top_k, max_context_chars=args.max_context_chars,
                                     chunk_window=args.chunk_window, chunk_overlap=args.chunk_overlap)
    run = execute(root=ROOT, project=args.project_root, bundle_dir=args.bundle, run=args.run_dir,
                  model_config=args.model_config, configuration=configuration, case_ids=args.case_ids)
    print(run/'submission.json')
    return run


if __name__ == '__main__':
    main()
