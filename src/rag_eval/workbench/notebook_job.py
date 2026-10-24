"""Trusted workbench adapter for the existing captured Notebook runner.

One execution supervises partition subprocesses (``parallel`` at a time). Each
partition retains its own runtime and original state; official scoring happens
after generation. Optional server settings:

- ``seed_runs`` ({case_id: run dir}): ask again on those runs' prepared
  notebooks instead of importing documents (answer-path candidates);
- ``retry_rounds``: re-ask a partition whose answer failed, on its own prepared
  notebook, up to this many rounds; every round stays on disk;
- ``adopt_runs`` ({case_id: run dir}): register already finished runs of the
  same plan, source and conditions as this execution, then score them;
- ``preparation_cache`` (directory, default ``<output_root>/preparation-cache``,
  ``false`` disables): content-addressed prepared notebooks shared by every run
  whose preparation inputs match (see ``rag_eval.preparation_cache``).
"""
import hashlib
import json
from pathlib import Path
import sys

from ..artifact_store import ArtifactStore
from ..artifacts import digest, save_json
from ..execution_diagnostics import exception_details, sanitize_diagnostic, sensitive_strings
from ..benchmark_runtime import DEFAULT_PREPARATION_POLICY, validate_preparation_policy
from .job_execution import ExecutionRejected, validate_execution_budget

SUMMARY = 'official-summary.json'


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
    parallel = config.get('parallel', 1)
    rounds = config.get('retry_rounds', 0)
    if type(parallel) is not int or not 1 <= parallel <= 64 or type(rounds) is not int or not 0 <= rounds <= 10:
        raise ExecutionRejected('Invalid parallel or retry_rounds setting')
    policy = config.get('preparation_policy', DEFAULT_PREPARATION_POLICY)
    try:
        validate_preparation_policy(policy)
    except ValueError:
        raise ExecutionRejected('Invalid preparation policy setting') from None
    cache = config.get('preparation_cache')
    if cache is None:
        cache = str(output / 'preparation-cache')
    elif cache is not False and (type(cache) is not str or not Path(cache).is_absolute()):
        raise ExecutionRejected('preparation_cache must be an absolute directory or false')
    extras = {}
    for key in ('seed_runs', 'adopt_runs'):
        value = config.get(key)
        if value is None:
            continue
        planned_ids = prep['input']['selection']['case_ids']
        if (type(value) is not dict or not set(planned_ids) <= set(value)
                or any(type(value[c]) is not str or not Path(value[c]).is_absolute() for c in planned_ids)):
            raise ExecutionRejected(key + ' must give an absolute run directory for every planned case')
        # One server map may serve several plans; only this plan's cases travel.
        extras[key] = {c: value[c] for c in planned_ids}
    if len(extras) > 1:
        raise ExecutionRejected('An execution either adopts finished runs or generates')
    maps = None
    if extras:
        # Hundreds of run paths exceed one argv string's limit; keep them beside the run.
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = json.dumps(extras, ensure_ascii=False, sort_keys=True).encode('utf-8')
        path = output / (job_id + '-' + suffix + '.runs.json')
        if not path.exists():
            with path.open('xb') as stream:
                stream.write(data)
        if path.is_symlink() or path.read_bytes() != data:
            raise ExecutionRejected('Saved run map differs for this request')
        maps = dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(), keys=sorted(extras))
    payload = dict(run_dir=run_dir, job_id=job_id, preparation_id=preparation_id,
        parallel=parallel, retry_rounds=rounds, preparation_policy=policy, run_maps=maps,
        artifact_root=str(store.root), bundle=str(bundle), bundle_ref=prep['input']['bundle_ref'],
        index_ref=prep['input']['index_ref'], sn_ref=prep['source']['artifact_ref'],
        selection=prep['input']['selection'], mode=prep['execution_intent']['mode'],
        project_root=str(Path(config['project_root']).resolve()), python=config['python'],
        files=files, scorer_sources=config['scorer_sources'], budget=approved,
        task_worktree=config.get('task_worktree'), preparation_cache=cache or None)
    script = Path(__file__).resolve().parents[3] / 'scripts/run_workbench_notebook.py'
    return dict(argv=[config['python'], str(script), '--payload', json.dumps(payload)],
                run_dir=run_dir, preparation_id=preparation_id, budget=approved)


def _needs_retry(run):
    """An answer failed on a prepared notebook: re-asking can succeed."""
    try:
        state = json.loads((run / 'product-artifacts/state.json').read_text())
    except (OSError, ValueError):
        return False
    if not state.get('prepared'):
        return False
    try:
        rows = [json.loads(line) for line in (run / 'outputs.jsonl').read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        return True
    saved = json.loads((run / 'state.json').read_text()) if (run / 'state.json').is_file() else {}
    return not rows or saved.get('phase') != 'finished' or any(row.get('status') == 'error' for row in rows)


def _adopt(payload, jobs):
    """Bind finished runs that match this execution's plan, source and conditions."""
    from ..artifact_store import ArtifactStore
    store = ArtifactStore(payload['artifact_root'])
    source = json.loads((store.resolve(payload['sn_ref']) / 'object.json').read_text())['identity']
    adopted = []
    for job in jobs:
        dirs = {payload['adopt_runs'][cid] for cid in job['case_ids']}
        if len(dirs) != 1:
            raise ValueError('Cases of one partition must come from one adopted run')
        run = Path(dirs.pop()).resolve()
        if run.is_symlink() or not (run / 'manifest.json').is_file():
            raise ValueError('Adopted run lacks a manifest: ' + str(run))
        manifest = json.loads((run / 'manifest.json').read_text())
        identity = manifest.get('identity') or {}
        code = identity.get('code') or {}
        checks = (
            (manifest.get('mode') == payload['mode'], 'mode'),
            ((identity.get('notebook_context') or {}).get('partition_id') == job['partition_id'], 'partition'),
            ((identity.get('notebook_context') or {}).get('request_revision') == 'notebook-request-v3', 'request'),
            # The same committed tree is the same source; capture manifests of
            # older evaluator versions may hash it differently.
            (code.get('head_revision') == source.get('head_revision') is not None
             and code.get('head_tree') in (None, source.get('head_tree'))
             and not (code.get('modified_paths') or code.get('added_paths') or code.get('deleted_paths')),
             'SN source'),
        )
        failed = [name for ok, name in checks if not ok]
        if failed:
            raise ValueError(f'Adopted run {run} differs from this execution: ' + ', '.join(failed))
        adopted.append(dict(job, run_dir=str(run), adopted=True, argv=None))
    return adopted


def execute(payload):
    """Run existing partition CLI and independent official scoring; never retry by score."""
    from ..benchmark_official import prepare_inputs, score_prepared
    from ..benchmark_submission import export_sn_runs
    from ..notebook_batch import plan_batch, execute_batch
    from ..notebook_bundle import load_bundle
    from ..run_reader import RunReadContext
    run = Path(payload['run_dir']).resolve()
    # Check before publishing runtime artifacts or starting any child.
    for file in payload['files'].values():
        if digest(file['path']) != file['sha256']:
            raise ValueError('Runtime file changed after execution was prepared')
    if payload.get('run_maps'):
        data = Path(payload['run_maps']['path']).read_bytes()
        if hashlib.sha256(data).hexdigest() != payload['run_maps']['sha256']:
            raise ValueError('Run map changed after execution was prepared')
        payload = dict(payload, **json.loads(data))
    run.mkdir(parents=True, exist_ok=False, mode=0o700)
    planned = len(payload['selection']['case_ids'])
    def state(phase, **details):
        save_json(run / 'state.json', dict(phase=phase, terminal=phase != 'running', **details))
    stage = 'preparing'
    state('running', stage=stage, planned=planned)
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
        common = dict(bundle=payload['bundle'], artifact_root=payload['artifact_root'],
            artifact_index_id=payload['index_ref']['id'], mode=payload['mode'],
            project_root=payload['project_root'], python=payload['python'],
            model_config=frozen_files['model_config'],
            sn_source_ref=inputs / 'sn-source-ref.json',
            dependency_requirements=frozen_files['dependency_requirements'],
            runtime_expectation=frozen_files['runtime_expectation'],
            budget_ledger=budget_ledger, task_worktree_ref=association,
            preparation_cache=payload.get('preparation_cache'))
        seeds = None
        if payload.get('seed_runs'):
            seeds = inputs / 'seed-runs.json'
            save_json(seeds, payload['seed_runs'])
        jobs = plan_batch(case_id_file=ids_file, output_root=run / 'partitions', **common,
            seed_prepared_map=seeds, seed_allow_source_change=seeds is not None,
            preparation_policy='from-seed' if seeds else payload.get('preparation_policy', DEFAULT_PREPARATION_POLICY))
        if [j['partition_id'] for j in jobs] != payload['selection']['partition_ids']:
            raise ValueError('Partition expansion differs from the fixed plan')
        rounds, rows = [], {}
        if payload.get('adopt_runs'):
            stage = 'adopting'
            state('running', stage=stage, planned=planned)
            jobs = _adopt(payload, jobs)
            save_json(run / 'adopted-runs.json', [job['run_dir'] for job in jobs])
            exit_code = 0
        else:
            save_json(run / 'batch-plan.json', jobs)
            stage = 'generating'
            state('running', stage=stage, planned=planned)
            report = execute_batch(jobs, output_root=run / 'partitions', parallel=payload.get('parallel', 1),
                                   ledger_path=run / 'runs.jsonl')
            save_json(run / 'batch-report.json', report)
            rows.update({row['run_dir']: row for row in report['runs']})
            for number in range(1, payload.get('retry_rounds', 0) + 1):
                failed = [job for job in jobs if _needs_retry(Path(job['run_dir']))]
                if not failed:
                    break
                stage = f'retry-{number}'
                state('running', stage=stage, planned=planned, retrying=len(failed))
                retry_ids = inputs / f'retry-{number}-case-ids.txt'
                retry_ids.write_text('\n'.join(cid for job in failed for cid in job['case_ids']) + '\n')
                retry_seeds = inputs / f'retry-{number}-seeds.json'
                save_json(retry_seeds, {cid: job['run_dir'] for job in failed for cid in job['case_ids']})
                retried = plan_batch(case_id_file=retry_ids, output_root=run / f'retry-{number}', **common,
                                     seed_prepared_map=retry_seeds, seed_allow_source_change=True,
                                     preparation_policy='from-seed')
                retry_report = execute_batch(retried, output_root=run / f'retry-{number}',
                                             parallel=payload.get('parallel', 1), ledger_path=run / 'runs.jsonl')
                save_json(run / f'retry-{number}-report.json', retry_report)
                rows.update({row['run_dir']: row for row in retry_report['runs']})
                replaced = {job['partition_id']: job for job in retried}
                rounds.append(dict(round=number, partitions=len(retried),
                                   still_failed=sum(_needs_retry(Path(j['run_dir'])) for j in retried)))
                jobs = [replaced.get(job['partition_id'], job) for job in jobs]
            save_json(run / 'retry-rounds.json', rounds)
            exit_code = 0
        for ordinal, job in enumerate(jobs, 1):
            job['official_name'] = '%04d' % ordinal
        save_json(run / 'batch-plan.json', jobs)
        generation_failures = []
        for job in jobs:
            partition = Path(job['run_dir'])
            row = rows.get(job['run_dir'])
            if row is None:
                # Adopted runs have no child process in this execution; read their saved terminal state.
                saved = json.loads((partition / 'state.json').read_text()) if (partition / 'state.json').is_file() else {}
                if saved.get('phase') == 'finished':
                    continue
                row = dict(state=saved, exit_code=None)
            elif row.get('exit_code') == 0 and not row.get('stage_metrics_error') and not row.get('ledger_append_error'):
                continue
            exit_code = 1
            saved = row.get('state') or {}
            details = dict(error_type=saved.get('error_type') or row.get('error_type') or
                row.get('stage_metrics_error') or row.get('ledger_append_error') or 'child_exit',
                error_message=saved.get('error_message') or row.get('error_message') or
                    row.get('stderr_tail') or f"Partition process exited with code {row.get('exit_code')}",
                failed_phase=saved.get('failed_phase') or row.get('failed_phase') or saved.get('phase') or stage,
                diagnostic_path=saved.get('diagnostic_path') or row.get('diagnostic_path') or row.get('stderr_path'))
            details = {key: sanitize_diagnostic(value)[:2048] if isinstance(value, str) else value
                       for key, value in details.items()}
            generation_failures.append(dict(run_dir=str(partition), **details))
        stage = 'scoring'
        state('running', stage=stage, planned=planned, generation_failures=generation_failures)
        bundle = load_bundle(payload['bundle'])
        context = RunReadContext()
        failures, per_case, metric = [], {}, None
        for job in jobs:
            partition = Path(job['run_dir'])
            if not (partition / 'manifest.json').is_file():
                failures.append(dict(run_dir=str(partition), error_type='missing_manifest',
                    error_message='Partition did not save a manifest; official scoring is unavailable',
                    failed_phase=stage, diagnostic_path=None))
                continue
            submission = None
            try:
                official = run / 'official' / job['official_name']
                submission_path = export_sn_runs(payload['bundle'], [partition], official / 'submission',
                                                case_ids=job['case_ids'], context=context)
                submission = json.loads(submission_path.read_text())
                scored = score_prepared(bundle, submission, prepare_inputs(bundle, submission),
                                        source_directory=payload['scorer_sources'], output_dir=official / 'scores')
                from .run_views import PRIMARY_METRIC
                metric = metric or PRIMARY_METRIC.get(scored.get('suite'))
                for row in scored.get('per_case', []):
                    per_case[row['case_id']] = (row.get('metrics') or {}).get(metric)
            except Exception as exc:
                failures.append(dict(run_dir=str(partition), **exception_details(exc, phase=stage, directory=run,
                    sensitive_values=(*sensitive_strings(bundle.get('cases', [])),
                                      *sensitive_strings(submission)))))
        values = [v for v in per_case.values() if v is not None]
        save_json(run / SUMMARY, dict(metric=metric, planned=planned, scored=len(values),
                                      mean=sum(values) / len(values) if values else None,
                                      partitions=len(jobs), adopted=bool(payload.get('adopt_runs')),
                                      seeded=bool(seeds), retry_rounds=rounds))
        save_json(run / 'scoring-status.json', dict(failures=failures))
        first_failure = next(iter(generation_failures + failures), {})
        failed = exit_code or failures
        state('finished_with_errors' if failed else 'finished',
              stage='complete', planned=planned, scoring_failures=failures,
              generation_failures=generation_failures,
              **{key: value for key, value in first_failure.items() if key != 'run_dir'})
        return 1 if failed else 0
    except BaseException as exc:
        state('interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
              stage=stage, **exception_details(exc, phase=stage, directory=run))
        raise
