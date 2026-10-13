"""Read committed candidate receipts without replaying an uncertain write."""
from __future__ import annotations

import json

from .repository import _text
from .sessions import TaskSessionReader


class CandidateReceipts:
    def __init__(self, repository):
        self.repository = repository

    def _read(self, conn, task_id, actor, request_key, operation, target_field, target_id):
        actor = _text(actor, 'actor', limit=200)
        _text(request_key, 'request key', limit=200)
        row = conn.execute('SELECT receipt FROM requests WHERE scope=? AND request_key=?',
                           (operation + ':' + task_id + ':' + actor, request_key)).fetchone()
        # Missing only means no committed receipt at this observation; the
        # original save can still be in flight. It never authorizes a replay.
        if row is None:
            return {'status': 'missing'}
        receipt = json.loads(row['receipt'])
        if receipt[target_field] != target_id:
            raise ValueError('Receipt does not belong to this target')
        return {'status': 'ok', 'receipt': receipt}

    def candidate(self, task_id, session_id, *, actor, request_key):
        session, _ = TaskSessionReader(self.repository, None).scope(task_id, session_id)
        with self.repository.connect() as conn:
            return self._read(conn, session['task_id'], actor, request_key,
                              'candidate.save', 'session_id', session['id'])

    def decision(self, task_id, candidate_id, *, actor, request_key):
        task_id = _text(task_id, 'task identity', limit=200)
        candidate_id = _text(candidate_id, 'candidate identity', limit=200)
        with self.repository.connect() as conn:
            self.repository._require(conn, 'tasks', task_id)
            if not conn.execute("SELECT 1 FROM history WHERE task_id=? AND kind='candidate.saved' AND object_id=?",
                                (task_id, candidate_id)).fetchone():
                raise ValueError('Candidate is not attached to this task')
            return self._read(conn, task_id, actor, request_key,
                              'candidate.decide', 'candidate_id', candidate_id)
