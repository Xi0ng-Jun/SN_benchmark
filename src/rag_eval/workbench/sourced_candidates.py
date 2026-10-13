"""Resolve a browser locator through the SDK before appending a candidate."""
from __future__ import annotations

import json
import re

from .candidates import CandidateStore
from .repository import Conflict, _text


_LOCATOR_FIELDS = {'epoch', 'seq_start', 'seq_end', 'source_seq_ranges', 'turn_id', 'sha256'}


def _validate_locator(locator):
    if not isinstance(locator, dict) or set(locator) != _LOCATOR_FIELDS:
        raise ValueError('Invalid candidate locator')
    _text(locator['epoch'], 'source epoch', limit=200)
    if (type(locator['seq_start']) is not int or type(locator['seq_end']) is not int
            or not 0 <= locator['seq_start'] <= locator['seq_end'] < 9007199254740991
            or not isinstance(locator['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', locator['sha256'])):
        raise ValueError('Invalid candidate locator')
    ranges = locator['source_seq_ranges']
    if (not isinstance(ranges, list) or not ranges or len(ranges) > 1000
            or any(not isinstance(r, dict) or set(r) != {'start_seq', 'end_seq'}
                   or type(r['start_seq']) is not int or type(r['end_seq']) is not int
                   or not locator['seq_start'] <= r['start_seq'] <= r['end_seq'] <= locator['seq_end'] for r in ranges)):
        raise ValueError('Invalid candidate locator ranges')
    if locator['turn_id'] is not None:
        _text(locator['turn_id'], 'source turn', limit=200)


class SourcedCandidates:
    def __init__(self, repository, timeline):
        self.repository = repository
        self.timeline = timeline
        self.store = CandidateStore(repository)

    def save(self, task_id, session_id, locator, *, kind, text, actor, request_key):
        _validate_locator(locator)
        if kind not in {'finding', 'direction', 'next_step'}:
            raise ValueError('Unknown candidate kind')
        text = _text(text, 'candidate text')
        actor = _text(actor, 'actor', limit=200)
        _text(request_key, 'request key', limit=200)
        session, _ = self.timeline.scope_reader.scope(task_id, session_id)
        task_id = session['task_id']
        # A committed receipt remains authoritative if the remote source later
        # changes or disappears. Compare the complete submitted intent before
        # returning it; never reinterpret a reused key as a new selection.
        with self.repository.connect() as conn:
            row = conn.execute('SELECT receipt FROM requests WHERE scope=? AND request_key=?',
                               ('candidate.save:' + task_id + ':' + actor, request_key)).fetchone()
        if row:
            prior = json.loads(row['receipt'])
            prior_locator = {key: prior['source'].get(key) for key in _LOCATOR_FIELDS}
            if (prior['session_id'] != session_id or prior['kind'] != kind or prior['text'] != text
                    or prior['selected_by'] != actor or prior['source']['agent_id'] != session['agent_id']
                    or prior_locator != locator):
                raise Conflict('Request key was already used with different content')
            return prior
        response = self.timeline.read_page(task_id, session_id, direction='before',
                                           cursor={'epoch': locator['epoch'], 'seq': locator['seq_end'] + 1})
        result = response['result']
        if result['status'] != 'ok' or response['release']['status'] != 'ok':
            raise Conflict('Candidate source cannot be verified; reread the session before saving')
        entry = next((entry for entry in result['entries'] if entry['kind'] == 'assistant'
                      and entry['candidateBlockedReason'] is None and entry['source_locator'] == locator), None)
        if entry is None:
            raise Conflict('Candidate source changed or is unavailable; reread before saving')
        source = {key: value for key, value in entry['source_locator'].items() if key != 'sha256'}
        source.update(agent_id=session['agent_id'], text=entry['text'])
        return self.store.save(task_id, session_id, source, kind=kind, text=text, actor=actor, request_key=request_key)
