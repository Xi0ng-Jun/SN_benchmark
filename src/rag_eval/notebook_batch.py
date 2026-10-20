"""Expand a frozen case list into fresh single-partition CLI processes.

This module does not import SN or change runner/scoring behavior. An append_run
callable can override the delivered M0-B append. Incomplete runs that the ledger
cannot derive go to a clearly marked fallback. Failed partitions never retry.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from .run_results import EventJournal
from .artifacts import digest
from .benchmark_runtime import DEFAULT_PREPARATION_POLICY, validate_preparation_policy
from .identity import fingerprint
from .execution_diagnostics import exception_details, persist_stderr, sanitize_diagnostic


def plan_batch(*, bundle, artifact_root, artifact_index_id, case_id_file, mode,
               output_root, project_root, python=None, model_config=None,
               sn_source_ref=None, dependency_requirements=None, runtime_expectation=None,
               root=None, budget_ledger=None, task_worktree_ref=None,
               reasoning_protocol='reasoning-v1', preparation_policy=DEFAULT_PREPARATION_POLICY,
               seed_prepared_map=None):
    """Validate all selected IDs before any process; retain frozen case order."""
    from .bundle_index import find_installed_bundle, load_partition
    from .notebook_bundle import OFFICIAL_REQUEST_REVISION
    from .notebook_runner import load_case_ids_file
    validate_preparation_policy(preparation_policy)
    if mode not in {'chunk', 'reasoning'}:
        raise ValueError('Mode must be chunk or reasoning')
    from .reasoning_protocol import protocol_identity
    protocol_identity(reasoning_protocol)
    if mode != 'reasoning' and reasoning_protocol != 'reasoning-v1':
        raise ValueError('Reasoning protocol variants require reasoning mode')
    bundle, output_root = Path(bundle).resolve(), Path(output_root).resolve()
    project_root, artifact_root = Path(project_root).resolve(), Path(artifact_root).resolve()
    root = Path(root).resolve() if root else Path(__file__).resolve().parents[2]
    if output_root.exists():
        raise ValueError('Use a new output root')
    for protected in (bundle, project_root, artifact_root, root / 'src', root / 'scripts'):
        if output_root.is_relative_to(protected) or protected.is_relative_to(output_root):
            raise ValueError('Output root must be separate from input/store/project')
    startup = (sn_source_ref, dependency_requirements, runtime_expectation)
    if any(v is not None for v in startup) and not all(v is not None for v in startup):
        raise ValueError('Captured startup requires all three startup inputs')
    selected = set(load_case_ids_file(case_id_file))
    refs = find_installed_bundle(bundle, artifact_root, expected_index_id=artifact_index_id)
    from .artifact_store import ArtifactStore
    store = ArtifactStore(artifact_root)
    index = json.loads((store.resolve(refs['index'], files=['index.json']) / 'index.json').read_text())
    groups, seen = [], set()
    for pid in index['partitions']:
        capsule = load_partition(artifact_root, refs, pid, OFFICIAL_REQUEST_REVISION)
        ids = [cid for cid in capsule['partition']['case_ids'] if cid in selected]
        if seen.intersection(ids):
            raise ValueError('Selected case belongs to multiple partitions')
        seen.update(ids)
        if ids:
            groups.append((pid, ids))
    if seen != selected:
        raise ValueError('Case ID file contains IDs outside frozen bundle')
    seeds = None
    if seed_prepared_map is not None:
        seeds = json.loads(Path(seed_prepared_map).read_text(encoding='utf-8'))
        if (type(seeds) is not dict or set(seeds) != selected
                or any(type(value) is not str or not Path(value).is_absolute() for value in seeds.values())):
            raise ValueError('Seed map must give an absolute earlier run directory for every selected case')
    jobs = []
    for ordinal, (pid, ids) in enumerate(groups, 1):
        run = output_root / ('%04d-' % ordinal + hashlib.sha256(pid.encode()).hexdigest()[:16] + '-' + mode)
        argv = [str(python or sys.executable), str(root / 'scripts/run_notebook_benchmarks.py'),
                '--bundle', str(bundle), '--artifact-root', str(artifact_root),
                '--artifact-index-id', artifact_index_id, '--partition-id', pid,
                '--mode', mode, '--run-dir', str(run), '--project-root', str(project_root),
                '--request-revision', OFFICIAL_REQUEST_REVISION,
                '--reasoning-protocol', reasoning_protocol,
                '--preparation-policy', preparation_policy]
        for flag, value in (('--model-config', model_config), ('--sn-source-ref', sn_source_ref),
                            ('--dependency-requirements', dependency_requirements),
                            ('--runtime-expectation', runtime_expectation), ('--budget-ledger', budget_ledger), ('--task-worktree-ref', task_worktree_ref)):
            if value is not None:
                argv.extend([flag, str(Path(value).resolve())])
        for cid in ids:
            argv.extend(['--case-id', cid])
        seed = None
        if seeds is not None:
            # One partition is one prepared notebook; its cases must share the seed.
            seed_runs = {seeds[cid] for cid in ids}
            if len(seed_runs) != 1:
                raise ValueError('Cases of one partition must share one prepared seed run')
            seed = seed_runs.pop()
            argv.extend(['--seed-prepared-run', seed])
        jobs.append(dict(partition_id=pid, case_ids=ids, run_dir=str(run), mode=mode,
                         **({'seed_prepared_run': seed} if seed is not None else {}),
                         reasoning_protocol=reasoning_protocol, preparation_policy=preparation_policy,
                         suite=index['source_manifest']['suite'], bundle_id=fingerprint(index['source_manifest']),
                         bundle=str(bundle), artifact_root=str(artifact_root),
                         artifact_index_id=artifact_index_id, case_id_file=str(Path(case_id_file).resolve()),
                         case_id_file_sha256=digest(case_id_file),
                         argv=argv))
    return jobs


def _safe_tail(value):
    """Child stderr is untrusted; never expose service URLs or token values."""
    return sanitize_diagnostic(value)[-4096:]


def _run_job(job, diagnostic_directory):
    started = datetime.now(timezone.utc).isoformat()
    clock = time.monotonic()
    details = {}
    try:
        child = subprocess.run(job['argv'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', errors='replace', check=False)
        code, stderr = child.returncode, sanitize_diagnostic(child.stderr)
        error_type = None
    except OSError as exc:
        code, error_type = None, type(exc).__name__
        details = exception_details(exc, phase='spawn', directory=diagnostic_directory)
        stderr = (Path(details['diagnostic_path']).read_text(encoding='utf-8') if details['diagnostic_path']
                  else details['error_type'] + ': ' + details['error_message'])
    stderr_path = None
    try:
        stderr_path = str(persist_stderr(stderr, directory=diagnostic_directory))
    except OSError as exc:
        details['diagnostic_write_error'] = type(exc).__name__
    tail = _safe_tail(stderr)
    run = Path(job['run_dir'])
    state = None
    if (run / 'state.json').is_file():
        try:
            state = json.loads((run / 'state.json').read_text())
        except (OSError, ValueError):
            state = dict(phase='unknown', reason='unreadable_saved_state')
    row = {k: v for k, v in job.items() if k != 'argv'}
    row.update(format='sn-notebook-batch-run-v1', started_at=started,
               finished_at=datetime.now(timezone.utc).isoformat(), exit_code=code,
               execution_status='exited' if code is not None else 'spawn_failed',
               error_type=error_type, stderr_path=stderr_path, stderr_tail=tail, state=state,
               latency_seconds=time.monotonic() - clock)
    row.update(details)
    if (run / 'outputs.jsonl').is_file() and (run / 'manifest.json').is_file():
        try:
            from .stage_metrics import write_stage_metrics
            metrics = write_stage_metrics([run])['runs'][0]
            row['stage_metrics'] = metrics['summary']
            row['stage_metrics_path'] = str(run / 'stage-metrics.json')
        except Exception as exc:
            # An offline diagnostic failure cannot erase a saved answer/exit.
            row['stage_metrics_error'] = type(exc).__name__
    return row


def execute_batch(jobs, *, output_root, parallel=1, append_run=None, ledger_path=None):
    """Launch each partition once, append its row as soon as it terminates.

    The default calls M0-B append_run(ledger_path, run_dir). An override
    append_run(row) executes on the coordinator thread. Append failures are
    preserved locally and make the batch fail; subsequent partitions still run.
    Exit 0 means all child exits were zero and bookkeeping succeeded, not that
    answers or official scores are successful.
    """
    if type(parallel) is not int or parallel < 1 or not jobs:
        raise ValueError('Provide jobs and a positive parallel limit')
    output_root = Path(output_root).resolve()
    if output_root.exists():
        raise FileExistsError('Use a new output root')
    paths = [Path(j['run_dir']).resolve() for j in jobs]
    if len(set(paths)) != len(paths) or any(p.exists() for p in paths):
        raise ValueError('Use distinct new run directories')
    if any(not p.is_relative_to(output_root) for p in paths):
        raise ValueError('Run directories must belong to the output root')
    backend = 'external-append' if append_run else None
    if append_run is None:
        try:
            from .run_ledger import LedgerAppender
        except ModuleNotFoundError as exc:
            if exc.name != 'rag_eval.run_ledger':
                raise
        else:
            ledger_path = Path(ledger_path).resolve() if ledger_path else output_root / 'runs.jsonl'
            # Same checks as run_ledger.append_run, without re-parsing the
            # whole ledger for every finished partition.
            ledger_append = LedgerAppender(ledger_path).append
            def append_run(row):
                ledger_append(row['run_dir'], stage_metrics_path=row.get('stage_metrics_path'),
                              exit_code=row['exit_code'], stderr_summary=row['stderr_tail'])
            backend = 'M0-B append_run'
    output_root.mkdir(parents=True, exist_ok=False)
    diagnostic_directory = output_root / 'diagnostics'
    diagnostic_directory.mkdir(mode=0o700)
    executions = EventJournal(output_root / 'batch-executions.jsonl')
    local = None
    rows, bookkeeping_failed = [], False
    try:
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = [pool.submit(_run_job, j, diagnostic_directory) for j in jobs]
            for future in as_completed(futures):
                row = future.result()
                if append_run is not None:
                    try:
                        row['ledger_backend'] = backend
                        append_run(row)
                    except Exception as exc:
                        bookkeeping_failed = True
                        row['ledger_append_error'] = type(exc).__name__
                        row['ledger_backend'] = ('local-fallback; M0-B append failed' if backend == 'M0-B append_run'
                                                 else 'local-fallback; external append failed')
                else:
                    row['ledger_backend'] = 'local-fallback; M0-B append not connected'
                if row['ledger_backend'].startswith('local-fallback'):
                    if local is None:
                        local = EventJournal(output_root / 'runs.local.jsonl')
                    local(row)
                executions(row)
                rows.append(row)
    finally:
        executions.close()
        if local is not None:
            local.close()
    return dict(format='sn-notebook-batch-v1', runs=rows,
                exit_code=1 if bookkeeping_failed or any(r['exit_code'] != 0 or r.get('stage_metrics_error')
                                                        for r in rows) else 0)
