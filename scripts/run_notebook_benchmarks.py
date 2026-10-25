#!/usr/bin/env python3
"""Run ONE frozen notebook partition/mode using isolated SN. This calls models."""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    from rag_eval.benchmark_runtime import DEFAULT_PREPARATION_POLICY, PREPARATION_POLICIES
    from rag_eval.notebook_bundle import OFFICIAL_REQUEST_REVISION, REQUEST_REVISIONS
    from rag_eval.execution_diagnostics import exception_details, failure_summary, config_sensitive_values

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-root', type=Path, help='Campaign immutable store; bundle must be installed before running')
    parser.add_argument('--artifact-index-id', help='Pinned partition index ID from trusted canonical installation; required with --artifact-root')
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--partition-id', required=True)
    parser.add_argument('--mode', choices=('chunk', 'reasoning'), required=True)
    parser.add_argument('--preparation-policy', choices=PREPARATION_POLICIES, default=DEFAULT_PREPARATION_POLICY,
                        help='Document preparation policy; deferred metadata refresh is opt-in')
    parser.add_argument('--reasoning-protocol', choices=('reasoning-v1', 'reasoning-no-clarification-v1'),
                        default='reasoning-v1', help='Explicit reasoning protocol variant')
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--seed-prepared-run', type=Path,
                        help='Copy the prepared SN notebook of this earlier run (same partition and conditions) '
                             'instead of importing documents again')
    parser.add_argument('--seed-allow-source-change', action='store_true',
                        help='Accept a seed prepared by different SN source (answer-path changes only); recorded')
    parser.add_argument('--preparation-cache', type=Path,
                        help='Content-addressed cache of prepared notebooks; a verified hit replaces importing')
    parser.add_argument('--shared-sn-source', type=Path,
                        help='Read-only tree of the captured SN source materialized once for this execution')
    parser.add_argument('--preparation-workers', type=int, default=1,
                        help='Imported documents processed at once (uploads stay sequential)')
    parser.add_argument('--question-workers', type=int, default=1,
                        help='Questions of this partition asked at once (independent requests)')
    parser.add_argument('--case-deadline-seconds', type=float,
                        help='Save a question still running after this long as an error and continue')
    parser.add_argument('--partition-deadline-seconds', type=float,
                        help='Stop asking at this age of the run; unanswered questions are saved as errors')
    parser.add_argument('--model-gate', type=Path,
                        help='Directory of cross-process model request slots shared by concurrent partitions')
    parser.add_argument('--model-gate-slots', type=int, help='Number of slots under --model-gate')
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--request-revision', choices=REQUEST_REVISIONS, default=OFFICIAL_REQUEST_REVISION,
                        help='Generation request contract (default: v3 without gold fields); explicit v1/v2 preserve historical requests.')
    parser.add_argument('--task-worktree-ref', type=Path, help='Saved explicit Task capture association')
    parser.add_argument('--budget-ledger', type=Path, help='Approved shared request-reservation ledger')
    parser.add_argument('--model-config', type=Path, help='SN model-services TOML copied into the isolated runtime')
    parser.add_argument('--sn-source-ref', type=Path, help='JSON reference to an already captured SN source object')
    parser.add_argument('--dependency-requirements', type=Path, help='Explicit public direct-dependency declaration captured for this startup')
    parser.add_argument('--runtime-expectation', type=Path, help='JSON selected interpreter: executable and venv prefix')
    parser.add_argument('--case-id', dest='case_ids', action='append', help='Run this case only; repeat for several cases. Entire corpus retained.')
    parser.add_argument('--case-id-file', type=Path,
                        help='Read one frozen case ID per line; may be combined with --case-id')
    args = parser.parse_args()

    def input_failure(exc, phase):
        details = exception_details(exc, phase=phase,
                                    sensitive_values=config_sensitive_values(args.model_config))
        print('Execution failed: ' + failure_summary(details), file=sys.stderr)
        return 2

    if (args.artifact_root is None) != (args.artifact_index_id is None):
        parser.error('--artifact-root and --artifact-index-id must be supplied together')
    startup_args = (args.sn_source_ref, args.dependency_requirements, args.runtime_expectation)
    startup_kwargs = {}
    if any(value is not None for value in startup_args):
        if not all(value is not None for value in startup_args) or args.artifact_root is None:
            parser.error('Captured startup requires source ref, requirements, runtime expectation and shared artifact index')
        try:
            source_ref = json.loads(args.sn_source_ref.read_text(encoding='utf-8'))
            expectation = json.loads(args.runtime_expectation.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return input_failure(exc, 'startup_inputs')
        if (type(source_ref) is not dict or set(source_ref) != {'kind', 'id', 'manifest_sha256'}
                or source_ref.get('kind') != 'sn' or type(expectation) is not dict
                or set(expectation) != {'executable', 'prefix'}
                or any(type(value) is not str or not value or not Path(value).is_absolute()
                       for value in expectation.values())):
            parser.error('Invalid captured startup reference or interpreter expectation')
        startup_kwargs = dict(sn_source_ref=source_ref,
                              dependency_requirements=args.dependency_requirements.resolve(),
                              expected_interpreter=expectation)
    if args.task_worktree_ref:
        try:
            association = json.loads(args.task_worktree_ref.read_text())
        except (OSError, ValueError) as exc:
            return input_failure(exc, 'task_association')
        startup_kwargs['task_worktree'] = association
    from rag_eval.notebook_runner import execute, load_case_ids_file
    case_ids = list(args.case_ids or [])
    if args.case_id_file is not None:
        try:
            case_ids.extend(load_case_ids_file(args.case_id_file))
        except (OSError, ValueError) as exc:
            return input_failure(exc, 'case_selection')
    if not case_ids:
        case_ids = None
    if (args.model_gate is None) != (args.model_gate_slots is None):
        parser.error('--model-gate and --model-gate-slots must be supplied together')
    from rag_eval.run_report import write_report
    from rag_eval.system_runtime import is_cancellation
    run = args.run_dir.resolve()
    run_existed = run.exists()
    code = 0
    try:
        from rag_eval.model_budget import install_budget_guard
        from rag_eval.model_gate import install_model_gate
        with install_budget_guard(args.budget_ledger) if args.budget_ledger else nullcontext(), \
                install_model_gate(args.model_gate, args.model_gate_slots) if args.model_gate else nullcontext():
            execute(root=ROOT, project=args.project_root, bundle_dir=args.bundle, run=run,
                    mode=args.mode, partition_id=args.partition_id, request_revision=args.request_revision,
                    case_ids=case_ids, model_config=args.model_config, artifact_root=args.artifact_root,
                    artifact_index_id=args.artifact_index_id, reasoning_protocol=args.reasoning_protocol,
                    preparation_policy=args.preparation_policy, seed_prepared_run=args.seed_prepared_run,
                    seed_allow_source_change=args.seed_allow_source_change,
                    preparation_cache=args.preparation_cache, question_workers=args.question_workers,
                    preparation_workers=args.preparation_workers,
                    case_deadline_seconds=args.case_deadline_seconds,
                    partition_deadline_seconds=args.partition_deadline_seconds,
                    shared_source=args.shared_sn_source, **startup_kwargs)
    except BaseException as exc:
        details = getattr(exc, '_rag_eval_diagnostic_details', None) or exception_details(
            exc, phase='validation', sensitive_values=config_sensitive_values(args.model_config))
        interrupted = is_cancellation(exc)
        print(('Execution interrupted: ' if interrupted else 'Execution failed: ') + failure_summary(details),
              file=sys.stderr)
        code = 130 if interrupted else 2
    if not run_existed and (run / 'state.json').exists():
        try:
            report = write_report([run], run / 'report', partition_only=args.artifact_root is not None,
                                  save_stage_metrics=True)
            if any(r['state']['phase'] != 'finished' or r['warnings'] for r in report['runs']):
                code = code or 2
            print(run / 'report/report.md')
        except Exception as exc:
            details = exception_details(exc, phase='reporting', directory=run,
                                        sensitive_values=config_sensitive_values(args.model_config))
            print('Report failed: ' + failure_summary(details), file=sys.stderr)
            code = code or 2
    return code


if __name__ == '__main__':
    raise SystemExit(main())
