#!/usr/bin/env python3
"""Workbench execution wrapper; calls the existing captured Notebook runner."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rag_eval.workbench.notebook_job import execute
from rag_eval.execution_diagnostics import exception_details, failure_summary
from rag_eval.system_runtime import is_cancellation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', required=True)
    args = parser.parse_args()
    try:
        code = execute(json.loads(args.payload))
    except BaseException as exc:
        details = getattr(exc, '_rag_eval_diagnostic_details', None) or exception_details(exc, phase='validation')
        print('Notebook Job failed: ' + failure_summary(details), file=sys.stderr)
        code = 130 if is_cancellation(exc) else 2
    return code


if __name__ == '__main__':
    raise SystemExit(main())
