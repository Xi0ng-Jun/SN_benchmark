#!/usr/bin/env python3
"""Append and query the agent-facing SN evaluation run ledger."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag_eval.run_ledger import append_run, query_runs, render_table  # noqa: E402


DEFAULT_LEDGER = ROOT / "var/sn-eval/runs.jsonl"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER,
                        help=f"ledger JSONL path (default: {DEFAULT_LEDGER})")
    commands = parser.add_subparsers(dest="command", required=True)

    append = commands.add_parser("append", help="derive and append one run")
    append.add_argument("--ledger", type=Path, default=argparse.SUPPRESS)
    append.add_argument("--run-dir", type=Path, required=True)
    append.add_argument("--scores", type=Path,
                        help="optional official score JSON or score directory")
    append.add_argument("--stage-metrics", type=Path,
                        help="optional stage-metrics JSON")
    append.add_argument("--exit-code", type=int, help="observed process exit code; absent stays null")
    append.add_argument("--stderr-summary", help="stderr tail; redacted and limited to 4096 characters")

    query = commands.add_parser("query", help="query existing rows")
    query.add_argument("--ledger", type=Path, default=argparse.SUPPRESS)
    query.add_argument("--suite")
    query.add_argument("--mode")
    query.add_argument("--state")
    query.add_argument("--format", choices=("json", "table"), default="json")

    args = parser.parse_args(argv)
    try:
        if args.command == "append":
            row = append_run(args.ledger, args.run_dir, scores_path=args.scores,
                             stage_metrics_path=args.stage_metrics, exit_code=args.exit_code,
                             stderr_summary=args.stderr_summary)
            print(json.dumps(row, ensure_ascii=False, indent=2))
        else:
            rows = query_runs(args.ledger, suite=args.suite, mode=args.mode, state=args.state)
            if args.format == "table":
                print(render_table(rows))
            else:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        print(f"ledger error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
