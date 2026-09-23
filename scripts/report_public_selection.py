#!/usr/bin/env python3
"""Report complete selection/partition coverage from local saved artifacts only."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag_eval.selection_report import write_selection_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--partition-plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New report directory")
    parser.add_argument("--modes", nargs="+", choices=("chunk", "reasoning"), default=["chunk", "reasoning"])
    parser.add_argument("run_dirs", nargs="*", type=Path, help="Zero or more saved R runs; missing partitions are retained")
    args = parser.parse_args()
    write_selection_report(args.bundle, args.partition_plan, args.run_dirs, args.output, args.modes)


if __name__ == "__main__":
    main()
