#!/usr/bin/env python3
"""Run QMSum BM25 + an explicit generation model on one frozen meeting.

Performs model inference only when explicitly invoked. No dataset downloads or
SN Ask calls. Use a new output directory for every attempt.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-root', type=Path, help='Campaign immutable store; bundle must be installed before running')
    parser.add_argument('--artifact-index-id', help='Pinned partition index ID from trusted canonical installation; required with --artifact-root')
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--partition-id', required=True)
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--model-config', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--top-k', type=int, default=8)
    parser.add_argument('--max-context-chars', type=int, default=12000)
    args = parser.parse_args(argv)
    if (args.artifact_root is None) != (args.artifact_index_id is None):
        parser.error('--artifact-root and --artifact-index-id must be supplied together')
    from rag_eval.notebook_baseline_runner import execute
    run = execute(root=ROOT, project=args.project_root, bundle_dir=args.bundle,
                  run=args.run_dir, partition_id=args.partition_id, model_config=args.model_config, artifact_root=args.artifact_root, artifact_index_id=args.artifact_index_id,
                  top_k=args.top_k, max_context_chars=args.max_context_chars)
    print(run)


if __name__ == '__main__':
    main()
