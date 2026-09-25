#!/usr/bin/env python3
"""Run ONE frozen notebook partition/mode using isolated SN. This calls models."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    from rag_eval.notebook_bundle import OFFICIAL_REQUEST_REVISION, REQUEST_REVISIONS

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--partition-id', required=True)
    parser.add_argument('--mode', choices=('chunk', 'reasoning'), required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--request-revision', choices=REQUEST_REVISIONS, default=OFFICIAL_REQUEST_REVISION,
                        help='Generation request contract (default: v3 without gold fields); explicit v1/v2 preserve historical requests.')
    parser.add_argument('--model-config', type=Path, help='SN model-services TOML copied into the isolated runtime')
    parser.add_argument('--case-id', dest='case_ids', action='append', help='Run this case only; repeat for several cases. Entire corpus retained.')
    args = parser.parse_args()
    from rag_eval.notebook_runner import execute
    from rag_eval.starter_report import write_report
    run = args.run_dir.resolve()
    code = 0
    try:
        execute(root=ROOT, project=args.project_root, bundle_dir=args.bundle, run=run,
                mode=args.mode, partition_id=args.partition_id, request_revision=args.request_revision,
                case_ids=args.case_ids, model_config=args.model_config)
    except KeyboardInterrupt:
        code = 130
    except Exception as exc:
        print('Execution failed: ' + type(exc).__name__ + '; inspect saved state/artifacts', file=sys.stderr)
        code = 2
    if (run / 'state.json').exists():
        try:
            report = write_report([run], run / 'report')
            if any(r['state']['phase'] != 'finished' or r['warnings'] for r in report['runs']):
                code = code or 2
            print(run / 'report/report.md')
        except Exception as exc:
            print('Report failed: ' + type(exc).__name__, file=sys.stderr)
            code = code or 2
    return code


if __name__ == '__main__':
    raise SystemExit(main())
