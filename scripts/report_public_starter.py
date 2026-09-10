#!/usr/bin/env python3
"""Build an offline report from one or more public starter run directories."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag_eval.starter_report import write_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New report directory")
    args = parser.parse_args()
    write_report(args.run_dirs, args.output)
    print(str(args.output.resolve() / "report.md"))


if __name__ == "__main__":
    main()
