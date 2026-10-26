"""Hypothesis cards with pre-registered per-case predictions.

An analyst Agent (or a person) drafts a card; only a person approves it. An
approved card is the only thing an implementer Agent is built from, through its
`implementer_brief`, which must not carry benchmark text. Verification checks
the predictions against a paired flip table; effect and cause are concluded
separately by a person. Every change is appended to the Task history.
"""
from __future__ import annotations

import json
import re

from .repository import Conflict, _text, now, uid

EXPECTATIONS = ('improve', 'unchanged', 'not_worse', 'worse')
EFFECTS = ('supported', 'not_observed', 'regression_or_tradeoff', 'insufficient')
CAUSES = ('supported', 'refuted', 'undecided')
# draft -> approved | rejected; approved -> implementing (implementer created)
# -> verified (predictions checked) -> concluded.
STATUSES = ('draft', 'approved', 'rejected', 'implementing', 'verified', 'concluded')
_REQUIRED = ('title', 'component', 'mechanism', 'predictions', 'falsification', 'implementer_brief')
_OPTIONAL = ('evidence', 'alternatives', 'expected_benefit', 'possible_harm', 'change_scope', 'contract', 'cluster_id')


def _str(value, name, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\0' in value:
        raise ValueError(f'Hypothesis field {name} must be non-empty text within {limit} characters')
    return value


def validate_card(card):
    """Closed card shape; returns a normalized copy."""
    if not isinstance(card, dict) or not set(_REQUIRED) <= set(card) or not set(card) <= set(_REQUIRED + _OPTIONAL):
        raise ValueError('Hypothesis card fields: required ' + ', '.join(_REQUIRED) + '; optional ' + ', '.join(_OPTIONAL))
    out = {key: _str(card[key], key) for key in ('title', 'component', 'mechanism', 'falsification', 'implementer_brief')}
    for key in ('expected_benefit', 'possible_harm'):
        if key in card:
            out[key] = _str(card[key], key)
    out['alternatives'] = [_str(v, 'alternatives', 1000) for v in card.get('alternatives', [])]
    out['change_scope'] = [_str(v, 'change_scope', 300) for v in card.get('change_scope', [])]
    out['evidence'] = []
    for item in card.get('evidence', []):
        if not isinstance(item, dict) or not {'run_id', 'case_id'} <= set(item) or not set(item) <= {'run_id', 'case_id', 'note'}:
            raise ValueError('Evidence items are {run_id, case_id, note?}')
        out['evidence'].append({k: _str(item[k], 'evidence.' + k, 1000) for k in item})
    predictions = card['predictions']
    # Orchestrated merge cards authorize only integration work, with no invented
    # benchmark prediction. They never enter LCD cluster selection or gate checks.
    integration_only = (card['component'] == 'integration' and 'contract' not in card and 'cluster_id' not in card)
    if not isinstance(predictions, list) or (not predictions and not integration_only) or len(predictions) > 200:
        raise ValueError('A card needs 1-200 per-case predictions')
    out['predictions'] = []
    for item in predictions:
        if (not isinstance(item, dict) or not {'case_id', 'expect'} <= set(item)
                or not set(item) <= {'case_id', 'expect', 'metric', 'note', 'guard'}
                or item['expect'] not in EXPECTATIONS or not isinstance(item.get('guard', False), bool)):
            raise ValueError('Predictions are {case_id, expect: ' + '|'.join(EXPECTATIONS) + ', metric?, note?, guard?}')
        out['predictions'].append({'case_id': _str(item['case_id'], 'case_id', 300), 'expect': item['expect'],
                                   'metric': _str(item['metric'], 'metric', 100) if 'metric' in item else None,
                                   'note': _str(item['note'], 'note', 1000) if 'note' in item else None,
                                   'guard': item.get('guard', False)})
    if 'cluster_id' in card:
        out['cluster_id'] = _str(card['cluster_id'], 'cluster_id', 64)
    if 'contract' in card:
        from .gate import validate_contract
        out['contract'] = validate_contract(card['contract'])
    from .clusters import referenced_cluster
    referenced_cluster(out)
    return out


def check_predictions(predictions, flips):
    """Compare each prediction with the paired flip row for its case."""
    rows = {row['case_id']: row for row in flips['rows']}
    results = []
    for p in predictions:
        row = rows.get(p['case_id'])
        delta = row.get('delta') if row else None
        if p['metric'] not in (None, flips.get('metric')):
            held, reason = None, f'flip table reports {flips.get("metric")}, prediction is on {p["metric"]}'
        elif delta is None:
            held, reason = None, 'case missing or unpaired in the flip table'
        else:
            held = {'improve': delta > 0, 'unchanged': delta == 0, 'not_worse': delta >= 0, 'worse': delta < 0}[p['expect']]
            reason = None
        results.append(dict(case_id=p['case_id'], expect=p['expect'], guard=p['guard'], delta=delta,
                            left=row.get('left') if row else None, right=row.get('right') if row else None,
                            held=held, reason=reason))
    decided = [r for r in results if r['held'] is not None]
    return dict(metric=flips.get('metric'), admission=flips.get('admission'), results=results,
                noise_label='无噪声带', held=sum(r['held'] for r in decided), decided=len(decided), total=len(results))


class HypothesisStore:
    def __init__(self, repository):
        self.repository = repository
        with repository.connect(write=True) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS hypotheses(
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                status TEXT NOT NULL, card TEXT NOT NULL, author TEXT NOT NULL,
                decision TEXT, verification TEXT, conclusion TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
            # Iteration proposals name the evaluated candidate they hang under.
            if 'parent_candidate_id' not in {r['name'] for r in conn.execute('PRAGMA table_info(hypotheses)')}:
                conn.execute('ALTER TABLE hypotheses ADD COLUMN parent_candidate_id TEXT')

    @staticmethod
    def _record(row):
        record = dict(row)
        for key in ('card', 'decision', 'verification', 'conclusion'):
            record[key] = json.loads(record[key]) if record[key] is not None else None
        return record

    def _get(self, conn, task_id, hypothesis_id):
        row = conn.execute('SELECT * FROM hypotheses WHERE id=? AND task_id=?', (hypothesis_id, task_id)).fetchone()
        if row is None:
            raise ValueError('Hypothesis card is not in this task')
        return self._record(row)

    def list(self, task_id):
        with self.repository.connect() as conn:
            self.repository._require(conn, 'tasks', task_id)
            return [self._record(r) for r in conn.execute(
                'SELECT * FROM hypotheses WHERE task_id=? ORDER BY created_at,id', (task_id,))]

    def get(self, task_id, hypothesis_id):
        with self.repository.connect() as conn:
            return self._get(conn, task_id, hypothesis_id)

    def draft(self, task_id, card, *, author, parent_candidate_id=None):
        card = validate_card(card)
        author = _text(author, 'author', limit=200)
        from .splits import Policy
        policy = Policy(self.repository, task_id)
        ids = [p['case_id'] for p in card['predictions']] + [e['case_id'] for e in card['evidence']]
        ids += card.get('contract', {}).get('predicted_flips', []) + card.get('contract', {}).get('guards', [])
        for cid in ids:
            policy.refuse_sealed(cid)
        from .clusters import ClusterStore, referenced_cluster
        cluster_id = referenced_cluster(card)
        if cluster_id:
            cluster = ClusterStore(self.repository, initialize=False).get(task_id, cluster_id)
            if not set(card.get('contract', {}).get('predicted_flips', [])) <= set(cluster['scan']['members']):
                raise ValueError('Contract predicted_flips must be cluster members')
        with self.repository.connect(write=True) as conn:
            self.repository._require(conn, 'tasks', task_id)
            record = dict(id=uid(), task_id=task_id, status='draft', card=json.dumps(card, ensure_ascii=False),
                          author=author, created_at=now(), updated_at=now(), parent_candidate_id=parent_candidate_id)
            conn.execute('INSERT INTO hypotheses(id,task_id,status,card,author,created_at,updated_at,parent_candidate_id) '
                         'VALUES(:id,:task_id,:status,:card,:author,:created_at,:updated_at,:parent_candidate_id)', record)
            self.repository._event(conn, task_id, 'hypothesis.drafted', record['id'],
                                   {'title': card['title'], 'author': author, 'predictions': len(card['predictions']),
                                    **({'parent_candidate_id': parent_candidate_id} if parent_candidate_id else {})})
            return self._get(conn, task_id, record['id'])

    def decide(self, task_id, hypothesis_id, decision, *, actor, note=''):
        if decision not in ('approve', 'reject'):
            raise ValueError('Decision is approve or reject')
        with self.repository.connect(write=True) as conn:
            record = self._get(conn, task_id, hypothesis_id)
            if record['status'] != 'draft':
                raise Conflict('Only a draft card can be approved or rejected')
            status = 'approved' if decision == 'approve' else 'rejected'
            body = dict(decision=decision, actor=actor, note=note, at=now())
            conn.execute('UPDATE hypotheses SET status=?, decision=?, updated_at=? WHERE id=?',
                         (status, json.dumps(body, ensure_ascii=False), now(), hypothesis_id))
            self.repository._event(conn, task_id, 'hypothesis.decided', hypothesis_id, body)
            return self._get(conn, task_id, hypothesis_id)

    def record_check(self, task_id, hypothesis_id, *, baseline_run, candidate_run, flips, leak, actor,
                     comparison_id=None):
        with self.repository.connect(write=True) as conn:
            record = self._get(conn, task_id, hypothesis_id)
            if record['status'] not in ('implementing', 'verified', 'approved'):
                raise Conflict('Predictions are checked after the card is approved')
            contract = record['card'].get('contract')
            if not contract and flips.get('admission') != 'admitted':
                raise ValueError('The two runs are not admitted as a pair: ' + str(flips.get('note')))
            body = dict(baseline_run=baseline_run, candidate_run=candidate_run, comparison_id=comparison_id,
                        actor=actor, at=now(),
                        counts=flips.get('counts'), leak=leak, **check_predictions(record['card']['predictions'], flips))
            if contract:
                from .gate import judge
                row = conn.execute('SELECT * FROM candidate_comparisons WHERE id=?', (comparison_id,)).fetchone()
                if row is None:
                    raise ValueError('A contract check needs a frozen candidate comparison')
                candidate = conn.execute('SELECT * FROM candidates WHERE id=?', (row['candidate_id'],)).fetchone()
                iteration = conn.execute('SELECT * FROM iterations WHERE id=?', (row['iteration_id'],)).fetchone()
                if (candidate['proposal_id'] != hypothesis_id or iteration['task_id'] != task_id
                        or row['kind'] != 'parent' or candidate['parent_candidate_id'] != row['reference_candidate_id']):
                    raise ValueError('Comparison does not check this proposal against its parent')
                comparison = json.loads(row['record'])
                protocol = json.loads(iteration['protocol_json']) or {}
                from .gate import DEFAULT_RULE
                verdict = json.loads(row['verdict_json']) if row['verdict_json'] else judge(
                    comparison, comparison.get('noise') or {}, contract, protocol.get('gate_rule', DEFAULT_RULE))
                if not row['verdict_json']:
                    conn.execute('UPDATE candidate_comparisons SET verdict_json=? WHERE id=?',
                                 (json.dumps(verdict), comparison_id))
                    previous = json.loads(candidate['decision_json']) if candidate['decision_json'] else None
                    decision = dict(decision={'accept': 'adopted', 'reject': 'rejected'}.get(verdict['verdict'], 'undecided'),
                                    actor='gate', at=now(), note='', comparison_id=comparison_id,
                                    gate_verdict=verdict, previous=previous)
                    conn.execute('UPDATE candidates SET decision_json=?,updated_at=? WHERE id=?',
                                 (json.dumps(decision), now(), candidate['id']))
                    self.repository._event(conn, task_id, 'candidate.decided', candidate['id'], decision)
                body['verdict'] = verdict
                body.pop('noise_label', None)
            conn.execute("UPDATE hypotheses SET status='verified', verification=?, updated_at=? WHERE id=?",
                         (json.dumps(body, ensure_ascii=False), now(), hypothesis_id))
            self.repository._event(conn, task_id, 'hypothesis.checked', hypothesis_id,
                                   {k: body[k] for k in ('baseline_run', 'candidate_run', 'held', 'decided', 'total', 'counts')})
            return self._get(conn, task_id, hypothesis_id)

    def conclude(self, task_id, hypothesis_id, *, effect, cause, note, actor):
        if effect not in EFFECTS or cause not in CAUSES:
            raise ValueError('effect: ' + '|'.join(EFFECTS) + '; cause: ' + '|'.join(CAUSES))
        with self.repository.connect(write=True) as conn:
            record = self._get(conn, task_id, hypothesis_id)
            if record['status'] != 'verified':
                raise Conflict('Conclude after predictions are checked against a paired run')
            body = dict(effect=effect, cause=cause, note=note, actor=actor, at=now())
            conn.execute("UPDATE hypotheses SET status='concluded', conclusion=?, updated_at=? WHERE id=?",
                         (json.dumps(body, ensure_ascii=False), now(), hypothesis_id))
            self.repository._event(conn, task_id, 'hypothesis.concluded', hypothesis_id, body)
            return self._get(conn, task_id, hypothesis_id)


# ---------------------------------------------------------------- leak scan
def _norm(text):
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', text.lower())).strip()


def _shingles(text, n=8):
    words = _norm(text).split()
    return {' '.join(words[i:i + n]) for i in range(len(words) - n + 1)}


def benchmark_material(run_views, run_ids):
    """Questions, gold answers/evidence and case ids of the given runs' bundles."""
    questions, shingles, case_ids = {}, {}, set()
    for run_id in run_ids:
        bundle = run_views._load(run_id)['_bundle'] or {}
        for case in bundle.get('cases', []):
            case_ids.add(case['case_id'])
            question = _norm(case.get('question') or '')
            if len(question) >= 24:
                questions[question] = case['case_id']
            texts = list(case.get('references') or [])
            for annotation in (case.get('gold') or {}).get('annotations', []):
                texts.append(annotation.get('answer') or '')
                texts.extend(annotation.get('evidence') or [])
            for text in texts:
                for shingle in _shingles(text):
                    shingles.setdefault(shingle, case['case_id'])
    return questions, shingles, case_ids


def scan_text(text, material):
    """Hits name the case and the kind of overlap, never the benchmark text."""
    questions, shingles, case_ids = material
    norm = _norm(text)
    hits = [dict(kind='question', case_id=c) for q, c in questions.items() if q in norm]
    found = {shingles[s] for s in _shingles(text) if s in shingles}
    hits += [dict(kind='gold_or_reference_8gram', case_id=c) for c in sorted(found)]
    hits += [dict(kind='case_id', case_id=c) for c in sorted(case_ids)
             if c in text or c.split(':')[-1][:16] in text]
    return hits


def leak_check(run_views, task_id, run_ids, extra_texts=(), *, hypothesis_id=None, candidate_id=None):
    """Scan added diff lines in the Task worktree (and given texts) for benchmark material."""
    material = benchmark_material(run_views, run_ids)
    findings = []
    found = None
    if candidate_id is not None:
        from .iterations import IterationService
        service = run_views._worktree_service
        if service is None:
            raise ValueError('Candidate worktree service unavailable')
        view = IterationService(service.repository, service).view(candidate_id)
        if view.task_id != task_id:
            raise ValueError('Candidate belongs to another iteration Task')
        if view.binding(task_id) is not None:
            found = (view.read(task_id), 'candidate_worktree')
    elif hypothesis_id is not None:
        from .hypothesis_worktrees import HypothesisWorktreeService
        service = run_views._worktree_service
        if service is None:
            raise ValueError('Hypothesis worktree service unavailable')
        HypothesisStore(service.repository).get(task_id, hypothesis_id)
        trees = HypothesisWorktreeService(service.repository, service)
        view = trees.view(task_id, hypothesis_id)
        if view.binding(task_id) is not None:
            found = (view.read(task_id), 'hypothesis_worktree')
    if found is None:
        found = run_views._binding_read(task_id)
    files = found[0].get('files', []) if found else []
    for file in files:
        added = '\n'.join(line[1:] for line in (file.get('diff') or '').splitlines()
                          if line.startswith('+') and not line.startswith('+++'))
        for hit in scan_text(added, material):
            findings.append(dict(hit, source=file.get('path')))
    for name, text in extra_texts:
        for hit in scan_text(text, material):
            findings.append(dict(hit, source=name))
    return dict(status='suspect' if findings else 'clean', checked_files=len(files), runs=list(run_ids),
                findings=findings, at=now(),
                note='overlap with questions, gold answers/evidence (8-word) or case ids of these runs; not proof of misuse')
