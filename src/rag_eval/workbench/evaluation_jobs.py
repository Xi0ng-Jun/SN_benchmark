"""Fixed-plan evaluation intentions, without dispatch or executable bindings.

``jobs`` alone owns current state. ``history`` audits transitions; ``requests``
keeps immutable original-command records, which may differ from current state.
Intent transitions share one SQLite transaction. Preparation reads artifacts
outside that transaction, then rechecks the Job before saving separate facts.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

from ..identity import fingerprint
from .evaluation_plans import (
    FixedPlanRejected, PlanDraftStore, PlanSnapshotRejected, validate_plan_snapshot,
)
from .repository import Conflict, UnknownIdentity, now, uid


FORMAT = 'sn-workbench-evaluation-job-v1'
_FIELDS = {
    'format', 'job_id', 'task_id', 'workspace_id', 'plan_id', 'plan_revision',
    'plan_content_hash', 'plan_snapshot', 'bindings', 'state', 'state_version',
    'executable', 'execution_spec', 'prepared_by', 'prepared_at',
    'cancelled_by', 'cancelled_at',
}
_BINDINGS = ('input', 'source', 'model_runtime', 'scorer')


class JobRejected(ValueError):
    """An explicit input, ownership or persisted Job schema rejection.

    Internal SQL, write, commit and programming failures retain their original
    exceptions. Adapters should handle this type and Conflict as domain errors.
    """


def _text(value, field, *, identity=False):
    # Original spelling belongs to the command, even for actor/request keys.
    if (type(value) is not str or not value.strip() or len(value) > 200
            or (identity and any(char.isspace() for char in value))):
        raise JobRejected('Invalid ' + field)
    return value


def _hash(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise JobRejected('Invalid expected plan content hash')
    return value


def _version(value):
    if type(value) is not int or value < 1:
        raise JobRejected('Invalid Job state version')
    return value


def _scope(task_id, actor, operation):
    if type(operation) is not str or operation not in ('prepare', 'cancel'):
        raise JobRejected('Invalid Job receipt operation')
    _text(task_id, 'task identity', identity=True)
    _text(actor, 'actor')
    # Tuple encoding prevents ambiguous task/actor pairs containing colons.
    return 'job.' + operation + ':' + json.dumps([task_id, actor], ensure_ascii=False,
                                                separators=(',', ':'))


def _saved(value, *, task_id, workspace_id, job_id=None):
    """Closed record validation, reusing the authoritative draft schema."""
    try:
        if type(value) is not dict or set(value) != _FIELDS:
            raise JobRejected
        if value['format'] != FORMAT or type(value['format']) is not str:
            raise JobRejected
        for field in ('job_id', 'task_id', 'workspace_id', 'plan_id', 'plan_revision'):
            _text(value[field], field, identity=True)
        for field in ('prepared_by', 'prepared_at'):
            _text(value[field], field)
        _hash(value['plan_content_hash'])
        if (value['task_id'] != task_id or value['workspace_id'] != workspace_id
                or (job_id is not None and value['job_id'] != job_id)):
            raise JobRejected
        snapshot = _snapshot(value['plan_snapshot'], task_id=task_id, plan_id=value['plan_id'])
        if (snapshot['workspace_id'] != workspace_id
                or snapshot['revision'] != value['plan_revision']
                or snapshot['content_hash'] != value['plan_content_hash']):
            raise JobRejected
        if value['executable'] is not False or value['execution_spec'] is not None:
            raise JobRejected
        bindings = value['bindings']
        if (type(bindings) is not dict or set(bindings) != set(_BINDINGS)
                or any(type(v) is not str or v != 'not_captured' for v in bindings.values())):
            raise JobRejected
        state = value['state']
        if type(state) is not str or state not in ('not_dispatched', 'cancelled'):
            raise JobRejected
        _version(value['state_version'])
        if state == 'not_dispatched':
            if (value['state_version'] != 1 or value['cancelled_by'] is not None
                    or value['cancelled_at'] is not None):
                raise JobRejected
        else:
            if value['state_version'] != 2:
                raise JobRejected
            _text(value['cancelled_by'], 'cancelling actor')
            _text(value['cancelled_at'], 'cancellation time')
    except JobRejected:
        raise JobRejected('Unsupported or invalid saved Job schema') from None
    return value


def _decode(encoded):
    if type(encoded) is not str:
        raise JobRejected('Unsupported or invalid saved Job schema')
    try:
        return json.loads(encoded)
    except json.JSONDecodeError:
        raise JobRejected('Unsupported or invalid saved Job schema') from None


def _snapshot(value, *, task_id, plan_id):
    try:
        snapshot = validate_plan_snapshot(value, task_id=task_id, plan_id=plan_id)
    except PlanSnapshotRejected:
        raise JobRejected('Unsupported or invalid saved plan schema') from None
    # Only guard the projection consumed here. Full draft validation belongs
    # to validate_plan_snapshot; do not duplicate its schema or swallow bugs.
    if (type(snapshot) is not dict
            or not {'workspace_id', 'revision', 'content_hash'} <= snapshot.keys()):
        raise JobRejected('Invalid fixed plan snapshot projection')
    return snapshot


def _require(repo, conn, table, object_id):
    try:
        return repo._require(conn, table, object_id)
    except UnknownIdentity:
        raise JobRejected('Unknown ' + table + ' identity') from None


def _prior(repo, conn, scope, request_key, payload):
    try:
        prior = repo._prior(conn, scope, request_key, payload)
    except json.JSONDecodeError:
        raise JobRejected('Unsupported or invalid saved Job receipt') from None
    # The shared helper uses None for missing requests; persisted JSON null
    # must not be mistaken for that sentinel and permit a second command.
    if prior is None and conn.execute(
            'SELECT 1 FROM requests WHERE scope=? AND request_key=?',
            (scope, request_key)).fetchone() is not None:
        raise JobRejected('Unsupported or invalid saved Job receipt')
    return prior


def _receipt_record(value, *, task_id, workspace_id, actor, operation, payload=None):
    record = _saved(value, task_id=task_id, workspace_id=workspace_id,
                    job_id=payload['job_id'] if payload is not None and operation == 'cancel' else None)
    if operation == 'prepare':
        if record['state'] != 'not_dispatched' or record['prepared_by'] != actor:
            raise JobRejected('Invalid original prepare receipt')
        if payload is not None and (
                record['plan_id'] != payload['plan_id']
                or record['plan_revision'] != payload['revision']
                or record['plan_content_hash'] != payload['expected_content_hash']):
            raise JobRejected('Prepare receipt does not match original command')
    elif record['state'] != 'cancelled':
        raise JobRejected('Invalid original cancel receipt')
    return record


def _workspace(repo, conn, task_id):
    if conn.execute('PRAGMA user_version').fetchone()[0] not in (3, 4, 5):
        raise JobRejected('Unsupported workbench database version')
    task = _require(repo, conn, 'tasks', task_id)
    return _require(repo, conn, 'workspaces', task['workspace_id'])['id']


def _current(repo, conn, task_id, job_id, workspace_id):
    row = _require(repo, conn, 'jobs', job_id)
    if row['task_id'] != task_id or row['workspace_id'] != workspace_id:
        raise JobRejected('Job task or workspace ownership changed')
    return _saved(_decode(row['record']), task_id=task_id,
                  workspace_id=workspace_id, job_id=job_id)


def _preparation_scope(task_id, actor):
    _text(task_id, 'task identity', identity=True)
    _text(actor, 'actor')
    return 'job.capture-inputs-source:' + json.dumps([task_id, actor], ensure_ascii=False,
                                                    separators=(',', ':'))


def _preparation_record(value, job, *, actor=None, command_digest=None):
    """Offline validation of a historical fact, even after current cancellation."""
    from .job_preparation import validate_job_preparation
    if type(value) is not dict or set(value) != {'job_snapshot', 'preparation', 'actor', 'recorded_at'}:
        raise JobRejected('Invalid preparation receipt')
    original = _saved(value['job_snapshot'], task_id=job['task_id'],
                      workspace_id=job['workspace_id'], job_id=job['job_id'])
    if (original['state'] != 'not_dispatched'
            or original['plan_snapshot'] != job['plan_snapshot']):
        raise JobRejected('Preparation fixed Job identity changed')
    _text(value['actor'], 'preparation actor')
    _text(value['recorded_at'], 'preparation time')
    if actor is not None and value['actor'] != actor:
        raise JobRejected('Preparation receipt actor changed')
    validate_job_preparation(value['preparation'], original)
    if command_digest is not None:
        prepared = value['preparation']
        command = dict(task_id=original['task_id'], job_id=original['job_id'],
                       expected_state_version=original['state_version'],
                       bundle_ref=prepared['input']['bundle_ref'],
                       index_ref=prepared['input']['index_ref'],
                       sn_ref=prepared['source']['artifact_ref'], actor=value['actor'])
        if fingerprint(command) != command_digest:
            raise JobRejected('Preparation receipt does not match original command')
    return value


class EvaluationJobStore:
    """Actor-scoped commands; task-shared current records and immutable receipts.

    prepare/cancel/get return closed Job records; list returns current records
    in creation order. receipt defaults to prepare, with an explicit cancel
    operation available. An old receipt is a command fact, never a state read.
    """

    def __init__(self, repository):
        self.repository = repository

    def capture_inputs_source(self, task_id, job_id, expected_state_version, *, artifact_store,
                              bundle_ref, index_ref, sn_ref, actor, request_key):
        """Append verified preparation facts; never dispatch or promote intent.

        Artifact reads happen outside the write transaction. A final current
        Job comparison prevents a cancelled/stale preparation from committing.
        Exact request retries recover the original fact without artifact I/O.
        """
        from .job_preparation import build_job_preparation
        scope = _preparation_scope(task_id, actor)
        _text(job_id, 'Job identity', identity=True)
        _text(request_key, 'request key')
        _version(expected_state_version)
        payload = deepcopy(dict(task_id=task_id, job_id=job_id,
                                expected_state_version=expected_state_version,
                                bundle_ref=bundle_ref, index_ref=index_ref, sn_ref=sn_ref,
                                actor=actor))
        command_digest = fingerprint(payload)
        repo = self.repository
        with repo.connect() as conn:
            job = self.get_in_transaction(conn, task_id, job_id)
            prior = _prior(repo, conn, scope, request_key, payload)
            if prior is not None:
                return _preparation_record(prior, job, actor=actor, command_digest=command_digest)
            if job['state_version'] != expected_state_version or job['state'] != 'not_dispatched':
                raise Conflict('Job changed before preparation')
        prepared = build_job_preparation(job, artifact_store,
                                         bundle_ref=payload['bundle_ref'],
                                         index_ref=payload['index_ref'], sn_ref=payload['sn_ref'])
        result = dict(job_snapshot=deepcopy(job), preparation=prepared, actor=actor, recorded_at=now())
        _preparation_record(result, job, actor=actor, command_digest=command_digest)
        with repo.connect(write=True) as conn:
            current = self.get_in_transaction(conn, task_id, job_id)
            prior = _prior(repo, conn, scope, request_key, payload)
            if prior is not None:
                return _preparation_record(prior, current, actor=actor, command_digest=command_digest)
            if current != job:
                raise Conflict('Job changed during preparation; no preparation recorded')
            repo._event(conn, task_id, 'job.inputs_source_captured', job_id, result)
            repo._receipt(conn, scope, request_key, payload, result)
        return result

    def preparation_receipt(self, task_id, *, actor, request_key):
        scope = _preparation_scope(task_id, actor)
        _text(request_key, 'request key')
        with self.repository.connect() as conn:
            _workspace(self.repository, conn, task_id)
            row = conn.execute('SELECT receipt, digest FROM requests WHERE scope=? AND request_key=?',
                               (scope, request_key)).fetchone()
            if row is None:
                return None
            value = _decode(row['receipt'])
            if type(value) is not dict or type(value.get('job_snapshot')) is not dict:
                raise JobRejected('Invalid preparation receipt')
            job = self.get_in_transaction(conn, task_id, value['job_snapshot'].get('job_id'))
            return _preparation_record(value, job, actor=actor, command_digest=row['digest'])

    def preparations(self, task_id, job_id):
        """Historical verified preparation facts, not executable current state."""
        with self.repository.connect() as conn:
            job = self.get_in_transaction(conn, task_id, job_id)
            rows = conn.execute('SELECT content FROM history WHERE task_id=? AND object_id=? '
                                'AND kind=? ORDER BY seq',
                                (task_id, job_id, 'job.inputs_source_captured'))
            return [_preparation_record(_decode(row['content']), job) for row in rows]

    def prepare(self, task_id, plan_id, revision, expected_content_hash, *, actor, request_key, _sealed_capability=None):
        scope = _scope(task_id, actor, 'prepare')
        _text(request_key, 'request key')
        payload = deepcopy(dict(task_id=task_id, plan_id=plan_id, revision=revision,
                                expected_content_hash=expected_content_hash, actor=actor))
        repo = self.repository
        with repo.connect(write=True) as conn:
            prior = _prior(repo, conn, scope, request_key, payload)
            workspace_id = _workspace(repo, conn, task_id)
            if prior is not None:
                return _receipt_record(prior, task_id=task_id, workspace_id=workspace_id,
                                       actor=actor, operation='prepare', payload=payload)
            _text(plan_id, 'plan identity', identity=True)
            _text(revision, 'plan revision', identity=True)
            _hash(expected_content_hash)
            # This fixed read must use our transaction, never a head or a second
            # connection. inputs is unused by the fixed-revision seam.
            try:
                snapshot = PlanDraftStore(repo, None).get_fixed_in_transaction(
                    conn, task_id, plan_id, revision)
            except (UnknownIdentity, FixedPlanRejected, PlanSnapshotRejected, json.JSONDecodeError):
                raise JobRejected('Invalid fixed plan revision') from None
            snapshot = _snapshot(snapshot, task_id=task_id, plan_id=plan_id)
            from .sealed import _CAPABILITY
            if _sealed_capability is not _CAPABILITY:
                from .splits import Policy
                policy = Policy(repo, task_id)
                for cid in snapshot['content']['selection']['case_ids']:
                    policy.refuse_sealed(cid)
            if snapshot['workspace_id'] != workspace_id:
                raise JobRejected('Plan workspace ownership changed')
            if snapshot['content_hash'] != expected_content_hash:
                raise Conflict('Expected plan content hash changed')
            result = dict(format=FORMAT, job_id=uid(), task_id=task_id, workspace_id=workspace_id,
                          plan_id=plan_id, plan_revision=revision,
                          plan_content_hash=expected_content_hash, plan_snapshot=deepcopy(snapshot),
                          bindings={field: 'not_captured' for field in _BINDINGS},
                          state='not_dispatched', state_version=1, executable=False,
                          execution_spec=None, prepared_by=actor, prepared_at=now(),
                          cancelled_by=None, cancelled_at=None)
            _saved(result, task_id=task_id, workspace_id=workspace_id)
            conn.execute('INSERT INTO jobs(id,task_id,workspace_id,record) VALUES(?,?,?,?)',
                         (result['job_id'], task_id, workspace_id, json.dumps(result, ensure_ascii=False)))
            repo._event(conn, task_id, 'job.prepared', result['job_id'], result)
            repo._receipt(conn, scope, request_key, payload, result)
            return result

    def get(self, task_id, job_id):
        with self.repository.connect() as conn:
            return self.get_in_transaction(conn, task_id, job_id)

    def get_in_transaction(self, conn, task_id, job_id):
        """Validate current intent in a lifecycle caller's actual transaction.

        Probe observations are separate from this unchanged closed v1 record.
        No second connection, plan head, source or process operation is used.
        """
        _text(task_id, 'task identity', identity=True)
        _text(job_id, 'Job identity', identity=True)
        repo = self.repository
        workspace_id = _workspace(repo, conn, task_id)
        return _current(repo, conn, task_id, job_id, workspace_id)

    def list(self, task_id):
        _text(task_id, 'task identity', identity=True)
        repo = self.repository
        with repo.connect() as conn:
            workspace_id = _workspace(repo, conn, task_id)
            result = []
            for row in conn.execute('SELECT * FROM jobs WHERE task_id=? ORDER BY rowid', (task_id,)):
                if row['workspace_id'] != workspace_id:
                    raise JobRejected('Job workspace ownership changed')
                result.append(_saved(_decode(row['record']), task_id=task_id,
                                     workspace_id=workspace_id, job_id=row['id']))
            return result

    def receipt(self, task_id, *, actor, request_key, operation='prepare'):
        scope = _scope(task_id, actor, operation)
        _text(request_key, 'request key')
        repo = self.repository
        with repo.connect() as conn:
            workspace_id = _workspace(repo, conn, task_id)
            row = conn.execute('SELECT receipt FROM requests WHERE scope=? AND request_key=?',
                               (scope, request_key)).fetchone()
            return (_receipt_record(_decode(row['receipt']), task_id=task_id,
                                    workspace_id=workspace_id, actor=actor, operation=operation)
                    if row else None)

    def cancel(self, task_id, job_id, expected_state_version, *, actor, request_key):
        scope = _scope(task_id, actor, 'cancel')
        _text(request_key, 'request key')
        payload = deepcopy(dict(task_id=task_id, job_id=job_id,
                                expected_state_version=expected_state_version, actor=actor))
        repo = self.repository
        with repo.connect(write=True) as conn:
            prior = _prior(repo, conn, scope, request_key, payload)
            workspace_id = _workspace(repo, conn, task_id)
            if prior is not None:
                return _receipt_record(prior, task_id=task_id, workspace_id=workspace_id,
                                       actor=actor, operation='cancel', payload=payload)
            _text(job_id, 'Job identity', identity=True)
            _version(expected_state_version)
            result = _current(repo, conn, task_id, job_id, workspace_id)
            if result['state_version'] != expected_state_version:
                raise Conflict('Job state version changed; reload before cancelling')
            if result['state'] == 'not_dispatched':
                result = dict(result, state='cancelled', state_version=result['state_version'] + 1,
                              cancelled_by=actor, cancelled_at=now())
                _saved(result, task_id=task_id, workspace_id=workspace_id, job_id=job_id)
                # BEGIN IMMEDIATE owns the current row through commit; the CAS
                # check above and this update are one serialized transition.
                conn.execute('UPDATE jobs SET record=? WHERE id=? AND task_id=?',
                             (json.dumps(result, ensure_ascii=False), job_id, task_id))
                repo._event(conn, task_id, 'job.cancelled', job_id, result)
                # Only the first intent transition requests probe cancellation.
                # The pure hook shares this transaction; it never signals a
                # process, and its failure rolls back intent, audit and receipt.
                if repo._require(conn, 'jobs', job_id).get('probe') is not None:
                    from .job_lifecycle_probe import request_probe_cancel_in_transaction
                    request_probe_cancel_in_transaction(repo, conn, task_id, job_id)
            # A fresh acknowledgement at cancelled's current version has only
            # a receipt, preserving the original cancelling actor and time.
            repo._receipt(conn, scope, request_key, payload, result)
            return result
