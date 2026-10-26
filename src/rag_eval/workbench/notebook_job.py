"""Trusted workbench adapter for the existing captured Notebook runner.

One execution supervises partition subprocesses (``parallel`` at a time). Each
partition retains its own runtime and original state, and is officially scored
as soon as it (and its retries) end, while other partitions still generate.
The child stops starting partitions shortly before the supervisor's timeout so
finished work is scored instead of killed. Optional server settings:

- ``seed_runs`` ({case_id: run dir}): ask again on those runs' prepared
  notebooks instead of importing documents (answer-path candidates);
- ``retry_rounds``: re-ask only the failed cases (``error`` or missing) of a
  partition, on its own prepared notebook, up to this many rounds; every round
  stays on disk and ``retry-rounds.json`` names the run used for each case;
- ``preparation_workers``: imported documents processed at once per partition
  (uploads stay sequential; content does not depend on it);
- ``question_workers``, ``case_deadline_seconds``, ``partition_deadline_seconds``:
  questions asked at once inside a partition, and deadlines after which a
  question is saved as ``error`` and the partition finishes;
- ``model_concurrency``: model requests allowed at once across all partition
  processes of this server (slot files under ``<output_root>/model-gate``);
- ``adopt_runs`` ({case_id: run dir}): register already finished runs of the
  same plan, source and conditions as this execution, then score them;
- ``preparation_cache`` (directory, default ``<output_root>/preparation-cache``,
  ``false`` disables): content-addressed prepared notebooks shared by every run
  whose preparation inputs match (see ``rag_eval.preparation_cache``).
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

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
    workers = config.get('question_workers', 1)
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ExecutionRejected('Invalid question_workers setting')
    preparation_workers = config.get('preparation_workers', 1)
    if type(preparation_workers) is not int or not 1 <= preparation_workers <= 64:
        raise ExecutionRejected('Invalid preparation_workers setting')
    from ..model_service_limits import check_preparation_workers
    try:
        check_preparation_workers(preparation_workers, config.get('model_config'))
    except ValueError as exc:
        raise ExecutionRejected(str(exc)) from None
    deadlines = {}
    for key in ('case_deadline_seconds', 'partition_deadline_seconds'):
        value = config.get(key)
        if value is not None and (type(value) not in (int, float) or not 0 < value <= 86400):
            raise ExecutionRejected('Invalid ' + key + ' setting')
        deadlines[key] = value
    gate = config.get('model_concurrency')
    if gate is not None and (type(gate) is not int or not 1 <= gate <= 256):
        raise ExecutionRejected('Invalid model_concurrency setting')
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
        task_worktree=config.get('task_worktree'), preparation_cache=cache or None,
        question_workers=workers, preparation_workers=preparation_workers, **deadlines,
        model_gate=[str(output / 'model-gate'), gate] if gate is not None else None)
    script = Path(__file__).resolve().parents[3] / 'scripts/run_workbench_notebook.py'
    return dict(argv=[config['python'], str(script), '--payload', json.dumps(payload)],
                run_dir=run_dir, preparation_id=preparation_id, budget=approved)


_COMPLETE = ('finished', 'finished_with_errors')


def _outputs(run):
    try:
        return {row['case_id']: row for row in
                (json.loads(line) for line in (run / 'outputs.jsonl').read_text().splitlines() if line.strip())}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _failed_cases(run, case_ids):
    """Cases whose answer failed on a prepared notebook; None when not prepared.

    Only ``error`` and missing answers are asked again: ``no_answer`` and
    ``clarification`` are real outcomes, and nothing is chosen by score.
    """
    try:
        state = json.loads((run / 'product-artifacts/state.json').read_text())
    except (OSError, ValueError):
        return None
    if not state.get('prepared'):
        return None
    outputs = _outputs(run)
    return [cid for cid in case_ids if (outputs.get(cid) or {}).get('status') in (None, 'error')]


def _partition_complete(run):
    """The runner reached its own terminal phase; case errors are statuses, not failures."""
    try:
        phase = json.loads((run / 'state.json').read_text()).get('phase')
    except (OSError, ValueError, AttributeError):
        return False
    return phase in _COMPLETE and (run / 'manifest.json').is_file()


def _preparation_totals(outcomes):
    """Execution-wide preparation diagnostics from each partition's preparation-diagnostics.json."""
    from collections import Counter
    sources = fallback = 0
    errors, infrastructure, partitions = Counter(), Counter(), 0
    for outcome in outcomes:
        path = Path(outcome['job']['run_dir']) / 'preparation-diagnostics.json'
        try:
            item = json.loads(path.read_text())
        except (OSError, ValueError):
            continue  # prepared from the cache or a seed, or not reached
        partitions += 1
        sources += item.get('sources') or 0
        fallback += item.get('fallback_summaries') or 0
        errors.update(item.get('model_call_errors') or {})
        infrastructure.update(item.get('infrastructure_errors') or {})
    return dict(prepared_partitions=partitions, sources=sources, fallback_summaries=fallback,
                model_call_errors=dict(sorted(errors.items())),
                infrastructure_errors=dict(sorted(infrastructure.items())))


def _deadline(payload, started):
    """Stop starting work early enough to score before the supervisor's timeout."""
    from ..notebook_batch import PARTITION_KILL_GRACE
    timeout = (payload.get('budget') or {}).get('timeout_seconds')
    if type(timeout) is not int or timeout <= 0:
        return None
    margin = max(PARTITION_KILL_GRACE + 180, 0.1 * timeout)
    return started + max(timeout / 2, timeout - margin)


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
    started = time.monotonic()
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
        # Verified, byte-compiled and made read-only once; every partition and
        # retry links it into its own run instead of extracting the archive.
        from ..runtime_environment import materialize_shared_sn_source
        shared_source = materialize_shared_sn_source(ArtifactStore(payload['artifact_root']), payload['sn_ref'],
                                                     inputs / 'sn-source')
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
            preparation_cache=payload.get('preparation_cache'),
            question_workers=payload.get('question_workers', 1),
            preparation_workers=payload.get('preparation_workers', 1),
            case_deadline_seconds=payload.get('case_deadline_seconds'),
            partition_deadline_seconds=payload.get('partition_deadline_seconds'),
            model_gate=tuple(payload['model_gate']) if payload.get('model_gate') else None,
            shared_sn_source=shared_source)
        seeds = None
        if payload.get('seed_runs'):
            seeds = inputs / 'seed-runs.json'
            save_json(seeds, payload['seed_runs'])
        jobs = plan_batch(case_id_file=ids_file, output_root=run / 'partitions', **common,
            seed_prepared_map=seeds, seed_allow_source_change=seeds is not None,
            preparation_policy='from-seed' if seeds else payload.get('preparation_policy', DEFAULT_PREPARATION_POLICY))
        if [j['partition_id'] for j in jobs] != payload['selection']['partition_ids']:
            raise ValueError('Partition expansion differs from the fixed plan')
        for ordinal, job in enumerate(jobs, 1):
            job['official_name'] = '%04d' % ordinal
        parallel = payload.get('parallel', 1)
        from ..run_ledger import LedgerAppender
        ledger, ledger_lock = LedgerAppender(run / 'runs.jsonl'), threading.Lock()

        def append_ledger(*args, **kwargs):
            with ledger_lock:
                return ledger.append(*args, **kwargs)

        context, scoring_lock, loaded = RunReadContext(), threading.Lock(), {}

        def score(partition, case_ids, name):
            """Official scoring of one saved run; serialized, overlapping other partitions' generation."""
            with scoring_lock:
                if 'bundle' not in loaded:
                    # The same context serves export below: one verified load per execution.
                    loaded['bundle'] = context.bundle(payload['bundle'])
                bundle = loaded['bundle']
                submission = None
                try:
                    official = run / 'official' / name
                    submission_path = export_sn_runs(payload['bundle'], [partition], official / 'submission',
                                                     case_ids=case_ids, context=context)
                    submission = json.loads(submission_path.read_text())
                    scored = score_prepared(bundle, submission, prepare_inputs(bundle, submission),
                                            source_directory=payload['scorer_sources'], output_dir=official / 'scores')
                except Exception as exc:
                    return None, None, dict(run_dir=str(partition), **exception_details(
                        exc, phase='scoring', directory=run,
                        sensitive_values=(*sensitive_strings(bundle.get('cases', [])),
                                          *sensitive_strings(submission))))
            from .run_views import PRIMARY_METRIC
            metric = PRIMARY_METRIC.get(scored.get('suite'))
            return {row['case_id']: (row.get('metrics') or {}).get(metric)
                    for row in scored.get('per_case', [])}, metric, None

        outcomes, retry_count = {}, payload.get('retry_rounds', 0)

        def retry(job, outcome, failed, number):
            """Ask only ``failed`` again, seeded from the partition's own prepared notebook."""
            tag = f"{job['official_name']}-r{number}"
            retry_ids, retry_seeds = inputs / f'retry-{tag}-case-ids.txt', inputs / f'retry-{tag}-seeds.json'
            retry_ids.write_text('\n'.join(failed) + '\n')
            save_json(retry_seeds, {cid: job['run_dir'] for cid in failed})
            retry_root = run / f'retry-{number}' / job['official_name']
            retried = plan_batch(case_id_file=retry_ids, output_root=retry_root, **common,
                                 seed_prepared_map=retry_seeds, seed_allow_source_change=True,
                                 preparation_policy='from-seed')
            report = execute_batch(retried, output_root=retry_root, parallel=1, ledger_path=run / 'runs.jsonl',
                                   deadline=deadline, ledger_appender=append_ledger)
            save_json(retry_root / 'batch-report.json', report)
            retry_run = Path(retried[0]['run_dir'])
            complete = _partition_complete(retry_run)
            answered = _outputs(retry_run) if complete else {}
            for cid in failed:
                if cid in answered:
                    outcome['selection'][cid] = str(retry_run)
            outcome['retries'].append(dict(round=number, run_dir=str(retry_run), official_name=tag,
                                           case_ids=list(failed), complete=complete,
                                           exit_code=(report['runs'][0] if report['runs'] else {}).get('exit_code')))
            return [cid for cid in failed if (answered.get(cid) or {}).get('status') in (None, 'error')]

        def finish_partition(job, row):
            """Retry failed cases on the partition's own notebook, then score every run it used."""
            partition = Path(job['run_dir'])
            outcome = outcomes[job['partition_id']] = dict(job=job, row=row, retries=[], selection={},
                                                           failures=[], per_case={}, metric=None)
            failed = _failed_cases(partition, job['case_ids']) if row is not None else None
            for number in range(1, retry_count + 1):
                if not failed or (deadline is not None and time.monotonic() >= deadline):
                    break
                try:
                    failed = retry(job, outcome, failed, number)
                except Exception as exc:
                    # The original answers are still scored; the retry failure is kept.
                    outcome['retry_error'] = exception_details(exc, phase=f'retry-{number}', directory=run)
                    break
            outcome['still_failed'] = len(failed or [])
            if row is None:
                saved = json.loads((partition / 'state.json').read_text()) if (partition / 'state.json').is_file() else {}
                generated = saved.get('phase') == 'finished'
                row = outcome['row'] = dict(state=saved, exit_code=None)
            else:
                # Exit 0 already means a finished run; a non-zero exit after the
                # runner's own terminal phase only reflects case-level errors.
                generated = ((row.get('exit_code') == 0 or _partition_complete(partition))
                             and not row.get('stage_metrics_error') and not row.get('ledger_append_error'))
            if not generated:
                saved = row.get('state') or {}
                details = dict(error_type=saved.get('error_type') or row.get('error_type') or
                    row.get('stage_metrics_error') or row.get('ledger_append_error') or 'child_exit',
                    error_message=saved.get('error_message') or row.get('error_message') or
                        row.get('stderr_tail') or f"Partition process exited with code {row.get('exit_code')}",
                    failed_phase=saved.get('failed_phase') or row.get('failed_phase') or saved.get('phase') or 'generating',
                    diagnostic_path=saved.get('diagnostic_path') or row.get('diagnostic_path') or row.get('stderr_path'))
                outcome['generation_failure'] = dict(run_dir=str(partition), **{
                    key: sanitize_diagnostic(value)[:2048] if isinstance(value, str) else value
                    for key, value in details.items()})
            used = {}
            targets = [(partition, job['case_ids'], job['official_name'])] + [
                (Path(r['run_dir']), r['case_ids'], r['official_name']) for r in outcome['retries'] if r['complete']]
            for path, case_ids, name in targets:
                if not (path / 'manifest.json').is_file():
                    outcome['failures'].append(dict(run_dir=str(path), error_type='missing_manifest',
                        error_message='Partition did not save a manifest; official scoring is unavailable',
                        failed_phase='scoring', diagnostic_path=None))
                    continue
                values, metric, failure = score(path, case_ids, name)
                if failure is not None:
                    outcome['failures'].append(failure)
                    continue
                used[str(path)] = values
                outcome['metric'] = outcome['metric'] or metric
            for cid in job['case_ids']:
                source = outcome['selection'].get(cid, job['run_dir'])
                if cid in used.get(source, {}):
                    outcome['per_case'][cid] = used[source][cid]

        deadline = _deadline(payload, started)
        if payload.get('adopt_runs'):
            stage = 'adopting'
            state('running', stage=stage, planned=planned)
            jobs = _adopt(payload, jobs)
            for ordinal, job in enumerate(jobs, 1):
                job['official_name'] = '%04d' % ordinal
            save_json(run / 'adopted-runs.json', [job['run_dir'] for job in jobs])
            stage = 'scoring'
            state('running', stage=stage, planned=planned)
            for job in jobs:
                finish_partition(job, None)
        else:
            save_json(run / 'batch-plan.json', jobs)
            stage = 'generating'
            state('running', stage=stage, planned=planned)
            by_run = {job['run_dir']: job for job in jobs}
            futures = {}
            with ThreadPoolExecutor(max_workers=parallel) as followups:
                def after_run(job, row):
                    futures[job['run_dir']] = followups.submit(finish_partition, job, row)
                report = execute_batch(jobs, output_root=run / 'partitions', parallel=parallel,
                                       ledger_path=run / 'runs.jsonl', after_run=after_run, deadline=deadline,
                                       ledger_appender=append_ledger)
                save_json(run / 'batch-report.json', report)
                for row in report['runs']:
                    if row['run_dir'] not in futures:
                        futures[row['run_dir']] = followups.submit(finish_partition, by_run[row['run_dir']], row)
                stage = 'scoring'
                state('running', stage=stage, planned=planned)
                for future in futures.values():
                    future.result()
        ordered = [outcomes[job['partition_id']] for job in jobs if job['partition_id'] in outcomes]
        for outcome in ordered:
            outcome['job']['retries'] = [dict(r, selected_case_ids=sorted(
                cid for cid, source in outcome['selection'].items() if source == r['run_dir']))
                for r in outcome['retries']]
        save_json(run / 'batch-plan.json', jobs)
        rounds = [dict(round=n, partitions=sum(1 for o in ordered if len(o['retries']) >= n),
                       still_failed=sum(o['still_failed'] for o in ordered if len(o['retries']) == n))
                  for n in range(1, max((len(o['retries']) for o in ordered), default=0) + 1)]
        save_json(run / 'retry-rounds.json', dict(format='sn-workbench-retry-rounds-v2', rounds=rounds,
            note='Only failed (error/missing) cases are asked again; a case uses the latest run that answered it.',
            partitions={o['job']['official_name']: dict(run_dir=o['job']['run_dir'], retries=o['job']['retries'],
                                                        selection=o['selection'])
                        for o in ordered if o['retries']}))
        generation_failures = [o['generation_failure'] for o in ordered if 'generation_failure' in o]
        generation_failures += [dict(run_dir=job['run_dir'], error_type='not_started',
                                     error_message='Partition produced no batch row', failed_phase='generating',
                                     diagnostic_path=None)
                                for job in jobs if job['partition_id'] not in outcomes]
        failures = [failure for o in ordered for failure in o['failures']]
        per_case = {cid: value for o in ordered for cid, value in o['per_case'].items()}
        metric = next((o['metric'] for o in ordered if o['metric']), None)
        values = [v for v in per_case.values() if v is not None]
        save_json(run / SUMMARY, dict(metric=metric, planned=planned, scored=len(values),
                                      mean=sum(values) / len(values) if values else None,
                                      partitions=len(jobs), adopted=bool(payload.get('adopt_runs')),
                                      seeded=bool(seeds), retry_rounds=rounds,
                                      partitions_with_case_errors=sum(
                                          1 for o in ordered if any(row.get('status') == 'error' for row in
                                                                    _outputs(Path(o['job']['run_dir'])).values())),
                                      preparation=_preparation_totals(ordered)))
        save_json(run / 'scoring-status.json', dict(failures=failures))
        first_failure = next(iter(generation_failures + failures), {})
        failed = bool(generation_failures or failures)
        state('finished_with_errors' if failed else 'finished',
              stage='complete', planned=planned, scoring_failures=failures,
              generation_failures=generation_failures,
              **{key: value for key, value in first_failure.items() if key != 'run_dir'})
        return 1 if failed else 0
    except BaseException as exc:
        state('interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
              stage=stage, **exception_details(exc, phase=stage, directory=run))
        raise
