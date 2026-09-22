#!/usr/bin/env python3
"""Report deterministic diagnostics from saved Silicon Notebook records."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from rag_eval.agent_evaluator import evaluate_run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    summary = evaluate_run(args.run_dir, args.output_dir)
    print(
        f"records={summary['record_count']} "
        f"complete={summary['completeness_counts']['complete']} "
        f"output={Path(args.output_dir).resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
