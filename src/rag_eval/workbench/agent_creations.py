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

# Brief selector of Agents created by an automatic method-v3 run (briefs.resolve_brief).
BRIEF_V3 = 'method-v3'
# method-v4: analyst / implementer briefs of a v4 run, and the reviewer (one evidence file in, one verdict file out).
BRIEF_V4 = 'method-v4'

# These receipts prove refusal before createAgent. Identity conflicts and unknown
# outcomes need investigation, not a new UUID masquerading as a retry.
RETRYABLE_CREATION_CODES = frozenset({
    'SCOPE_MISMATCH', 'WORKSPACE_NOT_FOUND', 'WORKSPACE_UNAVAILABLE',
    'BRIDGE_UNAVAILABLE', 'NOT_CONNECTED', 'CONNECTION_UNAVAILABLE', 'CONNECT_FAILED',
})


class CreationProfileUnavailable(ValueError):
    """Safe, stable category; never carries provider configuration or credentials."""
    code = 'agent_profile_not_allowed'


def require_profile_binding(binding, profile_id):
    if (not isinstance(binding, dict) or set(binding) != {'workspaceId', 'profiles'}
            or not isinstance(binding['profiles'], dict) or profile_id not in binding['profiles']):
        raise CreationProfileUnavailable('Creation profile is not allowed for this task workspace')


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
        # task_id -> evaluated candidates of the iteration it backs (None: no iteration).
        # The server wires the iteration service here; without it an iteration
        # analyst cannot be created.
        self.evaluated_count = None
        # candidate_id -> derived state; an analyst needs a node that has finished a run.
        self.candidate_state = None
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
            conn.execute('''CREATE TABLE IF NOT EXISTS agent_creation_retries(
                predecessor_id TEXT PRIMARY KEY REFERENCES agent_creation_intents(id),
                successor_id TEXT NOT NULL UNIQUE REFERENCES agent_creation_intents(id))''')

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
        require_profile_binding(binding, profile_id)
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
        if bound is not None and role not in ('analyst', 'reviewer', 'calibrator'):
            if not bound['paseo_workspace_id']:
                raise ValueError('Task worktree needs a registered Paseo workspace for agent creation')
            command.update(workspaceId=bound['paseo_workspace_id'], expectedCwd=bound['worktree_path'])
        if title is not None:
            command['title'] = title
        return validate_create_command(command)

    def prepare(self, task_id, profile_id, *, actor, request_key, title=None, role=None, hypothesis_id=None,
                candidate_id=None, scope=None, brief=None, _retry_of=None):
        task_id = _opaque(task_id, 'task identity')
        profile_id = _opaque(profile_id, 'creation profile')
        actor = _opaque(actor, 'actor')
        # Existing requests elsewhere preserve exact keys. This table does too.
        _text(request_key, 'request key', limit=200)
        if title is not None:
            _opaque(title, 'creation title')
        if role not in (None, 'analyst', 'implementer', 'reviewer', 'calibrator'):
            raise ValueError('Invalid Agent role')
        if (role == 'implementer') != (hypothesis_id is not None):
            raise ValueError('An implementer Agent is created for exactly one approved hypothesis card')
        if candidate_id is not None and role not in ('analyst', 'implementer', 'reviewer', 'calibrator'):
            raise ValueError('Only an analyst, implementer or reviewer Agent is created for a candidate')
        if role == 'reviewer' and (candidate_id is None or brief != BRIEF_V4 or scope is not None):
            raise ValueError('A reviewer Agent reviews one candidate of a method-v4 run (brief method-v4)')
        if role == 'calibrator' and (candidate_id is None or brief != BRIEF_V4 or scope is not None):
            raise ValueError('A calibrator needs a frozen method-v4 seed binding')
        if scope not in (None, 'iteration') or (scope and (role != 'analyst' or candidate_id is not None)):
            raise ValueError('Only an analyst without a candidate is created for the whole iteration')
        # 'method-v3': an Agent of an automatic method-v3 run gets the short v3 brief.
        if brief not in (None, BRIEF_V3, BRIEF_V4) or (brief == BRIEF_V3 and role not in ('analyst', 'implementer')):
            raise ValueError('Only an analyst or implementer Agent takes the method-v3 brief')
        if brief == BRIEF_V4 and role not in ('analyst', 'implementer', 'reviewer', 'calibrator'):
            raise ValueError('Only an analyst, implementer or reviewer Agent takes the method-v4 brief')
        payload = {'profile_id': profile_id, 'title': title}
        if role is not None:
            payload.update(role=role, hypothesis_id=hypothesis_id)
        if candidate_id is not None:
            payload['candidate_id'] = candidate_id
        if scope is not None:
            payload['scope'] = scope
        if brief is not None:
            payload['brief'] = brief
        # Read before the write transaction: evaluated states come from run admission.
        evaluated = self.evaluated_count(task_id) if scope and callable(self.evaluated_count) else None
        state = (self.candidate_state(candidate_id)
                 if role == 'analyst' and candidate_id is not None and callable(self.candidate_state) else None)
        repo = self.repository
        with repo.connect(write=True) as conn:
            task = repo._require(conn, 'tasks', task_id)
            if _retry_of is not None:
                _, old = self._scope(conn, task_id, _retry_of, actor)
                self._require_retryable(old)
                linked = conn.execute('SELECT successor_id FROM agent_creation_retries WHERE predecessor_id=?',
                                      (_retry_of,)).fetchone()
                if linked:
                    return self._latest(conn, task_id, linked['successor_id'], actor)
                old_payload = json.loads(conn.execute('SELECT payload FROM agent_creation_intents WHERE id=?',
                                                     (_retry_of,)).fetchone()['payload'])
                if payload != old_payload or request_key != 'creation-retry:' + _retry_of:
                    raise Conflict('Creation retry must preserve the original request payload')
            if role == 'calibrator':
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='auto_runs'").fetchone():
                    raise ValueError('calibrator requires an automatic run')
                owner = conn.execute('SELECT i.root_candidate_id,a.method FROM iterations i JOIN auto_runs a '
                                     'ON a.iteration_id=i.id WHERE i.task_id=?', (task_id,)).fetchone()
                if not owner or owner['method'] != 'method-v4' or owner['root_candidate_id'] != candidate_id:
                    raise ValueError('calibrator requires its automatic method-v4 seed')
            prior = conn.execute('SELECT * FROM agent_creation_intents WHERE task_id=? AND actor=? AND request_key=?',
                                 (task_id, actor, request_key)).fetchone()
            if prior is not None:
                if _retry_of is not None:
                    raise Conflict('Creation retry key exists without its recovery link')
                if json.loads(prior['payload']) != payload:
                    raise Conflict('Creation request key was already used with different content')
                return self._record(prior)
            iteration = (conn.execute('SELECT 1 FROM iterations WHERE task_id=?', (task_id,)).fetchone()
                         if conn.execute("SELECT 1 FROM sqlite_master WHERE name='iterations'").fetchone() else None)
            # In an iteration every Agent has a binding: a candidate, or the whole
            # iteration once it has something to compare (root and an evaluated child).
            if iteration is not None and role != 'implementer' and candidate_id is None and scope is None:
                raise ValueError('An analyst of an iteration is bound to a candidate or to the whole iteration')
            if scope is not None and (iteration is None or evaluated is None or evaluated < 2):
                raise ValueError('An iteration analyst needs at least two evaluated candidates')
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
                if candidate is None or (candidate['status'] != 'frozen' if role in ('analyst', 'reviewer', 'calibrator') else (
                        candidate['proposal_id'] != hypothesis_id or candidate['status'] != 'ready')):
                    raise ValueError('The candidate is not frozen for analysis' if role in ('analyst', 'reviewer', 'calibrator')
                                     else 'The candidate is not implementing this proposal')
                # Evaluated or not admitted: there are runs to read. Frozen-but-unrun and
                # running nodes have nothing yet (the workbench offers no button there either).
                if role == 'analyst' and state is not None and state not in ('evaluated', 'failed'):
                    raise ValueError('The candidate has no finished run to analyse yet')
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
                         **({'candidate_id': candidate_id} if candidate_id else {}),
                         **({'scope': scope} if scope else {})})
            if _retry_of is not None:
                conn.execute('INSERT INTO agent_creation_retries VALUES(?,?)', (_retry_of, intent_id))
                repo._event(conn, task_id, 'agent.creation.retry_prepared', intent_id,
                            dict(predecessor_id=_retry_of, actor=actor))
            return self._scope(conn, task_id, intent_id, actor)[1]

    @staticmethod
    def _require_retryable(intent):
        from .creation_bridge import validate_create_envelope
        if intent['status'] != 'not_attempted' or intent.get('session_id'):
            raise Conflict('Only a confirmed pre-dispatch refusal can be retried')
        try:
            result = validate_create_envelope(intent['envelope'], intent['command'])['result']
        except ValueError:
            raise Conflict('Creation refusal has no valid receipt') from None
        if result['status'] != 'not_attempted' or result['code'] not in RETRYABLE_CREATION_CODES:
            raise Conflict('Creation refusal requires investigation; automatic replay is forbidden')

    def retry_not_attempted(self, task_id, intent_id, *, actor):
        """Explicitly prepare one successor, with fresh authorization; never dispatch here.

        The old receipt and request key remain immutable. Preparation and linking
        share one transaction, so concurrent or repeated confirmations converge.
        """
        old = self.get(task_id, intent_id, actor=actor)
        self._require_retryable(old)
        return self.prepare(task_id, old['profile_id'], actor=actor,
            request_key='creation-retry:' + intent_id, _retry_of=intent_id,
            **{key: old.get(key) for key in ('title', 'role', 'hypothesis_id', 'candidate_id', 'scope', 'brief')})

    def _latest(self, conn, task_id, intent_id, actor):
        seen = set()
        while intent_id not in seen:
            seen.add(intent_id)
            _, intent = self._scope(conn, task_id, intent_id, actor)
            linked = conn.execute('SELECT successor_id FROM agent_creation_retries WHERE predecessor_id=?',
                                  (intent_id,)).fetchone()
            if not linked:
                return intent
            intent_id = linked['successor_id']
        raise Conflict('Creation recovery chain contains a cycle')

    def find_request(self, task_id, request_key, *, actor):
        """Resolve the effective receipt for dispatch, cancellation and analysis."""
        actor = _opaque(actor, 'actor')
        with self.repository.connect() as conn:
            row = conn.execute('SELECT id FROM agent_creation_intents WHERE task_id=? AND actor=? AND request_key=?',
                               (task_id, actor, request_key)).fetchone()
            return self._latest(conn, task_id, row['id'], actor) if row else None

    def require_profile(self, task_id, profile_id):
        """Read-only preflight using the same workspace authorization as dispatch."""
        with self.repository.connect() as conn:
            task = self.repository._require(conn, 'tasks', task_id)
            require_profile_binding(self._bindings.get(task['workspace_id']), profile_id)

    def preflight_command(self, task_id, profile_id, *, role='analyst', candidate_id=None, hypothesis_id=None):
        """Construct the real authorized scope without persisting or creating an Agent."""
        with self.repository.connect() as conn:
            task = self.repository._require(conn, 'tasks', task_id)
            return self._command(conn, task, profile_id, None,
                '00000000-0000-4000-8000-000000000000', 'read-only-preflight',
                role, hypothesis_id, candidate_id)

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
                    if intent.get('scope'):
                        conn.execute('UPDATE task_sessions SET scope=? WHERE id=?', (intent['scope'], session['id']))
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
