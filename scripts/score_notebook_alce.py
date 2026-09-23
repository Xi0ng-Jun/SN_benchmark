#!/usr/bin/env python3
"""Export a saved ALCE run, or attach official model scores to a new run. Offline."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from rag_eval.notebook_alce_results import export_run, attach_scores


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export')
    export.add_argument('--run', type=Path, required=True)
    export.add_argument('--output', type=Path, required=True)
    attach = commands.add_parser('attach')
    attach.add_argument('--run', type=Path, required=True)
    attach.add_argument('--official-dir', type=Path, required=True)
    attach.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'export':
        print(export_run(args.run, args.output))
    else:
        print(attach_scores(args.run, args.official_dir, args.output))


if __name__ == '__main__':
    main()
