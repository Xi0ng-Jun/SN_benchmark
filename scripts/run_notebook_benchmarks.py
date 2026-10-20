#!/usr/bin/env python3
"""Run ONE frozen notebook partition/mode using isolated SN. This calls models."""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


class _ReadinessError(Exception):
    """Stable rejection category from the captured readiness gate."""

    def __init__(self, category):
        super().__init__(category)
        self.category = category


def _captured_scope(*, bundle, artifact_root, artifact_index_id, source_ref, case_ids,
                    partition_id, request_revision):
    """Derive the immutable captured identity without starting a subprocess."""
    from rag_eval.artifact_store import ArtifactStore
    from rag_eval.bundle_index import find_installed_bundle
    from rag_eval.identity import fingerprint
    bundle = Path(bundle).resolve()
    find_installed_bundle(bundle, artifact_root, expected_index_id=artifact_index_id)
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    source = ArtifactStore(artifact_root).resolve(dict(source_ref))
    identity = json.loads((source / 'object.json').read_text(encoding='utf-8'))['identity']
    return {
        'case_id': case_ids[0] if case_ids is not None and len(case_ids) == 1 else None,
        'partition_id': partition_id,
        'request_revision': request_revision,
        'bundle_id': fingerprint(manifest),
        'index_id': artifact_index_id,
        'sn_source_manifest_sha256': identity['source_manifest_sha256'],
    }


def _consume_readiness_receipt(path, expected_scope):
    """Validate the sealed receipt; raise only stable rejection categories."""
    from rag_eval.s3_readiness import validate_readiness_receipt
    path = Path(path)
    if not path.is_file():
        raise _ReadinessError('readiness_receipt_missing')
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _ReadinessError('readiness_receipt_invalid') from None
    try:
        receipt = validate_readiness_receipt(record)
    except (TypeError, ValueError):
        raise _ReadinessError('readiness_receipt_invalid') from None
    if receipt['status'] != 'ready':
        raise _ReadinessError('readiness_not_ready')
    scope = receipt['scope']
    if (expected_scope['case_id'] is None
            or any(scope[name] != expected_scope[name]
                   for name in ('case_id', 'partition_id', 'request_revision', 'bundle_id'))):
        raise _ReadinessError('readiness_scope_mismatch')
    if scope['sn_source_manifest_sha256'] != expected_scope['sn_source_manifest_sha256']:
        raise _ReadinessError('readiness_source_mismatch')
    if scope['index_id'] != expected_scope['index_id']:
        raise _ReadinessError('readiness_index_mismatch')


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
    parser.add_argument('--readiness-receipt', type=Path,
                        help='Sealed sn-e5-s3-readiness-receipt-v1 for this exact captured single-case smoke')
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
    if startup_kwargs and args.readiness_receipt is not None:
        try:
            expected_scope = _captured_scope(
                bundle=args.bundle, artifact_root=args.artifact_root,
                artifact_index_id=args.artifact_index_id, source_ref=source_ref,
                case_ids=case_ids, partition_id=args.partition_id,
                request_revision=args.request_revision)
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            return input_failure(exc, 'readiness_identity')
        try:
            _consume_readiness_receipt(args.readiness_receipt, expected_scope)
        except _ReadinessError as exc:
            print('Readiness failed: ' + exc.category, file=sys.stderr)
            return 2
    from rag_eval.run_report import write_report
    from rag_eval.system_runtime import is_cancellation
    run = args.run_dir.resolve()
    run_existed = run.exists()
    code = 0
    try:
        from rag_eval.model_budget import install_budget_guard
        with install_budget_guard(args.budget_ledger) if args.budget_ledger else nullcontext():
            execute(root=ROOT, project=args.project_root, bundle_dir=args.bundle, run=run,
                    mode=args.mode, partition_id=args.partition_id, request_revision=args.request_revision,
                    case_ids=case_ids, model_config=args.model_config, artifact_root=args.artifact_root,
                    artifact_index_id=args.artifact_index_id, reasoning_protocol=args.reasoning_protocol,
                    preparation_policy=args.preparation_policy, seed_prepared_run=args.seed_prepared_run,
                    **startup_kwargs)
    except BaseException as exc:
        details = getattr(exc, '_rag_eval_diagnostic_details', None) or exception_details(
            exc, phase='validation', sensitive_values=config_sensitive_values(args.model_config))
        interrupted = is_cancellation(exc)
        print(('Execution interrupted: ' if interrupted else 'Execution failed: ') + failure_summary(details),
              file=sys.stderr)
        code = 130 if interrupted else 2
    if not run_existed and (run / 'state.json').exists():
        try:
            report = write_report([run], run / 'report', partition_only=args.artifact_root is not None)
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
