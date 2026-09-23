#!/usr/bin/env python3
"""Build a local dashboard from saved benchmark runs; never executes an experiment."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from rag_eval.experiment_aggregation import discover_runs, write_dashboard

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, help="Discover saved runs (ignore input manifests and runtime files)")
    parser.add_argument("--output", type=Path, required=True, help="New report directory outside all runs")
    parser.add_argument("runs", nargs="*", type=Path, help="Explicit run directories")
    args = parser.parse_args()
    if bool(args.runs_root) == bool(args.runs):
        parser.error("provide exactly one of --runs-root or explicit RUN directories")
    try:
        runs = discover_runs(args.runs_root) if args.runs_root else args.runs
        path = write_dashboard(runs, args.output)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"Dashboard generation stopped: {exc}\n")
    print(path / "dashboard.html")


if __name__ == "__main__":
    main()
