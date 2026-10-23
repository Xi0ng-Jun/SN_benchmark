"""Small, durable dispatcher for executable workbench Jobs.

The legacy Job record remains a closed intention.  This module owns the
runtime record and the process handle that belongs to one isolated run
directory.  Callers provide a server-built argv and a frozen execution budget;
HTTP input is never treated as an executable command.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time

from .evaluation_jobs import _text, _version
from ..execution_diagnostics import exception_details, sanitize_diagnostic
from .repository import Conflict, UnknownIdentity, now, uid


FORMAT = 'sn-workbench-job-execution-v1'
_FIELDS = {
    'format', 'execution_id', 'job_id', 'task_id', 'state', 'terminal',
    'argv', 'run_dir', 'budget', 'preparation_id', 'started_at', 'finished_at',
    'cancelled_at', 'exit_code', 'run_state', 'error_type'
}


class ExecutionRejected(ValueError):
    """The execution contract or current lifecycle state is invalid."""


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
            rows = conn.execute("SELECT h.object_id,h.task_id FROM history h JOIN tasks t ON t.id=h.task_id "
                                "WHERE t.workspace_id=? AND h.kind='job.execution_started' ORDER BY h.seq",
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
            repo._event(conn, task_id, 'job.execution_started', record['execution_id'], record)
            repo._receipt(conn, scope, request_key, payload, record)
        try:
            self._start(record)
        except BaseException as exc:
            details = exception_details(exc, phase='launching')
            self._finish(record['execution_id'], state='failed', exit_code=None,
                         error_type=type(exc).__name__, run_state=dict(phase='failed', **details))
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
        try:
            process = subprocess.Popen(record['argv'], cwd=parent, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, start_new_session=True,
                                       close_fds=True)
        except BaseException:
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
        run.mkdir(parents=True, exist_ok=True, mode=0o700)
        for source, name in ((stdout_path, 'stdout.log'), (stderr_path, 'stderr.log')):
            try:
                os.replace(source, run / name)
            except FileNotFoundError:
                pass

    def _watch(self, record, process, stdout, stderr, stdout_path, stderr_path):
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
        self._finalize_logs(record['run_dir'], stdout_path, stderr_path)
        with self._lock:
            self._processes.pop(execution_id, None)
        if timed_out:
            self._finish(execution_id, state='timeout', exit_code=None,
                         error_type='timeout', run_state=None)
            return
        try:
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
                         error_type=None if code == 0 else 'child_exit', run_state=run_state)
        except Exception:
            # The run directory remains the source of truth even if bookkeeping
            # is interrupted; a later status read can still inspect it.
            return

    def _finish(self, execution_id, *, state, exit_code, error_type, run_state):
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
                return _closed(prior)
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
        if process is not None and process.poll() is None:
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
