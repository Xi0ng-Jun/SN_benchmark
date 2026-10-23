"""Parent/root comparison records for iteration candidates (design §4.1, §10 step 2).

A measurement is one execution of a candidate. Its first admitted execution
remains the provenance reference. Comparisons average every admitted attempt,
validate each against a shared pairing anchor, and freeze the complete attempt
set, noise estimates and source pairs. New measurements create new records.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import threading
import time
import uuid

from ..artifacts import save_json
from .repository import UnknownIdentity, now

FORMAT = 'sn-candidate-comparison-v1'
KINDS = ('parent', 'root', 'fresh')
ADMISSION_FORMAT = 'sn-execution-admission-cache-v1'
ADMISSION_CACHE = 'admission-cache.json'


def initialize_comparisons(repository):
    with repository.connect(write=True) as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS candidate_comparisons(
                id TEXT PRIMARY KEY, iteration_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
                kind TEXT NOT NULL, reference_candidate_id TEXT NOT NULL,
                subject_execution TEXT NOT NULL, reference_execution TEXT NOT NULL,
                record TEXT NOT NULL, content_hash TEXT NOT NULL, created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(candidate_id, kind, reference_candidate_id, subject_execution, reference_execution));
        ''')
        columns = {r['name'] for r in conn.execute('PRAGMA table_info(candidate_comparisons)')}
        for name in ('attempts_used_json', 'noise_json', 'verdict_json'):
            if name not in columns:
                conn.execute(f'ALTER TABLE candidate_comparisons ADD COLUMN {name} TEXT')


class Admission:
    """Per-execution admission, cached once the execution is terminal.

    Hundreds of partitions take minutes to admit, so an admitted result is also
    saved beside the execution and reused by later processes (the web service
    after a restart, every sn-eval call) while each partition's manifest,
    outputs, submission and scores keep their size and mtime. Concurrent
    requests for one execution wait for a single computation.
    """

    def __init__(self, run_views):
        self.views = run_views
        self._cache = {}
        self._locks = {}
        self._guard = threading.Lock()

    def __call__(self, measurement):
        state, execution_id = measurement.get('execution_state'), measurement.get('execution_id')
        if execution_id is None or state is None:
            return dict(status='not_admitted', reason='execution record unavailable')
        if state == 'running':
            return dict(status='pending', reason=None)
        if state != 'finished':
            return dict(status='not_admitted', reason=f'execution {state}')
        if execution_id in self._cache:
            return self._cache[execution_id]
        with self._guard:
            lock = self._locks.setdefault(execution_id, threading.Lock())
        with lock:
            if execution_id not in self._cache:
                self._cache[execution_id] = self._admit(execution_id, measurement.get('run_dir'))
        return self._cache[execution_id]

    def _admit(self, execution_id, execution_dir):
        runs = self.views.execution_runs(execution_id)
        saved = Path(execution_dir) / ADMISSION_CACHE if execution_dir and runs else None
        signature = _signature(runs) if saved is not None else None
        if signature is not None:
            try:
                cached = json.loads(saved.read_text())
                if cached.get('format') == ADMISSION_FORMAT and cached.get('signature') == signature:
                    return cached['result']
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass
        started = time.monotonic()
        result = dict(status='admitted', reason=None) if runs else dict(
            status='not_admitted', reason='no run discovered for this execution')
        # Partitions of one execution share a frozen bundle: verify and load it once.
        bundle = self.views.shared_bundle(runs[0].id) if len(runs) > 1 else None
        for run in runs:
            single = (self.views.admission(run.id, bundle=bundle) if bundle is not None
                      else self.views.admission(run.id))
            if single['status'] != 'admitted':
                result = dict(status='not_admitted', reason=f'{run.id}: {single["reason"]}')
                break
        if len(runs) > 20:
            print(f'admission {execution_id}: {len(runs)} partitions, shared bundle '
                  f'{"yes" if bundle is not None else "no"}, {result["status"]} in '
                  f'{time.monotonic() - started:.1f}s', file=sys.stderr, flush=True)
        # Only an admission is saved: a refusal may come from a transient read failure.
        if signature is not None and result['status'] == 'admitted':
            try:
                save_json(saved, dict(format=ADMISSION_FORMAT, signature=signature, result=result))
            except OSError:
                pass
        return result


def _signature(runs):
    """Size and mtime of every file a partition's admission reads; None when a run has no paths."""
    def stamp(path):
        try:
            stat = Path(path).stat()
        except OSError:
            return None
        return [stat.st_size, stat.st_mtime_ns]
    rows = []
    for run in runs:
        run_dir, scores = getattr(run, 'run_dir', None), getattr(run, 'scores', None)
        if run_dir is None or scores is None:
            return None
        files = [Path(run_dir) / 'manifest.json', Path(run_dir) / 'outputs.jsonl', Path(run_dir) / 'planned.jsonl',
                 Path(run_dir) / 'state.json', Path(scores), Path(scores).parent / 'submission.json']
        rows.append([run.id, str(run_dir), [stamp(f) for f in files]])
    return _hash(rows)


def _hash(body):
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


class ComparisonService:
    def __init__(self, repository, iterations, run_views, *, initialize=True):
        self.repository = repository
        self.iterations = iterations
        self.views = run_views
        if initialize:
            initialize_comparisons(repository)

    # ------------------------------------------------------------ computing
    def _side(self, candidate):
        reference = candidate.get('reference')
        if reference is None:
            return None
        runs = self.views.execution_runs(reference['execution_id'])
        return dict(candidate_id=candidate['id'], attempt=reference['attempt'],
                    execution_id=reference['execution_id'], runs=[r.id for r in runs]), runs

    def _compute_pair(self, subject, reference, kind):
        """Pair the two references; ``None`` with a reason when one side is not evaluated."""
        sides = self._side(subject), self._side(reference)
        if sides[0] is None or sides[1] is None:
            missing = subject if sides[0] is None else reference
            return None, f'候选 {missing["id"]} 没有通过准入的运行'
        (subject_side, subject_runs), (reference_side, reference_runs) = sides
        key = lambda run: (run.suite, run.mode, tuple(run.case_ids))
        subject_by, reference_by = {key(r): r for r in subject_runs}, {key(r): r for r in reference_runs}
        body = dict(format=FORMAT, kind=kind, iteration_id=subject['iteration_id'],
                    subject=subject_side, reference=reference_side, metric=None, partitions=[],
                    rows=[], counts=dict(better=0, worse=0, same=0, unpaired=0))
        if set(subject_by) != set(reference_by):
            body['admission'] = dict(status='not_admitted', reason='两端的分区集合不一致')
        else:
            reasons, metrics = [], set()
            first = next(iter(reference_by.values()), None)
            bundle = self.views.shared_bundle(first.id) if first is not None and len(reference_by) > 1 else None
            for partition in sorted(reference_by):
                pair = reference_by[partition].id, subject_by[partition].id
                flips = self.views.flips(*pair, bundle=bundle) if bundle is not None else self.views.flips(*pair)
                metrics.add(flips['metric'])
                body['partitions'].append(dict(reference_run=flips['left_run'], subject_run=flips['right_run'],
                                               admission=flips['admission'], reason=flips['note']))
                if flips['admission'] != 'admitted':
                    reasons.append(f'{flips["left_run"]} → {flips["right_run"]}: {flips["note"]}')
                    continue
                for row in flips['rows']:
                    body['rows'].append(dict(row, partition=len(body['partitions'])))
            if len(metrics) > 1:
                reasons.append('分区的主指标不一致: ' + ', '.join(sorted(map(str, metrics))))
            body['metric'] = next(iter(metrics)) if len(metrics) == 1 else None
            body['admission'] = (dict(status='admitted', reason=None) if not reasons else
                                 dict(status='not_admitted', reason='; '.join(reasons)))
        if body['admission']['status'] != 'admitted':
            body['rows'] = []
        for row in body['rows']:
            body['counts'][row['flip']] += 1
        # left/right in rows are reference/subject, as in a flip table read reference → subject.
        body['mean'] = dict(reference=_mean(r['left'] for r in body['rows']),
                            subject=_mean(r['right'] for r in body['rows']),
                            delta=_mean(r['delta'] for r in body['rows']))
        body['regressed'] = [r['case_id'] for r in body['rows'] if r['delta'] is not None and r['delta'] < 0]
        return body, None

    @staticmethod
    def admitted(candidate):
        """All admitted executions in recorded order, with legacy links deduplicated."""
        found = {}
        for m in candidate.get('attempts', []) + candidate.get('links', []):
            if (m.get('admission') or {}).get('status') == 'admitted':
                found.setdefault(m['execution_id'], dict(attempt=m.get('attempt'), execution_id=m['execution_id']))
        return list(found.values())

    def _compute(self, subject, reference, kind):
        from .noise import estimate
        sa, ra = self.admitted(subject), self.admitted(reference)
        if not sa or not ra:
            return self._compute_pair(subject, reference, kind)
        samples = {'subject': {}, 'reference': {}}
        body = None
        # Admission is transitive across the shared anchor: k+l-1 pair checks,
        # rather than k*l checks or bypassing the existing identity contract.
        for si, ri in [(i, 0) for i in range(len(sa))] + [(0, j) for j in range(1, len(ra))]:
            pair, reason = self._compute_pair(dict(subject, reference=sa[si]), dict(reference, reference=ra[ri]), kind)
            if pair is None:
                return None, reason
            if body is None:
                body = pair
            if pair['admission']['status'] != 'admitted':
                return pair, None
            for side, attempt, field in [('subject', sa[si], 'right'), ('reference', ra[ri], 'left')]:
                samples[side][attempt['execution_id']] = dict(attempt, rows=[
                    dict(case_id=r['case_id'], family=str(r['partition']), value=r[field]) for r in pair['rows']])
        protocol = self.iterations.iteration(subject['iteration_id']).get('protocol') or {}
        body['attempts_used'] = dict(subject=sa, reference=ra)
        body['noise'] = {side: estimate(dict(attempts=list(values.values())),
                                        success_threshold=protocol.get('success_threshold', .5),
                                        k_base=protocol.get('k_base', 2)) for side, values in samples.items()}
        body['noise']['eta'] = body['noise']['reference']['eta']
        for row in body['rows']:
            row['left'] = body['noise']['reference']['cases'][row['case_id']]['mean']
            row['right'] = body['noise']['subject']['cases'][row['case_id']]['mean']
            row['delta'] = row['right'] - row['left'] if row['left'] is not None and row['right'] is not None else None
            d = row['delta']
            row['flip'] = 'unpaired' if d is None else 'better' if d > 0 else 'worse' if d < 0 else 'same'
        body['counts'] = {key: sum(r['flip'] == key for r in body['rows']) for key in body['counts']}
        body['mean'] = dict(reference=_mean(r['left'] for r in body['rows']),
                            subject=_mean(r['right'] for r in body['rows']), delta=_mean(r['delta'] for r in body['rows']))
        body['regressed'] = [r['case_id'] for r in body['rows'] if r['delta'] is not None and r['delta'] < 0]
        return body, None

    def _measurement_key(self, candidate):
        attempts = self.admitted(candidate)
        if len(attempts) == 1:
            return attempts[0]['execution_id']
        return _hash(attempts) if attempts else None

    # ------------------------------------------------------------ records
    @staticmethod
    def _record(row):
        return dict(json.loads(row['record']), id=row['id'], candidate_id=row['candidate_id'],
                    reference_candidate_id=row['reference_candidate_id'], content_hash=row['content_hash'],
                    created_by=row['created_by'], created_at=row['created_at'])

    def get(self, comparison_id):
        with self.repository.connect() as conn:
            row = conn.execute('SELECT * FROM candidate_comparisons WHERE id=?', (comparison_id,)).fetchone()
        if row is None:
            raise UnknownIdentity('Unknown comparison identity')
        return self._record(row)

    def _stored(self, subject, reference, kind, actor, store=True):
        """The immutable record for these two references, created on first request.

        ``store=False`` reads an existing record or computes one without saving it.
        """
        ends = (subject['id'], kind, reference['id'],
                self._measurement_key(subject), self._measurement_key(reference))
        query = ('SELECT * FROM candidate_comparisons WHERE candidate_id=? AND kind=? AND reference_candidate_id=? '
                 'AND subject_execution=? AND reference_execution=?')
        with self.repository.connect() as conn:
            # A reader that did not initialize may meet a state with no records yet.
            row = conn.execute(query, ends).fetchone() if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='candidate_comparisons'").fetchone() else None
        if row is not None:
            return self._record(row)
        body, reason = self._compute(subject, reference, kind)
        if body is None:
            return dict(kind=kind, status='unavailable', reason=reason, reference_candidate_id=reference['id'])
        if not store:
            return dict(body, id=None, candidate_id=subject['id'], reference_candidate_id=reference['id'], stored=False)
        with self.repository.connect(write=True) as conn:
            conn.execute('INSERT OR IGNORE INTO candidate_comparisons '
                         '(id,iteration_id,candidate_id,kind,reference_candidate_id,subject_execution,reference_execution,'
                         'record,content_hash,created_by,created_at,attempts_used_json,noise_json) '
                         'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (uuid.uuid4().hex, subject['iteration_id'], *ends, json.dumps(body, ensure_ascii=False),
                          _hash(body), actor, now(), json.dumps(body.get('attempts_used')), json.dumps(body.get('noise'))))
            return self._record(conn.execute(query, ends).fetchone())

    def for_candidate(self, candidate_id, *, actor, iteration=None, store=True):
        """Δ父 and Δ根 of one candidate; a root has neither, a child of the root shares one record.

        Records are derived on first request (reading never changes an existing one).
        """
        if iteration is None:
            iteration = self.iterations.iteration(self.iterations.candidate(candidate_id)['iteration_id'])
        by_id = {c['id']: c for c in iteration['candidates']}
        subject = by_id[candidate_id]
        root_id, parent_id = iteration['root_candidate_id'], subject['parent_candidate_id']
        if subject['id'] == root_id or parent_id is None:
            note = '根候选' if subject['id'] == root_id else '没有父候选'
            return dict(candidate_id=candidate_id, parent=dict(kind='parent', status='none', reason=note),
                        root=dict(kind='root', status='none', reason=note))
        parent = self._stored(subject, by_id[parent_id], 'parent', actor, store)
        if parent_id == root_id:
            root = dict(kind='root', status='same_as_parent', comparison_id=parent.get('id'))
        elif root_id in by_id:
            root = self._stored(subject, by_id[root_id], 'root', actor, store)
        else:
            root = dict(kind='root', status='unavailable', reason='迭代没有根候选')
        return dict(candidate_id=candidate_id, parent=parent, root=root)

    def pinned(self, candidate_id, reference_candidate_id):
        """An ad-hoc comparison; computed on request and never stored as a conclusion."""
        subject = self.iterations.candidate(candidate_id)
        reference = self.iterations.candidate(reference_candidate_id)
        if subject['iteration_id'] != reference['iteration_id']:
            raise ValueError('Pinned comparisons stay inside one iteration')
        body, reason = self._compute(subject, reference, 'pinned')
        if body is None:
            return dict(kind='pinned', status='unavailable', reason=reason, reference_candidate_id=reference['id'])
        return dict(body, candidate_id=candidate_id, reference_candidate_id=reference_candidate_id, stored=False)


def summary(entry):
    """The tree-row projection of one side of ``for_candidate``."""
    if 'id' not in entry:
        return {k: entry.get(k) for k in ('kind', 'status', 'reason', 'comparison_id')}
    return dict(kind=entry['kind'], status=entry['admission']['status'], reason=entry['admission']['reason'],
                comparison_id=entry['id'], metric=entry['metric'], counts=entry['counts'],
                mean_delta=entry['mean']['delta'], regressed=entry['regressed'])
