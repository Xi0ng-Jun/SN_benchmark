"""Durable round measurement footprints over existing candidate executions."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path

from .action_requests import ActionRequestStore
from .budgets import BudgetLedger, subject_roles, is_subject
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
        row = conn.execute('SELECT * FROM iteration_round_invocations WHERE id=?', (parts[1],)).fetchone()
    return bool(row and (row['stop_actor'] is not None or
        ('record' in row.keys() and json.loads(row['record']).get('status') in ('completed', 'failed', 'stopped'))))


def fresh_ready(repository, request_key, *, service=None):
    """Legacy fresh claims reconcile normally; rounds wait for bounded retries."""
    if not request_key.startswith('action:'):
        return True
    action_id = request_key.removeprefix('action:')
    with repository.connect() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='round_measurements'").fetchone():
            return True
        row = conn.execute("SELECT payload_json FROM round_measurements WHERE "
                           "json_extract(payload_json,'$.action_id')=?", (action_id,)).fetchone()
    if row is None:
        return True
    state = json.loads(row[0])
    if state.get('ready_to_reconcile') or stopped(repository, state['original_key']):
        return True
    # A final generation may finish while no coordinator tick is available.
    if service is None or len(state['generations']) <= state['infra_retry']:
        return False
    for steps in state['generations']:
        for step in steps:
            actor = state.get('step_actors', {}).get(step['request_key'], state['actor'])
            receipt = service.jobs.preparation_receipt(service.iteration(state['iteration_id'])['task_id'],
                actor=actor, request_key='candidate:' + step['candidate_id'] + ':' + step['request_key'])
            if receipt is None:
                return False
            attempt = next((a for a in service.candidate(step['candidate_id'])['attempts']
                            if a['job_id'] == receipt['job_snapshot']['job_id']), None)
            if not attempt or attempt.get('execution_state') in (None, 'running'):
                return False
    return True


def fresh_abandoned(repository, request_key):
    if not request_key.startswith('action:'):
        return False
    with repository.connect() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='round_measurements'").fetchone():
            return False
        row = conn.execute("SELECT payload_json FROM round_measurements WHERE "
                           "json_extract(payload_json,'$.action_id')=?", (request_key[7:],)).fetchone()
    if not row:
        return False
    state = json.loads(row[0])
    return bool(state.get('dispatch_failed') or stopped(repository, state['original_key']))


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
                    budget=dict(timeout_seconds=state['timeout_seconds']),
                    actor=state.get('step_actors', {}).get(step['request_key'], state['actor']))
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
        if attempt.get('execution_state') == 'cancelled':
            return False  # Explicit operator cancellation is not an infrastructure retry.
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
        with self.repository.connect() as conn:
            roles = subject_roles(conn, tree['id'])
            reservations = ({r['execution_id']: json.loads(r['step_json']) for r in conn.execute(
                'SELECT execution_id,step_json FROM iteration_budget_reservations WHERE iteration_id=?', (tree['id'],))}
                if BudgetLedger._available(conn) else {})
        for scope, count in scopes:
            sides = list(candidates)
            if scope is not None and kind == 'measure' and candidate.get('parent_candidate_id'):
                sides.insert(0, candidate['parent_candidate_id'])
            for cid in sides:
                existing = [a for a in self.service.candidate(cid)['attempts'] if a.get('batch_id') == scope]
                watched.extend(dict(candidate_id=cid, attempt=a['attempt'], batch_id=scope, case_count=count,
                                        counts_toward_candidate_cap=is_subject(reservations[a['execution_id']], roles)
                                        if a.get('execution_id') in reservations else cid == candidate['id'])
                               for a in existing)
                # Historical failed slots already consumed their initial attempt.
                # They may receive bounded infra retries below, never a second
                # independent top-up merely because a new round key is used.
                missing = 0 if kind == 'cluster' else max(0, protocol['k_base'] - len(existing))
                for index in range(missing):
                    stepkey = 'round-measure:' + hashlib.sha256(f'{key}:{cid}:{scope}:{index}'.encode()).hexdigest()
                    steps.append(dict(candidate_id=cid, request_key=stepkey, batch_id=scope, case_count=count,
                                        counts_toward_candidate_cap=cid == candidate['id']))
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
                attempt = self._step_attempt(candidate, step, state.get('step_actors', {}).get(step['request_key'], state['actor']))
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

    def _recover_retry_mappings(self, iteration_id):
        """A crash after launch must not reinterpret a retry as a fresh root slot."""
        with self.repository.connect() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='round_infra_retries'").fetchone():
                return
            missing = conn.execute('SELECT request_key,candidate_id FROM round_infra_retries '
                                   'WHERE iteration_id=? AND actual_attempt IS NULL', (iteration_id,)).fetchall()
            owners = [json.loads(r[0]) for r in conn.execute('SELECT payload_json FROM round_measurements')]
        for retry in missing:
            for owner in owners:
                if owner.get('iteration_id') != iteration_id:
                    continue
                step = next((s for gen in owner.get('generations', []) for s in gen
                             if s['request_key'] == retry['request_key']), None)
                if step is None:
                    continue
                actor = owner.get('step_actors', {}).get(step['request_key'], owner['actor'])
                attempt = self._step_attempt(self.service.candidate(retry['candidate_id']), step, actor)
                if attempt:
                    with self.repository.connect(write=True) as conn:
                        conn.execute('UPDATE round_infra_retries SET actual_attempt=? WHERE request_key=? '
                                     'AND actual_attempt IS NULL', (attempt['attempt'], retry['request_key']))
                    break

    def _adopt_prepared_retries(self, state, tree):
        """Recover the preparation-before-execution crash window of old retries."""
        with self.repository.connect() as conn:
            owners = [json.loads(r[0]) for r in conn.execute('SELECT payload_json FROM round_measurements')]
        for owner in owners:
            if owner.get('iteration_id') != tree['id'] or not stopped(self.repository, owner['original_key']):
                continue
            for generation in owner.get('generations', [])[1:]:
                for step in generation:
                    actor = owner.get('step_actors', {}).get(step['request_key'], owner['actor'])
                    attempt = self._step_attempt(self.service.candidate(step['candidate_id']), step, actor)
                    if not attempt or attempt.get('execution_id'):
                        continue
                    matches = [w for w in state['watched'] if w['candidate_id'] == step['candidate_id']
                               and w['attempt'] == attempt['attempt']]
                    if not matches:
                        continue
                    state['watched'] = [w for w in state['watched'] if w not in matches]
                    state['generations'][0].append(step)
                    state.setdefault('step_actors', {})[step['request_key']] = actor

    def _complete(self, state, tree, observed):
        candidates = {step['candidate_id'] for step, _ in observed} | {state['candidate_id']}
        scopes = {step.get('batch_id') for step, _ in observed}
        if state['kind'] == 'measure':
            scopes.add(None)
        return all(sum((a.get('admission') or {}).get('status') == 'admitted'
                       for a in self.service.candidate(cid)['attempts'] if a.get('batch_id') == scope)
                   >= tree['protocol']['k_base']
                   for cid in candidates for scope in scopes
                   if any(s['candidate_id'] == cid and s.get('batch_id') == scope for s, _ in observed)
                   or (cid == state['candidate_id'] and scope is None))

    @staticmethod
    def _retry_slot(conn, iteration_id, candidate_id, attempt, limit):
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='round_infra_retries'").fetchone()
        prior = conn.execute('SELECT root_attempt,retry_number FROM round_infra_retries '
            'WHERE iteration_id=? AND candidate_id=? AND actual_attempt=?',
            (iteration_id, candidate_id, attempt)).fetchone() if exists else None
        root, number = (prior[0], prior[1] + 1) if prior else (attempt, 1)
        if number > limit:
            return None
        saved = conn.execute('SELECT request_key,actual_attempt FROM round_infra_retries WHERE iteration_id=? '
            'AND candidate_id=? AND root_attempt=? AND retry_number=?',
            (iteration_id, candidate_id, root, number)).fetchone() if exists else None
        return root, number, saved

    def _observe(self, state, tree):
        if state is None:
            return dict(status='running', reason='Observation only; measurement not planned')
        if state.get('result'):
            return state['result']
        observed = self._attempts(state)
        expected = len(state['watched']) + sum(map(len, state['generations']))
        if len(observed) != expected or any(a.get('execution_state') in (None, 'running') for _, a in observed):
            return dict(status='running', reason='Observation only; execution or dispatch incomplete')
        latest_steps = state['generations'][-1]
        latest = observed if len(state['generations']) == 1 else observed[-len(latest_steps):]
        with self.repository.connect() as conn:
            for step, attempt in latest:
                if not self._infra(attempt):
                    continue
                slot = self._retry_slot(conn, tree['id'], step['candidate_id'], attempt['attempt'], state['infra_retry'])
                if slot is not None and (slot[2] is None or slot[2]['actual_attempt'] is None):
                    return dict(status='running', reason='Observation only; infrastructure retry still required')
        return dict(status='complete' if self._complete(state, tree, observed) else 'failed',
                    attempts=len(observed), observation_only=True)

    def measure(self, candidate_id, *, key, actor, override=False, infra_retry=1, timeout_seconds=7200, observe_only=False):
        return self._advance('measure', candidate_id, key=key, actor=actor, override=override,
                             infra_retry=infra_retry, timeout_seconds=timeout_seconds, observe_only=observe_only)

    def fresh(self, candidate_id, *, key, actor, infra_retry=1, timeout_seconds=7200, observe_only=False):
        return self._advance('fresh', candidate_id, key=key, actor=actor, override=False,
                             infra_retry=infra_retry, timeout_seconds=timeout_seconds, observe_only=observe_only)

    def cluster(self, candidate_id, cluster_id, *, key, actor, infra_retry=1, timeout_seconds=7200, observe_only=False):
        return self._advance('cluster', candidate_id, key=key, actor=actor, override=False,
                             infra_retry=infra_retry, timeout_seconds=timeout_seconds, cluster_id=cluster_id, observe_only=observe_only)

    def _advance(self, kind, candidate_id, *, key, actor, override, infra_retry, timeout_seconds, cluster_id=None, observe_only=False):
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
            self._recover_retry_mappings(tree['id'])
            state = self._read(storage_key)
            if observe_only:
                return self._observe(state, tree)
            if state is None:
                state = self._plan(candidate, tree, kind, storage_key)
                if not state.get('exhausted'):
                    self._adopt_prepared_retries(state, tree)
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
                                 budget=dict(timeout_seconds=timeout_seconds),
                                 actor=state.get('step_actors', {}).get(step['request_key'], actor))
                return None
            except Exception as exc:
                from .loop import transient
                return dict(type=type(exc).__name__, transient=transient(exc))
        with ThreadPoolExecutor(max_workers=max(1, len(steps))) as pool:
            errors = list(pool.map(launch, steps))
        with self._lock(tree['id']):
            state = self._read(storage_key)
            if stopped(self.repository, key):
                return dict(status='stopped')
            observed = self._attempts(state)
            if any(errors):
                state['dispatch_errors'] = errors
                self._save(storage_key, state)
            permanent = [e for e in errors if isinstance(e, dict) and not e['transient']]
            if permanent:
                state.update(dispatch_errors=errors, dispatch_failed=True, ready_to_reconcile=True)
                state['result'] = dict(status='failed', reason='Execution preparation or dispatch failed', errors=errors)
                self._save(storage_key, state)
                return state['result']
            if any(a.get('execution_state') in (None, 'running') for _, a in observed):
                return dict(status='running', attempts=len(observed))
            expected = len(state['watched']) + sum(map(len, state['generations']))
            if len(observed) != expected:
                # Preparation/launch failures remain visible. Re-entering retries
                # the same durable keys rather than creating unbounded attempts.
                retryable = any(isinstance(e, dict) and e.get('transient') for e in errors)
                state['dispatch_errors'] = errors
                if not retryable:
                    state['dispatch_failed'] = True
                    state['ready_to_reconcile'] = True
                self._save(storage_key, state)
                return dict(status='running' if retryable else 'failed',
                            reason='Execution preparation or dispatch failed', errors=errors)
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
                        slot = self._retry_slot(conn, tree['id'], step['candidate_id'], attempt['attempt'], infra_retry)
                        if slot is None:
                            continue
                        root_attempt, number, saved = slot
                        retrykey = saved[0] if saved else 'round-retry:' + hashlib.sha256(
                            f'{tree["id"]}:{step["candidate_id"]}:{root_attempt}:{number}'.encode()).hexdigest()
                        retry_step = dict(candidate_id=step['candidate_id'], batch_id=step['batch_id'],
                            case_count=step['case_count'], request_key=retrykey,
                            counts_toward_candidate_cap=step.get('counts_toward_candidate_cap', True))
                        if saved:
                            if saved['actual_attempt'] is not None:
                                continue
                            owners = conn.execute('SELECT key,payload_json FROM round_measurements').fetchall()
                            owner = next((json.loads(r['payload_json']) for r in owners
                                if r['key'] != storage_key and any(s.get('request_key') == retrykey
                                    for gen in json.loads(r['payload_json']).get('generations', []) for s in gen)), None)
                            if owner is None or not stopped(self.repository, owner['original_key']):
                                continue
                            # Reuse the old actor and exact reserved step, including its cap role.
                            retry_step = next(s for gen in owner['generations'] for s in gen if s['request_key'] == retrykey)
                            state.setdefault('step_actors', {})[retrykey] = owner.get('step_actors', {}).get(retrykey, owner['actor'])
                        else:
                            conn.execute('INSERT INTO round_infra_retries VALUES(?,?,?,?,?,?,NULL)',
                                (tree['id'], step['candidate_id'], attempt['attempt'], root_attempt, number, retrykey))
                        if retrykey not in {s['request_key'] for s in retries}:
                            retries.append(retry_step)
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
            complete = self._complete(state, tree, observed)
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
