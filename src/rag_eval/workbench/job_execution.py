"""Small, durable dispatcher for executable workbench Jobs.

The legacy Job record remains a closed intention.  This module owns the
runtime record and the process handle that belongs to one isolated run
directory.  Callers provide a server-built argv and a frozen execution budget;
HTTP input is never treated as an executable command.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import time

from .evaluation_jobs import _text, _version
from . import env_guard
from ..execution_diagnostics import exception_details, sanitize_diagnostic
from .repository import Conflict, UnknownIdentity, now, uid
from .execution_recovery import claim_supervisor, completed_state, process_identity, RecoveredProcess


FORMAT = 'sn-workbench-job-execution-v1'
_FIELDS = {
    'format', 'execution_id', 'job_id', 'task_id', 'state', 'terminal',
    'argv', 'run_dir', 'budget', 'preparation_id', 'started_at', 'finished_at',
    'cancelled_at', 'exit_code', 'run_state', 'error_type'
}


class ExecutionRejected(ValueError):
    """The execution contract or current lifecycle state is invalid."""


class CredentialsUnavailable(ExecutionRejected):
    """Infrastructure, not the candidate: declared model credentials are absent (names only)."""

    def __init__(self, names):
        self.names = sorted(names)
        super().__init__('Model service credentials are missing from the execution environment: ' + ', '.join(self.names))


def validate_execution_budget(budget, *, max_limit_cny=None):
    """Keep a timeout for every run; cost enforcement is explicitly optional."""
    if type(budget) is not dict:
        raise ExecutionRejected('Invalid execution budget')
    timeout = budget.get('timeout_seconds')
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ExecutionRejected('A timeout between 1 and 86400 seconds is required')
    if 'limit_cny' in budget:
        if budget.get('confirmed') is not True:
            raise ExecutionRejected('Explicit budget confirmation is required for a cost limit')
        limit = budget['limit_cny']
        if (type(limit) not in (int, float) or limit <= 0
                or (type(limit) is float and not math.isfinite(limit))
                or (max_limit_cny is not None and limit > max_limit_cny)):
            raise ExecutionRejected('A positive allowed cost limit is required')
    return deepcopy(budget)


def _validate_spec(spec):
    if type(spec) is not dict or set(spec) != {'argv', 'run_dir', 'budget', 'preparation_id'}:
        raise ExecutionRejected('Invalid execution specification')
    argv = spec['argv']
    if (type(argv) is not list or not argv or
            any(type(value) is not str or not value for value in argv)):
        raise ExecutionRejected('Invalid execution argv')
    run_dir = spec['run_dir']
    if type(run_dir) is not str or not run_dir:
        raise ExecutionRejected('Invalid execution run directory')
    path = Path(run_dir).resolve()
    if not path.is_absolute() or path.exists():
        raise ExecutionRejected('Execution run directory must be new')
    budget = validate_execution_budget(spec['budget'])
    if type(spec['preparation_id']) is not str or len(spec['preparation_id']) != 64:
        raise ExecutionRejected('Verified preparation is required')
    return dict(argv=list(argv), run_dir=str(path), budget=budget,
                preparation_id=spec['preparation_id'])


def build_execution_spec(store, task_id, job_id, preparation_id, execution_config, *, request_key='', budget=None):
    """Build argv solely from trusted server configuration and a receipt."""
    if not isinstance(execution_config, dict):
        raise ExecutionRejected('Job execution is not configured')
    from .budgets import validate_model_budget
    validate_model_budget(budget, execution_config)
    if execution_config.get('mode') == 'notebook':
        from .notebook_job import execution_spec
        return execution_spec(store, task_id, job_id, preparation_id, execution_config,
                              request_key=request_key, budget=budget)
    template = execution_config.get('argv')
    output_root = execution_config.get('output_root')
    if (type(template) is not list or not template or
            any(type(value) is not str or not value for value in template) or
            not isinstance(output_root, str) or not output_root.strip()):
        raise ExecutionRejected('Job execution is not configured')
    suffix = hashlib.sha256(request_key.encode('utf-8')).hexdigest()[:16] if request_key else uid()
    run_dir = str((Path(output_root).resolve() / (job_id + '-' + suffix)).resolve())
    argv = [value.replace('{run_dir}', run_dir).replace('{job_id}', job_id)
            .replace('{task_id}', task_id).replace('{preparation_id}', preparation_id)
            for value in template]
    return dict(argv=argv, run_dir=run_dir, preparation_id=preparation_id)


def _closed(record):
    if type(record) is not dict or set(record) != _FIELDS:
        raise ExecutionRejected('Invalid saved execution record')
    if record['format'] != FORMAT or type(record['terminal']) is not bool:
        raise ExecutionRejected('Invalid saved execution record')
    if record['state'] not in {'running', 'finished', 'failed', 'cancelled', 'timeout'}:
        raise ExecutionRejected('Invalid saved execution state')
    if record['terminal'] != (record['state'] != 'running'):
        raise ExecutionRejected('Invalid saved execution terminal state')
    return record


class JobExecutionStore:
    """Persist and supervise one process per execution record."""

    _lock = threading.RLock()
    _processes = {}

    def __init__(self, repository):
        self.repository = repository
        self._supervisors = {}

    def _claim_supervisor(self, execution_id):
        with self._lock:
            if execution_id in self._supervisors:
                return False
            handle = claim_supervisor(self.repository.root, execution_id)
            if handle is None:
                return False
            self._supervisors[execution_id] = handle
            return True

    def _release_supervisor(self, execution_id):
        with self._lock:
            handle = self._supervisors.pop(execution_id, None)
            if handle is not None:
                handle.close()

    def recover(self, *, execution_id=None):
        """Claim orphaned executions without competing with a live supervisor.

        Locks cover launch through settlement. A crash before launch identity
        persistence cannot be distinguished from a child that never started;
        those legacy/ambiguous records are conservatively execution_lost.
        """
        with self.repository.connect() as conn:
            rows = conn.execute("SELECT task_id,object_id FROM history "
                                "WHERE kind='job.execution_started' "
                                "AND (? IS NULL OR object_id=?) ORDER BY seq",
                                (execution_id, execution_id)).fetchall()
        for row in rows:
            with self.repository.connect() as conn:
                record = self._read(conn, row['task_id'], row['object_id'])
            if ((record['terminal'] and record['state'] != 'cancelled')
                    or not self._claim_supervisor(record['execution_id'])):
                continue
            try:
                with self.repository.connect() as conn:
                    record = self._read(conn, row['task_id'], row['object_id'])
                    saved = conn.execute("SELECT content FROM history WHERE object_id=? "
                                         "AND kind='job.execution_process' ORDER BY seq DESC LIMIT 1",
                                         (record['execution_id'],)).fetchone()
                if record['terminal'] and record['state'] != 'cancelled':
                    self._release_supervisor(record['execution_id'])
                    continue
                metadata = json.loads(saved['content']) if saved else {}
                identity = metadata.get('process_identity') if (
                    isinstance(metadata, dict) and metadata.get('execution_id') == record['execution_id']
                    and metadata.get('run_dir') == record['run_dir']) else None
                process = RecoveredProcess.attach(identity)
                if process is not None:
                    with self._lock:
                        self._processes[record['execution_id']] = process
                threading.Thread(target=self._watch_recovered, args=(record, identity, process),
                                 daemon=True).start()
            except BaseException:
                self._release_supervisor(record['execution_id'])
                raise

    def reconcile(self):
        """Settle running executions whose supervisor is gone, such as a dispatching CLI that exited."""
        with self.repository.connect() as conn:
            rows = conn.execute("SELECT object_id FROM history s WHERE kind='job.execution_started' AND NOT EXISTS "
                                "(SELECT 1 FROM history t WHERE t.object_id=s.object_id AND t.kind IN "
                                "('job.execution_finished','job.execution_cancelled'))").fetchall()
        for row in rows:
            if row['object_id'] not in self._supervisors:
                self.recover(execution_id=row['object_id'])

    def _watch_recovered(self, record, identity, process):
        execution_id = record['execution_id']
        try:
            if record['state'] == 'cancelled':
                # The durable cancellation intent can precede physical stop.
                # A restart must finish it without publishing a new outcome.
                if process is not None:
                    if process.signal_group(15):
                        process.stop_after_grace()
                    process.wait()
                self._settle_budget(record)
                try:
                    self._write_record_file(record)
                except OSError:
                    pass
                return
            if process is not None:
                elapsed = (datetime.now(timezone.utc)
                           - datetime.fromisoformat(record['started_at'])).total_seconds()
                remaining = max(0, record['budget']['timeout_seconds'] - elapsed)
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    if process.signal_group(15):
                        process.stop_after_grace()
                    process.wait()
                    self._settle(record, now(), lambda failures: self._finish(
                        execution_id, state='timeout', exit_code=None, error_type='timeout', run_state=None,
                        finish_failures=failures))
                    return
            parent = Path(record['run_dir']).parent
            try:
                self._finalize_logs(record['run_dir'], parent / (Path(record['run_dir']).name + '.stdout.log'),
                                    parent / (Path(record['run_dir']).name + '.stderr.log'))
            except OSError:
                # Runtime artifacts can be unavailable after a crash. SQLite
                # still needs its terminal record and reservation settlement.
                pass
            self._settle(record, now(), lambda failures: self._finish_recovered(record, identity, failures))
        finally:
            with self._lock:
                self._processes.pop(execution_id, None)
            if process is not None:
                process.close()
            self._release_supervisor(execution_id)

    def _finish_recovered(self, record, identity, failures):
        state = completed_state(record, identity)
        if state is not None:
            successful = state['phase'] == 'finished'
            self._finish(record['execution_id'], state='finished' if successful else 'failed',
                         exit_code=None, error_type=None if successful else 'recovered_failure',
                         run_state=state, finish_failures=failures)
        else:
            self._finish(record['execution_id'], state='failed', exit_code=None,
                         error_type='execution_lost', run_state=dict(
                             phase='failed', failed_phase='supervising', error_type='execution_lost',
                             error_message='Execution supervision was lost; no trustworthy terminal outcome is available'),
                         finish_failures=failures)

    def _workspace(self, conn, task_id):
        try:
            return self.repository._require(conn, 'tasks', task_id)['workspace_id']
        except UnknownIdentity:
            raise ExecutionRejected('Unknown execution task identity') from None

    def _read(self, conn, task_id, execution_id):
        rows = conn.execute("SELECT content FROM history WHERE task_id=? AND object_id=? "
                            "AND kind IN ('job.execution_started','job.execution_finished',"
                            "'job.execution_cancelled') ORDER BY seq",
                            (task_id, execution_id)).fetchall()
        if not rows:
            raise ExecutionRejected('Unknown execution identity')
        return _closed(json.loads(rows[-1]['content']))

    def get(self, task_id, execution_id):
        _text(task_id, 'task identity', identity=True)
        _text(execution_id, 'execution identity', identity=True)
        with self.repository.connect() as conn:
            record = deepcopy(self._read(conn, task_id, execution_id))
        path = Path(record['run_dir']) / 'state.json'
        if not record['terminal'] and not path.is_symlink():
            try:
                record['run_state'] = json.loads(path.read_text())
            except (OSError, ValueError):
                pass
        return record

    def latest_for_job(self, task_id, job_id):
        with self.repository.connect() as conn:
            rows = conn.execute("SELECT object_id,content FROM history WHERE task_id=? "
                                "AND kind='job.execution_started' ORDER BY seq DESC", (task_id,)).fetchall()
            for row in rows:
                if json.loads(row['content']).get('job_id') == job_id:
                    execution_id = row['object_id']
                    break
            else:
                return None
        return self.get(task_id, execution_id)

    def records(self, workspace_id):
        with self.repository.connect() as conn:
            rows = conn.execute("SELECT object_id,task_id FROM history WHERE task_id IN "
                                "(SELECT id FROM tasks WHERE workspace_id=?) "
                                "AND kind='job.execution_started' ORDER BY seq",
                                (workspace_id,)).fetchall()
            return [self._read(conn, row['task_id'], row['object_id']) for row in rows]

    def run_receipt(self, task_id, job_id, expected_state_version, preparation_id, budget, *, actor, request_key):
        """Recover a dispatch before consulting mutable runtime files again."""
        scope = 'job.run:' + task_id + ':' + actor
        with self.repository.connect() as conn:
            row = conn.execute('SELECT receipt FROM requests WHERE scope=? AND request_key=?',
                               (scope, request_key)).fetchone()
            if row is None:
                return None
            record = _closed(json.loads(row['receipt']))
            if record['job_id'] != job_id or record['preparation_id'] != preparation_id:
                raise Conflict('Run request key changed')
            spec = {k: deepcopy(record[k]) for k in ('argv', 'run_dir', 'preparation_id', 'budget')}
            spec['budget'] = deepcopy(budget)
            payload = dict(task_id=task_id, job_id=job_id, expected_state_version=expected_state_version,
                           execution_spec=spec, actor=actor)
            return _closed(self.repository._prior(conn, scope, request_key, payload))

    def run(self, task_id, job_id, expected_state_version, *, execution_spec,
            actor, request_key, _sealed_capability=None):
        _text(task_id, 'task identity', identity=True)
        _text(job_id, 'Job identity', identity=True)
        _text(actor, 'actor')
        _text(request_key, 'request key')
        _version(expected_state_version)
        raw_spec = deepcopy(execution_spec)
        payload = dict(task_id=task_id, job_id=job_id,
                       expected_state_version=expected_state_version,
                       execution_spec=raw_spec, actor=actor)
        scope = 'job.run:' + task_id + ':' + actor
        repo = self.repository
        claimed_execution = None
        try:
            with repo.connect(write=True) as conn:
                self._workspace(conn, task_id)
                prior = repo._prior(conn, scope, request_key, payload)
                if prior is not None:
                    return _closed(prior)
                spec = _validate_spec(raw_spec)
                job_row = conn.execute('SELECT record FROM jobs WHERE id=? AND task_id=?',
                                       (job_id, task_id)).fetchone()
                if job_row is None:
                    raise ExecutionRejected('Unknown execution Job identity')
                job = json.loads(job_row['record'])
                from .sealed import _CAPABILITY
                if _sealed_capability is not _CAPABILITY:
                    from .splits import Policy
                    policy = Policy(repo, task_id)
                    for cid in job['plan_snapshot']['content']['selection']['case_ids']:
                        policy.refuse_sealed(cid)
                if job.get('state') != 'not_dispatched' or job.get('state_version') != expected_state_version:
                    raise Conflict('Job changed before execution')
                prepared = conn.execute("SELECT content FROM history WHERE task_id=? AND object_id=? "
                                        "AND kind='job.inputs_source_captured' ORDER BY seq DESC",
                                        (task_id, job_id)).fetchall()
                if not any(json.loads(row['content']).get('preparation', {}).get('preparation_id') ==
                           spec['preparation_id'] for row in prepared):
                    raise ExecutionRejected('Verified preparation is required')
                latest = {}
                for row in conn.execute("SELECT object_id,content FROM history WHERE task_id=? "
                                        "AND kind IN ('job.execution_started','job.execution_finished',"
                                        "'job.execution_cancelled') ORDER BY seq", (task_id,)):
                    latest[row['object_id']] = json.loads(row['content'])
                existing = any(value.get('job_id') == job_id for value in latest.values())
                if existing:
                    raise Conflict('Job already has an execution; prepare a new Job to rerun')
                record = dict(format=FORMAT, execution_id=uid(), job_id=job_id, task_id=task_id,
                              state='running', terminal=False, argv=spec['argv'], run_dir=spec['run_dir'],
                              budget=spec['budget'], preparation_id=spec['preparation_id'],
                              started_at=now(), finished_at=None, cancelled_at=None, exit_code=None,
                              run_state=None, error_type=None)
                _closed(record)
                if not self._claim_supervisor(record['execution_id']):
                    raise Conflict('Execution already has a live supervisor')
                claimed_execution = record['execution_id']
                repo._event(conn, task_id, 'job.execution_started', record['execution_id'], record)
                repo._receipt(conn, scope, request_key, payload, record)
        except BaseException:
            if claimed_execution is not None:
                self._release_supervisor(claimed_execution)
            raise
        try:
            self._start(record)
        except BaseException as exc:
            details = exception_details(exc, phase='launching')
            try:
                self._settle(record, now(), lambda failures: self._finish(
                    record['execution_id'], state='failed', exit_code=None,
                    error_type=type(exc).__name__, run_state=dict(phase='failed', **details),
                    finish_failures=failures))
            finally:
                self._release_supervisor(record['execution_id'])
            return self.get(task_id, record['execution_id'])
        return record

    def _start(self, record):
        run = Path(record['run_dir'])
        if run.exists() or run.is_symlink():
            raise ExecutionRejected('Execution run directory must be new')
        parent = run.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if parent.is_symlink():
            raise ExecutionRejected('Execution run parent cannot be a symlink')
        stdout_path = parent / (run.name + '.stdout.log')
        stderr_path = parent / (run.name + '.stderr.log')
        stdout = stdout_path.open('w', encoding='utf-8')
        stderr = stderr_path.open('w', encoding='utf-8')
        env, restored = env_guard.child_env()
        try:
            process = subprocess.Popen(record['argv'], cwd=parent, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, start_new_session=True,
                                       close_fds=True, env=env)
        except BaseException:
            stdout.close(); stderr.close()
            raise
        try:
            # An immediately exited child remains an unreaped zombie here;
            # its kernel start identity still proves this particular launch.
            identity = process_identity(process.pid, allow_exited=True)
            with self.repository.connect(write=True) as conn:
                self.repository._event(conn, record['task_id'], 'job.execution_process', record['execution_id'],
                                       dict(execution_id=record['execution_id'], run_dir=record['run_dir'],
                                            process_identity=identity,
                                            **({'restored_env_keys': restored} if restored else {})))
        except BaseException:
            try:
                os.killpg(process.pid, 15)
            except ProcessLookupError:
                pass
            self._stop_after_grace(process)
            process.wait()
            stdout.close(); stderr.close()
            raise
        with self._lock:
            self._processes[record['execution_id']] = process
        thread = threading.Thread(target=self._watch,
                                  args=(record, process, stdout, stderr, stdout_path, stderr_path), daemon=True)
        thread.start()

    @staticmethod
    def _write_record_file(record):
        run = Path(record['run_dir'])
        run.mkdir(parents=True, exist_ok=True, mode=0o700)
        if run.is_symlink():
            raise OSError('Execution run directory cannot be a symlink')
        path = run / 'execution-record.json'
        temporary = run / '.execution-record.json.tmp'
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, path)

    @staticmethod
    def _finalize_logs(run_dir, stdout_path, stderr_path):
        run = Path(run_dir)
        if run.is_symlink():
            raise OSError('Execution run directory cannot be a symlink')
        run.mkdir(parents=True, exist_ok=True, mode=0o700)
        for source, name in ((stdout_path, 'stdout.log'), (stderr_path, 'stderr.log')):
            try:
                os.replace(source, run / name)
            except FileNotFoundError:
                pass

    def _watch(self, record, process, stdout, stderr, stdout_path, stderr_path):
        try:
            self._watch_process(record, process, stdout, stderr, stdout_path, stderr_path)
        finally:
            with self._lock:
                self._processes.pop(record['execution_id'], None)
            self._release_supervisor(record['execution_id'])

    def _watch_process(self, record, process, stdout, stderr, stdout_path, stderr_path):
        execution_id = record['execution_id']
        timed_out = False
        try:
            timeout = record['budget'].get('timeout_seconds')
            if type(timeout) is not int or timeout <= 0:
                timeout = None
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, 15)
            except ProcessLookupError:
                pass
            self._stop_after_grace(process)
            process.wait()
            code = None
        finally:
            stdout.close(); stderr.close()
        exited_at = now()
        def finish(failures):
            try:
                self._finalize_logs(record['run_dir'], stdout_path, stderr_path)
            except OSError:
                # The original sibling log files remain available. Moving
                # them cannot block authoritative terminal publication.
                pass
            if timed_out:
                self._finish(execution_id, state='timeout', exit_code=None, error_type='timeout', run_state=None,
                             finish_failures=failures)
            else:
                self._finish_exited(execution_id, code, failures)
        self._settle(record, exited_at, finish)

    # Terminal bookkeeping retries while SQLite is busy or briefly unavailable.
    # Waits double up to a cap, so contention lasting well over a minute is
    # still recorded instead of leaving the execution "running" forever.
    _FINISH_RETRY_FIRST = 1.0
    _FINISH_RETRY_CAP = 60.0
    _sleep = staticmethod(time.sleep)

    def _settle(self, record, exited_at, attempt):
        failures = []
        delay = self._FINISH_RETRY_FIRST
        while True:
            try:
                attempt(failures)
                return
            except Exception as exc:
                failure = dict(attempt=len(failures) + 1, at=now(), process_exited_at=exited_at,
                               error_type=type(exc).__name__,
                               error_message=sanitize_diagnostic(str(exc))[-512:], logged=False)
                failures.append(failure)
                failure['logged'] = self._log_finish_failure(record, failure)
                if not isinstance(exc, sqlite3.OperationalError):
                    # Not transient: retrying the same bookkeeping cannot help. The
                    # run directory stays the source of truth, but the execution must
                    # still end, or rounds wait on a "running" record forever.
                    try:
                        self._finish(record['execution_id'], state='failed', exit_code=None,
                                     error_type='finish_failed', run_state=dict(
                                         phase='finish_failed', error_type=failure['error_type'],
                                         error_message=failure['error_message']),
                                     finish_failures=failures)
                    except Exception:
                        pass
                    return
                self._sleep(delay)
                delay = min(delay * 2, self._FINISH_RETRY_CAP)

    def _log_finish_failure(self, record, failure):
        try:
            with self.repository.connect(write=True) as conn:
                self.repository._event(conn, record['task_id'], 'execution.finish_failed',
                                       record['execution_id'], {k: v for k, v in failure.items() if k != 'logged'})
            return True
        except Exception:
            return False

    def _finish_exited(self, execution_id, code, failures):
        with self.repository.connect() as conn:
            started = conn.execute("SELECT task_id FROM history WHERE object_id=? "
                                   "AND kind='job.execution_started' ORDER BY seq LIMIT 1",
                                   (execution_id,)).fetchone()
            if started is None:
                return
            current = self._read(conn, started['task_id'], execution_id)
        if current.get('state') == 'cancelled':
            self._write_record_file(current)
            return
        state = 'finished' if code == 0 else 'failed'
        run_state = None
        path = Path(current['run_dir']) / 'state.json'
        try:
            run_state = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
        except (OSError, ValueError):
            run_state = {'phase': 'unreadable'}
        if run_state is not None and not isinstance(run_state, dict):
            run_state = {'phase': 'unreadable'}
        if code != 0 and not (run_state or {}).get('error_message'):
            log = Path(current['run_dir']) / 'stderr.log'
            try:
                # Redact before truncating: a sliced traceback loses the
                # header needed to identify source lines and credentials.
                tail = sanitize_diagnostic(log.read_text(encoding='utf-8', errors='replace'))[-2048:]
            except OSError:
                tail = ''
            run_state = dict(run_state or {}, error_type='child_exit',
                error_message=tail.strip() or f'Execution process exited with code {code}',
                failed_phase=(run_state or {}).get('stage') or (run_state or {}).get('phase') or 'launching',
                diagnostic_path=str(log))
        self._finish(execution_id, state=state, exit_code=code,
                     error_type=None if code == 0 else 'child_exit', run_state=run_state,
                     finish_failures=failures)

    def _finish(self, execution_id, *, state, exit_code, error_type, run_state, finish_failures=()):
        with self.repository.connect(write=True) as conn:
            rows = conn.execute("SELECT content FROM history WHERE object_id=? AND kind IN "
                                "('job.execution_started','job.execution_finished','job.execution_cancelled') ORDER BY seq",
                                (execution_id,)).fetchall()
            if not rows:
                return
            current = json.loads(rows[-1]['content'])
            if current.get('terminal'):
                return
            current.update(state=state, terminal=True, finished_at=now(), exit_code=exit_code,
                           error_type=error_type, run_state=run_state)
            _closed(current)
            repo = self.repository
            # Failed attempts that could not be logged while the database was
            # busy land atomically with the terminal record they delayed.
            for failure in finish_failures:
                if not failure['logged']:
                    repo._event(conn, current['task_id'], 'execution.finish_failed', execution_id,
                                {k: v for k, v in failure.items() if k != 'logged'})
            repo._event(conn, current['task_id'], 'job.execution_finished', execution_id, current)
        self._settle_budget(current)
        try:
            self._write_record_file(current)
        except OSError:
            pass

    def cancel(self, task_id, execution_id, *, actor, request_key):
        _text(task_id, 'task identity', identity=True)
        _text(execution_id, 'execution identity', identity=True)
        _text(actor, 'actor'); _text(request_key, 'request key')
        payload = dict(task_id=task_id, execution_id=execution_id, actor=actor)
        scope = 'job.execution-cancel:' + task_id + ':' + actor
        with self.repository.connect(write=True) as conn:
            prior = self.repository._prior(conn, scope, request_key, payload)
            if prior is not None:
                current = _closed(prior)
            else:
                current = self._read(conn, task_id, execution_id)
                if not current['terminal']:
                    current.update(state='cancelled', terminal=True, cancelled_at=now(), finished_at=now())
                    _closed(current)
                    self.repository._event(conn, task_id, 'job.execution_cancelled', execution_id, current)
                self.repository._receipt(conn, scope, request_key, payload, current)
        self._settle_budget(current)
        try:
            self._write_record_file(current)
        except OSError:
            pass
        with self._lock:
            process = self._processes.get(execution_id)
        if process is None and current['state'] == 'cancelled':
            # Idempotent receipt replay must also retry the physical effect.
            # An existing supervisor's flock prevents competing ownership.
            self.recover(execution_id=execution_id)
        if process is not None and process.poll() is None:
            if isinstance(process, RecoveredProcess):
                if process.signal_group(15):
                    threading.Thread(target=process.stop_after_grace, daemon=True).start()
            else:
                try:
                    os.killpg(process.pid, 15)
                except ProcessLookupError:
                    pass
                threading.Thread(target=self._stop_after_grace, args=(process,), daemon=True).start()
        return current

    def _settle_budget(self, record):
        from .budgets import BudgetLedger
        try:
            BudgetLedger(self.repository, initialize=False).settle_execution(record)
        except Exception:
            # A bookkeeping failure must not prevent cancellation or terminal
            # file publication. Reservations stay charged; the next budget
            # operation reconciles the already durable terminal receipt.
            pass

    @staticmethod
    def _stop_after_grace(process):
        # The leader can exit before a partition child. Observe the group
        # throughout the grace period, and stop tracking when it disappears.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            process.poll()  # Reap an exited leader without assuming its children exited.
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return
            time.sleep(.05)
        try:
            os.killpg(process.pid, 9)
        except ProcessLookupError:
            pass
