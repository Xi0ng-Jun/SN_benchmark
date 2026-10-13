"""Deterministic local executor for the workbench UI lifecycle smoke.

This writes only a run-local state and marker. It never loads SN, contacts a
model service, reads benchmark inputs, or produces an official score. Real
benchmark execution must use the separately reviewed runner argv.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--preparation-id', required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    run.mkdir(parents=True, exist_ok=False)
    (run / 'smoke-marker.json').write_text(json.dumps({
        'kind': 'workbench-ui-smoke', 'job_id': args.job_id,
        'preparation_id': args.preparation_id,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (run / 'state.json').write_text(json.dumps({
        'phase': 'finished', 'terminal': True, 'status': 'smoke_complete',
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
