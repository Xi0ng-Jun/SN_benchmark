"""Trusted workbench adapter for the existing captured Notebook runner.

One execution supervises serial partition subprocesses. Each partition retains
its own runtime and original state; official scoring happens after generation.
"""
import hashlib
import json
from pathlib import Path
import sys

from ..artifact_store import ArtifactStore
from ..artifacts import digest, save_json
from ..execution_diagnostics import exception_details, sanitize_diagnostic, sensitive_strings
from .job_execution import ExecutionRejected, validate_execution_budget


def validate_budget(budget, config):
    return validate_execution_budget(budget, max_limit_cny=config.get('max_limit_cny', 10))


def execution_spec(jobs, task_id, job_id, preparation_id, config, *, request_key, budget):
    approved = validate_budget(budget, config)
    preparations = jobs.preparations(task_id, job_id)
    receipt = next((r for r in preparations if r['preparation']['preparation_id'] == preparation_id), None)
    if receipt is None:
        raise ExecutionRejected('Verified preparation is required')
    prep = receipt['preparation']
    required = ('python', 'output_root', 'artifact_root', 'project_root', 'model_config',
                'dependency_requirements', 'runtime_expectation', 'scorer_sources')
    if any(type(config.get(k)) is not str or not config[k] for k in required):
        raise ExecutionRejected('Notebook execution environment is not configured')
    # Recheck consumed artifacts without consulting mutable plan heads.
    store = ArtifactStore(config['artifact_root'])
    try:
        bundle = store.resolve(prep['input']['bundle_ref'])
        store.resolve(prep['input']['index_ref'])
        store.resolve(prep['source']['artifact_ref'])
        files = {key: dict(path=str(Path(config[key]).resolve()), sha256=digest(config[key]))
                 for key in ('model_config', 'dependency_requirements', 'runtime_expectation')}
    except (ValueError, OSError):
        raise ExecutionRejected('Notebook inputs or runtime files are unavailable') from None
    suffix = hashlib.sha256(request_key.encode()).hexdigest()[:16]
    run_dir = str(Path(config['output_root']).resolve() / (job_id + '-' + suffix))
    output = Path(config['output_root']).resolve()
    for protected in (bundle, store.root, Path(config['project_root']).resolve(),
                      Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[3] / 'scripts'):
        if output.is_relative_to(protected) or protected.is_relative_to(output):
            raise ExecutionRejected('Execution output must be isolated from inputs and source')
    payload = dict(run_dir=run_dir, job_id=job_id, preparation_id=preparation_id,
        artifact_root=str(store.root), bundle=str(bundle), bundle_ref=prep['input']['bundle_ref'],
        index_ref=prep['input']['index_ref'], sn_ref=prep['source']['artifact_ref'],
        selection=prep['input']['selection'], mode=prep['execution_intent']['mode'],
        project_root=str(Path(config['project_root']).resolve()), python=config['python'],
        files=files, scorer_sources=config['scorer_sources'], budget=approved,
        task_worktree=config.get('task_worktree'))
    script = Path(__file__).resolve().parents[3] / 'scripts/run_workbench_notebook.py'
    return dict(argv=[config['python'], str(script), '--payload', json.dumps(payload)],
                run_dir=run_dir, preparation_id=preparation_id, budget=approved)


def execute(payload):
    """Run existing partition CLI and independent official scoring; never retry."""
    from ..benchmark_official import prepare_inputs, score_prepared
    from ..benchmark_submission import export_sn_runs
    from ..notebook_batch import plan_batch, execute_batch
    from ..notebook_bundle import load_bundle
    run = Path(payload['run_dir']).resolve()
    # Check before publishing runtime artifacts or starting any child.
    for file in payload['files'].values():
        if digest(file['path']) != file['sha256']:
            raise ValueError('Runtime file changed after execution was prepared')
    run.mkdir(parents=True, exist_ok=False, mode=0o700)
    def state(phase, **details):
        save_json(run / 'state.json', dict(phase=phase, terminal=phase != 'running', **details))
    stage = 'preparing'
    state('running', stage=stage, planned=len(payload['selection']['case_ids']))
    try:
        inputs = run / 'invocation'
        inputs.mkdir(mode=0o700)
        frozen_files = {}
        for key, file in payload['files'].items():
            data = Path(file['path']).read_bytes()
            if hashlib.sha256(data).hexdigest() != file['sha256']:
                raise ValueError('Runtime file changed during preparation')
            target = inputs / (key + Path(file['path']).suffix)
            with target.open('xb') as stream:
                stream.write(data)
            target.chmod(0o600)
            frozen_files[key] = str(target)
        save_json(inputs / 'sn-source-ref.json', payload['sn_ref'])
        ids_file = inputs / 'case-ids.txt'
        ids_file.write_text('\n'.join(payload['selection']['case_ids']) + '\n')
        budget_ledger = None
        if 'limit_cny' in payload['budget']:
            from ..model_budget import ModelBudget
            budget_ledger = inputs / 'budget.json'
            ModelBudget.create(budget_ledger, limit_cny=payload['budget']['limit_cny'])
        association = None
        if payload.get('task_worktree'):
            association = inputs / 'task-worktree.json'
            save_json(association, dict(payload['task_worktree'], source_ref=payload['sn_ref']['id']))
        jobs = plan_batch(bundle=payload['bundle'], artifact_root=payload['artifact_root'],
            artifact_index_id=payload['index_ref']['id'], case_id_file=ids_file,
            mode=payload['mode'], output_root=run / 'partitions',
            project_root=payload['project_root'], python=payload['python'],
            model_config=frozen_files['model_config'],
            sn_source_ref=inputs / 'sn-source-ref.json',
            dependency_requirements=frozen_files['dependency_requirements'],
            runtime_expectation=frozen_files['runtime_expectation'],
            budget_ledger=budget_ledger, task_worktree_ref=association)
        if [j['partition_id'] for j in jobs] != payload['selection']['partition_ids']:
            raise ValueError('Partition expansion differs from the fixed plan')
        save_json(run / 'batch-plan.json', jobs)
        stage = 'generating'
        state('running', stage=stage, planned=len(payload['selection']['case_ids']))
        report = execute_batch(jobs, output_root=run / 'partitions', parallel=1,
                               ledger_path=run / 'runs.jsonl')
        save_json(run / 'batch-report.json', report)
        generation_failures = []
        for row in report['runs']:
            saved = row.get('state') or {}
            if row.get('exit_code') == 0 and not row.get('stage_metrics_error') and not row.get('ledger_append_error'):
                continue
            details = dict(error_type=saved.get('error_type') or row.get('error_type') or
                row.get('stage_metrics_error') or row.get('ledger_append_error') or 'child_exit',
                error_message=saved.get('error_message') or row.get('error_message') or
                    row.get('stderr_tail') or f"Partition process exited with code {row.get('exit_code')}",
                failed_phase=saved.get('failed_phase') or row.get('failed_phase') or saved.get('phase') or stage,
                diagnostic_path=saved.get('diagnostic_path') or row.get('diagnostic_path') or row.get('stderr_path'))
            details = {key: sanitize_diagnostic(value)[:2048] if isinstance(value, str) else value
                       for key, value in details.items()}
            generation_failures.append(dict(run_dir=row['run_dir'], **details))
        stage = 'scoring'
        state('running', stage=stage, planned=len(payload['selection']['case_ids']),
              generation_failures=generation_failures)
        bundle = load_bundle(payload['bundle'])
        failures = []
        for job in jobs:
            partition = Path(job['run_dir'])
            if not (partition / 'manifest.json').is_file():
                failures.append(dict(run_dir=str(partition), error_type='missing_manifest',
                    error_message='Partition did not save a manifest; official scoring is unavailable',
                    failed_phase=stage, diagnostic_path=None))
                continue
            submission = None
            try:
                official = run / 'official' / partition.name
                submission_path = export_sn_runs(payload['bundle'], [partition], official / 'submission',
                                                case_ids=job['case_ids'])
                submission = json.loads(submission_path.read_text())
                score_prepared(bundle, submission, prepare_inputs(bundle, submission),
                               source_directory=payload['scorer_sources'], output_dir=official / 'scores')
            except Exception as exc:
                failures.append(dict(run_dir=str(partition), **exception_details(exc, phase=stage, directory=run,
                    sensitive_values=(*sensitive_strings(bundle.get('cases', [])),
                                      *sensitive_strings(submission)))))
        save_json(run / 'scoring-status.json', dict(failures=failures))
        first_failure = next(iter(generation_failures + failures), {})
        state('finished_with_errors' if report['exit_code'] or failures else 'finished',
              stage='complete', planned=len(payload['selection']['case_ids']), scoring_failures=failures,
              generation_failures=generation_failures,
              **{key: value for key, value in first_failure.items() if key != 'run_dir'})
        return 1 if report['exit_code'] or failures else 0
    except BaseException as exc:
        state('interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
              stage=stage, **exception_details(exc, phase=stage, directory=run))
        raise
