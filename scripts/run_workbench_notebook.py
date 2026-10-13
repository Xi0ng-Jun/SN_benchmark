#!/usr/bin/env python3
"""Workbench execution wrapper; calls the existing captured Notebook runner."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rag_eval.workbench.notebook_job import execute

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', required=True)
    args = parser.parse_args()
    try:
        code = execute(json.loads(args.payload))
    except BaseException as exc:
        print('Notebook Job failed: ' + type(exc).__name__, file=sys.stderr)
        code = 2
    raise SystemExit(code)
