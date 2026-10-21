"""Freeze explicit message inputs and commit uncertainty before one dispatch.

This local record describes an intended message and the public SDK call outcome.
Neither acknowledged nor timeline matching proves model execution/completion.
Unknown is never replayable, even when no remote message can currently be found.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid

from .context import resolve_context
from .repository import Conflict, _text, now, uid


# Current bounded selection policy, not a token count or permanent product limit.
MAX_MESSAGE_BYTES = 8192
MAX_REFERENCES = 4
MAX_REFERENCED_BYTES = 32768
MAX_BODY_BYTES = 32768
MAX_BODY_UTF16_UNITS = 16384
BODY_FORMAT = 'workbench-message-v1'
_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', re.I)


class MessageContextChanged(Conflict):
    """A new intent's selected hash changed before any domain write."""


class MessageContextUnavailable(ValueError):
    """A new intent's selected source could not be read before INSERT."""


class MessageInputRejected(ValueError):
    """A new intent failed a pure budget check before INSERT."""


def _opaque(value, name):
    _text(value, name, limit=200)
    if value.strip() != value or '\0' in value:
        raise ValueError('Invalid ' + name)
    return value


def _bytes(value, name):
    if type(value) is not str or '\0' in value:
        raise ValueError('Invalid ' + name)
    try:
        return value.encode('utf-8')
    except UnicodeError:
        raise ValueError('Invalid UTF-8 ' + name) from None


def _input(session_id, text, contexts, behavior):
    _opaque(session_id, 'message session')
    encoded = _bytes(text, 'message text')
    if not text.strip() or len(encoded) > MAX_MESSAGE_BYTES:
        raise ValueError('Message must contain text within the current 8 KiB budget')
    if behavior not in ('interrupt', 'steer') or type(behavior) is not str:
        raise ValueError('Choose an explicit supported send behavior')
    if type(contexts) is not list or len(contexts) > MAX_REFERENCES:
        raise ValueError('Message exceeds the current four-reference budget')
    fields = {'goal': {'kind'}, 'file': {'kind', 'path'}, 'result': {'kind', 'result_id'},
              'case': {'kind', 'result_id', 'case_id'}, 'run_case': {'kind', 'run_id', 'case_id'}, 'task_runs': {'kind'}, 'iteration_tree': {'kind'}, 'brief': {'kind', 'session_id'}}
    for selection in contexts:
        if type(selection) is not dict or set(selection) != {'reference', 'sha256'}:
            raise ValueError('Invalid selected context')
        reference, sha = selection['reference'], selection['sha256']
        if (type(reference) is not dict or type(reference.get('kind')) is not str
                or reference['kind'] not in fields or set(reference) != fields[reference['kind']]
                or any(type(value) is not str or not value.strip() or '\0' in value for value in reference.values())
                or type(sha) is not str or re.fullmatch('[0-9a-f]{64}', sha) is None):
            raise ValueError('Invalid selected context identity')
    return copy.deepcopy({'session_id': session_id, 'text': text, 'contexts': contexts,
                          'active_turn_behavior': behavior, 'body_format': BODY_FORMAT})


def _unknown(command, cause):
    return {'result': {'status': 'unknown', 'code': 'SEND_OUTCOME_UNKNOWN', 'cause': cause,
                       'agentId': command['agentId'], 'messageId': command['messageId']},
            'release': {'status': 'unavailable', 'code': 'BRIDGE_RELEASE_UNKNOWN'}}


class AgentMessageStore:
    def __init__(self, repository, *, workspace_ids, run_views=None):
        self.repository = repository
        self.run_views = run_views  # resolves run_case references; set by the app
        self.workspace_ids = frozenset(_opaque(value, 'allowed workspace') for value in workspace_ids)
        with repository.connect(write=True) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS agent_message_intents(
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                workspace_id TEXT NOT NULL,
                actor TEXT NOT NULL, request_key TEXT NOT NULL,
                session_id TEXT NOT NULL REFERENCES task_sessions(id), agent_id TEXT NOT NULL,
                message_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, snapshots TEXT NOT NULL, command TEXT NOT NULL,
                body_sha256 TEXT NOT NULL, input_sha256 TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('prepared','unknown','acknowledged','not_attempted')),
                envelope TEXT, prepared_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                UNIQUE(task_id,actor,request_key))''')
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(agent_message_intents)')}
            if 'workspace_id' not in columns:
                # The earlier table did not record the original workspace.
                # Preserve readability; NULL deliberately blocks a prepared
                # intent instead of inventing authorization from current Task.
                conn.execute('ALTER TABLE agent_message_intents ADD COLUMN workspace_id TEXT')

    @staticmethod
    def _record(row):
        record = dict(row)
        record.update(json.loads(record.pop('payload')))
        record['snapshots'] = json.loads(record['snapshots'])
        record['command'] = json.loads(record['command'])
        record['envelope'] = json.loads(record['envelope']) if record['envelope'] is not None else None
        return record

    def _task(self, conn, task_id):
        task = self.repository._require(conn, 'tasks', task_id)
        if task['workspace_id'] not in self.workspace_ids:
            raise ValueError('Task is outside the allowed message workspace')
        return task

    def _session(self, conn, task, session_id):
        session = self.repository._require(conn, 'task_sessions', session_id)
        if session['task_id'] != task['id'] or _UUID.fullmatch(session['agent_id']) is None:
            raise ValueError('Message session is not a complete Agent reference owned by this task')
        # Same scope as creation and session reads: the cwd the Agent was created in.
        return session, self.repository.session_cwd(conn, session, task)

    def _scope(self, conn, task_id, intent_id, actor):
        task = self._task(conn, task_id)
        row = conn.execute('SELECT * FROM agent_message_intents WHERE id=? AND task_id=? AND actor=?',
                           (intent_id, task_id, actor)).fetchone()
        if row is None:
            raise ValueError('Message intent is not owned by this task and actor')
        return task, self._record(row)

    def prepare(self, task_id, session_id, text, contexts, active_turn_behavior, *, actor, request_key, preflight=None):
        actor = _opaque(actor, 'actor')
        _text(request_key, 'request key', limit=200)
        _bytes(request_key, 'request key')  # Validate only; leading/trailing spaces remain significant.
        payload = _input(session_id, text, contexts, active_turn_behavior)
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        input_sha = hashlib.sha256(_bytes(serialized, 'message input')).hexdigest()
        repo = self.repository
        with repo.connect(write=True) as conn:
            task = self._task(conn, task_id)
            session, location = self._session(conn, task, session_id)
            if 'removed_at' in session.keys() and session['removed_at'] is not None:
                raise MessageInputRejected('This Agent was removed from the workbench')
            prior = conn.execute('SELECT * FROM agent_message_intents WHERE task_id=? AND actor=? AND request_key=?',
                                 (task_id, actor, request_key)).fetchone()
            if prior is not None:
                if json.loads(prior['payload']) != payload:
                    raise Conflict('Message request key was already used with different content')
                return self._record(prior)
            # Role separation: an implementer never receives benchmark material
            # (cases, run evidence, scores); a brief belongs to its own session.
            benchmark = {'case', 'run_case', 'task_runs', 'iteration_tree', 'result'}
            for selection in payload['contexts']:
                reference = selection['reference']
                if reference['kind'] == 'brief' and reference['session_id'] != session_id:
                    raise MessageInputRejected('A brief can only be sent to its own session')
                if session['role'] == 'implementer' and reference['kind'] in benchmark:
                    raise MessageInputRejected('Benchmark material is not sent to an implementer Agent')
            # Frozen snapshots come only from the existing server context resolver.
            # File reads are bounded; they are observations, not filesystem locks.
            snapshots = []
            for selection in payload['contexts']:
                try:
                    snapshot = resolve_context(repo, task_id, selection['reference'], run_views=self.run_views)
                except (ValueError, OSError, LookupError):
                    raise MessageContextUnavailable('Selected message context unavailable before write') from None
                if snapshot['sha256'] != selection['sha256']:
                    raise MessageContextChanged('Selected message context changed; preview and select it again')
                snapshots.append(snapshot)
            if sum(len(_bytes(snapshot['text'], 'context text')) for snapshot in snapshots) > MAX_REFERENCED_BYTES:
                raise MessageInputRejected('Selected contexts exceed the current 32 KiB message budget')
            # Actual source provenance remains in the internal record. The model
            # receives only the explicitly selected text and human-readable scope.
            body_context = [{key: snapshot[key] for key in ('reference', 'title', 'text', 'sha256')} for snapshot in snapshots]
            body = json.dumps({'message': text, 'context': body_context}, ensure_ascii=False, separators=(',', ':'))
            encoded = _bytes(body, 'message body')
            if (len(encoded) > MAX_BODY_BYTES
                    or sum(2 if ord(char) > 0xffff else 1 for char in body) > MAX_BODY_UTF16_UNITS):
                raise MessageInputRejected('Message body exceeds the bridge UTF-8 or UTF-16 budget')
            intent_id, message_id = uid(), str(uuid.uuid4())
            command = {'op': 'sendAgent', 'agentId': session['agent_id'], 'expectedCwd': location,
                       'text': body, 'messageId': message_id, 'activeTurnBehavior': active_turn_behavior}
            # Server-owned pure serialization check. It runs before any domain
            # write, so input-budget rejection really leaves no prepared intent.
            # Existing receipts returned above never revalidate mutable config.
            if preflight is not None:
                if not callable(preflight):
                    raise ValueError('Message preflight is not configured')
                preflight(copy.deepcopy(command))
            conn.execute('''INSERT INTO agent_message_intents
                (id,task_id,workspace_id,actor,request_key,session_id,agent_id,message_id,payload,snapshots,command,
                 body_sha256,input_sha256,status,prepared_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'prepared',?)''',
                         (intent_id, task_id, task['workspace_id'], actor, request_key, session_id, session['agent_id'], message_id,
                          serialized, json.dumps(snapshots, ensure_ascii=False), json.dumps(command, ensure_ascii=False),
                          hashlib.sha256(encoded).hexdigest(), input_sha, now()))
            repo._event(conn, task_id, 'agent.message.prepared', intent_id,
                        {'session_id': session_id, 'actor': actor,
                         'active_turn_behavior': active_turn_behavior, 'context_count': len(snapshots)})
            return self._scope(conn, task_id, intent_id, actor)[1]

    def get(self, task_id, intent_id, *, actor):
        actor = _opaque(actor, 'actor')
        with self.repository.connect() as conn:
            return self._scope(conn, task_id, intent_id, actor)[1]

    def list(self, task_id, *, actor):
        actor = _opaque(actor, 'actor')
        with self.repository.connect() as conn:
            self._task(conn, task_id)
            return [self._record(row) for row in conn.execute(
                'SELECT * FROM agent_message_intents WHERE task_id=? AND actor=? ORDER BY prepared_at,id', (task_id, actor))]

    def attempt(self, task_id, intent_id, *, actor, bridge):
        # The adapter's closed protocol validator will be supplied by the
        # separately reviewed IPC slice; this module makes no SDK/CLI calls.
        from .message_bridge import validate_send_envelope
        actor = _opaque(actor, 'actor')
        repo = self.repository
        with repo.connect(write=True) as conn:
            task, intent = self._scope(conn, task_id, intent_id, actor)
            if intent['status'] != 'prepared':
                return intent
            session, location = self._session(conn, task, intent['session_id'])
            command = intent['command']
            if (intent['workspace_id'] != task['workspace_id']
                    or session['agent_id'] != command['agentId']
                    or location != command['expectedCwd']):
                raise Conflict('Message authorization changed before dispatch')
            if not callable(getattr(bridge, 'call', None)):
                raise ValueError('Message bridge is not configured')
            conn.execute("UPDATE agent_message_intents SET status='unknown',started_at=? WHERE id=?", (now(), intent_id))
            repo._event(conn, task_id, 'agent.message.started', intent_id,
                        {'session_id': intent['session_id'], 'status': 'unknown'})
        try:
            raw = bridge.call(copy.deepcopy(command))
        except Exception:
            envelope = _unknown(command, 'BRIDGE_UNAVAILABLE')
        else:
            try:
                envelope = validate_send_envelope(raw, command)
            except (ValueError, TypeError, KeyError, RecursionError):
                envelope = _unknown(command, 'BRIDGE_INVALID_RESPONSE')
        with repo.connect(write=True) as conn:
            _, current = self._scope(conn, task_id, intent_id, actor)
            if current['envelope'] is not None:
                return current
            result = envelope['result']
            conn.execute('UPDATE agent_message_intents SET status=?,envelope=?,finished_at=? WHERE id=?',
                         (result['status'], json.dumps(envelope, ensure_ascii=False), now(), intent_id))
            repo._event(conn, task_id, 'agent.message.observed', intent_id,
                        {'session_id': intent['session_id'],
                         **{key: result[key] for key in ('status', 'code', 'cause') if key in result},
                         'release': envelope['release']})
            return self._scope(conn, task_id, intent_id, actor)[1]
