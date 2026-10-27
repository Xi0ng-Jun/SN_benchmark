"""Recorded actions with explicit human or frozen iteration-budget confirmation.

Budgeted execution requests reserve their complete candidate/parent footprint
before automatic confirmation. Other requests remain pending for a person.
Requests and execution receipts remain auditable across retries; Agent role
parameters are a collaboration contract, not a shell sandbox.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

from .repository import Conflict, UnknownIdentity, now, uid

# verb -> (needs a target candidate, touches external systems)
VERBS = {
    'node-derive': (True, True),
    'node-freeze': (True, True),
    'node-run': (True, True),
    'node-fresh-run': (True, True),
    'node-cluster-run': (True, True),
    'node-adopt': (True, False),
    'node-reject': (True, False),
    'iteration-create': (False, True),
    'iteration-sealed-run': (True, True),
}
IMPLEMENTER_VERBS = frozenset({'node-freeze', 'node-run', 'node-cluster-run'})
ITERATION_FIELDS = ('title', 'start_commit', 'input_id', 'expected_bundle_id', 'case_ids', 'mode')


def initialize_action_requests(repository):
    with repository.connect(write=True) as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS action_requests(
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), verb TEXT NOT NULL,
                target_id TEXT, params_json TEXT NOT NULL, reason TEXT NOT NULL,
                requested_by TEXT NOT NULL, content_hash TEXT NOT NULL, status TEXT NOT NULL,
                decided_by TEXT, decided_at TEXT, decision_note TEXT, result_json TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        ''')


def normalize(verb, params):
    """The parameters a person will confirm, in closed form."""
    if verb not in VERBS:
        raise ValueError('Unknown action verb')
    params = dict(params or {})
    allowed = {'node-derive': {'proposal_id', 'integrated_from'}, 'node-freeze': set(), 'node-adopt': set(), 'node-reject': set(),
               'node-run': {'timeout_seconds', 'limit_cny', 'attempts'}, 'node-fresh-run': {'timeout_seconds', 'limit_cny'},
               'node-cluster-run': {'timeout_seconds', 'limit_cny', 'cluster_id'},
               'iteration-sealed-run': {'timeout_seconds', 'limit_cny'},
               'iteration-create': set(ITERATION_FIELDS) | {'sealed_ref', 'split_seed', 'fresh_batch_count', 'fresh_papers_per_batch', 'budget', 'execution', 'round_settings', 'case_retry'}}[verb]
    if set(params) - allowed:
        raise ValueError('Unexpected action parameters: ' + ', '.join(sorted(set(params) - allowed)))
    if verb == 'node-cluster-run' and (not isinstance(params.get('cluster_id'), str) or not params['cluster_id']):
        raise ValueError('cluster-run needs cluster_id')
    if verb in ('node-run', 'node-fresh-run', 'node-cluster-run', 'iteration-sealed-run'):
        attempts = params.get('attempts', 1)
        if type(attempts) is not int or not 1 <= attempts <= 4:
            raise ValueError('attempts must be an integer between 1 and 4')
        timeout = params.setdefault('timeout_seconds', 3600)
        if type(timeout) is not int or not 60 <= timeout <= 7200:
            raise ValueError('timeout_seconds must be an integer between 60 and 7200')
        limit = params.get('limit_cny')
        if limit is not None and (type(limit) not in (int, float) or not math.isfinite(limit) or limit <= 0):
            raise ValueError('limit_cny must be a positive number')
    if verb == 'node-derive' and 'integrated_from' in params:
        # A merge derivation names the candidates folded in; the merged node runs on its own.
        items = params['integrated_from']
        if (not isinstance(items, list) or not 1 <= len(items) <= 5 or any(
                not isinstance(i, dict) or set(i) - {'candidate_id', 'method', 'note'}
                or not isinstance(i.get('candidate_id'), str) or not re.fullmatch('[0-9a-f]{32}', i['candidate_id'])
                or i.get('method') not in ('merge', 'cherry-pick', 'rewrite')
                or not isinstance(i.get('note', ''), str) or len(i.get('note', '')) > 2000 for i in items)
                or len({i['candidate_id'] for i in items}) != len(items)):
            raise ValueError('integrated_from lists 1-5 distinct candidates with method merge|cherry-pick|rewrite')
        params['integrated_from'] = [dict(i, note=i.get('note', '')) for i in items]
    if verb == 'iteration-create' and not set(ITERATION_FIELDS) <= set(params):
        raise ValueError('iteration-create needs ' + ', '.join(ITERATION_FIELDS))
    return params


def _record(row):
    record = dict(row)
    record['params'] = json.loads(record.pop('params_json'))
    record['result'] = json.loads(record.pop('result_json')) if record['result_json'] else None
    return record


class ActionRequestStore:
    def __init__(self, repository, *, execution_config=None):
        self.repository = repository
        self.execution_config = execution_config
        initialize_action_requests(repository)

    def get(self, request_id):
        with self.repository.connect() as conn:
            row = conn.execute('SELECT * FROM action_requests WHERE id=?', (request_id,)).fetchone()
        if row is None:
            raise UnknownIdentity('Unknown action request identity')
        return _record(row)

    def list(self, *, workspace_id=None, task_id=None, status=None):
        query, args = 'SELECT a.* FROM action_requests a JOIN tasks t ON t.id=a.task_id WHERE 1=1', []
        for column, value in (('t.workspace_id', workspace_id), ('a.task_id', task_id), ('a.status', status)):
            if value is not None:
                query += f' AND {column}=?'
                args.append(value)
        with self.repository.connect() as conn:
            return [_record(r) for r in conn.execute(query + ' ORDER BY a.created_at, a.id', args)]

    def request(self, task_id, verb, target_id, params, *, reason, requested_by):
        """Record a pending request; an identical pending one is returned instead of duplicated."""
        needs_target = VERBS.get(verb, (None,))[0]
        params = normalize(verb, params)
        from .budgets import validate_model_budget
        validate_model_budget(params.get('budget') if verb == 'iteration-create' else params, self.execution_config)
        if needs_target != (target_id is not None):
            raise ValueError(f'{verb} {"needs" if needs_target else "takes no"} target candidate')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
            raise ValueError('A request needs a reason (at most 4000 characters)')
        from .splits import Policy
        policy = Policy(self.repository, task_id)
        for cid in params.get('case_ids', []):
            policy.refuse_sealed(cid)
        content = hashlib.sha256(json.dumps([verb, target_id, params], sort_keys=True).encode()).hexdigest()
        with self.repository.connect(write=True) as conn:
            self.repository._require(conn, 'tasks', task_id)
            row = conn.execute("SELECT * FROM action_requests WHERE task_id=? AND content_hash=? AND status='pending'",
                               (task_id, content)).fetchone()
            if row is not None:
                return _record(row)
            request_id, at = uid(), now()
            conn.execute('INSERT INTO action_requests(id,task_id,verb,target_id,params_json,reason,requested_by,'
                         "content_hash,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'pending',?,?)",
                         (request_id, task_id, verb, target_id, json.dumps(params, ensure_ascii=False),
                          reason.strip(), requested_by, content, at, at))
            self.repository._event(conn, task_id, 'action.requested', request_id,
                                   dict(verb=verb, target_id=target_id, requested_by=requested_by))
            return _record(conn.execute('SELECT * FROM action_requests WHERE id=?', (request_id,)).fetchone())

    def _decide(self, request_id, status, *, actor, note, expected=('pending',)):
        with self.repository.connect(write=True) as conn:
            row = conn.execute('SELECT * FROM action_requests WHERE id=?', (request_id,)).fetchone()
            if row is None:
                raise UnknownIdentity('Unknown action request identity')
            if row['status'] not in expected:
                raise Conflict(f'Action request is already {row["status"]}')
            at = now()
            conn.execute('UPDATE action_requests SET status=?, decided_by=?, decided_at=?, decision_note=?, '
                         'updated_at=? WHERE id=?', (status, actor, at, note, at, request_id))
            self.repository._event(conn, row['task_id'], 'action.' + status, request_id, dict(actor=actor))
            return _record(conn.execute('SELECT * FROM action_requests WHERE id=?', (request_id,)).fetchone())

    def reject(self, request_id, *, actor, note):
        if not isinstance(note, str) or not note.strip():
            raise ValueError('Rejecting a request needs a note')
        return self._decide(request_id, 'rejected', actor=actor, note=note.strip())

    def withdraw(self, request_id, *, actor, note):
        """The requester takes back a pending request (for example one filed by mistake)."""
        if not isinstance(note, str) or not note.strip():
            raise ValueError('Withdrawing a request needs a reason')
        return self._decide(request_id, 'withdrawn', actor=actor, note=note.strip())

    def claim(self, request_id, *, actor, note=''):
        """Mark confirmed before executing; a confirmed request left by a crash may be executed again."""
        return self._decide(request_id, 'confirmed', actor=actor, note=note, expected=('pending', 'confirmed'))

    def finish(self, request_id, *, result=None, error=None):
        with self.repository.connect(write=True) as conn:
            row = conn.execute('SELECT * FROM action_requests WHERE id=?', (request_id,)).fetchone()
            status = 'failed' if error is not None else 'executed'
            body = dict(error=error) if error is not None else result
            conn.execute('UPDATE action_requests SET status=?, result_json=?, updated_at=? WHERE id=?',
                         (status, json.dumps(body, ensure_ascii=False, default=str), now(), request_id))
            self.repository._event(conn, row['task_id'], 'action.' + status, request_id, dict(verb=row['verb']))
            return _record(conn.execute('SELECT * FROM action_requests WHERE id=?', (request_id,)).fetchone())


def _candidate_summary(view):
    attempts = view.get('attempts') or []
    return dict(candidate_id=view['id'], state=view['state'], frozen_commit=view['frozen_commit'],
                attempt=attempts[-1]['attempt'] if attempts else None,
                execution_id=attempts[-1]['execution_id'] if attempts else None)


def execute(service, record, *, workspace_id, actor, budget_override=False, human_confirmed=False):
    """Run a confirmed request through IterationService; returns a compact receipt."""
    verb, target, params, key = record['verb'], record['target_id'], record['params'], 'action:' + record['id']
    from .budgets import validate_model_budget
    validate_model_budget(params.get('budget') if verb == 'iteration-create' else params,
                          getattr(service, 'execution_config', None))
    from .loop_measurements import execute_round_action
    round_result = execute_round_action(service, record, actor=actor, override=budget_override)
    if round_result is not None:
        return round_result
    if verb == 'iteration-sealed-run':
        from .sealed import SealedService
        budget = dict(timeout_seconds=params['timeout_seconds'])
        if params.get('limit_cny') is not None:
            budget.update(limit_cny=params['limit_cny'], confirmed=True)
        return SealedService(service.repository, service, None).run(
            target, budget=budget, actor=actor, confirmed=human_confirmed)
    if verb == 'node-derive':
        view = service.derive(target, proposal_id=params.get('proposal_id'), note=record['reason'],
                              integrated_from=params.get('integrated_from', ()), actor=actor, request_key=key)
    elif verb == 'node-freeze':
        view = service.freeze(target, actor=actor)
    elif verb in ('node-run', 'node-fresh-run', 'node-cluster-run'):
        budget = dict(timeout_seconds=params['timeout_seconds'])
        if params.get('limit_cny') is not None:
            budget.update(limit_cny=params['limit_cny'], confirmed=True)
        tree = service.iteration(service.candidate(target)['iteration_id'])
        if tree['protocol'].get('budget'):
            from .execution_scopes import dispatch_steps, reserve_dispatch
            steps = dispatch_steps(service, record)
            reserved = reserve_dispatch(service, tree, record, steps, override=budget_override)
            if reserved['status'] != 'reserved':
                raise Conflict('Iteration budget exceeded; explicit human confirmation required')
            for step in steps:
                service.run(step['candidate_id'], request_key=step['request_key'], batch_id=step['batch_id'],
                            budget=budget, actor=actor)
            return dict(candidate_id=target, status='dispatched', cluster_id=params.get('cluster_id'))
        if verb == 'node-cluster-run':
            return service.cluster_run(target, params['cluster_id'], budget=budget, actor=actor, request_key=key)
        if verb == 'node-fresh-run':
            return service.fresh_run(target, budget=budget, actor=actor, request_key=key)
        if hasattr(service, 'fill_validation'):
            service.fill_validation(target, budget=budget, actor=actor, request_key=key)
        for index in range(params.get('attempts', 1)):
            attempt_key = key if index == 0 else f'{key}:attempt:{index + 1}'
            view = service.run(target, budget=budget, actor=actor, request_key=attempt_key)
    elif verb in ('node-adopt', 'node-reject'):
        view = service.decide(target, {'node-adopt': 'adopted', 'node-reject': 'rejected'}[verb],
                              actor=actor, note=record['reason'])
    else:
        tree = service.create_iteration(workspace_id, actor=actor, request_key=key, **params)
        return dict(iteration_id=tree['id'], task_id=tree['task_id'], root_candidate_id=tree['root_candidate_id'])
    return _candidate_summary(view)


def auto_execute(service, record, *, workspace_id):
    """Reserve a whole request atomically before policy confirmation and dispatch."""
    if (record['status'] != 'pending' and not (record['status'] == 'confirmed' and record.get('decided_by') == 'budget')) or record['verb'] not in (
            'node-run', 'node-cluster-run', 'node-fresh-run', 'node-freeze'):
        return record
    from .budgets import validate_model_budget
    validate_model_budget(record['params'], getattr(service, 'execution_config', None))
    candidate = service.candidate(record['target_id'])
    tree = service.iteration(candidate['iteration_id'])
    if not tree['protocol'].get('budget'):
        return record
    if service.jobs is None or service.build_spec is None:
        return record
    from .execution_scopes import dispatch_steps, reserve_dispatch
    steps = dispatch_steps(service, record)
    try:
        reserved = reserve_dispatch(service, tree, record, steps)
    except Conflict:
        # A different request may have consumed this frozen fresh batch since
        # planning. Keep this request pending without claiming another batch.
        return record
    if reserved['status'] != 'reserved':
        return record
    requests = ActionRequestStore(service.repository)
    requests.claim(record['id'], actor='budget', note='Within the frozen iteration budget')
    try:
        result = execute(service, record, workspace_id=workspace_id, actor='budget')
    except (Conflict, ValueError) as exc:
        return requests.finish(record['id'], error=str(exc))
    return requests.finish(record['id'], result=result)
