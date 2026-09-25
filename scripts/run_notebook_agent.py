#!/usr/bin/env python3
"""Run a frozen notebook partition with native DeepEval component/Agent scoring.

Explicit online entrypoint: calls SN and the configured judge. No downloads.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main(argv=None):
    from rag_eval.notebook_bundle import OFFICIAL_REQUEST_REVISION, REQUEST_REVISIONS

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--partition-id', required=True)
    parser.add_argument('--mode', choices=('chunk', 'reasoning'), required=True)
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--judge-config', type=Path, required=True,
                        help='Explicit judge JSON (public-starter-models format, judge role only)')
    parser.add_argument('--model-config', type=Path,
                        help='SN model-services TOML; default is project/.local/model-services.toml')
    parser.add_argument('--trajectory', action='store_true',
                        help='Also score complete trajectories; requires a judge that fits the full trace')
    parser.add_argument('--metric', dest='metrics', action='append',
                        help='Select a metric ID; repeat for several. Whole-trace IDs also require --trajectory')
    parser.add_argument('--task-timeout', type=float,
                        help='SDK evaluate task budget (e.g. multi-query samples); native sync iterator uses judge transport limits')
    parser.add_argument('--case-id', dest='case_ids', action='append',
                        help='Run selected case(s) with the entire partition corpus; repeat for several')
    parser.add_argument('--request-revision', choices=REQUEST_REVISIONS, default=OFFICIAL_REQUEST_REVISION,
                        help='Generation request contract (default: v3 without gold fields); explicit v1/v2 preserve historical requests.')
    args = parser.parse_args(argv)
    # Set before any optional SDK import. The judge uses its explicit own credentials.
    os.environ.update(DEEPEVAL_TELEMETRY_OPT_OUT='YES', DEEPEVAL_DISABLE_DOTENV='1',
                      DEEPEVAL_NO_INSPECT_PROMPT='1', CONFIDENT_TRACING_ENABLED='NO')
    os.environ.pop('CONFIDENT_API_KEY', None)
    from rag_eval.notebook_runner import execute
    from rag_eval.starter_report import write_report

    run = args.run_dir.resolve()
    code = 0
    try:
        execute(root=ROOT, project=args.project_root, bundle_dir=args.bundle, run=run,
                mode=args.mode, partition_id=args.partition_id, request_revision=args.request_revision,
                case_ids=args.case_ids, model_config=args.model_config,
                agent_config={'judge_config': args.judge_config.resolve(), 'trajectory': args.trajectory,
                              'metrics': args.metrics, 'task_timeout': args.task_timeout})
    except KeyboardInterrupt:
        code = 130
    except Exception as exc:
        print('Execution failed: ' + type(exc).__name__ + '; inspect saved state/artifacts', file=sys.stderr)
        code = 2
    if (run / 'manifest.json').is_file():
        try:
            report = write_report([run], run / 'report')
            if any(r['state']['phase'] != 'finished' or r['warnings'] for r in report['runs']):
                code = code or 2
        except Exception as exc:
            print('Report failed: ' + type(exc).__name__, file=sys.stderr)
            code = code or 2
    print(run)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
