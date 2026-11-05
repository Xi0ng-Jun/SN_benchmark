"""Frozen execution selections separate from the iteration's base/fresh plans."""
from __future__ import annotations
import json
from .repository import Conflict, UnknownIdentity, now


def initialize(repository):
    with repository.connect(write=True) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS iteration_scopes(
            iteration_id TEXT NOT NULL, scope_id TEXT NOT NULL, kind TEXT NOT NULL,
            record_json TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(iteration_id,scope_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS action_dispatches(
            request_id TEXT PRIMARY KEY, iteration_id TEXT NOT NULL,
            steps_json TEXT NOT NULL, created_at TEXT NOT NULL)''')


def get(repository, iteration_id, scope_id):
    with repository.connect() as conn:
        row = conn.execute('SELECT record_json FROM iteration_scopes WHERE iteration_id=? AND scope_id=?',
                           (iteration_id, scope_id)).fetchone()
    if row is None:
        raise UnknownIdentity('Unknown execution scope')
    return json.loads(row['record_json'])


def cluster_plan(service, candidate_id, cluster_id):
    from .clusters import ClusterStore, candidate_cluster
    from .splits import Policy
    candidate = service.candidate(candidate_id)
    iteration = service.iteration(candidate['iteration_id'])
    task = iteration['task_id']
    if candidate_cluster(service.repository, task, candidate_id) != cluster_id:
        raise ValueError('A candidate may run only its own cluster')
    if not candidate['parent_candidate_id']:
        raise ValueError('Cluster runs require a child candidate')
    record = ClusterStore(service.repository).get(task, cluster_id)
    members = record['scan']['members']
    if not members:
        raise ValueError('An empty cluster cannot run')
    policy = Policy(service.repository, task)
    for cid in members:
        policy.require_search(cid)
    scope_id = 'cluster:' + cluster_id
    try:
        return get(service.repository, iteration['id'], scope_id)
    except UnknownIdentity:
        pass
    protocol = iteration['protocol']
    p = service.plans.save(task_id=task, input_id=protocol['input']['input_id'],
        expected_bundle_id=protocol['input']['bundle_id'], case_ids=members,
        title=f'Cluster {cluster_id}', mode=protocol['mode'], actor='scope',
        request_key=scope_id, roles={c: 'search' for c in members})
    scope = dict(scope_id=scope_id, kind='cluster', cluster_id=cluster_id, case_ids=members,
                 plan_id=p['plan_id'], plan_revision=p['revision'], plan_content_hash=p['content_hash'],
                 selection_hash=p['content']['selection']['selection_hash'])
    with service.repository.connect(write=True) as conn:
        conn.execute('INSERT OR IGNORE INTO iteration_scopes VALUES(?,?,?,?,?)',
                     (iteration['id'], scope_id, 'cluster', json.dumps(scope), now()))
    return get(service.repository, iteration['id'], scope_id)


def smoke_scope_id(protocol, case_ids):
    """Content-addressed id of a search-only smoke sample; refuses any other split."""
    if (not isinstance(case_ids, (list, tuple)) or not case_ids or any(type(c) is not str for c in case_ids)
            or len(set(case_ids)) != len(case_ids)):
        raise ValueError('Smoke cases must be a non-empty list of unique case ids')
    if not set(case_ids) <= set(((protocol or {}).get('splits') or {}).get('search', ())):
        raise ValueError('Smoke cases must be search cases')
    import hashlib
    return 'smoke:' + hashlib.sha256('\n'.join(sorted(case_ids)).encode()).hexdigest()[:16]


def smoke_plan(service, iteration, case_ids):
    """Register (idempotently) the frozen plan of one smoke sample, as cluster_plan does."""
    from .splits import Policy
    scope_id = smoke_scope_id(iteration['protocol'], case_ids)
    task = iteration['task_id']
    policy = Policy(service.repository, task)
    for cid in case_ids:
        policy.require_search(cid)
    try:
        return get(service.repository, iteration['id'], scope_id)
    except UnknownIdentity:
        pass
    protocol, members = iteration['protocol'], sorted(case_ids)
    p = service.plans.save(task_id=task, input_id=protocol['input']['input_id'],
        expected_bundle_id=protocol['input']['bundle_id'], case_ids=members,
        title=f'Smoke {scope_id}', mode=protocol['mode'], actor='scope',
        request_key=scope_id, roles={c: 'search' for c in members})
    scope = dict(scope_id=scope_id, kind='smoke', case_ids=members,
                 plan_id=p['plan_id'], plan_revision=p['revision'], plan_content_hash=p['content_hash'],
                 selection_hash=p['content']['selection']['selection_hash'])
    with service.repository.connect(write=True) as conn:
        conn.execute('INSERT OR IGNORE INTO iteration_scopes VALUES(?,?,?,?,?)',
                     (iteration['id'], scope_id, 'smoke', json.dumps(scope), now()))
    return get(service.repository, iteration['id'], scope_id)


SPLITS = ('search', 'validation')


def split_scope_id(protocol, split):
    """Content-addressed id of one whole protocol split (search or validation)."""
    if split not in SPLITS:
        raise ValueError('Unknown split')
    members = ((protocol or {}).get('splits') or {}).get(split)
    if not members:
        raise ValueError(f'The protocol has no {split} split')
    import hashlib
    return f'split:{split}:' + hashlib.sha256('\n'.join(sorted(members)).encode()).hexdigest()[:16]


def split_plan(service, iteration, split):
    """Register (idempotently) the frozen plan of one whole split, as smoke_plan does.

    The base plan (scope None) always runs search and validation together; a
    split scope lets a caller that reads only one of them measure only that one.
    """
    from .splits import Policy
    scope_id = split_scope_id(iteration['protocol'], split)
    try:
        return get(service.repository, iteration['id'], scope_id)
    except UnknownIdentity:
        pass
    task, protocol = iteration['task_id'], iteration['protocol']
    members = sorted(protocol['splits'][split])
    policy = Policy(service.repository, task)
    for cid in members:
        policy.refuse_sealed(cid)
    p = service.plans.save(task_id=task, input_id=protocol['input']['input_id'],
        expected_bundle_id=protocol['input']['bundle_id'], case_ids=members,
        title=f'Split {scope_id}', mode=protocol['mode'], actor='scope',
        request_key=scope_id, roles={c: split for c in members})
    scope = dict(scope_id=scope_id, kind='split', split=split, case_ids=members,
                 plan_id=p['plan_id'], plan_revision=p['revision'], plan_content_hash=p['content_hash'],
                 selection_hash=p['content']['selection']['selection_hash'])
    with service.repository.connect(write=True) as conn:
        conn.execute('INSERT OR IGNORE INTO iteration_scopes VALUES(?,?,?,?,?)',
                     (iteration['id'], scope_id, 'split', json.dumps(scope), now()))
    return get(service.repository, iteration['id'], scope_id)


SUBSET_KINDS = ('mb', 'rest')


def subset_scope_id(protocol, kind, case_ids):
    """Content-addressed id of a validation subset (method-v4 minibatch / rest); refuses other splits."""
    if kind not in SUBSET_KINDS:
        raise ValueError('Unknown subset kind')
    if (not isinstance(case_ids, (list, tuple)) or not case_ids or any(type(c) is not str for c in case_ids)
            or len(set(case_ids)) != len(case_ids)):
        raise ValueError('Subset cases must be a non-empty list of unique case ids')
    if not set(case_ids) <= set(((protocol or {}).get('splits') or {}).get('validation', ())):
        raise ValueError('Subset cases must be validation cases')
    import hashlib
    return f'subset:{kind}:' + hashlib.sha256('\n'.join(sorted(case_ids)).encode()).hexdigest()[:16]


def subset_plan(service, iteration, kind, case_ids):
    """Register (idempotently) the frozen plan of one validation subset, as split_plan does."""
    from .splits import Policy
    scope_id = subset_scope_id(iteration['protocol'], kind, case_ids)
    try:
        return get(service.repository, iteration['id'], scope_id)
    except UnknownIdentity:
        pass
    task, protocol, members = iteration['task_id'], iteration['protocol'], sorted(case_ids)
    policy = Policy(service.repository, task)
    for cid in members:
        policy.refuse_sealed(cid)
    p = service.plans.save(task_id=task, input_id=protocol['input']['input_id'],
        expected_bundle_id=protocol['input']['bundle_id'], case_ids=members,
        title=f'Subset {scope_id}', mode=protocol['mode'], actor='scope',
        request_key=scope_id, roles={c: 'validation' for c in members})
    scope = dict(scope_id=scope_id, kind='subset', subset=kind, case_ids=members,
                 plan_id=p['plan_id'], plan_revision=p['revision'], plan_content_hash=p['content_hash'],
                 selection_hash=p['content']['selection']['selection_hash'])
    with service.repository.connect(write=True) as conn:
        conn.execute('INSERT OR IGNORE INTO iteration_scopes VALUES(?,?,?,?,?)',
                     (iteration['id'], scope_id, 'subset', json.dumps(scope), now()))
    return get(service.repository, iteration['id'], scope_id)


def dispatch_steps(service, record):
    """Freeze an explicit request's entire footprint, including parent top-ups."""
    repository = service.repository
    command = {k: record[k] for k in ('verb', 'target_id', 'params')}
    with repository.connect(write=True) as conn:
        prior = repository._prior(conn, 'action.dispatch.command', record['id'], command)
        if prior is None:
            repository._receipt(conn, 'action.dispatch.command', record['id'], command, dict(bound=True))
    with repository.connect() as conn:
        prior = conn.execute('SELECT steps_json FROM action_dispatches WHERE request_id=?', (record['id'],)).fetchone()
    if prior:
        return json.loads(prior['steps_json'])
    candidate = service.candidate(record['target_id'])
    tree = service.iteration(candidate['iteration_id'])
    k = tree['protocol']['k_base']
    base_count = len(tree['protocol']['splits']['search']) + len(tree['protocol']['splits']['validation'])
    key, steps = 'action:' + record['id'], []
    def add(cid, request_key, scope=None, count=base_count):
        steps.append(dict(candidate_id=cid, request_key=request_key, batch_id=scope, case_count=count,
                          counts_toward_candidate_cap=cid == candidate["id"]))
    def fill(scope, count, prefix):
        for cid in filter(None, (candidate['parent_candidate_id'], candidate['id'])):
            attempts = [a for a in service.candidate(cid)['attempts'] if a.get('batch_id') == scope]
            eligible = sum((a.get('admission') or {}).get('status') == 'admitted'
                           or a.get('execution_state') == 'running' for a in attempts)
            for i in range(max(0, k - eligible)):
                add(cid, f'{prefix}:{cid}:{i}', scope, count)
    verb = record['verb']
    if candidate['status'] != 'frozen' and verb != 'node-freeze':
        raise ValueError('Freeze the candidate before dispatch')
    if verb == 'node-cluster-run':
        scope = cluster_plan(service, candidate['id'], record['params']['cluster_id'])
        fill(scope['scope_id'], len(scope['case_ids']), key)
    elif verb == 'node-fresh-run':
        from .splits import batches
        if not candidate['parent_candidate_id']:
            raise ValueError('fresh-run requires a child candidate')
        used = batches(repository, tree['id'])
        prior = next((b for b in used if b['request_key'] == key), None)
        scope = next((b for b in tree['protocol']['fresh_batches'] if b['batch_id'] == prior['batch_id']), None) if prior else next(
            (b for b in tree['protocol']['fresh_batches'] if b['batch_id'] not in {r['batch_id'] for r in used}), None)
        if scope is None:
            raise ValueError('No unused fresh batch remains')
        fill(scope['batch_id'], len(scope['case_ids']), key)
    elif verb == 'node-run':
        from .splits import batches
        for batch in batches(repository, tree['id'], completed=True):
            scope = next(b for b in tree['protocol']['fresh_batches'] if b['batch_id'] == batch['batch_id'])
            fill(scope['batch_id'], len(scope['case_ids']), f'{key}:validation:{scope["batch_id"]}')
        for i in range(record['params'].get('attempts', 1)):
            add(candidate['id'], key if i == 0 else f'{key}:attempt:{i+1}')
    elif verb != 'node-freeze':
        raise ValueError('Action is not budget-automatable')
    with repository.connect(write=True) as conn:
        conn.execute('INSERT OR IGNORE INTO action_dispatches VALUES(?,?,?,?)',
                     (record['id'], tree['id'], json.dumps(steps), now()))
        saved = conn.execute('SELECT steps_json FROM action_dispatches WHERE request_id=?', (record['id'],)).fetchone()
    return json.loads(saved['steps_json'])


def reserve_dispatch(service, tree, record, steps, *, override=False):
    """Admit the whole frozen footprint and claim exactly its fresh batch."""
    from .budgets import BudgetLedger
    key = 'action:' + record['id']
    callback = None
    if record['verb'] == 'node-fresh-run':
        from .splits import claim
        scopes = {s['batch_id'] for s in steps}
        if len(scopes) > 1 or None in scopes:
            raise Conflict('The planned fresh batch footprint is invalid')
        def callback(conn):
            expected = next(iter(scopes), None)
            if expected is None:
                prior = conn.execute('SELECT batch_id FROM iteration_fresh_batches WHERE iteration_id=? '
                                     'AND request_key=?', (tree['id'], key)).fetchone()
                if prior is None:
                    raise Conflict('An empty fresh footprint requires a prior fresh batch claim')
                expected = prior['batch_id']
            claim(service.repository, tree, record['target_id'], key, conn=conn, expected_batch_id=expected)
    return BudgetLedger(service.repository).reserve_many(tree, steps, group_key=key,
                                                        override=override, on_reserve=callback)
