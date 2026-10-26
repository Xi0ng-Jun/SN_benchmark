"""Offline LCD ledgers, derived from immutable measurements and frozen clusters.

No model execution, gate decisions, or frozen memberships are changed here.
Only aggregate scores and selection hashes cross this reporting boundary.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re

from ..artifacts import save_json
from .noise import summarize_cases


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _pool(value):
    if not isinstance(value, list) or not value:
        return None
    out = []
    for item in value:
        if (not isinstance(item, dict) or not isinstance(item.get('batch_id'), str)
                or not isinstance(item.get('selection_hash'), str) or not item['selection_hash']
                or type(item.get('n')) is not int or item['n'] < 0):
            return None
        out.append({k: item[k] for k in ('batch_id', 'selection_hash', 'n')})
    if len({b['batch_id'] for b in out}) != len(out):
        return None
    return sorted(out, key=lambda b: b['batch_id'])


def _pool_key(pool):
    return json.dumps(pool, sort_keys=True) if pool is not None else None


def _admitted(record):
    return (record.get('admission') or {}).get('status') == 'admitted'


def _candidate(record):
    return record.get('candidate_id') or (record.get('subject') or {}).get('candidate_id')


def _frozen(record):
    value = record.get('frozen_commits') or {}
    return value if all(isinstance(value.get(s), str) and value[s] for s in ('subject', 'reference')) else None


def _validation(record):
    return record.get('validation') or {}


def _statistics(iteration, record):
    validation = _validation(record)
    if validation.get('incomplete') or not _admitted(record):
        return dict(n=validation.get('n'), n_eff=None, eta=None)
    pool = _pool(validation.get('pool'))
    cases = ((record.get('noise') or {}).get('reference') or {}).get('cases') or {}
    protocol = iteration.get('protocol') or {}
    ids = set()
    for batch in pool or []:
        if batch['batch_id'] == 'base':
            ids.update((protocol.get('splits') or {}).get('validation', []))
        else:
            ids.update(c for b in protocol.get('fresh_batches', []) if b['batch_id'] == batch['batch_id'] for c in b['case_ids'])
    selected = {cid: c for cid, c in cases.items() if cid in ids}
    effective = summarize_cases(selected)['n_eff'] if selected else None
    # Never borrow search or another validation pool's effective sample size.
    if len(selected) != validation.get('n'):
        effective = None
    return dict(n=validation.get('n'), n_eff=effective, eta=_number(validation.get('eta')))


def _attempts(record):
    attempts = record.get('attempts_used') or {}
    return {side: {a['execution_id'] for a in attempts.get(side, []) if a.get('execution_id')}
            for side in ('subject', 'reference')}


def _fresh_summary(iteration, fresh, validations):
    batch_id = fresh.get('batch_id')
    metadata = next((b for b in (iteration.get('protocol') or {}).get('fresh_batches', []) if b['batch_id'] == batch_id), None)
    pool = _pool([dict(metadata, n=len(metadata['case_ids']))]) if metadata and 'case_ids' in metadata else None
    out = dict(batch_id=batch_id, comparison_id=fresh.get('id'), pool=pool,
               delta=_number((fresh.get('mean') or {}).get('delta')) if _admitted(fresh) else None,
               validation_delta=None, observed_bias=None, reason='no_matching_validation_batch')
    if metadata and (len(fresh.get('rows', [])) != len(metadata.get('case_ids', []))
            or {r.get('case_id') for r in fresh.get('rows', [])} != set(metadata.get('case_ids', []))
            or any(_number(r.get('delta')) is None for r in fresh.get('rows', []))):
        out.update(delta=None, reason='incomplete_fresh_batch')
        return out
    if pool is None or _frozen(fresh) is None:
        out['reason'] = 'unknown_composition_or_frozen_sources'
        return out
    for record in reversed(validations):
        if not _admitted(record) or _frozen(record) != _frozen(fresh):
            continue
        batch = next((b for b in _validation(record).get('batches', []) if _pool([b]) == pool), None)
        if batch is None:
            continue
        out['validation_comparison_id'] = record.get('id')
        out['validation_delta'] = _number(batch.get('delta')) if not batch.get('incomplete') else None
        left, right = _attempts(fresh), _attempts(record)
        if any(not left[s] or not right[s] for s in left):
            out['reason'] = 'unknown_measurements'
        elif any(left[s] & right[s] for s in left):
            out['reason'] = 'same_measurements'
        elif out['delta'] is None or out['validation_delta'] is None:
            out['reason'] = 'incomplete_measurements'
        else:
            out.update(observed_bias=out['validation_delta'] - out['delta'], reason=None)
        break
    return out


def _cluster_rho(record, members):
    # A stored rho may cover a smaller predicted-flip contract. Apply the same
    # per-member held predicate to the complete immutable membership, without
    # the hypothesis card's unrelated 200-prediction size limit.
    from .noise import difference_band
    if not members:
        return None
    noise = record.get('noise') or {}
    reference, subject = noise.get('reference', {}), noise.get('subject', {})
    threshold = reference.get('success_threshold', .5)
    rows = {r['case_id']: r for r in record.get('rows', [])}
    held = 0
    for cid in members:
        row = rows.get(cid) or {}
        left, right = reference.get('cases', {}).get(cid, {}), subject.get('cases', {}).get(cid, {})
        if (any(_number(row.get(k)) is None for k in ('left', 'right', 'delta'))
                or left.get('n', 0) < reference.get('k_base', 2)
                or right.get('n', 0) < subject.get('k_base', 2)
                or left.get('successes') is None or right.get('successes') is None):
            return None
        band = difference_band(left['successes'], left['n'], right['successes'], right['n'])
        held += row['left'] < threshold <= row['right'] and row['delta'] > band
    return held / len(members)


def _sealed_summary(score):
    if score is None:
        return None
    fields = ('status', 'candidate_id', 'frozen_commit', 'confirmed_by', 'claimed_at', 'metric', 'n', 'mean')
    return {key: score[key] for key in fields if key in score and (score[key] is None or type(score[key]) in (str, int, float, bool))}


def aggregate_ledger(iteration, comparisons, hypotheses, clusters, *, seed_search_mean=None, sealed_score=None):
    """Pure, privacy-preserving aggregation; unknown historical identity stays unknown.

    K counts both sides' distinct frozen commits, separately for every immutable
    validation pool. A fresh/validation bias requires the same batch/hash/count,
    frozen source pair, and disjoint observed execution sets on both sides.
    """
    by_candidate = {c['id']: c for c in iteration.get('candidates', [])}
    normalized = []
    for record in comparisons:
        if not record.get('frozen_commits'):
            subject = by_candidate.get(_candidate(record)) or {}
            reference_id = record.get('reference_candidate_id') or (record.get('reference') or {}).get('candidate_id')
            reference = by_candidate.get(reference_id) or {}
            # Candidate frozen revisions are immutable; unlike current pool
            # composition this identity can safely recover an older record.
            if subject.get('frozen_commit') and reference.get('frozen_commit'):
                record = dict(record, frozen_commits=dict(subject=subject['frozen_commit'], reference=reference['frozen_commit']))
        normalized.append(record)
    comparisons = sorted(normalized, key=lambda c: c.get('created_at') or '')
    validations = [c for c in comparisons if c.get('kind') not in ('fresh', 'cluster') and c.get('validation')]
    latest = validations[-1] if validations else {}
    pool = _pool(_validation(latest).get('pool'))
    grouped = {}
    for record in validations:
        composition = _pool(_validation(record).get('pool'))
        if composition is None:
            continue
        group = grouped.setdefault(_pool_key(composition), dict(pool=composition, records=[], commits=set(), unknown=False))
        group['records'].append(record)
        if _admitted(record) and not _validation(record).get('incomplete'):
            frozen = _frozen(record)
            if frozen:
                group['commits'].update(frozen.values())
            else:
                group['unknown'] = True
    pools = []
    for group in grouped.values():
        last = group['records'][-1]
        stats = _statistics(iteration, last)
        count = None if group['unknown'] else len(group['commits'])
        bias = stats['eta'] * math.sqrt(2 * math.log(count)) if count and stats['eta'] is not None else None
        pools.append(dict(pool=group['pool'], **stats, K=count, expected_bias=bias))
    current = next((p for p in pools if p['pool'] == pool), None)
    stats = current or dict(pool=pool, **_statistics(iteration, latest), K=None, expected_bias=None)
    accepted = {cid for cid, c in by_candidate.items() if (c.get('decision') or {}).get('decision') == 'adopted'}
    accepted_rows = []
    for cid in by_candidate:
        if cid not in accepted:
            continue
        records = [r for r in validations if _candidate(r) == cid]
        decision = by_candidate[cid].get('decision') or {}
        acceptance = next((r for r in records if r.get('id') == decision.get('comparison_id')), None)
        if acceptance is None:
            acceptance = next((r for r in reversed(records) if (r.get('verdict') or {}).get('verdict') == 'accept'), {})
        accepted_rows.append(dict(candidate_id=cid, comparison_id=acceptance.get('id'),
            validation=dict(delta=_number(_validation(acceptance).get('delta')),
                            pool=_pool(_validation(acceptance).get('pool'))),
            fresh=[_fresh_summary(iteration, r, records) for r in comparisons if r.get('kind') == 'fresh' and _candidate(r) == cid]))
    cards = {h['id']: h for h in hypotheses}
    contracts = []
    for candidate in by_candidate.values():
        hypothesis = cards.get(candidate.get('proposal_id')) or {}
        contract = (hypothesis.get('card') or {}).get('contract')
        if not contract:
            continue
        records = [r for r in validations if _candidate(r) == candidate['id'] and r.get('verdict')]
        record = records[-1] if records else {}
        verdict = record.get('verdict') or {}
        contracts.append(dict(hypothesis_id=hypothesis['id'], candidate_id=candidate['id'], cluster_id=contract.get('cluster_id'),
                              comparison_id=record.get('id'), rho=_number(verdict.get('rho')),
                              tau=_number(verdict.get('tau')), verdict=verdict.get('verdict')))
    implemented = {c['hypothesis_id'] for c in contracts}
    for hypothesis in hypotheses:
        contract = (hypothesis.get('card') or {}).get('contract')
        if contract and hypothesis['id'] not in implemented:
            contracts.append(dict(hypothesis_id=hypothesis['id'], candidate_id=None, cluster_id=contract.get('cluster_id'),
                                  comparison_id=None, rho=None, tau=None, verdict=None))
    cluster_rows, wishlist = [], []
    for cluster in clusters:
        cluster_id = cluster['cluster_id']
        resolved = []
        for record in comparisons:
            local = record.get('cluster') or {}
            tau = _number(local.get('tau', (record.get('verdict') or {}).get('tau')))
            rho = _cluster_rho(record, (cluster.get('scan') or {}).get('members', []))
            cid = _candidate(record)
            if (cid in accepted and _admitted(record)
                    and record.get('created_at') and cluster.get('frozen_at')
                    and record['created_at'] > cluster['frozen_at'] and rho is not None and tau is not None and rho >= tau):
                resolved.append(dict(candidate_id=cid, comparison_id=record.get('id'),
                                     reference_candidate_id=record.get('reference_candidate_id') or (record.get('reference') or {}).get('candidate_id'),
                                     rho=rho, tau=tau))
        scan = cluster.get('scan') or cluster
        numbers = {k: _number(scan.get(k)) for k in ('q', 'm', 'v')}
        cluster_rows.append(dict(cluster_id=cluster_id, status='resolved' if resolved else 'open',
                                 n_cases=scan.get('n_cases'), **numbers, resolved_by=resolved))
        if not resolved:
            wishlist.append(dict(cluster_id=cluster_id, **numbers))
    seed = _number(seed_search_mean)
    sampling = dict(k=1, verifier='none', seed_search_mean=seed, S_B=seed)
    if iteration.get('budget_usage') is not None:
        sampling['budget_usage'] = iteration['budget_usage']
    return dict(sampling_ledger=sampling,
                overfit_ledger=dict(stats, pools=pools, accepted=accepted_rows,
                    K_semantics='distinct subject and reference frozen commits within this validation pool'),
                structure_ledger=dict(clusters=cluster_rows, contracts=contracts,
                    audit_count=sum(c['verdict'] == 'audit' for c in contracts), wishlist=wishlist),
                sealed_score=_sealed_summary(sealed_score))


class LedgerStore:
    """Derived aggregate snapshots; identities and all writable paths stay local."""
    def __init__(self, state_root):
        self.root = Path(state_root).resolve()

    def write(self, iteration_id, value):
        if not isinstance(iteration_id, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,199}', iteration_id) is None:
            raise ValueError('Invalid ledger iteration identity')
        directory = self.root / 'ledgers'
        target = directory / (iteration_id + '.json')
        if any(path.is_symlink() for path in (directory, target, target.with_suffix('.json.tmp'))):
            raise ValueError('Ledger paths cannot be symlinks')
        if not target.resolve().is_relative_to(self.root):
            raise ValueError('Ledger path escapes state root')
        save_json(target, value)
        return target


class LedgerService:
    def __init__(self, repository, iterations, run_views):
        from .comparisons import ComparisonService
        self.repository, self.iterations = repository, iterations
        self.comparisons = ComparisonService(repository, iterations, run_views, initialize=False)
        self.store = LedgerStore(repository.root)

    def read(self, iteration_id, *, sealed_score=None):
        """Read existing observations only, then save their aggregate projection."""
        from .clusters import _record as cluster_record
        from .hypotheses import HypothesisStore
        tree = self.iterations.iteration(iteration_id)
        with self.repository.connect() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            records = [self.comparisons._record(r) for r in conn.execute(
                'SELECT * FROM candidate_comparisons WHERE iteration_id=? ORDER BY created_at,id', (iteration_id,))] if 'candidate_comparisons' in tables else []
            clusters = [cluster_record(r) for r in conn.execute(
                'SELECT * FROM clusters WHERE iteration_id=? ORDER BY frozen_at,cluster_id', (iteration_id,))] if 'clusters' in tables else []
            hypotheses = [HypothesisStore._record(r) for r in conn.execute(
                'SELECT * FROM hypotheses WHERE task_id=? ORDER BY created_at,id', (tree['task_id'],))] if 'hypotheses' in tables else []
            if sealed_score is None and 'iteration_sealed_claims' in tables:
                row = conn.execute('SELECT score_json FROM iteration_sealed_claims WHERE iteration_id=?', (iteration_id,)).fetchone()
                sealed_score = json.loads(row['score_json']) if row and row['score_json'] else None
        root = next((c for c in tree.get('candidates', []) if c['id'] == tree.get('root_candidate_id')), None)
        search = set(((tree.get('protocol') or {}).get('splits') or {}).get('search', []))
        seed = None
        if root and search:
            scope = self.comparisons.scoped(root, None)
            admitted = self.comparisons.admitted(scope)
            if admitted:
                first = admitted[0]
                measurement = next(a for a in scope.get('attempts', []) + scope.get('links', [])
                                   if a.get('execution_id') == first['execution_id'] and (a.get('admission') or {}).get('status') == 'admitted')
                scope = dict(scope, attempts=[measurement], links=[], reference=first)
            body, _ = self.comparisons._compute_scope(scope, scope, 'sampling')
            if body and _admitted(body):
                rows = {r['case_id']: r.get('left') for r in body['rows'] if r['case_id'] in search}
                values = [_number(v) for v in rows.values()]
                if set(rows) == search and all(v is not None for v in values):
                    seed = sum(values) / len(values)
        result = aggregate_ledger(tree, records, hypotheses, clusters, seed_search_mean=seed, sealed_score=sealed_score)
        self.store.write(iteration_id, result)
        return result
