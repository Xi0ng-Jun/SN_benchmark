#!/usr/bin/env python3
"""Batch existing Notebook CLI runs, or compute offline saved-run diagnostics."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run', help='Calls models unless --dry-run is supplied')
    for name in ('bundle', 'case-id-file', 'output-root', 'project-root'):
        run.add_argument('--' + name, type=Path, required=True)
    run.add_argument('--artifact-root', '--store', dest='artifact_root', type=Path, required=True)
    run.add_argument('--artifact-index-id', '--index', dest='artifact_index_id', required=True)
    run.add_argument('--mode', choices=('chunk', 'reasoning'), required=True)
    run.add_argument('--parallel', type=int, default=1)
    run.add_argument('--python', default=sys.executable)
    run.add_argument('--ledger', dest='ledger_path', type=Path,
                     help='M0-B run ledger (default: OUTPUT_ROOT/runs.jsonl); execution receipts stay under OUTPUT_ROOT')
    for name in ('model-config', 'sn-source-ref', 'dependency-requirements', 'runtime-expectation', 'budget-ledger', 'task-worktree-ref'):
        run.add_argument('--' + name, type=Path)
    run.add_argument('--dry-run', action='store_true', help='Validate and print argv; no process or output writes')
    metrics = commands.add_parser('metrics', help='Offline only; does not import SN or call models')
    metrics.add_argument('--run-dir', type=Path, action='append', required=True)
    metrics.add_argument('--summary-output', type=Path)
    metrics.add_argument('--no-write', action='store_true', help='Read original runs without writing stage-metrics.json')
    args = parser.parse_args(argv)
    try:
        if args.command == 'metrics':
            from rag_eval.stage_metrics import write_stage_metrics
            report = write_stage_metrics(args.run_dir, summary_output=args.summary_output, write_runs=not args.no_write)
            print(json.dumps(report, ensure_ascii=False, allow_nan=False))
            return 0
        from rag_eval.notebook_batch import execute_batch, plan_batch
        if args.parallel < 1:
            parser.error('--parallel must be positive')
        values = vars(args).copy()
        for key in ('command', 'parallel', 'dry_run', 'ledger_path'):
            values.pop(key)
        jobs = plan_batch(**values)
        if args.dry_run:
            print(json.dumps(dict(format='sn-notebook-batch-plan-v1', model_calls=False, jobs=jobs),
                             ensure_ascii=False))
            return 0
        report = execute_batch(jobs, output_root=args.output_root, parallel=args.parallel, ledger_path=args.ledger_path)
        print(json.dumps(report, ensure_ascii=False, allow_nan=False))
        return report['exit_code']
    except (OSError, ValueError, KeyError) as exc:
        # Do not echo untrusted input, service addresses or private config text.
        print('Batch failed: ' + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
