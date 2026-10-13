"""Server-side scope and browser projection for explicit Task-session reads."""
from __future__ import annotations

from pathlib import Path

from .repository import _text, now


def _outcome(value):
    # The owned process adapter validates status and fixed-code syntax. Never
    # forward its extra fields on a failure/release path.
    return {key: value[key] for key in ('status', 'code') if key in value}


class TaskSessionReader:
    def __init__(self, repository, bridge):
        self.repository = repository
        self.bridge = bridge

    def scope(self, task_id, session_id):
        task_id = _text(task_id, 'task identity', limit=200)
        session_id = _text(session_id, 'session identity', limit=200)
        with self.repository.connect() as conn:
            task = self.repository._require(conn, 'tasks', task_id)
            session = conn.execute('SELECT * FROM task_sessions WHERE id=? AND task_id=?',
                                   (session_id, task_id)).fetchone()
            if session is None:
                raise ValueError('Session is not attached to this task')
            cwd = self.repository.session_cwd(conn, session, task)
        return dict(session), cwd

    def read_agent(self, task_id, session_id):
        session, location = self.scope(task_id, session_id)
        envelope = self.bridge.call({'op': 'readAgent', 'agentId': session['agent_id']})
        raw = envelope['result']
        result = _outcome(raw)
        if raw['status'] == 'ok':
            agent = raw.get('agent')
            if (not isinstance(agent, dict) or not {'id', 'cwd', 'title', 'status', 'provider', 'model'} <= agent.keys()
                    or any(not isinstance(agent[key], str) for key in ('id', 'status', 'provider'))
                    or any(agent[key] is not None and not isinstance(agent[key], str) for key in ('title', 'model'))):
                result = {'status': 'unavailable', 'code': 'BRIDGE_INVALID_RESPONSE'}
            elif (agent.get('id') != session['agent_id'] or not isinstance(agent.get('cwd'), str)
                  or not Path(agent['cwd']).is_absolute()
                  ):
                result = {'status': 'unavailable', 'code': 'AGENT_SCOPE_MISMATCH'}
            else:
                try:
                    matched = Path(agent['cwd']).resolve() == Path(location).resolve()
                except (OSError, ValueError, RuntimeError):
                    result = {'status': 'unavailable', 'code': 'BRIDGE_INVALID_RESPONSE'}
                else:
                    # cwd/workspaceId/capabilities/metadata stay on the server.
                    result = ({'status': 'ok', 'agent': {key: agent[key] for key in
                               ('id', 'title', 'status', 'provider', 'model')}} if matched
                              else {'status': 'unavailable', 'code': 'AGENT_SCOPE_MISMATCH'})
        return {'result': result, 'release': _outcome(envelope['release']), 'observed_at': now()}
