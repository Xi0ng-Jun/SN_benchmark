#!/usr/bin/env python3
"""Inspect saved DeepEval trajectory inputs offline, without SN or judge calls."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

# Set before any SDK import: this command never needs telemetry or credentials.
os.environ['DEEPEVAL_TELEMETRY_OPT_OUT'] = 'YES'
os.environ['DEEPEVAL_DISABLE_DOTENV'] = '1'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from rag_eval.agent_input_inspection import inspect_run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--max-prompt-bytes', type=int,
                        help='Optional positive byte budget for measured prompts; NOT a model token limit')
    args = parser.parse_args(argv)
    summary = inspect_run(args.run_dir, args.output_dir, max_prompt_bytes=args.max_prompt_bytes)
    print(f"records={summary['record_count']} errors={summary['status_counts'].get('error', 0)} "
          f"over_byte_budget={summary['over_byte_budget_count']} context_fit=unknown "
          f"output={args.output_dir.resolve()}")
    return 1 if summary['status_counts'].get('error') else 0


if __name__ == '__main__':
    raise SystemExit(main())
