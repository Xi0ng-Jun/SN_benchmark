"""Explicit human-only, once-per-registered-holdout measurement capability."""
from __future__ import annotations
import hashlib
import json
import math

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


def authorize(repository, iteration_id, candidate_id, capability):
    if capability is not _CAPABILITY:
        raise ValueError('sealed execution requires the dedicated human-confirmed capability')
    with repository.connect() as conn:
        row = conn.execute('SELECT * FROM iteration_sealed_claims WHERE iteration_id=?', (iteration_id,)).fetchone()
    if row is None or row['candidate_id'] != candidate_id or row['execution_id'] is not None:
        raise Conflict('sealed execution is not available for this candidate')


class SealedService:
    def __init__(self, repository, iterations, run_views):
        self.repository, self.iterations, self.views = repository, iterations, run_views
        initialize(repository)

    def run(self, candidate_id, *, budget, actor, confirmed=False):
        """Consume the only claim before preparation; even failed calls cannot retry."""
        if confirmed is not True:
            raise ValueError('sealed-run requires explicit human confirmation')
        actor = _text(actor, 'human confirmer', limit=200)
        from .job_execution import validate_execution_budget
        validate_execution_budget(budget)
        from .budgets import validate_model_budget
        validate_model_budget(budget, self.iterations.execution_config)
        candidate = self.iterations.candidate(candidate_id)
        tree = self.iterations.iteration(candidate['iteration_id'])
        protocol = tree.get('protocol') or {}
        ref = protocol.get('splits', {}).get('sealed_ref')
        registration = (self.iterations.preparation_config or {}).get('sealed_selections', {}).get(ref)
        if (not isinstance(registration, dict) or registration.get('bundle_id') != protocol.get('input', {}).get('bundle_id')
                or not registration.get('case_ids') or registration['case_ids'] != protocol.get('splits', {}).get('sealed')):
            raise ValueError('sealed-run needs the unchanged trusted sealed registration')
        if candidate['status'] != 'frozen' or tree['origin'] != 'native':
            raise ValueError('sealed-run needs a frozen native candidate')
        ids = registration['case_ids']
        study = hashlib.sha256(json.dumps([registration['bundle_id'], sorted(ids)]).encode()).hexdigest()
        with self.repository.connect(write=True) as conn:
            if conn.execute('SELECT 1 FROM iteration_sealed_claims WHERE iteration_id=? OR study_key=?',
                            (tree['id'], study)).fetchone():
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

    def _measure(self, claim, execution):
        """Isolated registered entries, formal admission, numeric aggregate only."""
        from .run_views import RunViews
        from .comparisons import Admission
        entries = self.views.execution_runs(execution['execution_id'])
        ids = json.loads(claim['case_ids_json'])
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
        values, metrics = [], set()
        for entry in entries:
            view = private.run(entry.id)
            metric = view['official']['primary_metric']
            metrics.add(metric)
            rows = private.cases(entry.id)['cases']
            values.extend(r['official']['metrics'].get(metric) for r in rows)
        if len(metrics) != 1 or len(values) != len(ids) or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise ValueError('Sealed official score is incomplete')
        return dict(metric=next(iter(metrics)), n=len(values), mean=sum(values) / len(values))

    def score(self, iteration_id):
        """Return null before claiming, then only the immutable aggregate/status."""
        with self.repository.connect() as conn:
            row = conn.execute('SELECT * FROM iteration_sealed_claims WHERE iteration_id=?', (iteration_id,)).fetchone()
        if row is None:
            return None
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
