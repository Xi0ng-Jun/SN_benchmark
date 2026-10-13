"""Append-only evaluation plan drafts and exact original-request receipts.

SQLite history/requests are authoritative. Only a new revision reads canonical
inputs; reopening a revision or recovering a receipt never consults its source.
A draft records intentions and missing bindings, and cannot execute a protocol.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

from ..identity import fingerprint
from ..notebook_data import SUITES
from .plan_inputs import ADAPTER, ADAPTATION, _bundle_id, _id, _ids
from .repository import Conflict, now, uid


FORMAT = 'sn-workbench-evaluation-plan-draft-v1'
EVENT = 'plan.draft.saved'


class PlanSnapshotRejected(ValueError):
    """A stored draft fails its closed data schema."""


class FixedPlanRejected(ValueError):
    """An explicit fixed revision request cannot identify a valid record."""


def _text(value, field, *, limit=200):
    # Validate without normalization: spelling is part of original request identity.
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('Invalid ' + field)
    return value


def _scope(task_id, actor):
    return 'plan.draft.save:' + _text(task_id, 'task identity') + ':' + _text(actor, 'actor')


def _saved(value, *, task_id, plan_id=None):
    """Reject unknown schemas and corrupted stored revisions without guessing."""
    try:
        if not isinstance(value, dict) or set(value) != {
            'plan_id', 'revision', 'parent_revision', 'task_id', 'workspace_id',
            'title', 'actor', 'content', 'content_hash', 'state', 'execution', 'saved_at'
        }:
            raise ValueError
        content = value['content']
        if not isinstance(content, dict) or set(content) != {
            'format', 'title', 'input', 'selection', 'execution_intent', 'source_intent'
        } or content['format'] != FORMAT:
            raise ValueError
        if (value['task_id'] != task_id or (plan_id is not None and value['plan_id'] != plan_id)
                or value['state'] != 'draft' or value['execution'] is not False
                or value['content_hash'] != fingerprint(content) or value['title'] != content['title']):
            raise ValueError
        for field in ('plan_id', 'revision', 'workspace_id', 'actor', 'saved_at'):
            _text(value[field], field)
        _text(value['title'], 'title', limit=500)
        if value['parent_revision'] is not None:
            _text(value['parent_revision'], 'parent revision')
        identity = content['input']
        if (not isinstance(identity, dict) or set(identity) != {
                'input_id', 'adapter', 'bundle_id', 'suite', 'source_revision', 'adaptation_revision'}
                or identity['adapter'] != ADAPTER or identity['adaptation_revision'] != ADAPTATION
                or identity['suite'] not in SUITES):
            raise ValueError
        _id(identity['input_id'], 'input ID')
        _bundle_id(identity['bundle_id'])
        # Public revisions are content identities. Never expose or silently
        # migrate an old record containing the original revision text.
        source_revision = identity['source_revision']
        if (not isinstance(source_revision, str)
                or re.fullmatch('sha256:[0-9a-f]{64}', source_revision) is None):
            raise ValueError
        selection = content['selection']
        if (not isinstance(selection, dict) or set(selection) != {
                'kind', 'case_ids', 'partition_ids', 'selection_hash'} or selection['kind'] != 'explicit'):
            raise ValueError
        _ids(selection['case_ids'], 'case IDs')
        _ids(selection['partition_ids'], 'partition IDs')
        if selection['selection_hash'] != fingerprint({k: v for k, v in selection.items() if k != 'selection_hash'}):
            raise ValueError
        intent = content['execution_intent']
        if (not isinstance(intent, dict) or set(intent) != {
                'entrypoint', 'mode', 'request_revision', 'model_services_ref', 'official_scorer_ref'}
                or intent['entrypoint'] != 'scripts/run_notebook_benchmarks.py'
                or intent['mode'] not in {'chunk', 'reasoning'}
                or intent['request_revision'] != 'notebook-request-v3'
                or intent['model_services_ref'] is not None or intent['official_scorer_ref'] is not None):
            raise ValueError
        if content['source_intent'] != dict(kind='task-workspace-at-freeze',
                                           workspace_id=value['workspace_id'], binding='not_captured'):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise PlanSnapshotRejected('Unsupported or invalid saved plan schema') from None
    return value


def validate_plan_snapshot(value, *, task_id, plan_id=None):
    """Validate a persisted fixed draft without reading its original input."""
    return _saved(value, task_id=task_id, plan_id=plan_id)


class PlanDraftStore:
    """Use a repository and a trusted NotebookPlanInputs registry.

    ``actor`` is supplied by the server adapter, never copied from a client DTO.
    Save accepts only original choices; workspace/content/revision/partitions
    are derived here. plan_id only addresses an existing plan for append.
    """

    def __init__(self, repository, inputs):
        self.repository = repository
        self.inputs = inputs

    def _revisions(self, conn, task_id, plan_id=None):
        query = 'SELECT object_id,content FROM history WHERE task_id=? AND kind=?'
        params = [task_id, EVENT]
        if plan_id is not None:
            query += ' AND object_id=?'
            params.append(plan_id)
        query += ' ORDER BY seq'
        return [_saved(json.loads(row['content']), task_id=task_id, plan_id=row['object_id'])
                for row in conn.execute(query, params)]

    def save(self, *, task_id, input_id, expected_bundle_id, case_ids, title, mode,
             actor, request_key, plan_id=None, base_revision=None):
        """Recover an exact original request or append one CAS-guarded revision.

        New plans require plan_id/base_revision null. Appends require both the
        existing plan_id and its current global revision, regardless of actor.
        The request digest retains the original title, ID order and nulls.
        """
        scope = _scope(task_id, actor)
        request_key = _text(request_key, 'request key')
        payload = deepcopy(dict(task_id=task_id, actor=actor, input_id=input_id,
                                expected_bundle_id=expected_bundle_id, case_ids=case_ids,
                                title=title, mode=mode, plan_id=plan_id, base_revision=base_revision))
        repo = self.repository
        with repo.connect(write=True) as conn:
            prior = repo._prior(conn, scope, request_key, payload)
            if prior is not None:
                return _saved(prior, task_id=task_id, plan_id=plan_id)
            task = repo._require(conn, 'tasks', task_id)
            workspace = repo._require(conn, 'workspaces', task['workspace_id'])
            title = _text(payload['title'], 'plan title', limit=500)
            _id(payload['input_id'], 'input ID')
            _bundle_id(payload['expected_bundle_id'])
            _ids(payload['case_ids'], 'case IDs')
            if not isinstance(mode, str) or mode not in {'chunk', 'reasoning'}:
                raise ValueError('Unknown plan mode')
            if plan_id is None:
                if base_revision is not None:
                    raise Conflict('New plan cannot have a base revision')
                saved_plan_id = uid()
            else:
                _id(plan_id, 'plan identity')
                revisions = self._revisions(conn, task_id, plan_id)
                if not revisions:
                    raise ValueError('Plan is not attached to this task')
                head = revisions[-1]
                if head['workspace_id'] != workspace['id']:
                    raise ValueError('Plan workspace ownership changed')
                if base_revision != head['revision']:
                    raise Conflict('Plan revision changed; reload before saving')
                saved_plan_id = plan_id
            canonical = self.inputs.canonical(
                payload['input_id'], case_ids=payload['case_ids'],
                expected_bundle_id=payload['expected_bundle_id'])
            content = dict(
                format=FORMAT, title=title, input=canonical['input'], selection=canonical['selection'],
                execution_intent=dict(entrypoint='scripts/run_notebook_benchmarks.py', mode=mode,
                                      request_revision='notebook-request-v3', model_services_ref=None,
                                      official_scorer_ref=None),
                source_intent=dict(kind='task-workspace-at-freeze', workspace_id=workspace['id'],
                                   binding='not_captured'))
            result = dict(plan_id=saved_plan_id, revision=uid(), parent_revision=base_revision,
                          task_id=task_id, workspace_id=workspace['id'], title=title, actor=actor,
                          content=content, content_hash=fingerprint(content), state='draft',
                          execution=False, saved_at=now())
            _saved(result, task_id=task_id, plan_id=saved_plan_id)
            repo._event(conn, task_id, EVENT, saved_plan_id, result)
            repo._receipt(conn, scope, request_key, payload, result)
            return result

    def receipt(self, task_id, *, actor, request_key):
        """Read a lost response by exact key in the trusted task/actor scope."""
        scope = _scope(task_id, actor)
        request_key = _text(request_key, 'request key')
        with self.repository.connect() as conn:
            self.repository._require(conn, 'tasks', task_id)
            row = conn.execute('SELECT receipt FROM requests WHERE scope=? AND request_key=?',
                               (scope, request_key)).fetchone()
            return _saved(json.loads(row['receipt']), task_id=task_id) if row else None

    def list(self, task_id):
        """Return one frozen head per plan, ordered by plan creation."""
        _text(task_id, 'task identity')
        with self.repository.connect() as conn:
            self.repository._require(conn, 'tasks', task_id)
            heads = {}
            for revision in self._revisions(conn, task_id):
                heads[revision['plan_id']] = revision
            return list(heads.values())

    def get(self, task_id, plan_id, revision=None):
        """Read a fixed revision, or the frozen current head when omitted."""
        _text(task_id, 'task identity')
        _id(plan_id, 'plan identity')
        if revision is not None:
            _id(revision, 'revision')
        with self.repository.connect() as conn:
            if revision is not None:
                return self.get_fixed_in_transaction(conn, task_id, plan_id, revision)
            self.repository._require(conn, 'tasks', task_id)
            revisions = self._revisions(conn, task_id, plan_id)
            if not revisions:
                raise ValueError('Unknown plan identity')
            return revisions[-1]

    def get_fixed_in_transaction(self, conn, task_id, plan_id, revision):
        """Read an explicit immutable revision inside the caller's transaction.

        Job registration owns the transaction and checks workspace ownership
        before inserting. No canonical source or second connection is used.
        """
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200:
            raise FixedPlanRejected('Invalid fixed plan task identity')
        for value in (plan_id, revision):
            if not isinstance(value, str) or not value or any(char.isspace() for char in value):
                raise FixedPlanRejected('Invalid fixed plan identity')
        self.repository._require(conn, 'tasks', task_id)
        try:
            revisions = self._revisions(conn, task_id, plan_id)
        except (PlanSnapshotRejected, json.JSONDecodeError):
            raise FixedPlanRejected('Invalid saved fixed plan schema') from None
        for saved in revisions:
            if saved['revision'] == revision:
                return saved
        raise FixedPlanRejected('Unknown fixed plan revision')
