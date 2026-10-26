"""Durable round measurement footprints over existing candidate executions."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path

from .action_requests import ActionRequestStore
from .budgets import BudgetLedger
from .comparisons import ComparisonService
from .execution_scopes import initialize as initialize_scopes
from .repository import Conflict, now
from .splits import batches, claim


def stopped(repository, key):
    parts = key.split('/')
    if len(parts) < 3 or parts[0] != 'round':
        return False
    with repository.connect() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='iteration_round_invocations'").fetchone():
            return False
        row = conn.execute('SELECT stop_actor FROM iteration_round_invocations WHERE id=?', (parts[1],)).fetchone()
    return bool(row and row[0] is not None)


def fresh_ready(repository, request_key):
    """Legacy fresh claims reconcile normally; rounds wait for bounded retries."""
    if not request_key.startswith('action:'):
        return True
    action_id = request_key.removeprefix('action:')
    with repository.connect() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='round_measurements'").fetchone():
            return True
        row = conn.execute("SELECT payload_json FROM round_measurements WHERE "
                           "json_extract(payload_json,'$.action_id')=?", (action_id,)).fetchone()
    return row is None or bool(json.loads(row[0]).get('ready_to_reconcile'))


def execute_round_action(service, record, *, actor, override=False):
    """Manual confirmation uses the durable round footprint and original actor."""
    with service.repository.connect() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='round_measurements'").fetchone():
            return None
        row = conn.execute("SELECT payload_json FROM round_measurements WHERE "
                           "json_extract(payload_json,'$.action_id')=? OR EXISTS "
                           "(SELECT 1 FROM json_each(json_extract(payload_json,'$.actions')) WHERE value=?)",
                           (record['id'], record['id'])).fetchone()
    if row is None:
        return None
    state = json.loads(row[0])
    if stopped(service.repository, state['original_key']):
        return dict(status='stopped', candidate_id=state['candidate_id'])
    tree = service.iteration(state['iteration_id'])
    generation = state.get('actions', [state['action_id']]).index(record['id'])
    steps = state['generations'][generation]
    def fresh_claim(conn):
        if state['kind'] == 'fresh':
            claim(service.repository, tree, state['candidate_id'], 'action:' + state['action_id'],
                  conn=conn, expected_batch_id=state['scope_id'])
    reservation = BudgetLedger(service.repository).reserve_many(tree, steps,
        group_key='action:' + record['id'], override=override, on_reserve=fresh_claim)
    if reservation['status'] not in ('reserved', 'unbudgeted'):
        raise Conflict('Iteration budget exceeded; explicit human confirmation required')
    if reservation['status'] == 'unbudgeted':
        with service.repository.connect(write=True) as conn:
            fresh_claim(conn)
    def launch(step):
        if stopped(service.repository, state['original_key']):
            return
        service.run(step['candidate_id'], request_key=step['request_key'], batch_id=step['batch_id'],
                    budget=dict(timeout_seconds=state['timeout_seconds']), actor=state['actor'])
    with ThreadPoolExecutor(max_workers=max(1, len(steps))) as pool:
        list(pool.map(launch, steps))
    return dict(status='dispatched', candidate_id=state['candidate_id'])


class RoundMeasurements:
    def __init__(self, iteration_api):
        self.api = iteration_api
        self.service = iteration_api.service
        self.repository = iteration_api.repository
        self.views = iteration_api.views
        self.requests = ActionRequestStore(self.repository)
        initialize_scopes(self.repository)
        with self.repository.connect(write=True) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS round_measurements '
                         '(key TEXT PRIMARY KEY, payload_json TEXT NOT NULL)')

    def _read(self, key):
        with self.repository.connect() as conn:
            row = conn.execute('SELECT payload_json FROM round_measurements WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def _save(self, key, value):
        with self.repository.connect(write=True) as conn:
            conn.execute('INSERT INTO round_measurements VALUES(?,?) ON CONFLICT(key) DO UPDATE '
                         'SET payload_json=excluded.payload_json', (key, json.dumps(value)))

    @contextmanager
    def _lock(self, iteration_id):
        # Separate from the engine lock: short planning section, never launch I/O.
        root = Path(self.service.trees.store.root) / 'round-measurement-locks'
        root.mkdir(parents=True, exist_ok=True)
        with (root / (iteration_id + '.lock')).open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _infra(self, attempt):
        if attempt.get('execution_state') == 'running' or not attempt.get('execution_id'):
            return False
        if attempt.get('execution_state') != 'finished':
            return True
        runs = self.views.execution_runs(attempt['execution_id'])
        if not runs:
            return True
        return any(case['status'] in ('error', 'missing') for run in runs
                   for case in self.views.cases(run.id)['cases'])

    @staticmethod
    def _eligible(attempt):
        return (attempt.get('admission') or {}).get('status') == 'admitted' or attempt.get('execution_state') == 'running'

    def _plan(self, candidate, tree, kind, key):
        protocol = tree['protocol']
        candidates = [candidate['id']]
        scopes = [(None, len(protocol['splits']['search']) + len(protocol['splits']['validation']))]
        scope_id = None
        if kind == 'cluster':
            from .execution_scopes import get
            scope = get(self.repository, tree['id'], candidate['_round_cluster_scope'])
            scope_id = scope['scope_id']
            scopes = [(scope_id, len(scope['case_ids']))]
            candidates.insert(0, candidate['parent_candidate_id'])
        elif kind == 'fresh':
            if not candidate.get('parent_candidate_id'):
                raise ValueError('fresh-run requires a child candidate')
            used = {b['batch_id'] for b in batches(self.repository, tree['id'])}
            scope = next((b for b in protocol.get('fresh_batches', []) if b['batch_id'] not in used), None)
            if scope is None:
                return dict(exhausted=True)
            scope_id = scope['batch_id']
            scopes = [(scope_id, len(scope['case_ids']))]
            candidates.insert(0, candidate['parent_candidate_id'])
        else:
            for batch in batches(self.repository, tree['id'], completed=True):
                scope = next(b for b in protocol['fresh_batches'] if b['batch_id'] == batch['batch_id'])
                scopes.append((scope['batch_id'], len(scope['case_ids'])))
        steps, watched = [], []
        for scope, count in scopes:
            sides = list(candidates)
            if scope is not None and kind == 'measure' and candidate.get('parent_candidate_id'):
                sides.insert(0, candidate['parent_candidate_id'])
            for cid in sides:
                existing = [a for a in self.service.candidate(cid)['attempts'] if a.get('batch_id') == scope]
                watched.extend(dict(candidate_id=cid, attempt=a['attempt'], batch_id=scope, case_count=count)
                               for a in existing)
                # Historical failed slots already consumed their initial attempt.
                # They may receive bounded infra retries below, never a second
                # independent top-up merely because a new round key is used.
                missing = 0 if kind == 'cluster' else max(0, protocol['k_base'] - len(existing))
                for index in range(missing):
                    stepkey = 'round-measure:' + hashlib.sha256(f'{key}:{cid}:{scope}:{index}'.encode()).hexdigest()
                    steps.append(dict(candidate_id=cid, request_key=stepkey, batch_id=scope, case_count=count))
        return dict(candidate_id=candidate['id'], iteration_id=tree['id'], kind=kind, scope_id=scope_id,
                    generations=[steps], watched=watched, launched=[], created_at=now())

    def _reserve(self, tree, state, key, generation, actor, override):
        steps = state['generations'][generation]
        def fresh_claim(conn):
            if state['kind'] == 'fresh':
                claim(self.repository, tree, state['candidate_id'], 'action:' + state['action_id'], conn=conn,
                      expected_batch_id=state['scope_id'])
        result = BudgetLedger(self.repository).reserve_many(tree, steps,
            group_key=key + ':' + str(generation), override=override, on_reserve=fresh_claim)
        if result['status'] == 'unbudgeted':
            with self.repository.connect(write=True) as conn:
                fresh_claim(conn)
        if result['status'] not in ('reserved', 'unbudgeted'):
            return False
        if override and not state.get('override_recorded'):
            with self.repository.connect(write=True) as conn:
                self.repository._event(conn, tree['task_id'], 'round.measurement.confirmed', state['candidate_id'],
                    dict(actor=actor, request_key=key, reason='Round start confirms baseline cold-start reservation',
                         budget_override=True))
            state['override_recorded'] = True
        return True

    def _attempts(self, state):
        found = []
        for item in state['watched']:
            candidate = self.service.candidate(item['candidate_id'])
            attempt = next((a for a in candidate['attempts'] if a['attempt'] == item['attempt']), None)
            if attempt:
                found.append((item, attempt))
        for steps in state['generations']:
            for step in steps:
                candidate = self.service.candidate(step['candidate_id'])
                attempt = self._step_attempt(candidate, step, state['actor'])
                if attempt:
                    found.append((step, attempt))
        return found

    def _step_attempt(self, candidate, step, actor):
        tree = self.service.iteration(candidate['iteration_id'])
        receipt = self.service.jobs.preparation_receipt(tree['task_id'], actor=actor,
            request_key='candidate:' + candidate['id'] + ':' + step['request_key'])
        if receipt is None:
            return None
        job_id = receipt['job_snapshot']['job_id']
        return next((a for a in candidate['attempts'] if a['job_id'] == job_id), None)

    def measure(self, candidate_id, *, key, actor, override=False, infra_retry=1, timeout_seconds=7200):
        return self._advance('measure', candidate_id, key=key, actor=actor, override=override,
                             infra_retry=infra_retry, timeout_seconds=timeout_seconds)

    def fresh(self, candidate_id, *, key, actor, infra_retry=1, timeout_seconds=7200):
        return self._advance('fresh', candidate_id, key=key, actor=actor, override=False,
                             infra_retry=infra_retry, timeout_seconds=timeout_seconds)

    def cluster(self, candidate_id, cluster_id, *, key, actor, infra_retry=1, timeout_seconds=7200):
        return self._advance('cluster', candidate_id, key=key, actor=actor, override=False,
                             infra_retry=infra_retry, timeout_seconds=timeout_seconds, cluster_id=cluster_id)

    def _advance(self, kind, candidate_id, *, key, actor, override, infra_retry, timeout_seconds, cluster_id=None):
        if type(infra_retry) is not int or infra_retry < 0:
            raise ValueError('infra_retry must be a nonnegative integer')
        candidate = self.service.candidate(candidate_id)
        tree = self.service.iteration(candidate['iteration_id'])
        if kind == 'cluster':
            from .clusters import candidate_cluster
            if candidate_cluster(self.repository, tree['task_id'], candidate_id) != cluster_id:
                raise ValueError('A candidate may run only its own cluster')
            candidate['_round_cluster_scope'] = 'cluster:' + cluster_id
        self.api._scope(iteration_id=tree['id'])
        storage_key = 'round-measure:' + hashlib.sha256(key.encode()).hexdigest()
        with self._lock(tree['id']):
            state = self._read(storage_key)
            if state is None:
                state = self._plan(candidate, tree, kind, storage_key)
                state.update(original_key=key, actor=actor, infra_retry=infra_retry, timeout_seconds=timeout_seconds,
                             override=override, candidate_id=candidate_id, kind=kind)
                self._save(storage_key, state)
            elif any(state[k] != v for k, v in dict(actor=actor, infra_retry=infra_retry,
                    timeout_seconds=timeout_seconds, override=override, candidate_id=candidate_id, kind=kind).items()):
                raise Conflict('Round measurement request key changed')
            if state.get('exhausted'):
                return dict(status='exhausted')
            if state.get('result'):
                return state['result']
            generation = len(state['generations']) - 1
            actions = state.setdefault('actions', [])
            if len(actions) <= generation:
                action_id = hashlib.sha256(f'{storage_key}:{generation}'.encode()).hexdigest()[:32]
                params = dict(timeout_seconds=timeout_seconds)
                verb = {'fresh': 'node-fresh-run', 'cluster': 'node-cluster-run'}.get(kind, 'node-run')
                if kind == 'cluster':
                    params['cluster_id'] = cluster_id
                with self.repository.connect(write=True) as conn:
                    stamp = now()
                    conn.execute('INSERT OR IGNORE INTO action_requests(id,task_id,verb,target_id,params_json,'
                        'reason,requested_by,content_hash,status,created_at,updated_at) '
                        "VALUES(?,?,?,?,?,?,?,?, 'pending',?,?)",
                        (action_id, tree['task_id'], verb, candidate_id, json.dumps(params),
                         'Round measurement ' + storage_key, actor, hashlib.sha256(storage_key.encode()).hexdigest(),
                         stamp, stamp))
                actions.append(action_id)
                state.setdefault('action_id', action_id)  # stable fresh allocation identity
                self._save(storage_key, state)
            action_id = actions[generation]
            for prior_index, prior_action in enumerate(actions[:generation]):
                if self.requests.get(prior_action)['status'] == 'confirmed':
                    self.requests.finish(prior_action, result=dict(status='complete',
                        retry_generation=prior_index + 1))
            # The operator confirmation route consumes the SAME frozen steps.
            # Each retry generation owns an immutable action and dispatch footprint.
            with self.repository.connect(write=True) as conn:
                conn.execute('INSERT OR IGNORE INTO action_dispatches VALUES(?,?,?,?)',
                    (action_id, tree['id'], json.dumps(state['generations'][generation]), now()))
            try:
                admitted = self._reserve(tree, state, storage_key, generation, actor, override)
            except Conflict as error:
                if kind != 'fresh':
                    raise
                state['result'] = dict(status='failed', reason=str(error))
                self._save(storage_key, state)
                return state['result']
            if not admitted:
                with self.repository.connect(write=True) as conn:
                    conn.execute("UPDATE action_requests SET status='pending', decision_note=? WHERE id=?",
                        ('Round footprint exceeds frozen budget; manual confirmation preserves these steps', action_id))
                return dict(status='budget_exhausted', action_request_id=action_id)
            if self.requests.get(action_id)['status'] == 'pending':
                self.requests.claim(action_id, actor=actor, note='Frozen round footprint admitted')
            self._save(storage_key, state)
            steps = list(state['generations'][generation])
        # Every step was frozen and the whole group reserved before any launch.
        # Replaying run uses its durable preparation/execution receipts.
        def launch(step):
            if stopped(self.repository, key):
                return 'stopped'
            try:
                self.service.run(step['candidate_id'], request_key=step['request_key'], batch_id=step['batch_id'],
                                 budget=dict(timeout_seconds=timeout_seconds), actor=actor)
                return None
            except Exception as exc:
                return type(exc).__name__
        with ThreadPoolExecutor(max_workers=max(1, len(steps))) as pool:
            errors = list(pool.map(launch, steps))
        with self._lock(tree['id']):
            state = self._read(storage_key)
            if stopped(self.repository, key):
                return dict(status='stopped')
            observed = self._attempts(state)
            if any(a.get('execution_state') in (None, 'running') for _, a in observed):
                return dict(status='running', attempts=len(observed))
            expected = len(state['watched']) + sum(map(len, state['generations']))
            if len(observed) != expected:
                # Preparation/launch failures remain visible. Re-entering retries
                # the same durable keys rather than creating unbounded attempts.
                return dict(status='failed', reason='Execution preparation or dispatch failed', errors=errors)
            latest = observed if generation == 0 else observed[-len(steps):]
            infra = [(step, a) for step, a in latest if self._infra(a)]
            retries = []
            if infra:
                with self.repository.connect(write=True) as conn:
                    conn.execute('CREATE TABLE IF NOT EXISTS round_infra_retries '
                                 '(iteration_id TEXT, candidate_id TEXT, source_attempt INTEGER, '
                                 'root_attempt INTEGER, retry_number INTEGER, request_key TEXT UNIQUE, actual_attempt INTEGER, '
                                 'PRIMARY KEY(iteration_id,candidate_id,root_attempt,retry_number))')
                    for step, attempt in observed:
                        if step.get('request_key'):
                            conn.execute('UPDATE round_infra_retries SET actual_attempt=? WHERE request_key=?',
                                         (attempt['attempt'], step['request_key']))
                    for step, attempt in infra:
                        # Retry attempts are recognized by their stable step key.
                        prior = conn.execute('SELECT root_attempt,retry_number FROM round_infra_retries '
                            'WHERE iteration_id=? AND candidate_id=? AND actual_attempt=?',
                            (tree['id'], step['candidate_id'], attempt['attempt'])).fetchone()
                        root_attempt, number = (prior[0], prior[1] + 1) if prior else (attempt['attempt'], 1)
                        if number > infra_retry:
                            continue
                        saved = conn.execute('SELECT request_key FROM round_infra_retries WHERE iteration_id=? '
                            'AND candidate_id=? AND root_attempt=? AND retry_number=?',
                            (tree['id'], step['candidate_id'], root_attempt, number)).fetchone()
                        retrykey = saved[0] if saved else 'round-retry:' + hashlib.sha256(
                            f'{tree["id"]}:{step["candidate_id"]}:{root_attempt}:{number}'.encode()).hexdigest()
                        # Existing mappings belong to another durable invocation,
                        # which owns dispatch/recovery. Never append it a second time.
                        if saved:
                            continue
                        conn.execute('INSERT INTO round_infra_retries VALUES(?,?,?,?,?,?,NULL)',
                            (tree['id'], step['candidate_id'], attempt['attempt'], root_attempt, number, retrykey))
                        retries.append(dict(candidate_id=step['candidate_id'], batch_id=step['batch_id'],
                                            case_count=step['case_count'], request_key=retrykey))
                    if retries:
                        state['generations'].append(retries)
                        conn.execute('UPDATE round_measurements SET payload_json=? WHERE key=?',
                                     (json.dumps(state), storage_key))
            if retries:
                request = self.requests.get(action_id)
                if request['status'] != 'executed':
                    self.requests.finish(action_id, result=dict(status='complete', infra=len(infra),
                                                               retry_generation=generation + 1))
                return dict(status='running', attempts=len(observed), infra=len(infra), reason='Infrastructure retry planned')
            candidates = {step['candidate_id'] for step, _ in observed} | {candidate_id}
            required_scopes = {step.get('batch_id') for step, _ in observed}
            if kind == 'measure':
                required_scopes.add(None)
            complete = all(sum((a.get('admission') or {}).get('status') == 'admitted'
                    for a in self.service.candidate(cid)['attempts'] if a.get('batch_id') == scope)
                    >= tree['protocol']['k_base']
                for cid in candidates for scope in required_scopes
                if any(s['candidate_id'] == cid and s.get('batch_id') == scope for s, _ in observed)
                   or (cid == candidate_id and scope is None))
            result = dict(status='complete' if complete else 'failed', attempts=len(observed), infra=len(infra))
            if kind == 'fresh':
                state['ready_to_reconcile'] = True
                self._save(storage_key, state)
                comparisons = ComparisonService(self.repository, self.service, self.views)
                comparisons.reconcile_fresh(tree['id'], actor=actor)
                used = next(b for b in batches(self.repository, tree['id']) if b['batch_id'] == state['scope_id'])
                result['batch_id'] = state['scope_id']
                if used.get('comparison_id'):
                    record = comparisons.get(used['comparison_id'])
                    result.update(delta=record.get('mean', {}).get('delta'), eta=record.get('noise', {}).get('eta'))
            self.requests.finish(action_id, result=result)
            state['result'] = result
            self._save(storage_key, state)
            return result
