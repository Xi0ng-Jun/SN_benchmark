"""Parent/root comparison records for iteration candidates (design §4.1, §10 step 2).

A measurement is one execution of a candidate. Its first admitted execution
remains the provenance reference. Comparisons average every admitted attempt,
validate each against a shared pairing anchor, and freeze the complete attempt
set, noise estimates and source pairs. New measurements create new records.
"""
from __future__ import annotations

from copy import deepcopy
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
KINDS = ('parent', 'root', 'fresh', 'cluster')
ADMISSION_FORMAT = 'sn-execution-admission-cache-v1'
ADMISSION_CACHE = 'admission-cache.json'
NO_RUNS = 'no run discovered for this execution'

def _add_retry_stats(total, extra):
    """Fold one scope's re-ask disclosure (``ComparisonService._retry_stats``) into another."""
    total['attempts'].extend(extra['attempts'])
    for key in ('unobserved', 'cases', 'first_failed', 're_asks', 'still_failed'):
        total[key] += extra[key]
    categories = dict(total['categories'])
    for name, count in extra['categories'].items():
        categories[name] = categories.get(name, 0) + count
    total['categories'] = dict(sorted(categories.items()))
    total['re_ask_rate'] = total['re_asks'] / total['cases'] if total['cases'] else None


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
        # Admission caches describe artifact validity, never access authority.
        # Recheck current policy before either memory or persisted cache reuse;
        # the isolated sealed reader has no ordinary worktree policy.
        guard = getattr(self.views, '_assert_unsealed', None)
        once = getattr(self.views, 'once_per_request', None)
        if guard is not None and (once is None or once(('sealed', execution_id))):
            for run in self.views.execution_runs(execution_id):
                guard(run.id)
        if execution_id in self._cache:
            return self._cache[execution_id]
        with self._guard:
            lock = self._locks.setdefault(execution_id, threading.Lock())
        with lock:
            if execution_id in self._cache:
                return self._cache[execution_id]
            result = self._admit(execution_id, measurement.get('run_dir'))
            # A finished execution whose runs are not discovered yet is not a verdict.
            if result.get('reason') != NO_RUNS:
                self._cache[execution_id] = result
        return result

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
            status='not_admitted', reason=NO_RUNS)
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
    def _iteration_record(self, iteration_id):
        """Task, protocol and selection only; building the whole tree per record was quadratic."""
        read = getattr(self.iterations, 'iteration_record', None) or self.iterations.iteration
        return read(iteration_id)

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

    def _compute_quality(self, subject, reference, kind):
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
        protocol = self._iteration_record(subject['iteration_id']).get('protocol') or {}
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

    @staticmethod
    def terminal(candidate):
        found = {}
        for m in candidate.get('attempts', []) + candidate.get('links', []):
            if m.get('execution_id') and m.get('execution_state') not in (None, 'running'):
                found.setdefault(m['execution_id'], m)
        return list(found.values())

    def _infrastructure(self, candidate):
        """Count error/missing over every terminal execution, including rejected runs."""
        rows = []
        for m in self.terminal(candidate):
            runs = self.views.execution_runs(m['execution_id'])
            total = failed = 0
            for run in runs:
                cases = self.views.cases(run.id)['cases'] if hasattr(self.views, 'cases') else [
                    dict(status='error' if m['execution_state'] != 'finished' else 'success') for _ in run.case_ids]
                total += len(cases)
                failed += sum(c['status'] in ('error', 'missing') for c in cases)
            # A failure before run publication has no observed per-case denominator.
            rows.append(dict(execution_id=m['execution_id'], total=total, failed=failed,
                             unobserved=not total, execution_state=m['execution_state']))
        return dict(total=sum(r['total'] for r in rows), failed=sum(r['failed'] for r in rows),
                    unobserved=sum(r['unobserved'] for r in rows), attempts=rows)

    def _retry_stats(self, candidate):
        """In-attempt re-asks over every terminal execution (disclosure; the gate never reads it).

        From each execution's ``retry-rounds.json`` (``notebook_job``): cases
        planned, cases whose first answer failed, re-asks, cases still failed
        after them, and the first-failure categories.
        """
        from collections import Counter
        totals, categories, attempts = Counter(), Counter(), []
        for m in self.terminal(candidate):
            path = Path(m['run_dir']) / 'retry-rounds.json' if m.get('run_dir') else None
            saved = None
            if path is not None and path.is_file() and not path.is_symlink():
                try:
                    saved = json.loads(path.read_text(encoding='utf-8')).get('case_retry')
                except (OSError, ValueError, AttributeError):
                    saved = None
            if not isinstance(saved, dict):
                attempts.append(dict(execution_id=m['execution_id'], observed=False))
                continue
            attempts.append(dict(saved, execution_id=m['execution_id'], observed=True))
            totals.update({k: saved.get(k) or 0 for k in ('cases', 'first_failed', 're_asks', 'still_failed')})
            categories.update(saved.get('categories') or {})
        return dict(attempts=attempts, unobserved=sum(not a['observed'] for a in attempts),
                    **{k: totals[k] for k in ('cases', 'first_failed', 're_asks', 'still_failed')},
                    re_ask_rate=totals['re_asks'] / totals['cases'] if totals['cases'] else None,
                    categories=dict(sorted(categories.items())))

    def _compute_scope(self, subject, reference, kind):
        body, reason = self._compute_quality(subject, reference, kind)
        if body is None and ((self.terminal(subject) and self.terminal(reference)) or
                             (kind == 'fresh' and subject.get('_abandoned_fresh') and
                              (subject.get('attempts') or reference.get('attempts')))):
            def side(c):
                return dict(candidate_id=c['id'], execution_id=None, attempt=None,
                            runs=[r.id for m in self.terminal(c) for r in self.views.execution_runs(m['execution_id'])])
            body = dict(format=FORMAT, kind=kind, iteration_id=subject['iteration_id'],
                        subject=side(subject), reference=side(reference), metric=None, partitions=[], rows=[],
                        counts=dict(better=0, worse=0, same=0, unpaired=0), regressed=[],
                        mean=dict(reference=None, subject=None, delta=None),
                        admission=dict(status='not_admitted', reason=reason))
        if body is not None:
            if subject.get('_abandoned_fresh'):
                body['undispatched_attempts'] = {side: [dict(attempt=a.get('attempt'), job_id=a.get('job_id'),
                    preparation_id=a.get('preparation_id')) for a in c.get('attempts', []) if not a.get('execution_id')]
                    for side, c in (('subject', subject), ('reference', reference))}
            body['infrastructure'] = {s: self._infrastructure(c) for s, c in (
                ('subject', subject), ('reference', reference))}
            # Retry disclosure is reported for both sides; the gate does not use it.
            body['retry_summary'] = {s: self._retry_stats(c) for s, c in (
                ('subject', subject), ('reference', reference))}
        return body, reason

    @classmethod
    def scoped(cls, candidate, batch_id):
        attempts = [a for a in candidate.get('attempts', []) if a.get('batch_id') == batch_id]
        links = candidate.get('links', []) if batch_id is None else []
        out = dict(candidate, attempts=attempts, links=links, _batch_scope=batch_id)
        admitted = cls.admitted(out)
        out['reference'] = admitted[0] if admitted else None
        return out

    def reconcile_fresh(self, iteration_id, *, actor, tree=None):
        """Record terminal fresh measurements and enlarge the validation pool; never dispatch.

        ``tree`` reuses an already built iteration; returns how many batches completed.
        """
        from .splits import batches
        tree = tree or self.iterations.iteration(iteration_id)
        completed = 0
        for b in batches(self.repository, iteration_id):
            from .loop_measurements import fresh_ready
            if not fresh_ready(self.repository, b['request_key'], service=self.iterations):
                continue
            if b['completed_at']:
                continue
            # Read after the readiness fence, even if the caller supplied a
            # snapshot taken before a round's terminal retry publication.
            fresh_subject = self.iterations.candidate(b['candidate_id'])
            fresh_reference = self.iterations.candidate(fresh_subject['parent_candidate_id'])
            subject = self.scoped(fresh_subject, b['batch_id'])
            reference = self.scoped(fresh_reference, b['batch_id'])
            k = tree['protocol']['k_base']
            from .loop_measurements import fresh_abandoned
            abandoned = fresh_abandoned(self.repository, b['request_key'])
            if not subject['attempts'] and not reference['attempts']:
                continue
            if any((len(c['attempts']) < k and not abandoned) or
                   len(self.terminal(c)) != sum(bool(a.get('execution_id')) or not abandoned for a in c['attempts'])
                   for c in (subject, reference)):
                continue
            if abandoned:
                subject['_abandoned_fresh'] = True
            record = self._stored(subject, reference, 'fresh', actor)
            if not record.get('id'):
                continue
            with self.repository.connect(write=True) as conn:
                changed = conn.execute('UPDATE iteration_fresh_batches SET completed_at=?,comparison_id=? '
                    'WHERE iteration_id=? AND batch_id=? AND completed_at IS NULL',
                    (now(), record['id'], iteration_id, b['batch_id'])).rowcount
                if changed:
                    completed += 1
                    self.repository._event(conn, tree['task_id'], 'iteration.fresh.completed', b['candidate_id'],
                                           dict(batch_id=b['batch_id'], comparison_id=record['id']))
        return completed

    def _compute(self, subject, reference, kind):
        from .splits import batches, roles
        from .noise import summarize_cases
        tree = self._iteration_record(subject['iteration_id'])
        protocol = tree.get('protocol') or {}
        commits = dict(subject=subject.get('frozen_commit'), reference=reference.get('frozen_commit'))
        def descriptor(batch_id, ids):
            plan = next((b for b in protocol.get('fresh_batches', []) if b['batch_id'] == batch_id), {})
            return dict(batch_id=batch_id, selection_hash=plan.get('selection_hash') or tree.get('selection_hash'), n=len(ids))

        def batch_summary(record, desc, ids):
            rows = [r for r in (record or {}).get('rows', []) if r['case_id'] in ids]
            measured = bool(record and record['admission']['status'] == 'admitted' and len(rows) == len(ids) and ids
                            and all(r.get(k) is not None for r in rows for k in ('left', 'right', 'delta')))
            ref_noise = summarize_cases({cid: c for cid, c in (record or {}).get('noise', {}).get('reference', {}).get('cases', {}).items() if cid in ids})
            return dict(desc, reference=_mean(r['left'] for r in rows) if measured else None,
                subject=_mean(r['right'] for r in rows) if measured else None,
                delta=_mean(r['delta'] for r in rows) if measured else None, eta=ref_noise['eta'],
                attempts_used=deepcopy((record or {}).get('attempts_used', {})))

        if '_batch_scope' in subject:
            body, reason = self._compute_scope(subject, reference, kind)
            if body is not None:
                body['batch_id'] = subject['_batch_scope']
                body['frozen_commits'] = commits
                if kind == 'fresh':
                    ids = next(b['case_ids'] for b in protocol['fresh_batches'] if b['batch_id'] == subject['_batch_scope'])
                    body['pool'] = [descriptor(subject['_batch_scope'], ids)]
                if kind == 'cluster':
                    from .execution_scopes import get
                    scope = get(self.repository, subject['iteration_id'], subject['_batch_scope'])
                    if body['admission']['status'] == 'admitted' and set(scope['case_ids']) != {r['case_id'] for r in body['rows']}:
                        raise ValueError('Cluster measurements differ from the frozen selection')
                    from .gate import judge, DEFAULT_RULE
                    from .hypotheses import HypothesisStore
                    tree = self._iteration_record(subject['iteration_id'])
                    card = HypothesisStore(self.repository).get(tree['task_id'], subject['proposal_id'])['card']
                    contract = card.get('contract')
                    verdict = judge(body, body.get('noise', {}), contract, tree['protocol'].get('gate_rule', DEFAULT_RULE)) if contract else None
                    body['cluster'] = dict(cluster_id=scope['cluster_id'], selection_hash=scope['selection_hash'],
                        rho=verdict['rho'] if verdict else None, tau=verdict['tau'] if verdict else None,
                        evidence=verdict, note='Local diagnostic only; validation gate decides adoption')
            return body, reason
        body, reason = self._compute_scope(self.scoped(subject, None), self.scoped(reference, None), kind)
        if body is None:
            return body, reason
        protocol = self._iteration_record(subject['iteration_id']).get('protocol') or {}
        completed = {b['batch_id'] for b in batches(self.repository, subject['iteration_id'], completed=True)}
        role_map = roles(protocol, completed)
        if role_map is None:
            return body, reason
        body['frozen_commits'] = commits
        base_ids = {cid for cid, role in role_map.items() if role == 'validation'} - {cid for b in protocol.get('fresh_batches', []) for cid in b['case_ids']}
        pool = [descriptor('base', base_ids)]
        batch_rows = [batch_summary(body, pool[0], base_ids)]
        missing = []
        for b in protocol.get('fresh_batches', []):
            if b['batch_id'] not in completed:
                continue
            extra, _ = self._compute_scope(self.scoped(subject, b['batch_id']), self.scoped(reference, b['batch_id']), kind)
            desc = descriptor(b['batch_id'], b['case_ids'])
            pool.append(desc)
            batch_rows.append(batch_summary(extra, desc, set(b['case_ids'])))
            if extra and 'retry_summary' in extra:
                # Re-asks are disclosed for every fresh batch, admitted or not.
                for side in ('reference', 'subject'):
                    _add_retry_stats(body['retry_summary'][side], extra['retry_summary'][side])
            if not extra or extra['admission']['status'] != 'admitted':
                missing.append(b['batch_id'])
                continue
            body['rows'].extend(extra['rows'])
            if 'noise' not in body:
                body['noise'] = extra['noise']
                body['attempts_used'] = extra['attempts_used']
            else:
                for side in ('reference', 'subject'):
                    body['attempts_used'][side].extend(extra['attempts_used'][side])
                    body['noise'][side]['cases'].update({cid: dict(c, family=b['batch_id'] + ':' + c['family'])
                        for cid, c in extra['noise'][side]['cases'].items()})
                    body['noise'][side]['pairs'].extend(extra['noise'][side]['pairs'])
                    body['noise'][side]['k_below_base'] |= extra['noise'][side]['k_below_base']
                    body['noise'][side]['k'] = min(body['noise'][side]['k'], extra['noise'][side]['k'])
            for side in ('reference', 'subject'):
                a, e = body['infrastructure'][side], extra['infrastructure'][side]
                for key in ('failed', 'total', 'unobserved'):
                    a[key] += e[key]
                a['attempts'].extend(e['attempts'])
        for r in body['rows']:
            r['role'] = role_map.get(r['case_id'])
        expected = {cid for cid, role in role_map.items() if role == 'validation'}
        validation = [r for r in body['rows'] if r['case_id'] in expected]
        noise = body.get('noise', {})
        for side in ('reference', 'subject'):
            if side in noise:
                noise[side].update(summarize_cases(noise[side]['cases']))
        validation_noise = summarize_cases({cid: c for cid, c in noise.get('reference', {}).get('cases', {}).items() if cid in expected})
        noise['eta'] = validation_noise['eta']
        body['validation'] = dict(n=len(expected), reference=_mean(r['left'] for r in validation),
            subject=_mean(r['right'] for r in validation), delta=_mean(r['delta'] for r in validation),
            eta=validation_noise['eta'], incomplete=bool(missing or len(validation) != len(expected) or not expected),
            missing_batches=missing, pool=pool, batches=batch_rows)
        body['counts'] = {key: sum(r['flip'] == key for r in body['rows']) for key in body['counts']}
        body['mean'] = dict(reference=_mean(r['left'] for r in body['rows']), subject=_mean(r['right'] for r in body['rows']),
                            delta=_mean(r['delta'] for r in body['rows']))
        body['regressed'] = [r['case_id'] for r in body['rows'] if r['delta'] is not None and r['delta'] < 0]
        return body, reason

    def _measurement_key(self, candidate):
        attempts = self.admitted(candidate)
        terminal = self.terminal(candidate)
        from .splits import batches
        pool = [b['batch_id'] for b in batches(self.repository, candidate['iteration_id'], completed=True)]
        if pool and '_batch_scope' not in candidate:
            return _hash(dict(attempts=[(m['execution_id'], m['execution_state']) for m in terminal], validation=pool))
        if len(terminal) == len(attempts):
            return attempts[0]['execution_id'] if len(attempts) == 1 else _hash(attempts) if attempts else None
        return _hash([(m['execution_id'], m['execution_state']) for m in terminal])

    # ------------------------------------------------------------ records
    @staticmethod
    def _record(row):
        extra = dict(verdict=json.loads(row['verdict_json'])) if row['verdict_json'] else {}
        return dict(json.loads(row['record']), **extra, id=row['id'], candidate_id=row['candidate_id'],
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
        empty = _hash(dict(unobserved_batch=subject.get('_batch_scope'))) if subject.get('_abandoned_fresh') else None
        ends = (subject['id'], kind, reference['id'],
                self._measurement_key(subject) or empty, self._measurement_key(reference) or empty)
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

    def for_candidate(self, candidate_id, *, actor, iteration=None, store=True, reconcile=True):
        """Δ父 and Δ根 of one candidate; a root has neither, a child of the root shares one record.

        Records are derived on first request (reading never changes an existing one).
        """
        if iteration is None:
            iteration = self.iterations.iteration(self.iterations.candidate(candidate_id)['iteration_id'])
        if store and reconcile:
            self.reconcile_fresh(iteration['id'], actor=actor, tree=iteration)
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

    def for_cluster(self, candidate_id, cluster_id, *, actor, implementer=False):
        """Read a paired cluster execution; implementers receive only ordinal flips."""
        from .clusters import candidate_cluster, ClusterStore
        candidate = self.iterations.candidate(candidate_id)
        tree = self.iterations.iteration(candidate['iteration_id'])
        if candidate_cluster(self.repository, tree['task_id'], candidate_id) != cluster_id:
            raise ValueError('A candidate may read only its own cluster result')
        cluster = ClusterStore(self.repository).get(tree['task_id'], cluster_id)
        parent = self.iterations.candidate(candidate['parent_candidate_id'])
        scope_id = 'cluster:' + cluster_id
        record = self._stored(self.scoped(candidate, scope_id), self.scoped(parent, scope_id), 'cluster', actor)
        if not implementer:
            return record
        rows = {r['case_id']: r for r in record.get('rows', [])}
        return dict(cluster_id=cluster_id, status=record.get('admission', {}).get('status', 'unavailable'),
                    members=[dict(member=f'm{i:02d}', flip=rows.get(cid, {}).get('flip', 'unpaired'))
                             for i, cid in enumerate(cluster['scan']['members'], 1)])

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
