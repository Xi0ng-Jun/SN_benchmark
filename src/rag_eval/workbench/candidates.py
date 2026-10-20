"""Append sourced Agent candidates and separate, revisioned human decisions."""
from __future__ import annotations

import hashlib
import json

from .repository import Conflict, _text, now, uid


class CandidateStore:
    def __init__(self, repository):
        self.repository = repository

    def save(self, task_id, session_id, source, *, kind, text, actor, request_key):
        """Source must be an actual server-read assistant entry, not a browser DTO.

        The HTTP adapter will resolve epoch/sequence references through the SDK.
        Store the selected excerpt so later epoch replacement cannot erase it.
        """
        if kind not in {'finding', 'direction', 'next_step'}:
            raise ValueError('Unknown candidate kind')
        text = _text(text, 'candidate text')
        actor = _text(actor, 'actor', limit=200)
        required = {'agent_id', 'epoch', 'seq_start', 'seq_end', 'text'}
        if (not isinstance(source, dict) or not required <= source.keys()
                or not source.keys() <= required | {'source_seq_ranges', 'turn_id'}
                or type(source['seq_start']) is not int or type(source['seq_end']) is not int
                or not 0 <= source['seq_start'] <= source['seq_end']):
            raise ValueError('Invalid candidate source')
        _text(source['text'], 'source excerpt', limit=65536)
        source = dict(source, agent_id=_text(source['agent_id'], 'source agent', limit=200),
                      epoch=_text(source['epoch'], 'source epoch', limit=200))
        if 'source_seq_ranges' in source:
            ranges = source['source_seq_ranges']
            if (not isinstance(ranges, list) or not ranges or len(ranges) > 1000
                    or any(not isinstance(r, dict) or set(r) != {'start_seq', 'end_seq'}
                           or type(r['start_seq']) is not int or type(r['end_seq']) is not int
                           or not source['seq_start'] <= r['start_seq'] <= r['end_seq'] <= source['seq_end']
                           for r in ranges)):
                raise ValueError('Invalid candidate source ranges')
            source['source_seq_ranges'] = [dict(r) for r in ranges]
        if source.get('turn_id') is not None:
            source['turn_id'] = _text(source['turn_id'], 'source turn', limit=200)
        source['sha256'] = hashlib.sha256(source['text'].encode('utf-8')).hexdigest()
        payload = dict(session_id=session_id, kind=kind, text=text, source=source, origin='agent', selected_by=actor)
        repo = self.repository
        scope = 'candidate.save:' + task_id + ':' + actor
        with repo.connect(write=True) as conn:
            repo._require(conn, 'tasks', task_id)
            session = conn.execute('SELECT * FROM task_sessions WHERE id=? AND task_id=?', (session_id, task_id)).fetchone()
            if session is None or session['agent_id'] != source['agent_id']:
                raise ValueError('Candidate source session is not attached to this task')
            prior = repo._prior(conn, scope, request_key, payload)
            if prior:
                return prior
            result = dict(id=uid(), **payload, saved_at=now())
            repo._event(conn, task_id, 'candidate.saved', result['id'], result)
            repo._receipt(conn, scope, request_key, payload, result)
            return result

    def decide(self, task_id, candidate_id, *, judgment, reason, actor, base_revision, request_key):
        if judgment not in {'confirmed', 'rejected', 'undecided'}:
            raise ValueError('Unknown candidate judgment')
        actor = _text(actor, 'actor', limit=200)
        reason = _text(reason, 'decision reason')
        payload = dict(candidate_id=candidate_id, judgment=judgment, reason=reason, actor=actor, base_revision=base_revision)
        repo = self.repository
        scope = 'candidate.decide:' + task_id + ':' + actor
        with repo.connect(write=True) as conn:
            repo._require(conn, 'tasks', task_id)
            if not conn.execute("SELECT 1 FROM history WHERE task_id=? AND kind='candidate.saved' AND object_id=?",
                                (task_id, candidate_id)).fetchone():
                raise ValueError('Candidate is not attached to this task')
            prior = repo._prior(conn, scope, request_key, payload)
            if prior:
                return prior
            decisions = [json.loads(row['content']) for row in conn.execute(
                "SELECT content FROM history WHERE task_id=? AND kind='candidate.decided' AND object_id=? ORDER BY seq",
                (task_id, candidate_id))]
            head = next((d for d in reversed(decisions) if d['actor'] == actor), None)
            if base_revision != (head['id'] if head else None):
                raise Conflict('Candidate decision revision changed; reload before saving')
            result = dict(id=uid(), parent_id=base_revision, **payload, saved_at=now())
            repo._event(conn, task_id, 'candidate.decided', candidate_id, result)
            repo._receipt(conn, scope, request_key, payload, result)
            return result

    def list(self, task_id):
        history = self.repository.task(task_id)['history']
        candidates = {event['object_id']: dict(event['content'], decision=None, decisions=[])
                      for event in history if event['kind'] == 'candidate.saved'}
        for event in history:
            # Iteration candidates record their decisions under the same event kind;
            # only decisions on saved findings belong here.
            if event['kind'] == 'candidate.decided' and event['object_id'] in candidates:
                candidate = candidates[event['object_id']]
                candidate['decisions'].append(event['content'])
                candidate['decision'] = event['content']
        return list(candidates.values())
