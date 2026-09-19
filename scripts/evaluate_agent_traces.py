#!/usr/bin/env python3
"""Evaluate saved Silicon Notebook traces without rerunning the product.

Default mode is deterministic and offline.  Add ``--judge`` only when an
explicit DeepEval judge run is intended; it may call the configured model.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from rag_eval.agent_evaluator import evaluate_run
from rag_eval.agent_deepeval import available_agent_metrics


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--judge", action="store_true", help="Run DeepEval LLM trajectory metrics")
    parser.add_argument("--dag", action="store_true", help="Also run the SN Evidence Path DAGMetric (requires --judge)")
    parser.add_argument("--metrics", nargs="+", choices=available_agent_metrics(),
                        help="Selected DeepEval trajectory metrics; defaults to all when --judge is set")
    parser.add_argument("--judge-model", help="DeepEval model name or provider configuration key")
    args = parser.parse_args(argv)
    summary = evaluate_run(
        args.run_dir,
        args.output_dir,
        judge=args.judge,
        metrics=args.metrics,
        dag=args.dag,
        model=args.judge_model,
    )
    print(
        f"records={summary['record_count']} "
        f"complete={summary['completeness_counts']['complete']} "
        f"judge={summary['judge_enabled']} "
        f"output={Path(args.output_dir).resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
