"""Single substitute observation in jobs.probe; never a plan execution."""
from __future__ import annotations

from copy import deepcopy
import json

from .evaluation_jobs import EvaluationJobStore, JobRejected
from .probe_subprocess import HELPER_HASH, MODES, ProbeSubprocess, ProbeStartFailed, ProbeStartUncertain
from .repository import Conflict, now, uid


FORMAT = 'sn-workbench-single-substitute-probe-v1'
FIELDS = {'format', 'probe_id', 'task_id', 'job_id', 'job_version', 'version', 'mode',
          'helper_hash', 'owner_id', 'phase', 'start_consumed', 'cancel_requested',
          'process_identity', 'exit_observation', 'reason', 'claimed_at', 'updated_at'}
TERMINAL = ('exited', 'start_failed', 'not_started')


class ProbeRejected(JobRejected):
    """Explicit probe command or persisted observation rejection."""


def _text(value):
    if type(value) is not str or not value.strip() or len(value) > 200:
        raise ProbeRejected('Invalid probe identity or command text')
    return value


def _int(value):
    if type(value) is not int or value < 1:
        raise ProbeRejected('Invalid probe version')
    return value


def _identity(value, record):
    if (type(value) is not dict or set(value) != {'probe_id', 'owner_id', 'helper_hash', 'token', 'pid'}
            or value['probe_id'] != record['probe_id'] or value['owner_id'] != record['owner_id']
            or value['helper_hash'] != record['helper_hash']):
        raise ProbeRejected('Invalid helper process identity')
    _text(value['token'])
    _int(value['pid'])


def _saved(value, job):
    if type(value) is not dict or set(value) != FIELDS:
        raise ProbeRejected('Invalid substitute probe schema')
    if (value['format'] != FORMAT or value['helper_hash'] != HELPER_HASH
            or value['task_id'] != job['task_id'] or value['job_id'] != job['job_id']
            or type(value['mode']) is not str or value['mode'] not in MODES
            or type(value['phase']) is not str or value['phase'] not in (
                'claimed', 'running', 'exited', 'start_failed', 'unknown', 'not_started')):
        raise ProbeRejected('Unsupported substitute probe schema or identity')
    for field in ('probe_id', 'owner_id', 'claimed_at', 'updated_at'):
        _text(value[field])
    _int(value['version'])
    if type(value['job_version']) is not int or value['job_version'] != 1:
        raise ProbeRejected('Invalid claimed Job version')
    if type(value['start_consumed']) is not bool or type(value['cancel_requested']) is not bool:
        raise ProbeRejected('Invalid probe flags')
    if value['reason'] is not None:
        _text(value['reason'])
    identity = value['process_identity']
    if identity is not None:
        _identity(identity, value)
    observation = value['exit_observation']
    if observation is not None:
        if (type(observation) is not dict or set(observation) != {'identity', 'returncode', 'observed_at'}
                or type(observation['returncode']) is not int or observation['identity'] != identity):
            raise ProbeRejected('Invalid OS exit observation')
        _identity(observation['identity'], value)
        _text(observation['observed_at'])
    phase = value['phase']
    if ((phase == 'exited') != (observation is not None)
            or (phase == 'exited' and not value['start_consumed'])
            or (phase == 'running' and (identity is None or not value['start_consumed']))
            or (phase == 'claimed' and (value['start_consumed'] or identity is not None))
            or (phase == 'not_started' and (value['start_consumed'] or not value['cancel_requested'] or identity is not None))
            or (phase == 'start_failed' and (not value['start_consumed'] or identity is not None))):
        raise ProbeRejected('Inconsistent substitute observation')
    return value


def _read(repo, conn, task_id, job_id):
    job = EvaluationJobStore(repo).get_in_transaction(conn, task_id, job_id)
    if conn.execute('PRAGMA user_version').fetchone()[0] not in (4, 5, 6):
        raise ProbeRejected('Probe requires workbench schema4')
    encoded = conn.execute('SELECT probe FROM jobs WHERE id=?', (job_id,)).fetchone()['probe']
    if encoded is None:
        return job, None
    if type(encoded) is not str:
        raise ProbeRejected('Invalid substitute probe encoding')
    try:
        value = json.loads(encoded)
    except json.JSONDecodeError:
        raise ProbeRejected('Invalid substitute probe encoding') from None
    return job, _saved(value, job)


def _write(repo, conn, job, prior, result, event):
    _saved(result, job)
    if prior is None:
        cursor = conn.execute('UPDATE jobs SET probe=? WHERE id=? AND probe IS NULL',
                              (json.dumps(result), job['job_id']))
    else:
        # BEGIN IMMEDIATE serializes the validated read and this update. JSON
        # whitespace is not part of observation identity or version CAS.
        cursor = conn.execute('UPDATE jobs SET probe=? WHERE id=?',
                              (json.dumps(result), job['job_id']))
    if cursor.rowcount != 1:
        raise Conflict('Probe changed before observation persistence')
    repo._event(conn, job['task_id'], event, result['probe_id'], result)
    return result


def _changed(record, **changes):
    return dict(record, version=record['version'] + 1, updated_at=now(), **changes)


def _command_choices(conn, scope, request_key, version, *, mode=None):
    valid = type(version) is int and version > 0
    if mode is not None:
        valid = valid and type(mode) is str and mode in MODES
    if valid:
        return
    # These values could never be part of a successful original command. Keep
    # prior-key conflicts first, without hashing noncanonical values or catching
    # generic errors from the repository's real hashing/read implementation.
    if conn.execute('SELECT 1 FROM requests WHERE scope=? AND request_key=?',
                    (scope, request_key)).fetchone() is not None:
        raise Conflict('Probe request key was already used with different content')
    raise ProbeRejected('Invalid probe version or fixed mode')


def request_probe_cancel_in_transaction(repository, conn, task_id, job_id):
    """Only persist cancellation intent, in the caller's transaction."""
    job, record = _read(repository, conn, task_id, job_id)
    if record is None or record['cancel_requested']:
        return record
    result = _changed(record, cancel_requested=True)
    return _write(repository, conn, job, record, result, 'probe.cancel_requested')


class JobLifecycleProbeStore:
    def __init__(self, repository, *, adapter):
        if not isinstance(adapter, ProbeSubprocess):
            raise ProbeRejected('Probe requires the fixed private adapter')
        self.repository = repository
        self.adapter = adapter

    def _adapter(self, adapter):
        if adapter is not self.adapter or adapter.helper_hash != HELPER_HASH:
            raise ProbeRejected('Probe adapter owner mismatch')

    def get(self, task_id, job_id):
        with self.repository.connect() as conn:
            return _read(self.repository, conn, task_id, job_id)[1]

    def _prior(self, conn, scope, request_key, payload, job, operation):
        try:
            prior = self.repository._prior(conn, scope, request_key, payload)
        except json.JSONDecodeError:
            raise ProbeRejected('Invalid probe receipt') from None
        if prior is None:
            if conn.execute('SELECT 1 FROM requests WHERE scope=? AND request_key=?',
                            (scope, request_key)).fetchone() is not None:
                raise ProbeRejected('Invalid probe receipt')
            return None
        record = _saved(prior, job)
        if operation == 'claim':
            if (record['phase'] != 'claimed' or record['version'] != 1
                    or record['cancel_requested'] is not False or record['reason'] is not None
                    or record['mode'] != payload['mode']
                    or record['job_version'] != payload['expected_job_version']):
                raise ProbeRejected('Invalid original claim receipt')
        elif (record['cancel_requested'] is not True
              or record['version'] not in (payload['expected_probe_version'], payload['expected_probe_version'] + 1)):
            raise ProbeRejected('Invalid original cancellation receipt')
        return record

    def claim(self, task_id, job_id, expected_job_version, *, actor, request_key, mode):
        _text(task_id); _text(job_id); _text(actor); _text(request_key)
        scope = 'probe.claim:' + json.dumps([task_id, actor], separators=(',', ':'))
        payload = dict(task_id=task_id, job_id=job_id, expected_job_version=expected_job_version,
                       actor=actor, mode=mode)
        repo = self.repository
        with repo.connect(write=True) as conn:
            job, current = _read(repo, conn, task_id, job_id)
            _command_choices(conn, scope, request_key, expected_job_version, mode=mode)
            prior = self._prior(conn, scope, request_key, payload, job, 'claim')
            if prior is not None:
                return _saved(prior, job)
            _int(expected_job_version)
            if type(mode) is not str or mode not in MODES:
                raise ProbeRejected('Invalid fixed probe mode')
            if job['state_version'] != expected_job_version or job['state'] != 'not_dispatched' or current is not None:
                raise Conflict('Job is cancelled, changed, or already has its single probe')
            record = dict(format=FORMAT, probe_id=uid(), task_id=task_id, job_id=job_id,
                          job_version=expected_job_version, version=1, mode=mode,
                          helper_hash=HELPER_HASH, owner_id=self.adapter.owner_id, phase='claimed',
                          start_consumed=False, cancel_requested=False, process_identity=None,
                          exit_observation=None, reason=None, claimed_at=now(), updated_at=now())
            _write(repo, conn, job, None, record, 'probe.claimed')
            repo._receipt(conn, scope, request_key, payload, record)
            return record

    def request_cancel(self, task_id, job_id, expected_probe_version, *, actor, request_key):
        _text(task_id); _text(job_id); _text(actor); _text(request_key)
        scope = 'probe.cancel:' + json.dumps([task_id, actor], separators=(',', ':'))
        payload = dict(task_id=task_id, job_id=job_id, expected_probe_version=expected_probe_version,
                       actor=actor)
        repo = self.repository
        with repo.connect(write=True) as conn:
            job, record = _read(repo, conn, task_id, job_id)
            _command_choices(conn, scope, request_key, expected_probe_version)
            prior = self._prior(conn, scope, request_key, payload, job, 'cancel')
            if prior is not None:
                return prior
            _int(expected_probe_version)
            if record is None:
                raise ProbeRejected('Probe has not been claimed')
            if record['version'] != expected_probe_version:
                raise Conflict('Probe version changed before cancellation request')
            result = request_probe_cancel_in_transaction(repo, conn, task_id, job_id)
            repo._receipt(conn, scope, request_key, payload, result)
            return result

    def start_claimed(self, task_id, job_id, *, adapter):
        self._adapter(adapter)
        repo = self.repository
        with adapter.lock:
            with repo.connect(write=True) as conn:
                job, record = _read(repo, conn, task_id, job_id)
                if record is None:
                    raise ProbeRejected('Probe has not been claimed')
                if record['phase'] != 'claimed':
                    return record
                if record['owner_id'] != adapter.owner_id:
                    return _write(repo, conn, job, record, _changed(record, phase='unknown', reason='owner_lost'), 'probe.unknown')
                if record['cancel_requested'] or job['state'] == 'cancelled':
                    return _write(repo, conn, job, record, _changed(record, phase='not_started', cancel_requested=True), 'probe.not_started')
                consumed = _changed(record, phase='unknown', start_consumed=True, reason='start_unconfirmed')
                _write(repo, conn, job, record, consumed, 'probe.start_consumed')
            # Authorization is durable before any external process creation.
            try:
                identity = adapter.start(record['probe_id'], record['mode'])
            except ProbeStartFailed:
                return self._publish(task_id, job_id, consumed, phase='start_failed', reason='creation_rejected')
            except ProbeStartUncertain:
                return self.get(task_id, job_id)
            _identity(identity, consumed)
            result = self._publish(task_id, job_id, consumed, phase='running',
                                   process_identity=identity, reason=None)
            if result['cancel_requested']:
                return self.reconcile(task_id, job_id, adapter=adapter)
            return result

    def _publish(self, task_id, job_id, expected, **changes):
        repo = self.repository
        with repo.connect(write=True) as conn:
            job, current = _read(repo, conn, task_id, job_id)
            if (current['probe_id'] != expected['probe_id'] or current['owner_id'] != expected['owner_id']
                    or current['phase'] in TERMINAL):
                return current
            # Cancellation alone may change the version between start and persist;
            # preserve its monotonic flag while binding the same probe/owner.
            if current['version'] != expected['version']:
                cancellation_only = (
                    current['version'] == expected['version'] + 1
                    and current['cancel_requested'] is True and expected['cancel_requested'] is False
                    and {k: v for k, v in current.items() if k not in ('version', 'updated_at', 'cancel_requested')}
                        == {k: v for k, v in expected.items() if k not in ('version', 'updated_at', 'cancel_requested')})
                if not cancellation_only:
                    return current
            result = _changed(current, **changes)
            event = 'probe.started' if result['phase'] == 'running' else 'probe.' + result['phase']
            return _write(repo, conn, job, current, result, event)

    def reconcile(self, task_id, job_id, *, adapter):
        self._adapter(adapter)
        with adapter.lock:
            record = self.get(task_id, job_id)
            if record is None or record['phase'] in TERMINAL:
                return record
            if record['owner_id'] != adapter.owner_id:
                if record['phase'] == 'unknown' and record['reason'] in ('owner_lost', 'handle_lost'):
                    # Reopening cannot add evidence to an already recorded
                    # loss of the OS observation/control boundary.
                    return record
                return self._publish(task_id, job_id, record, phase='unknown', reason='owner_lost')
            if record['phase'] == 'claimed':
                # This owner has not consumed startup authorization yet. The
                # expected absence of a handle is not a lost-process fact.
                return record
            observed = adapter.observe(record['probe_id'], record['process_identity'],
                                       cancel=record['cancel_requested'], wait=False)
            if observed is None:
                if record['phase'] == 'unknown':
                    return record
                return self._publish(task_id, job_id, record, phase='unknown', reason='handle_lost')
            _identity(observed['identity'], record)
            if record['process_identity'] is not None and observed['identity'] != record['process_identity']:
                raise ProbeRejected('OS observation process identity changed')
            if observed['returncode'] is None:
                return record
            if type(observed['returncode']) is not int:
                raise ProbeRejected('Invalid OS exit code')
            return self._publish(task_id, job_id, record, phase='exited', reason=None,
                process_identity=observed['identity'], exit_observation=dict(
                    identity=observed['identity'], returncode=observed['returncode'], observed_at=now()))
