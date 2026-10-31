"""Explicit human-only, once-per-registered-holdout measurement capability.

Two claim shapes share one rule: a registered sealed selection is measured by
one study only. ``run`` (LCD v1) measures one candidate; ``run_lineage``
(method v2 §2.5 end step) measures, once, every node of a stopped iteration's
lineage - the seed and each confirmed parent - k times on the same cases, and
``lineage`` turns the paired scores into the confirmed-change × sealed-score
curve. Results are aggregate only; no case identity leaves this module.
"""
from __future__ import annotations
import hashlib
import json
import math
from statistics import NormalDist

from .repository import Conflict, _text, now

# Never accepted from a command DTO or serialized into an Agent context.
_CAPABILITY = object()


def initialize(repository):
    with repository.connect(write=True) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS iteration_sealed_claims(
            iteration_id TEXT PRIMARY KEY, study_key TEXT NOT NULL UNIQUE,
            candidate_id TEXT NOT NULL, frozen_commit TEXT NOT NULL,
            sealed_ref TEXT NOT NULL, case_ids_json TEXT NOT NULL,
            confirmed_by TEXT NOT NULL, claimed_at TEXT NOT NULL,
            execution_id TEXT, status TEXT NOT NULL, score_json TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS sealed_lineage_claims(
            iteration_id TEXT PRIMARY KEY, study_key TEXT NOT NULL UNIQUE,
            sealed_ref TEXT NOT NULL, case_ids_json TEXT NOT NULL, nodes_json TEXT NOT NULL,
            k INTEGER NOT NULL, confirmed_by TEXT NOT NULL, claimed_at TEXT NOT NULL,
            status TEXT NOT NULL, result_json TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS sealed_lineage_slots(
            iteration_id TEXT NOT NULL, node_index INTEGER NOT NULL, repetition INTEGER NOT NULL,
            candidate_id TEXT NOT NULL, execution_id TEXT,
            PRIMARY KEY(iteration_id, node_index, repetition))''')


def authorize(repository, iteration_id, candidate_id, capability):
    if capability is not _CAPABILITY:
        raise ValueError('sealed execution requires the dedicated human-confirmed capability')
    with repository.connect() as conn:
        row = conn.execute('SELECT * FROM iteration_sealed_claims WHERE iteration_id=?', (iteration_id,)).fetchone()
        # A lineage claim grants exactly its unfilled (node, repetition) slots.
        slot = conn.execute("SELECT 1 FROM sealed_lineage_slots s JOIN sealed_lineage_claims c USING(iteration_id) "
                            "WHERE s.iteration_id=? AND s.candidate_id=? AND s.execution_id IS NULL "
                            "AND c.status='dispatching'", (iteration_id, candidate_id)).fetchone()
    if slot is not None:
        return
    if row is None or row['candidate_id'] != candidate_id or row['execution_id'] is not None:
        raise Conflict('sealed execution is not available for this candidate')


def _claimed(conn, iteration_id, study):
    """Either claim shape spends the iteration and the sealed study."""
    return any(conn.execute(f'SELECT 1 FROM {table} WHERE iteration_id=? OR study_key=?',
                            (iteration_id, study)).fetchone()
               for table in ('iteration_sealed_claims', 'sealed_lineage_claims'))


def _stopped_baseline(conn, iteration_id):
    """The final parent of a stopped optimisation, or None while it may still move."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='iteration_round_invocations'").fetchone():
        return None
    from .loop import TERMINAL
    rows = conn.execute('SELECT record,stop_actor FROM iteration_round_invocations WHERE iteration_id=? ORDER BY rowid',
                        (iteration_id,)).fetchall()
    records = [json.loads(r['record']) for r in rows]
    if not records or any(r['stop_actor'] is None and record['status'] not in TERMINAL
                          for r, record in zip(rows, records)):
        return None
    return records[-1].get('baseline_candidate_id')


def _holm(pvalues):
    """Holm step-down adjusted p-values, in input order."""
    m, adjusted, running = len(pvalues), [None] * len(pvalues), 0.
    for rank, index in enumerate(sorted(range(m), key=lambda i: pvalues[i])):
        running = max(running, min(1., (m - rank) * pvalues[index]))
        adjusted[index] = running
    return adjusted


def lineage_curve(nodes, *, alpha=.05, families=None):
    """Paired confirmed-change × sealed-score curve; pure, aggregate-only output.

    ``nodes`` are in lineage order (seed first), each ``dict(candidate_id,
    frozen_commit, scores)`` where ``scores`` maps case → mean of its k sealed
    repetitions, or is None when the node could not be measured. Every node is
    paired with the seed on the same cases. ``lower``/``upper`` are Δ ± z·SE
    with the one-sided z of ``alpha`` (the bound read as "the change held").
    Across the m measured non-seed nodes, ``p_holm`` is Holm's step-down
    adjustment of the one-sided p of Δ ≤ 0; Holm has no compatible interval, so
    ``lower_adj``/``upper_adj`` are the simultaneous Bonferroni interval
    (z at alpha/m), the interval form of Holm's first step.
    """
    from .noise import paired_means
    normal = NormalDist()
    z = normal.inv_cdf(1 - alpha)
    seed = nodes[0]['scores']
    measured = [i for i, node in enumerate(nodes) if i and node['scores'] is not None and seed is not None]
    m = len(measured)
    z_adj = normal.inv_cdf(1 - alpha / m) if m else None
    curve, stats = [], {}
    for index, node in enumerate(nodes):
        row = dict(node_index=index, candidate_id=node['candidate_id'], frozen_commit=node['frozen_commit'],
                   status='measured' if node['scores'] is not None else 'unmeasurable',
                   score=None, se=None, delta_vs_seed=None, se_delta=None, lower=None, upper=None,
                   p=None, p_holm=None, lower_adj=None, upper_adj=None, holds_holm=None)
        if node['scores'] is not None:
            # Against a zero reference, the paired statistic is the node's own mean and SE.
            own = paired_means({c: 0. for c in node['scores']}, node['scores'], z=z, families=families)
            row.update(score=own['delta'], se=own['se'], n=own['n'], se_method=own['se_method'])
        if index in measured:
            paired = paired_means(seed, node['scores'], z=z, families=families)
            stats[index] = paired
            row.update(delta_vs_seed=paired['delta'], se_delta=paired['se'], lower=paired['lower'], upper=paired['upper'])
            se = paired['se']
            if se:
                row['p'] = 1 - normal.cdf(paired['delta'] / se)
                row.update(lower_adj=paired['delta'] - z_adj * se, upper_adj=paired['delta'] + z_adj * se)
            else:
                # No spread: the sign is certain on these cases.
                row['p'] = 0. if (paired['delta'] or 0) > 0 else 1.
                row.update(lower_adj=paired['delta'], upper_adj=paired['delta'])
        elif index == 0 and seed is not None:
            row.update(delta_vs_seed=0., se_delta=0., lower=0., upper=0., lower_adj=0., upper_adj=0.)
        curve.append(row)
    for index, adjusted in zip(measured, _holm([curve[i]['p'] for i in measured])):
        curve[index].update(p_holm=adjusted, holds_holm=adjusted < alpha)
    return dict(alpha=alpha, z=z, z_adj=z_adj, m=m, correction='holm-p+bonferroni-interval',
                n_families=len(set((families or {}).values())) or None, curve=curve)


class SealedService:
    def __init__(self, repository, iterations, run_views):
        self.repository, self.iterations, self.views = repository, iterations, run_views
        initialize(repository)

    def run(self, candidate_id, *, budget, actor, confirmed=False):
        """Consume the only claim before preparation; even failed calls cannot retry."""
        if confirmed is not True:
            raise ValueError('sealed-run requires explicit human confirmation')
        actor = _text(actor, 'human confirmer', limit=200)
        candidate = self.iterations.candidate(candidate_id)
        tree = self.iterations.iteration(candidate['iteration_id'])
        protocol = tree.get('protocol') or {}
        ref, registration, study = self._registration(tree, budget)
        if candidate['status'] != 'frozen' or tree['origin'] != 'native':
            raise ValueError('sealed-run needs a frozen native candidate')
        ids = registration['case_ids']
        with self.repository.connect(write=True) as conn:
            if _claimed(conn, tree['id'], study):
                raise Conflict('sealed study was already claimed; no second call is allowed')
            conn.execute('INSERT INTO iteration_sealed_claims VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (tree['id'], study, candidate_id, candidate['frozen_commit'], ref, json.dumps(ids), actor, now(), None, 'claimed', None))
            self.repository._event(conn, tree['task_id'], 'iteration.sealed.claimed', tree['id'],
                                   dict(candidate_id=candidate_id, frozen_commit=candidate['frozen_commit'], actor=actor, study_key=study))
        try:
            p = self.iterations.plans.save(task_id=tree['task_id'], input_id=protocol['input']['input_id'],
                expected_bundle_id=registration['bundle_id'], case_ids=ids, title='Sealed once-only measurement',
                mode=protocol['mode'], actor='sealed', request_key='sealed:' + study, _sealed_capability=_CAPABILITY)
            scope = dict(scope_id='sealed:once', kind='sealed', case_ids=ids, plan_id=p['plan_id'],
                         plan_revision=p['revision'], plan_content_hash=p['content_hash'],
                         selection_hash=p['content']['selection']['selection_hash'])
            with self.repository.connect(write=True) as conn:
                conn.execute('INSERT INTO iteration_scopes VALUES(?,?,?,?,?)',
                             (tree['id'], 'sealed:once', 'sealed', json.dumps(scope), now()))
            result = self.iterations.run(candidate_id, budget=budget, actor=actor, request_key='sealed:' + study,
                                         batch_id='sealed:once', _sealed_capability=_CAPABILITY)
            attempt = next(a for a in result['attempts'] if a.get('batch_id') == 'sealed:once')
            with self.repository.connect(write=True) as conn:
                conn.execute("UPDATE iteration_sealed_claims SET execution_id=?,status='dispatched' WHERE iteration_id=?",
                             (attempt['execution_id'], tree['id']))
        except Exception:
            with self.repository.connect(write=True) as conn:
                conn.execute("UPDATE iteration_sealed_claims SET status='failed' WHERE iteration_id=?", (tree['id'],))
            raise
        return dict(iteration_id=tree['id'], candidate_id=candidate_id, status='dispatched')

    def _registration(self, tree, budget):
        from .job_execution import validate_execution_budget
        validate_execution_budget(budget)
        from .budgets import validate_model_budget
        validate_model_budget(budget, self.iterations.execution_config)
        protocol = tree.get('protocol') or {}
        ref = protocol.get('splits', {}).get('sealed_ref')
        registration = (self.iterations.preparation_config or {}).get('sealed_selections', {}).get(ref)
        if (not isinstance(registration, dict) or registration.get('bundle_id') != protocol.get('input', {}).get('bundle_id')
                or not registration.get('case_ids') or registration['case_ids'] != protocol.get('splits', {}).get('sealed')):
            raise ValueError('sealed-run needs the unchanged trusted sealed registration')
        study = hashlib.sha256(json.dumps([registration['bundle_id'], sorted(registration['case_ids'])]).encode()).hexdigest()
        return ref, registration, study

    def _lineage_nodes(self, tree, baseline):
        """Seed first: the parent chain from the final confirmed parent back to the root."""
        chain, cid = [], baseline
        while cid is not None:
            candidate = self.iterations.candidate(cid)
            if candidate['iteration_id'] != tree['id'] or candidate['status'] != 'frozen' or len(chain) > 10000:
                raise ValueError('sealed lineage needs frozen nodes of this iteration')
            chain.append(dict(candidate_id=cid, frozen_commit=candidate['frozen_commit']))
            cid = candidate['parent_candidate_id']
        if chain[-1]['candidate_id'] != tree['root_candidate_id']:
            raise ValueError('sealed lineage does not reach the seed')
        return chain[::-1]

    def run_lineage(self, iteration_id, *, budget, actor, confirmed=False, k=None):
        """Claim the sealed study once for the whole lineage, then dispatch every slot.

        The node set is fixed at claim time: seed + every confirmed parent of a
        stopped optimisation. Each node runs k times (the protocol's k_sealed,
        else k_base) on the registered cases. The claim is never released; a
        dispatch interrupted by an error resumes only slots that never got an
        execution, under the same deterministic keys, so no measured slot can
        be repeated and no node set can be re-chosen.
        """
        if confirmed is not True:
            raise ValueError('sealed-run requires explicit human confirmation')
        actor = _text(actor, 'human confirmer', limit=200)
        tree = self.iterations.iteration(iteration_id)
        if tree['origin'] != 'native':
            raise ValueError('sealed-run needs a native iteration')
        ref, registration, study = self._registration(tree, budget)
        protocol = tree['protocol']
        pinned = protocol.get('k_sealed')
        if pinned is not None and k not in (None, pinned):
            raise ValueError('k_sealed is pre-registered in the iteration protocol')
        k = pinned if pinned is not None else k if k is not None else protocol.get('k_base', 1)
        if type(k) is not int or k < 1:
            raise ValueError('k_sealed must be a positive integer')
        ids = registration['case_ids']
        with self.repository.connect(write=True) as conn:
            claim = conn.execute('SELECT * FROM sealed_lineage_claims WHERE iteration_id=?', (tree['id'],)).fetchone()
            if claim is None:
                if _claimed(conn, tree['id'], study):
                    raise Conflict('sealed study was already claimed; no second call is allowed')
                baseline = _stopped_baseline(conn, tree['id'])
                if baseline is None:
                    raise ValueError('sealed lineage needs a stopped optimisation; every version must be frozen first')
                nodes = self._lineage_nodes(tree, baseline)
                conn.execute('INSERT INTO sealed_lineage_claims VALUES(?,?,?,?,?,?,?,?,?,?)',
                             (tree['id'], study, ref, json.dumps(ids), json.dumps(nodes), k, actor, now(), 'dispatching', None))
                conn.executemany('INSERT INTO sealed_lineage_slots VALUES(?,?,?,?,?)',
                                 [(tree['id'], i, r, n['candidate_id'], None) for i, n in enumerate(nodes) for r in range(k)])
                self.repository._event(conn, tree['task_id'], 'iteration.sealed.lineage_claimed', tree['id'],
                                       dict(nodes=nodes, k=k, actor=actor, study_key=study))
            elif claim['status'] != 'dispatching' or claim['study_key'] != study:
                raise Conflict('sealed study was already claimed; no second call is allowed')
            else:
                k = claim['k']
        p = self.iterations.plans.save(task_id=tree['task_id'], input_id=protocol['input']['input_id'],
            expected_bundle_id=registration['bundle_id'], case_ids=ids, title='Sealed lineage measurement',
            mode=protocol['mode'], actor='sealed', request_key='sealed-lineage:' + study, _sealed_capability=_CAPABILITY)
        scope = dict(scope_id='sealed:lineage', kind='sealed', case_ids=ids, plan_id=p['plan_id'],
                     plan_revision=p['revision'], plan_content_hash=p['content_hash'],
                     selection_hash=p['content']['selection']['selection_hash'])
        with self.repository.connect(write=True) as conn:
            conn.execute('INSERT OR IGNORE INTO iteration_scopes VALUES(?,?,?,?,?)',
                         (tree['id'], 'sealed:lineage', 'sealed', json.dumps(scope), now()))
            slots = conn.execute('SELECT * FROM sealed_lineage_slots WHERE iteration_id=? AND execution_id IS NULL '
                                 'ORDER BY node_index,repetition', (tree['id'],)).fetchall()
        for slot in slots:
            cid = slot['candidate_id']
            result = self.iterations.run(cid, budget=budget, actor=actor, batch_id='sealed:lineage',
                                         request_key=f"sealed-lineage:{study}:{slot['node_index']}:{slot['repetition']}",
                                         _sealed_capability=_CAPABILITY)
            with self.repository.connect(write=True) as conn:
                known = {r[0] for r in conn.execute('SELECT execution_id FROM sealed_lineage_slots WHERE iteration_id=? '
                                                    'AND execution_id IS NOT NULL', (tree['id'],))}
                execution = next(a['execution_id'] for a in reversed(result['attempts'])
                                 if a.get('batch_id') == 'sealed:lineage' and a['execution_id'] not in known)
                conn.execute('UPDATE sealed_lineage_slots SET execution_id=? WHERE iteration_id=? AND node_index=? '
                             'AND repetition=? AND execution_id IS NULL',
                             (execution, tree['id'], slot['node_index'], slot['repetition']))
        with self.repository.connect(write=True) as conn:
            conn.execute("UPDATE sealed_lineage_claims SET status='dispatched' WHERE iteration_id=? AND status='dispatching'",
                         (tree['id'],))
            count = conn.execute('SELECT COUNT(*) FROM sealed_lineage_slots WHERE iteration_id=?', (tree['id'],)).fetchone()[0]
        return dict(iteration_id=tree['id'], nodes=count // k, k=k, status='dispatched')

    def lineage(self, iteration_id, *, alpha=.05):
        """Null before claiming; progress while running; then the immutable curve."""
        with self.repository.connect() as conn:
            claim = conn.execute('SELECT * FROM sealed_lineage_claims WHERE iteration_id=?', (iteration_id,)).fetchone()
            if claim is None:
                return None
            claim = dict(claim)
            if claim['result_json']:
                return json.loads(claim['result_json'])
            slots = [dict(r) for r in conn.execute('SELECT * FROM sealed_lineage_slots WHERE iteration_id=? '
                                                    'ORDER BY node_index,repetition', (iteration_id,))]
        nodes = json.loads(claim['nodes_json'])
        last = nodes[-1]
        result = dict(kind='lineage', status=claim['status'], confirmed_by=claim['confirmed_by'],
                      claimed_at=claim['claimed_at'], k=claim['k'], nodes=len(nodes),
                      candidate_id=last['candidate_id'], frozen_commit=last['frozen_commit'],
                      metric=None, n=None, mean=None, curve=None)
        task_id = self.iterations.iteration_record(iteration_id)['task_id']
        executions = [self.iterations._execution(task_id, s['execution_id']) for s in slots]
        result['progress'] = dict(slots=len(slots), dispatched=sum(s['execution_id'] is not None for s in slots),
                                  terminal=sum(bool(e and e['terminal']) for e in executions))
        if claim['status'] != 'dispatched' or result['progress']['terminal'] < len(slots):
            return result
        ids = json.loads(claim['case_ids_json'])
        repetitions, metrics, families = {}, set(), {}
        for slot, execution in zip(slots, executions):
            try:
                measured = self._case_scores(ids, execution)
            except (ValueError, LookupError, OSError):
                measured = None
            if measured is not None:
                metrics.add(measured['metric'])
                families.update(measured['families'])
            repetitions.setdefault(slot['node_index'], []).append(measured and measured['scores'])
        if len(metrics) > 1:
            result['status'] = 'unmeasurable'
        else:
            for index, node in enumerate(nodes):
                reps = repetitions.get(index, [])
                node['scores'] = None if not reps or None in reps else {
                    cid: sum(r[cid] for r in reps) / len(reps) for cid in ids}
            result.update(lineage_curve(nodes, alpha=alpha, families=families or None), status='finished',
                          metric=next(iter(metrics), None), n=len(ids))
            result['mean'] = result['curve'][-1]['score']
        with self.repository.connect(write=True) as conn:
            conn.execute('UPDATE sealed_lineage_claims SET status=?,result_json=? WHERE iteration_id=? AND result_json IS NULL',
                         (result['status'], json.dumps(result), iteration_id))
            saved = conn.execute('SELECT result_json FROM sealed_lineage_claims WHERE iteration_id=?', (iteration_id,)).fetchone()
        return json.loads(saved['result_json'])

    def _measure(self, claim, execution):
        measured = self._case_scores(json.loads(claim['case_ids_json']), execution)
        values = list(measured['scores'].values())
        return dict(metric=measured['metric'], n=len(values), mean=sum(values) / len(values))

    def _case_scores(self, ids, execution):
        """Isolated registered entries, formal admission, per-case official scores."""
        from .run_views import RunViews
        from .comparisons import Admission
        entries = self.views.execution_runs(execution['execution_id'])
        observed = [cid for e in entries for cid in e.case_ids]
        if sorted(observed) != sorted(ids):
            raise ValueError('Sealed execution selection differs from its claim')
        # The private reader can read only these execution entries. Ordinary
        # RunViews retains its sealed refusal and never receives this capability.
        private = RunViews(entries)
        admission = Admission(private)(dict(execution_id=execution['execution_id'],
                                            execution_state=execution['state'], run_dir=execution['run_dir']))
        if admission['status'] != 'admitted':
            raise ValueError('Sealed execution was not admitted')
        scores, families, metrics = {}, {}, set()
        for entry in entries:
            view = private.run(entry.id)
            metric = view['official']['primary_metric']
            metrics.add(metric)
            for r in private.cases(entry.id)['cases']:
                scores[r['case_id']] = r['official']['metrics'].get(metric)
                families[r['case_id']] = str(view.get('partition_id') or entry.id)
        if (len(metrics) != 1 or sorted(scores) != sorted(ids)
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in scores.values())):
            raise ValueError('Sealed official score is incomplete')
        return dict(metric=next(iter(metrics)), scores=scores, families=families)

    def score(self, iteration_id):
        """Return null before claiming, then only the immutable aggregate/status."""
        with self.repository.connect() as conn:
            row = conn.execute('SELECT * FROM iteration_sealed_claims WHERE iteration_id=?', (iteration_id,)).fetchone()
        if row is None:
            return self.lineage(iteration_id)
        claim = dict(row)
        if claim['score_json']:
            return json.loads(claim['score_json'])
        result = dict(status=claim['status'], candidate_id=claim['candidate_id'], frozen_commit=claim['frozen_commit'],
                      confirmed_by=claim['confirmed_by'], claimed_at=claim['claimed_at'], metric=None, n=None, mean=None)
        if claim['execution_id']:
            tree = self.iterations.iteration(iteration_id)
            execution = self.iterations._execution(tree['task_id'], claim['execution_id'])
            if execution and execution['terminal']:
                try:
                    result.update(self._measure(claim, execution), status='finished')
                except (ValueError, LookupError, OSError):
                    result['status'] = 'unmeasurable'
                with self.repository.connect(write=True) as conn:
                    conn.execute('UPDATE iteration_sealed_claims SET status=?,score_json=? WHERE iteration_id=? AND score_json IS NULL',
                                 (result['status'], json.dumps(result), iteration_id))
                    saved = conn.execute('SELECT score_json FROM iteration_sealed_claims WHERE iteration_id=?', (iteration_id,)).fetchone()
                return json.loads(saved['score_json'])
        return result
