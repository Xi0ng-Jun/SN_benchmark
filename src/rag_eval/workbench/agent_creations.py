"""Server-owned creation intents, with durable uncertainty before dispatch.

These are internal records, including filesystem scope and frozen SDK parameters.
HTTP must project them rather than return their command. No recovery read can
prove that an unknown creation did not occur, so this store never replays it.
"""
from __future__ import annotations

import copy
import json
import uuid

from .repository import Conflict, _text, now, uid


def _opaque(value, name, limit=200):
    _text(value, name, limit=limit)
    if value != value.strip() or '\0' in value:
        raise ValueError('Invalid ' + name)
    return value


def _unknown(command, cause):
    return {'result': {'status': 'unknown', 'code': 'CREATE_OUTCOME_UNKNOWN',
                       'cause': cause, 'agentId': command['agentId']},
            'release': {'status': 'unavailable', 'code': 'BRIDGE_RELEASE_UNKNOWN'}}


class AgentCreationStore:
    """Bindings map registered local workspace IDs to allowed Paseo profiles.

    Shape: {local_id: {workspaceId: remote_id, profiles: {name: closed_config}}}.
    Only the server supplies bindings/actor/bridge. prepare accepts a profile
    choice and optional title, never a free cwd, UUID, SDK key or config.
    """
    def __init__(self, repository, bindings):
        if not isinstance(bindings, dict):
            raise ValueError('Invalid creation bindings')
        self.repository = repository
        self._bindings = copy.deepcopy(bindings)
        with repository.connect(write=True) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS agent_creation_intents(
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id),
                actor TEXT NOT NULL, request_key TEXT NOT NULL,
                agent_id TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL, command TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('prepared','unknown','created','not_attempted')),
                envelope TEXT, session_id TEXT REFERENCES task_sessions(id),
                prepared_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                UNIQUE(task_id,actor,request_key)
            )''')

    @staticmethod
    def _record(row):
        record = dict(row)
        payload = json.loads(record.pop('payload'))
        record.update(payload)
        record['command'] = json.loads(record['command'])
        record['envelope'] = json.loads(record['envelope']) if record['envelope'] is not None else None
        return record

    def _scope(self, conn, task_id, intent_id, actor):
        task = self.repository._require(conn, 'tasks', task_id)
        row = conn.execute('SELECT * FROM agent_creation_intents WHERE id=? AND task_id=? AND actor=?',
                           (intent_id, task_id, actor)).fetchone()
        if row is None:
            raise ValueError('Creation intent is not owned by this task and actor')
        return task, self._record(row)

    def _command(self, conn, task, profile_id, title, agent_id, sdk_key, role=None, hypothesis_id=None,
                 candidate_id=None):
        from .creation_bridge import validate_create_command
        workspace = self.repository._require(conn, 'workspaces', task['workspace_id'])
        binding = self._bindings.get(workspace['id'])
        if (not isinstance(binding, dict) or set(binding) != {'workspaceId', 'profiles'}
                or not isinstance(binding['profiles'], dict) or profile_id not in binding['profiles']):
            raise ValueError('Creation profile is not allowed for this task workspace')
        command = {'op': 'createAgent', 'workspaceId': binding['workspaceId'],
                   'expectedCwd': workspace['location'], 'agentId': agent_id,
                   'idempotencyKey': sdk_key, 'config': copy.deepcopy(binding['profiles'][profile_id])}
        bound = (conn.execute('SELECT * FROM task_worktrees WHERE task_id=?', (task['id'],)).fetchone()
                 if conn.execute("SELECT 1 FROM sqlite_master WHERE name='task_worktrees'").fetchone() else None)
        if role == 'implementer' and candidate_id is not None:
            # An iteration candidate owns its worktree; several candidates may
            # implement one proposal, so the card cannot choose the directory.
            from .iterations import candidate_binding
            bound = candidate_binding(conn, candidate_id)
        elif role == 'implementer':
            from .hypothesis_worktrees import hypothesis_binding
            saved = hypothesis_binding(conn, task['id'], hypothesis_id)
            if saved is not None:
                bound = saved
            elif conn.execute("SELECT 1 FROM sqlite_master WHERE name='hypothesis_worktrees'").fetchone() and conn.execute(
                    'SELECT 1 FROM hypothesis_worktrees WHERE task_id=? AND hypothesis_id=?', (task['id'], hypothesis_id)).fetchone():
                raise Conflict('Hypothesis worktree creation is not confirmed')
        # Roles: an analyst works in the evaluation workspace and only reads SN;
        # an implementer works in the Task's SN worktree. Legacy (no role) keeps
        # the earlier rule: bound worktree when present.
        if role == 'implementer' and bound is None:
            raise ValueError('An implementer Agent needs a Task bound to an SN worktree')
        if bound is not None and role != 'analyst':
            if not bound['paseo_workspace_id']:
                raise ValueError('Task worktree needs a registered Paseo workspace for agent creation')
            command.update(workspaceId=bound['paseo_workspace_id'], expectedCwd=bound['worktree_path'])
        if title is not None:
            command['title'] = title
        return validate_create_command(command)

    def prepare(self, task_id, profile_id, *, actor, request_key, title=None, role=None, hypothesis_id=None,
                candidate_id=None):
        task_id = _opaque(task_id, 'task identity')
        profile_id = _opaque(profile_id, 'creation profile')
        actor = _opaque(actor, 'actor')
        # Existing requests elsewhere preserve exact keys. This table does too.
        _text(request_key, 'request key', limit=200)
        if title is not None:
            _opaque(title, 'creation title')
        if role not in (None, 'analyst', 'implementer'):
            raise ValueError('Invalid Agent role')
        if (role == 'implementer') != (hypothesis_id is not None):
            raise ValueError('An implementer Agent is created for exactly one approved hypothesis card')
        if candidate_id is not None and role not in ('analyst', 'implementer'):
            raise ValueError('Only an analyst or implementer Agent is created for a candidate')
        payload = {'profile_id': profile_id, 'title': title}
        if role is not None:
            payload.update(role=role, hypothesis_id=hypothesis_id)
        if candidate_id is not None:
            payload['candidate_id'] = candidate_id
        repo = self.repository
        with repo.connect(write=True) as conn:
            task = repo._require(conn, 'tasks', task_id)
            prior = conn.execute('SELECT * FROM agent_creation_intents WHERE task_id=? AND actor=? AND request_key=?',
                                 (task_id, actor, request_key)).fetchone()
            if prior is not None:
                if json.loads(prior['payload']) != payload:
                    raise Conflict('Creation request key was already used with different content')
                return self._record(prior)
            if hypothesis_id is not None:
                card = conn.execute('SELECT status FROM hypotheses WHERE id=? AND task_id=?', (hypothesis_id, task_id)).fetchone() \
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='hypotheses'").fetchone() else None
                if card is None or card['status'] not in ('approved', 'implementing'):
                    raise ValueError('The hypothesis card is not approved for implementation')
            if candidate_id is not None:
                candidate = conn.execute(
                    'SELECT c.status,c.proposal_id FROM candidates c JOIN iterations i ON i.id=c.iteration_id '
                    'WHERE c.id=? AND i.task_id=?', (candidate_id, task_id)).fetchone() \
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='candidates'").fetchone() else None
                # An analyst reads one frozen node (its runs are the evidence it cites);
                # an implementer changes one candidate that is still being implemented.
                if candidate is None or (candidate['status'] != 'frozen' if role == 'analyst' else (
                        candidate['proposal_id'] != hypothesis_id or candidate['status'] != 'ready')):
                    raise ValueError('The candidate is not frozen for analysis' if role == 'analyst'
                                     else 'The candidate is not implementing this proposal')
            intent_id, agent_id = uid(), str(uuid.uuid4())
            command = self._command(conn, task, profile_id, title, agent_id, 'creation:' + intent_id, role, hypothesis_id,
                                    candidate_id)
            conn.execute('''INSERT INTO agent_creation_intents
                (id,task_id,actor,request_key,agent_id,payload,command,status,prepared_at)
                VALUES(?,?,?,?,?,?,?,'prepared',?)''',
                         (intent_id, task_id, actor, request_key, agent_id, json.dumps(payload),
                          json.dumps(command, ensure_ascii=False), now()))
            repo._event(conn, task_id, 'agent.creation.prepared', intent_id,
                        {'agent_id': agent_id, 'profile_id': profile_id, 'title': title, 'actor': actor,
                         **({'role': role, 'hypothesis_id': hypothesis_id} if role else {}),
                         **({'candidate_id': candidate_id} if candidate_id else {})})
            return self._scope(conn, task_id, intent_id, actor)[1]

    def get(self, task_id, intent_id, *, actor):
        actor = _opaque(actor, 'actor')
        with self.repository.connect() as conn:
            return self._scope(conn, task_id, intent_id, actor)[1]

    def list(self, task_id, *, actor):
        """Read only this actor's local intents, including unresolved claims."""
        actor = _opaque(actor, 'actor')
        with self.repository.connect() as conn:
            self.repository._require(conn, 'tasks', task_id)
            return [self._record(row) for row in conn.execute(
                'SELECT * FROM agent_creation_intents WHERE task_id=? AND actor=? ORDER BY prepared_at,id',
                (task_id, actor))]

    def attempt(self, task_id, intent_id, *, actor, bridge):
        """Claim once, commit uncertainty, then make at most one adapter call.

        Repeated/concurrent calls only return the record. Even a crash after
        claim and before process start leaves unknown; safety cannot distinguish
        it from a crash after a remote side effect. There is no automatic retry.
        """
        from .creation_bridge import validate_create_envelope
        actor = _opaque(actor, 'actor')
        repo = self.repository
        with repo.connect(write=True) as conn:
            task, intent = self._scope(conn, task_id, intent_id, actor)
            if intent['status'] != 'prepared':
                return intent
            if not callable(getattr(bridge, 'call', None)):
                raise ValueError('Creation bridge is not configured')
            command = intent['command']
            try:
                authorized = self._command(conn, task, intent['profile_id'], intent['title'],
                                           command['agentId'], command['idempotencyKey'], intent.get('role'),
                                           intent.get('hypothesis_id'), intent.get('candidate_id'))
            except ValueError:
                raise Conflict('Creation authorization changed before dispatch') from None
            if authorized != command:
                raise Conflict('Creation authorization changed before dispatch')
            conn.execute("UPDATE agent_creation_intents SET status='unknown',started_at=? WHERE id=?",
                         (now(), intent_id))
            repo._event(conn, task_id, 'agent.creation.started', intent_id,
                        {'agent_id': command['agentId'], 'status': 'unknown'})
        # No SQLite transaction is held across the child process / external IO.
        try:
            raw = bridge.call(copy.deepcopy(command))
        except Exception:
            envelope = _unknown(command, 'BRIDGE_UNAVAILABLE')
        else:
            try:
                envelope = validate_create_envelope(raw, command)
            except (ValueError, TypeError, KeyError, RecursionError):
                envelope = _unknown(command, 'BRIDGE_INVALID_RESPONSE')
        return self._finish(task_id, intent_id, actor, envelope)

    def _finish(self, task_id, intent_id, actor, envelope):
        repo = self.repository
        with repo.connect(write=True) as conn:
            _, intent = self._scope(conn, task_id, intent_id, actor)
            if intent['envelope'] is not None:
                return intent
            result = envelope['result']
            session_id = None
            if result['status'] == 'created':
                # This is a reference to the observed Agent, not proof of SDK
                # creation-key ownership, a sandbox or future readiness.
                row = conn.execute('SELECT * FROM task_sessions WHERE task_id=? AND agent_id=?',
                                   (task_id, result['agentId'])).fetchone()
                if row is None:
                    session = {'id': uid(), 'task_id': task_id, 'agent_id': result['agentId'],
                               'linked_by': actor, 'linked_at': now()}
                    conn.execute('INSERT INTO task_sessions(id,task_id,agent_id,linked_by,linked_at,role,cwd,hypothesis_id) '
                                 'VALUES(:id,:task_id,:agent_id,:linked_by,:linked_at,:role,:cwd,:hypothesis_id)',
                                 dict(session, role=intent.get('role'), cwd=intent['command']['expectedCwd'],
                                      hypothesis_id=intent.get('hypothesis_id')))
                    if intent.get('candidate_id'):
                        conn.execute('UPDATE task_sessions SET candidate_id=? WHERE id=?',
                                     (intent['candidate_id'], session['id']))
                    repo._event(conn, task_id, 'session.attached', session['id'],
                                dict(session, **({'role': intent['role']} if intent.get('role') else {})))
                    session_id = session['id']
                    if intent.get('hypothesis_id'):
                        conn.execute("UPDATE hypotheses SET status='implementing', updated_at=? WHERE id=? AND status='approved'",
                                     (now(), intent['hypothesis_id']))
                else:
                    session_id = row['id']
            conn.execute('UPDATE agent_creation_intents SET status=?,envelope=?,session_id=?,finished_at=? WHERE id=?',
                         (result['status'], json.dumps(envelope, ensure_ascii=False), session_id, now(), intent_id))
            # History is browser-visible. Filesystem scope and SDK intent stay
            # in the internal table, rather than leaking via task history.
            content = {key: result[key] for key in ('status', 'code', 'cause', 'ready', 'readiness') if key in result}
            content.update(agent_id=intent['agent_id'], session_id=session_id, release=envelope['release'])
            repo._event(conn, task_id, 'agent.creation.observed', intent_id, content)
            return self._scope(conn, task_id, intent_id, actor)[1]
