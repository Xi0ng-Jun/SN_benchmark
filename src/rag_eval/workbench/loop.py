"""Durable LCD round coordinator; all model/execution work belongs to ports.

One invocation owns an iteration under a file lock. SQLite transactions only
save observations and intents, never span network or subprocess I/O. Port keys
are stable even if a process dies between an external effect and its snapshot.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import math
import sqlite3
import threading
import time
import uuid

from .repository import Conflict, UnknownIdentity, _text

ROUND_DEFAULTS = dict(analyst_profile='pi-flash', implementer_profile='pi-flash',
    merge_profile='pi-flash', max_clusters=6, b=2, pi=0.3, card_approval='auto',
    agent_timeout_seconds=3600, execution_timeout_seconds=7200, infra_retry=1,
    agent_start_grace_seconds=300, agent_followups_per_stage=1)
TERMINAL = frozenset({'completed', 'stopped', 'failed'})


def validate_settings(settings):
    """Validate the closed set of frozen orchestration settings."""
    if not isinstance(settings, dict):
        raise ValueError('Round settings must be an object')
    if set(settings) - set(ROUND_DEFAULTS):
        raise ValueError('Unknown round settings')
    result = dict(ROUND_DEFAULTS, **settings)
    for key in ('max_clusters', 'b', 'infra_retry', 'agent_followups_per_stage'):
        if type(result[key]) is not int or result[key] < (0 if key == 'infra_retry' else 1):
            raise ValueError('Invalid round setting: ' + key)
    for key in ('agent_timeout_seconds', 'execution_timeout_seconds', 'agent_start_grace_seconds'):
        if type(result[key]) not in (int, float) or not math.isfinite(result[key]) or result[key] <= 0:
            raise ValueError('Invalid round setting: ' + key)
    # This value is also the per-execution budget, whose existing runner
    # contract requires whole seconds and a maximum of one day.
    if type(result['execution_timeout_seconds']) is not int or result['execution_timeout_seconds'] > 86400:
        raise ValueError('execution_timeout_seconds must be an integer in [1, 86400]')
    if result['b'] > result['max_clusters']:
        raise ValueError('Round setting b exceeds max_clusters')
    if type(result['pi']) not in (int, float) or not math.isfinite(result['pi']) or not 0 <= result['pi'] <= 1:
        raise ValueError('Invalid round setting: pi')
    if result['card_approval'] not in ('auto', 'manual'):
        raise ValueError('Invalid round setting: card_approval')
    for key in ('analyst_profile', 'implementer_profile', 'merge_profile'):
        _text(result[key], key, limit=200)
    return result


def transient(error):
    from .bridge_adapter import InvalidBridgeResponse
    return isinstance(error, (OSError, sqlite3.OperationalError, InvalidBridgeResponse))


def execution_window(settings):
    # Includes every allowed retry and a bounded dispatch/scheduling margin.
    timeout = settings['execution_timeout_seconds']
    return timeout * (1 + settings['infra_retry']) + min(60, timeout)


class _Stopped(Exception):
    pass


class RoundService:
    def __init__(self, repository, operations, *, clock=time.time):
        self.repo, self.operations, self.clock = repository, operations, clock
        self._mutex = threading.RLock()
        self.lock_root = repository.root / 'round-locks'
        self.lock_root.mkdir(mode=0o700, exist_ok=True)
        with self.repo.connect(write=True) as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS iteration_round_invocations(
                    id TEXT PRIMARY KEY, iteration_id TEXT NOT NULL, record TEXT NOT NULL,
                    stop_actor TEXT, stop_at REAL);
                CREATE INDEX IF NOT EXISTS round_invocation_iteration ON iteration_round_invocations(iteration_id);
                CREATE TABLE IF NOT EXISTS round_service_clock(id INTEGER PRIMARY KEY, heartbeat REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS iteration_round_requests(
                    iteration_id TEXT NOT NULL, request_key TEXT NOT NULL, actor TEXT NOT NULL,
                    requested_rounds INTEGER NOT NULL, invocation_id TEXT NOT NULL,
                    PRIMARY KEY(iteration_id,request_key));
            ''')

    @contextmanager
    def _lock(self, iteration_id, *, blocking=True):
        path = self.lock_root / (hashlib.sha256(iteration_id.encode()).hexdigest() + '.lock')
        with path.open('a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def get(self, round_id):
        with self.repo.connect() as conn:
            row = conn.execute('SELECT * FROM iteration_round_invocations WHERE id=?', (round_id,)).fetchone()
        if row is None:
            raise UnknownIdentity('Unknown round invocation')
        record = json.loads(row['record'])
        if row['stop_actor'] is not None:
            record.update(status='stopped', stop_reason='operator_stop', stopped_by=row['stop_actor'],
                          stopped_at=row['stop_at'])
        return record

    def list(self, iteration_id=None):
        with self.repo.connect() as conn:
            rows = conn.execute('SELECT id FROM iteration_round_invocations' +
                                (' WHERE iteration_id=?' if iteration_id else '') + ' ORDER BY rowid',
                                (iteration_id,) if iteration_id else ()).fetchall()
        return [self.get(row['id']) for row in rows]

    def _save(self, state):
        with self._mutex:
            state['updated_at'] = self.clock()
            with self.repo.connect(write=True) as conn:
                conn.execute('UPDATE iteration_round_invocations SET record=? WHERE id=?',
                             (json.dumps(state, ensure_ascii=False, allow_nan=False), state['id']))

    def heartbeat(self):
        with self.repo.connect(write=True) as conn:
            conn.execute('INSERT INTO round_service_clock VALUES(1,?) ON CONFLICT(id) DO UPDATE '
                         'SET heartbeat=excluded.heartbeat', (self.clock(),))

    def resume(self):
        """Compensate downtime once per manager start, never on ordinary ticks."""
        with self._lock('__service_resume__'):
            with self.repo.connect() as conn:
                row = conn.execute('SELECT heartbeat FROM round_service_clock WHERE id=1').fetchone()
            previous = row[0] if row else None
            at = self.clock()
            if previous is not None and at - previous > 5:
                for identity in self.list():
                    if identity['status'] in TERMINAL:
                        continue
                    with self._lock(identity['iteration_id']):
                        state = self.get(identity['id'])
                        if state['status'] in TERMINAL:
                            continue
                        # Per-record receipt makes a crash midway through resume safe.
                        since = max(previous, state.get('resumed_at', previous), state['created_at'])
                        gap = max(0, at - since)
                        def extend(value):
                            if isinstance(value, dict):
                                for key, child in value.items():
                                    if key.endswith('_deadline') and type(child) in (int, float):
                                        value[key] += gap
                                    else:
                                        extend(child)
                            elif isinstance(value, list):
                                for child in value:
                                    extend(child)
                        extend(state)
                        state['resumed_at'] = at
                        state['downtime_seconds'] = state.get('downtime_seconds', 0) + gap
                        self._save(state)
            self.heartbeat()

    def _transient(self, state, record, error, *, save):
        at = self.clock()
        record['transient_error'] = dict(type=type(error).__name__, phase=record.get('phase', state['phase']), at=at)
        # Consecutive failures are bounded even at boundary/selection/ledger.
        holder = state['rounds'][-1] if record is state and state['rounds'] else record
        phase = record.get('phase', state['phase'])
        key = {'baseline': 'baseline_deadline', 'analysis': 'analysis_deadline',
               'selection': 'analysis_deadline', 'implementation':
               'execution_deadline' if record.get('waiting_execution') else 'agent_deadline',
               'measure': 'execution_deadline', 'fresh': 'fresh_deadline'}.get(phase)
        bound = holder.get(key) if key else None
        deadline = record.setdefault('transient_deadline', bound if bound is not None else
                                     at + state['settings']['agent_timeout_seconds'])
        save()
        return at >= deadline

    def _followup(self, state, record, stage, deadline_key):
        counts = record.setdefault('followups', {})
        count = counts.get(stage, 0)
        if count >= state['settings'].get('agent_followups_per_stage', 1):
            return False
        # Extra attempts, if configured, are spaced across full agent windows.
        if count and self.clock() < record[deadline_key]:
            return False
        counts[stage] = count + 1
        record['message_stage'] = f'{stage}/{count + 1}'
        record[deadline_key] = self.clock() + state['settings']['agent_timeout_seconds']
        return True

    @staticmethod
    def _fallback(state, record, reason):
        accepted = [c for c in record['candidates'] if c.get('verdict', {}).get('verdict') == 'accept']
        if not accepted:
            return
        def delta(candidate):
            verdict = candidate['verdict']
            value = verdict.get('delta', verdict.get('validation', {}).get('delta'))
            return value if type(value) in (int, float) and math.isfinite(value) else -math.inf
        chosen = max(accepted, key=delta)
        state['baseline_candidate_id'] = chosen['candidate_id']
        record['baseline_selection'] = dict(reason=reason, candidate_id=chosen['candidate_id'],
            validation_delta=None if delta(chosen) == -math.inf else delta(chosen))

    def start(self, iteration_id, *, actor, request_key, rounds=1):
        iteration_id = _text(iteration_id, 'iteration identity', limit=200)
        actor, request_key = _text(actor, 'actor', limit=200), _text(request_key, 'request key', limit=200)
        if type(rounds) is not int or rounds < 1:
            raise ValueError('rounds must be a positive integer')
        with self._lock(iteration_id):
            with self.repo.connect() as conn:
                prior = conn.execute('SELECT * FROM iteration_round_requests WHERE iteration_id=? AND request_key=?',
                                     (iteration_id, request_key)).fetchone()
            if prior:
                if prior['actor'] != actor or prior['requested_rounds'] != rounds:
                    raise Conflict('Round request key was already used with different content')
                return self.get(prior['invocation_id'])
            history = self.list(iteration_id)
            active = next((r for r in reversed(history) if r['status'] not in TERMINAL), None)
            if active is None:
                description = self.operations.describe(iteration_id)
                settings = validate_settings(description.get('settings') or {})
                previous = history[-1] if history else {}
                baseline = previous.get('baseline_candidate_id') or description.get('baseline_candidate_id') or description['root_candidate_id']
                active = dict(id=uuid.uuid4().hex, iteration_id=iteration_id, actor=actor,
                    request_key=request_key, requested_rounds=rounds, status='running', phase='boundary',
                    settings=settings, k_base=description['k_base'], baseline_candidate_id=baseline,
                    no_accept_streak=0, previous_no_accept_streak=previous.get('no_accept_streak', 0), rounds=[],
                    created_at=self.clock(), updated_at=self.clock())
                with self.repo.connect(write=True) as conn:
                    conn.execute('INSERT INTO iteration_round_invocations(id,iteration_id,record) VALUES(?,?,?)',
                                 (active['id'], iteration_id, json.dumps(active)))
            with self.repo.connect(write=True) as conn:
                conn.execute('INSERT INTO iteration_round_requests VALUES(?,?,?,?,?)',
                             (iteration_id, request_key, actor, rounds, active['id']))
            return active

    def stop(self, round_id, *, actor):
        actor = _text(actor, 'actor', limit=200)
        state = self.get(round_id)
        if state['status'] in TERMINAL:
            return state
        # Independent of tick's flock: a stop is durable even during a slow port.
        with self.repo.connect(write=True) as conn:
            conn.execute('UPDATE iteration_round_invocations SET stop_actor=COALESCE(stop_actor,?), '
                         'stop_at=COALESCE(stop_at,?) WHERE id=?', (actor, self.clock(), round_id))
        return self.get(round_id)

    def _call(self, state, method, *args, **kwargs):
        if self.get(state['id'])['status'] == 'stopped':
            raise _Stopped()
        return getattr(self.operations, method)(*args, **kwargs)

    def _key(self, state, suffix):
        return f"round/{state['id']}/{len(state['rounds'])}/{suffix}"

    def _phase(self, state, phase):
        record = state['rounds'][-1]
        at = self.clock()
        previous = record['phase']
        record.setdefault('timings', {}).setdefault(previous, {})['finished_at'] = at
        started = record['timings'][previous].get('started_at', record['started_at'])
        record['timings'][previous]['duration_seconds'] = max(0, at - started)
        record['phase'] = state['phase'] = phase
        record['timings'].setdefault(phase, {})['started_at'] = at
        state['status'] = 'running'
        self._save(state)

    def _deadline(self, state, record, key, seconds):
        if key not in record:
            record[key] = self.clock() + seconds
            self._save(state)
        return self.clock() >= record[key]

    def _finish(self, state, reason, *, failed=False):
        state.update(status='failed' if failed else 'completed', stop_reason=reason, finished_at=self.clock())
        self._save(state)

    def _mark_stop(self, state, reason):
        with self._mutex:
            # Budget stop takes precedence; both observations remain in records.
            if state.get('pending_stop') != 'budget_exhausted':
                state['pending_stop'] = reason
            self._save(state)

    def tick(self, round_id):
        identity = self.get(round_id)
        if identity['status'] in TERMINAL:
            return identity
        with self._lock(identity['iteration_id'], blocking=False) as acquired:
            if not acquired:
                return self.get(round_id)
            state = self.get(round_id)
            if state['status'] in TERMINAL:
                return state
            try:
                self._tick(state)
                if state.pop('transient_deadline', None) is not None:
                    self._save(state)
            except _Stopped:
                pass
            except Exception as exc:
                if transient(exc):
                    if self._transient(state, state, exc, save=lambda: self._save(state)):
                        self._finish(state, 'transient_timeout', failed=True)
                    return self.get(round_id)
                state['reason'] = 'round operation failed in ' + state['phase'] + ': ' + type(exc).__name__
                if state['rounds']:
                    state['rounds'][-1]['failure'] = state['reason']
                self._finish(state, 'operation_failed', failed=True)
            return self.get(round_id)

    def _tick(self, state):
        phase, settings = state['phase'], state['settings']
        if phase == 'boundary':
            description = self._call(state, 'describe', state['iteration_id'])
            reason = ('budget_exhausted' if description.get('budget_exhausted') else
                      'fresh_exhausted' if description.get('fresh_remaining') == 0 else
                      'no_accept' if state['no_accept_streak'] >= 2 else None)
            if reason:
                self._finish(state, reason)
                return
            at = self.clock()
            state['rounds'].append(dict(number=len(state['rounds']) + 1,
                baseline_candidate_id=state['baseline_candidate_id'], phase='baseline', started_at=at,
                timings=dict(baseline=dict(started_at=at)), candidates=[]))
            state['phase'] = 'baseline'
            self._save(state)
            phase = 'baseline'
        record = state['rounds'][-1]
        baseline = record['baseline_candidate_id']
        if phase == 'baseline':
            if self._deadline(state, record, 'baseline_deadline', execution_window(settings)):
                state['reason'] = 'baseline execution timeout'
                record['failure'] = state['reason']
                record['ledger'] = self._call(state, 'ledger', state['iteration_id'])
                self._finish(state, 'baseline_failed', failed=True)
                return
            measured = self._call(state, 'measure', baseline, key=self._key(state, 'baseline'),
                actor=state['actor'], override=True, infra_retry=settings['infra_retry'],
                timeout_seconds=settings['execution_timeout_seconds'])
            record['measurement'] = measured
            self._save(state)
            if measured['status'] == 'complete':
                self._phase(state, 'analysis')
            elif measured['status'] == 'budget_exhausted':
                self._mark_stop(state, 'budget_exhausted')
                self._phase(state, 'ledger')
            elif measured['status'] == 'failed':
                state['reason'] = measured.get('reason', 'baseline measurement failed')
                record['ledger'] = self._call(state, 'ledger', state['iteration_id'])
                self._finish(state, 'baseline_failed', failed=True)
            return
        if phase == 'analysis':
            if self._deadline(state, record, 'analysis_deadline', settings['agent_timeout_seconds']):
                if record.get('analyst', {}).get('status') == 'idle' and record.get('followups', {}).get('cards_missing'):
                    if self._followup(state, record, 'cards_missing', 'analysis_deadline'):
                        self._save(state)
                        return
                record['analysis_status'], record['analysis_reason'] = 'stalled', 'analyst timeout'
                self._phase(state, 'ledger')
                return
            if 'analyst_scope' not in record:
                description = self._call(state, 'describe', state['iteration_id'])
                record['analyst_scope'] = 'iteration' if description.get('evaluated_count', 1) >= 2 else None
                self._save(state)
            analyst = self._call(state, 'agent', 'analyst', baseline, iteration_id=state['iteration_id'],
                key=self._key(state, 'analyst'), profile=settings['analyst_profile'],
                scope=record['analyst_scope'], message_stage=record.get('message_stage', 'initial'),
                start_grace_seconds=settings.get('agent_start_grace_seconds', 300), text=(
                    f"LCD round: read search evidence for baseline {baseline}. Propose at most {settings['max_clusters']} "
                    f"mechanical frozen clusters; draft legal contract cards for the top {settings['b']} unresolved clusters "
                    f"under this baseline. Rank by |C|*(1-q)*max(m,v,pi), pi={settings['pi']}; "
                    "unmeasurable m/v remain null with explicit prior fallback. If clusters exist but cards are missing, "
                    "finish drafting their legal cards now. Follow the attached cluster and contract instructions."))
            record['analyst'] = analyst
            self._save(state)
            if analyst['status'] == 'idle':
                self._phase(state, 'selection')
            elif analyst['status'] in ('failed', 'unknown'):
                record['analysis_status'] = 'stalled'
                record['analysis_reason'] = analyst.get('reason', 'analyst ' + analyst['status'])
                self._phase(state, 'ledger')
            return
        if phase == 'selection':
            selection = self._call(state, 'select', state['iteration_id'], baseline,
                max_clusters=settings['max_clusters'], b=settings['b'], pi=settings['pi'])
            record['selection'] = selection
            record['cards'] = deepcopy(selection['cards'][:settings['b']])
            if not record['cards']:
                self._followup(state, record, 'cards_missing', 'analysis_deadline')
                self._phase(state, 'analysis')
            else:
                self._phase(state, 'approval')
            return
        if phase == 'approval':
            waiting = False
            for card in record['cards']:
                if card.get('approval') in ('approved', 'rejected'):
                    continue
                hid = card['hypothesis_id']
                if settings['card_approval'] == 'auto':
                    result = self._call(state, 'approve', hid, iteration_id=state['iteration_id'],
                                        key=self._key(state, 'approve/' + hid), actor=state['actor'])
                    status = result['status']
                else:
                    status = self._call(state, 'card_status', hid, iteration_id=state['iteration_id'])
                card['approval'] = status
                waiting |= status not in ('approved', 'rejected')
                self._save(state)
            if waiting:
                state['status'] = 'waiting_approval'
                self._save(state)
                return
            record['candidates'] = [dict(hypothesis_id=c['hypothesis_id'], cluster_id=c.get('cluster_id'),
                phase='derive', status='running', started_at=self.clock(), timings={})
                for c in record['cards'] if c['approval'] == 'approved']
            self._phase(state, 'candidates')
            return
        if phase == 'candidates':
            pending = [(i, deepcopy(c)) for i, c in enumerate(record['candidates']) if c['phase'] != 'done']
            if pending:
                # Each port observes or dispatches and returns; no waits for Agent
                # completion. Independent candidates also overlap slow RPC setup.
                with ThreadPoolExecutor(max_workers=len(pending)) as pool:
                    futures = [pool.submit(self._candidate_safe_tick, state, c, i) for i, c in pending]
                    for future in futures:
                        future.result()
            if all(c['phase'] == 'done' for c in record['candidates']):
                accepted = [c['candidate_id'] for c in record['candidates'] if c.get('verdict', {}).get('verdict') == 'accept']
                record['accepted'] = accepted
                if len(accepted) == 1:
                    state['baseline_candidate_id'] = accepted[0]
                if len(accepted) >= 2 and state.get('pending_stop') != 'budget_exhausted':
                    record['merge'] = dict(phase='derive', status='running', started_at=self.clock(), timings={},
                                           hypothesis_id=None, integrated_from=accepted)
                    self._phase(state, 'merge')
                else:
                    if len(accepted) >= 2:
                        self._fallback(state, record, 'merge_skipped_budget')
                    self._phase(state, 'ledger')
            return
        if phase == 'merge':
            self._candidate_safe_tick(state, deepcopy(record['merge']), None)
            if record['merge']['phase'] == 'done':
                if record['merge'].get('result', {}).get('status') == 'accepted':
                    state['baseline_candidate_id'] = record['merge']['candidate_id']
                else:
                    self._fallback(state, record, 'merge_fallback')
                self._phase(state, 'ledger')
            return
        if phase == 'ledger':
            record['ledger'] = self._call(state, 'ledger', state['iteration_id'])
            accepted = record.get('accepted') or [c['candidate_id'] for c in record['candidates']
                       if c.get('verdict', {}).get('verdict') == 'accept']
            state['no_accept_streak'] = 0 if accepted else state['no_accept_streak'] + 1
            record.update(accepted=accepted, next_baseline_candidate_id=state['baseline_candidate_id'],
                          finished_at=self.clock(), duration_seconds=max(0, self.clock() - record['started_at']))
            self._phase(state, 'done')
            return
        if phase == 'done':
            reason = state.get('pending_stop') or ('no_accept' if state['no_accept_streak'] >= 2 else
                     'rounds_exhausted' if len(state['rounds']) >= state['requested_rounds'] else None)
            if reason:
                self._finish(state, reason)
            else:
                state['phase'] = 'boundary'
                self._save(state)

    def _candidate_save(self, state, candidate, index):
        with self._mutex:
            record = state['rounds'][-1]
            if index is None:
                record['merge'] = deepcopy(candidate)
            else:
                record['candidates'][index] = deepcopy(candidate)
            self._save(state)

    def _candidate_phase(self, state, candidate, index, phase, *, status=None, reason=None):
        at, previous = self.clock(), candidate['phase']
        timing = candidate['timings'].setdefault(previous, dict(started_at=candidate['started_at']))
        timing.update(finished_at=at, duration_seconds=max(0, at - timing['started_at']))
        candidate['phase'] = phase
        candidate['timings'].setdefault(phase, dict(started_at=at))
        if status:
            candidate['status'] = status
        if reason:
            candidate['reason'] = reason
        if phase == 'done':
            candidate['finished_at'] = at
        self._candidate_save(state, candidate, index)

    def _candidate_tick(self, state, candidate, index):
        settings, record = state['settings'], state['rounds'][-1]
        merge = index is None
        prefix = 'merge' if merge else 'candidate/' + candidate['hypothesis_id']
        key = lambda suffix: self._key(state, prefix + '/' + suffix)
        phase = candidate['phase']
        def done(status, reason=None):
            self._candidate_phase(state, candidate, index, 'done', status=status, reason=reason)
        if phase == 'derive':
            candidate['candidate_id'] = self._call(state, 'derive', record['baseline_candidate_id'],
                hypothesis_id=candidate['hypothesis_id'], key=key('derive'), actor=state['actor'],
                integrated_from=candidate.get('integrated_from'))
            candidate['agent_deadline'] = self.clock() + settings['agent_timeout_seconds']
            self._candidate_phase(state, candidate, index, 'implementation')
            return
        cid = candidate['candidate_id']
        if phase == 'implementation':
            waiting_execution = candidate.get('waiting_execution', False)
            deadline = candidate['execution_deadline'] if waiting_execution else candidate['agent_deadline']
            implementation = self._call(state, 'implementation', cid,
                key=key('cluster-measure'), actor=state['actor'], infra_retry=settings['infra_retry'],
                timeout_seconds=settings['execution_timeout_seconds'],
                **({'observe_only': True} if self.clock() >= deadline else {}))
            if waiting_execution and not implementation.get('execution_in_flight'):
                candidate['waiting_execution'] = waiting_execution = False
                candidate['agent_deadline'] = self.clock() + settings['agent_timeout_seconds']
            candidate['implementation'] = implementation
            self._candidate_save(state, candidate, index)
            deadline = candidate['execution_deadline'] if waiting_execution else candidate['agent_deadline']
            if self.clock() >= deadline:
                if (not waiting_execution and candidate.get('agent', {}).get('status') == 'idle'
                        and not implementation.get('awaiting_confirmation')
                        and not implementation.get('execution_in_flight')
                        and implementation['status'] == 'running'):
                    stage = implementation.get('reason') or 'implementation_missing'
                    if self._followup(state, candidate, stage, 'agent_deadline'):
                        self._candidate_save(state, candidate, index)
                        return
                done('stalled', 'candidate execution timeout' if waiting_execution else 'implementation agent timeout')
                return
            text = (f"Merge only approved sources {', '.join(candidate['integrated_from'])} into candidate {cid}; "
                    "resolve conflicts without new behavior. " if merge else f"Implement the approved card in candidate {cid}. ")
            text += ("Required LCD order: implementation -> only relevant SN tests -> leak-check -> "
                     f"node freeze {cid} -> " + ('' if merge else f"node cluster-run {cid} -> read cluster result -> ") +
                     f"node run {cid} --attempts {state['k_base']}. Requests within the frozen budget execute automatically. "
                     "Use only approved implementer material; never inspect gold or validation cases.")
            agent = self._call(state, 'agent', 'implementer', cid, iteration_id=state['iteration_id'],
                key=key('agent'), profile=settings['merge_profile' if merge else 'implementer_profile'],
                text=text, hypothesis_id=candidate['hypothesis_id'],
                message_stage=candidate.get('message_stage', 'initial'),
                start_grace_seconds=settings.get('agent_start_grace_seconds', 300))
            candidate['agent'] = agent
            self._candidate_save(state, candidate, index)
            if implementation.get('budget_exhausted'):
                self._mark_stop(state, 'budget_exhausted')
                done('budget_exhausted', implementation.get('reason', 'implementation request exceeds budget'))
            elif implementation['status'] == 'ready':
                candidate['execution_deadline'] = self.clock() + execution_window(settings)
                self._candidate_phase(state, candidate, index, 'measure')
            elif implementation['status'] == 'failed':
                done('stalled', implementation.get('reason', 'implementation request failed'))
            elif agent['status'] in ('unknown', 'failed'):
                done('stalled', agent.get('reason', 'agent ' + agent['status']))
            elif agent['status'] == 'idle' and not implementation.get('execution_in_flight') and not implementation.get('awaiting_confirmation'):
                stage = implementation.get('reason') or 'implementation_missing'
                self._followup(state, candidate, stage, 'agent_deadline')
                self._candidate_save(state, candidate, index)
            elif implementation.get('execution_in_flight') and not waiting_execution:
                candidate['waiting_execution'] = True
                candidate['execution_deadline'] = self.clock() + execution_window(settings)
                self._candidate_save(state, candidate, index)
            return
        if phase == 'measure':
            if self.clock() >= candidate['execution_deadline']:
                done('stalled', 'candidate execution timeout')
                return
            measurement_key = 'measure' + ('/' + candidate['measurement_revision'] if candidate.get('measurement_revision') else '')
            result = self._call(state, 'measure', cid, key=key(measurement_key), actor=state['actor'],
                infra_retry=settings['infra_retry'], timeout_seconds=settings['execution_timeout_seconds'])
            candidate['measurement'] = result
            self._candidate_save(state, candidate, index)
            if result['status'] == 'complete':
                self._candidate_phase(state, candidate, index, 'merge_result' if merge else 'gate')
            elif result['status'] == 'budget_exhausted':
                self._mark_stop(state, 'budget_exhausted')
                done('budget_exhausted', result.get('reason'))
            elif result['status'] == 'failed':
                done('stalled', result.get('reason', 'candidate measurement failed'))
            return
        if phase == 'gate':
            candidate['verdict'] = self._call(state, 'gate', cid, key=key('gate'), actor=state['actor'])
            self._candidate_save(state, candidate, index)
            if candidate['verdict']['verdict'] == 'pending':
                revision = _text(candidate['verdict'].get('measurement_revision'), 'measurement revision', limit=200)
                if revision != candidate.get('measurement_revision'):
                    candidate.setdefault('measurement_history', []).append(deepcopy(candidate.get('measurement')))
                    candidate['measurement_revision'] = revision
                    candidate['execution_deadline'] = self.clock() + execution_window(settings)
                self._candidate_phase(state, candidate, index, 'measure')
            elif candidate['verdict']['verdict'] == 'accept':
                candidate['fresh_deadline'] = self.clock() + execution_window(settings)
                self._candidate_phase(state, candidate, index, 'fresh')
            else:
                done(candidate['verdict']['verdict'])
            return
        if phase == 'fresh':
            if self.clock() >= candidate['fresh_deadline']:
                candidate['fresh'] = dict(status='failed', reason='fresh execution timeout')
                done('accepted_unconfirmed', 'fresh execution timeout')
                return
            result = self._call(state, 'fresh', cid, key=key('fresh'), actor=state['actor'],
                infra_retry=settings['infra_retry'], timeout_seconds=settings['execution_timeout_seconds'])
            candidate['fresh'] = result
            self._candidate_save(state, candidate, index)
            if result['status'] == 'complete':
                done('accepted')
            elif result['status'] in ('exhausted', 'budget_exhausted', 'failed'):
                if result['status'] != 'failed':
                    self._mark_stop(state, 'fresh_exhausted' if result['status'] == 'exhausted' else 'budget_exhausted')
                done('accepted_unconfirmed', result.get('reason'))
            return
        if phase == 'merge_result':
            candidate['result'] = self._call(state, 'merge_result', cid, record['baseline_candidate_id'],
                                             key=key('result'), actor=state['actor'])
            done(candidate['result']['status'])

    def _candidate_safe_tick(self, state, candidate, index):
        try:
            self._candidate_tick(state, candidate, index)
            if candidate.pop('transient_deadline', None) is not None:
                self._candidate_save(state, candidate, index)
        except _Stopped:
            raise
        except Exception as exc:
            if transient(exc):
                expired = self._transient(state, candidate, exc,
                    save=lambda: self._candidate_save(state, candidate, index))
                if expired:
                    self._candidate_phase(state, candidate, index, 'done', status='stalled', reason='transient timeout')
                return
            # One bad request must not strand its siblings. Avoid copying raw
            # exception text (which may contain service addresses or secrets).
            self._candidate_phase(state, candidate, index, 'done', status='stalled',
                                  reason='candidate operation failed: ' + type(exc).__name__)
