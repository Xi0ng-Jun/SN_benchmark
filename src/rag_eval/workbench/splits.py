"""Frozen paper splits, consumed fresh batches and analyst visibility."""
from __future__ import annotations

import json
import random

from .repository import Conflict, now


def partition(partitions, selected, *, sealed=(), seed=0, batches=0, papers_per_batch=8):
    """Split selected papers 60/40 and draw disjoint whole fresh papers."""
    if type(seed) is not int or type(batches) is not int or not 0 <= batches <= 100:
        raise ValueError('Invalid split seed or fresh batch count')
    if type(papers_per_batch) is not int or not 1 <= papers_per_batch <= 100:
        raise ValueError('Invalid fresh paper count')
    owned = {c: p['partition_id'] for p in partitions for c in p['case_ids']}
    selected, sealed = set(selected), set(sealed)
    if not selected or not selected | sealed <= owned.keys():
        raise ValueError('Split cases are outside the registered bundle')
    papers = {owned[c] for c in selected}
    sealed_papers = {owned[c] for c in sealed}
    if papers & sealed_papers:
        raise ValueError('Selection overlaps sealed papers')
    rng = random.Random(seed)
    ordered = sorted(papers)
    rng.shuffle(ordered)
    count = min(len(ordered) - 1, max(1, round(len(ordered) * .6))) if len(ordered) > 1 else 1
    search_papers = set(ordered[:count])
    search = sorted(c for c in selected if owned[c] in search_papers)
    validation = sorted(selected - set(search))
    unused = sorted({p['partition_id'] for p in partitions} - papers - sealed_papers)
    rng.shuffle(unused)
    if len(unused) < batches * papers_per_batch:
        raise ValueError('Not enough disjoint papers for fresh batches')
    fresh = [sorted(c for c, p in owned.items() if p in unused[i * papers_per_batch:(i + 1) * papers_per_batch])
             for i in range(batches)]
    return dict(search=search, validation=validation, batches=fresh)


def add_roles(selection, roles, partitions=None):
    """Add roles without replacing case identities or weakening selection hashing."""
    from ..identity import fingerprint
    if (not isinstance(roles, dict) or set(roles) != set(selection['case_ids'])
            or any(r not in ('search', 'validation') for r in roles.values())):
        raise ValueError('Roles must cover precisely the selected cases')
    if partitions is not None:
        for p in partitions:
            if len({roles[c] for c in p['case_ids'] if c in roles}) > 1:
                raise ValueError('One paper cannot have both search and validation roles')
    selection['roles'] = dict(roles)
    selection['selection_hash'] = fingerprint({k: v for k, v in selection.items() if k != 'selection_hash'})
    return selection


def initialize(repository):
    with repository.connect(write=True) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS iteration_fresh_batches(
            iteration_id TEXT NOT NULL, batch_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
            request_key TEXT NOT NULL, used_at TEXT NOT NULL, completed_at TEXT, comparison_id TEXT,
            PRIMARY KEY(iteration_id,batch_id), UNIQUE(iteration_id,request_key))''')
        columns = {r['name'] for r in conn.execute('PRAGMA table_info(candidate_attempts)')}
        if columns and 'batch_id' not in columns:
            conn.execute('ALTER TABLE candidate_attempts ADD COLUMN batch_id TEXT')


def batches(repository, iteration_id, *, completed=False):
    with repository.connect() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='iteration_fresh_batches'").fetchone():
            return []
        return [dict(r) for r in conn.execute('SELECT * FROM iteration_fresh_batches WHERE iteration_id=?' +
            (' AND completed_at IS NOT NULL' if completed else '') + ' ORDER BY used_at,batch_id', (iteration_id,))]


def claim(repository, iteration, candidate_id, request_key):
    """Consume before dispatch; the same request resumes, failures never release a batch."""
    with repository.connect(write=True) as conn:
        rows = [dict(r) for r in conn.execute('SELECT * FROM iteration_fresh_batches WHERE iteration_id=?', (iteration['id'],))]
        prior = next((r for r in rows if r['request_key'] == request_key), None)
        if prior:
            if prior['candidate_id'] != candidate_id:
                raise Conflict('Fresh request belongs to another candidate')
            return prior
        used = {r['batch_id'] for r in rows}
        batch = next((b for b in iteration['protocol'].get('fresh_batches', []) if b['batch_id'] not in used), None)
        if batch is None:
            raise Conflict('No unused fresh batch remains')
        row = dict(iteration_id=iteration['id'], batch_id=batch['batch_id'], candidate_id=candidate_id,
                   request_key=request_key, used_at=now(), completed_at=None, comparison_id=None)
        conn.execute('INSERT INTO iteration_fresh_batches VALUES(:iteration_id,:batch_id,:candidate_id,:request_key,'
                     ':used_at,:completed_at,:comparison_id)', row)
        repository._event(conn, iteration['task_id'], 'iteration.fresh.used', candidate_id, row)
        return row


def roles(protocol, completed=()):
    split = (protocol or {}).get('splits')
    if not split:
        return None
    out = {c: role for role in ('search', 'validation') for c in split[role]}
    for b in protocol.get('fresh_batches', []):
        if b['batch_id'] in completed:
            out.update({c: 'validation' for c in b['case_ids']})
    return out


class Policy:
    """Project analyst outputs at the delivery boundary, never stored measurements."""
    def __init__(self, repository, task_id):
        self.search, self.hidden, self.sealed = None, set(), set()
        with repository.connect() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='iterations'").fetchone():
                return
            for row in conn.execute('SELECT task_id,protocol_json FROM iterations'):
                protocol = json.loads(row['protocol_json']) if row['protocol_json'] else {}
                split = (protocol or {}).get('splits')
                if not split:
                    continue
                self.sealed.update(split.get('sealed', []))
                if row['task_id'] == task_id:
                    self.search = set(split['search'])
                    self.hidden.update(split['validation'])
                    self.hidden.update(c for b in protocol.get('fresh_batches', []) for c in b['case_ids'])
        self.hidden |= self.sealed

    def require_search(self, case_id):
        if case_id in self.sealed:
            raise ValueError('sealed cases are unavailable')
        if self.search is not None and case_id not in self.search:
            raise ValueError('Only search cases are available to analysts')

    def refuse_sealed(self, case_id):
        if case_id in self.sealed:
            raise ValueError('sealed cases are unavailable')

    def project(self, value):
        """Remove hidden rows, keyed case maps, identities and artifact escape paths."""
        hidden = self.hidden
        def visit(v):
            if isinstance(v, list):
                return [visit(x) for x in v if not (isinstance(x, dict) and 'case_id' in x and
                        (x['case_id'] in hidden or (self.search is not None and x['case_id'] not in self.search)))
                        and not (isinstance(x, str) and x in hidden)]
            if isinstance(v, dict):
                out = {k: visit(x) for k, x in v.items() if k not in hidden
                       and not (self.search is not None and k in ('run_dir', 'artifacts', 'bundle_ref', 'index_ref', 'sealed'))}
                if self.search is not None:
                    for key in ('cases', 'deltas', 'roles'):
                        if isinstance(v.get(key), dict):
                            out[key] = {cid: visit(x) for cid, x in v[key].items() if cid in self.search}
                    for key in ('case_ids', 'regressed', 'attention', 'predicted_flips', 'guards', 'validation_pool'):
                        if isinstance(v.get(key), list) and all(isinstance(x, str) for x in v[key]):
                            out[key] = [x for x in v[key] if x in self.search]
                    visible = out.get('rows') if isinstance(out.get('rows'), list) else out.get('cases')
                    def mean(values):
                        values = [x for x in values if isinstance(x, (int, float))]
                        return sum(values) / len(values) if values else None
                    if isinstance(visible, list):
                        if 'counts' in out:
                            out['counts'] = {k: sum(r.get('flip') == k for r in visible) for k in out['counts']}
                        if isinstance(out.get('mean'), dict):
                            out['mean'] = {k: mean(r.get(field) for r in visible) for k, field in
                                           (('reference', 'left'), ('subject', 'right'), ('delta', 'delta'))}
                        elif 'mean' in out:
                            out['mean'] = mean(r.get('value') for r in visible)
                        if 'total' in out:
                            out['total'] = len(visible)
                        if isinstance(out.get('cases'), int):
                            out['cases'] = len(visible)
                    elif 'counts' in out:
                        out.pop('counts')
                    if isinstance(out.get('cases'), dict) and 'eta' in out:
                        from .noise import summarize_cases
                        out.update(summarize_cases(out['cases']))
                    aggregate = v.get('validation')
                    if isinstance(aggregate, dict):
                        out['validation'] = dict(n=aggregate.get('n'), mean={k: aggregate.get(k) for k in
                            ('reference', 'subject', 'delta')}, eta=aggregate.get('eta'))
                    else:
                        rows = v.get('rows') if isinstance(v.get('rows'), list) else v.get('cases')
                        if isinstance(rows, list):
                            withheld = [r for r in rows if isinstance(r, dict) and r.get('case_id') in hidden - self.sealed]
                            if withheld:
                                metric = v.get('metric')
                                values = [r.get('value', (r.get('official', {}).get('metrics') or {}).get(metric)) for r in withheld]
                                values = [x for x in values if isinstance(x, (int, float))]
                                out['validation'] = dict(n=len(withheld), mean=sum(values)/len(values) if values else None, eta=None)
                return out
            if isinstance(v, str):
                for cid in sorted(hidden, key=len, reverse=True):
                    v = v.replace(cid, '[withheld]')
            return v
        return visit(value)


def analyst_projection(repository, task_id, value):
    return Policy(repository, task_id).project(value)
