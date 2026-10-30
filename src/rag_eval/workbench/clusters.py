"""Mechanical search clusters and frozen, explicitly projected member evidence."""
from __future__ import annotations

from copy import deepcopy
import json
import math
import re

from .repository import Conflict, UnknownIdentity, _text, now

FIELDS = frozenset({'status', 'prediction', 'response.answer', 'response.grounded', 'response.evidence_level',
    'intent_preview.needs_clarification', 'deterministic.evidence_hit', 'deterministic.citation_valid_count',
    'deterministic.citation_invalid_count', 'stage_metrics.gold_evidence_recall.value',
    'stage_metrics.context_precision.value', 'stage_metrics.citation_precision.value', 'usage.total_tokens'})
DEFAULT_MISSING = dict(kind='range', field='stage_metrics.gold_evidence_recall.value', min=0, max=0)
DEFAULT_VIOLATION = {'any_of': [dict(kind='range', field='deterministic.citation_invalid_count', min=1),
    {'all_of': [dict(kind='regex', field='response.answer', pattern='(?i)unanswerable|not (stated|mentioned)'),
                dict(kind='range', field='deterministic.evidence_hit', min=1, max=1)]}]}


def validate_signature(signature, primary='answer_f1', *, _depth=0):
    """Closed JSON predicates: regex/range, {all_of:[]}, {any_of:[]}, {not:{}}."""
    if not isinstance(signature, dict) or _depth > 8:
        raise ValueError('Invalid or excessively nested cluster signature')
    for key in ('all_of', 'any_of', 'not'):
        if key in signature:
            if set(signature) != {key}:
                raise ValueError('A signature has exactly one operator')
            children = [signature[key]] if key == 'not' else signature[key]
            if not isinstance(children, list) or not 1 <= len(children) <= 32:
                raise ValueError('Combinators require 1-32 signatures')
            for child in children:
                validate_signature(child, primary, _depth=_depth + 1)
            return deepcopy(signature)
    kind, field = signature.get('kind'), signature.get('field')
    if field not in FIELDS | {f'metrics.{primary}', 'retrieval_context.*.text'}:
        raise ValueError('Signature field is not whitelisted')
    if kind == 'regex':
        if set(signature) != {'kind', 'field', 'pattern'} or type(signature['pattern']) is not str or not 1 <= len(signature['pattern']) <= 512:
            raise ValueError('Regex requires a bounded pattern')
        try:
            re.compile(signature['pattern'])
        except re.error as exc:
            raise ValueError('Invalid signature regex') from exc
    elif kind == 'range':
        if (field == 'retrieval_context.*.text' or set(signature) - {'kind', 'field', 'min', 'max'}
                or not {'min', 'max'} & set(signature)):
            raise ValueError('Range requires a numeric bound and a scalar field')
        for key in ('min', 'max'):
            if key in signature and (type(signature[key]) not in (int, float) or not math.isfinite(signature[key])):
                raise ValueError('Range bounds must be finite numbers')
        if signature.get('min', -math.inf) > signature.get('max', math.inf):
            raise ValueError('Range bounds are reversed')
    else:
        raise ValueError('Unknown signature operator')
    return deepcopy(signature)


def _matches(signature, observation):
    if 'all_of' in signature:
        return all(_matches(s, observation) for s in signature['all_of'])
    if 'any_of' in signature:
        return any(_matches(s, observation) for s in signature['any_of'])
    if 'not' in signature:
        return not _matches(signature['not'], observation)
    field = signature['field']
    if field == 'retrieval_context.*.text':
        return any(re.search(signature['pattern'], b.get('text', '')) is not None for b in observation.get('retrieval_context', []))
    value = observation
    for part in field.split('.'):
        value = value.get(part) if isinstance(value, dict) else None
    if signature['kind'] == 'regex':
        return isinstance(value, str) and re.search(signature['pattern'], value) is not None
    return (isinstance(value, (int, float)) and math.isfinite(value)
            and signature.get('min', -math.inf) <= value <= signature.get('max', math.inf))


def matches(signature, observation, primary='answer_f1'):
    return _matches(validate_signature(signature, primary), observation)


def _observable(signature, observation):
    """Attribution is unknown when any required input was not measured."""
    for operator in ('all_of', 'any_of', 'not'):
        if operator in signature:
            children = [signature[operator]] if operator == 'not' else signature[operator]
            return all(_observable(child, observation) for child in children)
    field = signature['field']
    if field == 'retrieval_context.*.text':
        return isinstance(observation.get('retrieval_context'), list)
    value = observation
    for part in field.split('.'):
        value = value.get(part) if isinstance(value, dict) else None
    return value is not None


def scan(iteration, cluster, executions):
    """Union membership across attempts; score unknowns remain unknown, never zero."""
    protocol = iteration.get('protocol') or {}
    primary = cluster.get('primary', 'answer_f1')
    signature = validate_signature(cluster['signature'], primary)
    missing = validate_signature(cluster.get('missing_signature', DEFAULT_MISSING), primary)
    violation = validate_signature(cluster.get('violation_signature', DEFAULT_VIOLATION), primary)
    search = set(protocol.get('splits', {}).get('search', []))
    if 'splits' not in protocol:
        search = {c['case_id'] for e in executions for c in e['cases']}
    threshold = protocol.get('success_threshold', .5)
    observations = [{r['case_id']: r for r in e['cases'] if r['case_id'] in search} for e in executions]
    observations = [{cid: rows.get(cid, dict(case_id=cid, status='missing', metrics={})) for cid in search}
                    for rows in observations]
    members = sorted({cid for rows in observations for cid, row in rows.items() if _matches(signature, row)})
    saved = {cid: [deepcopy(rows.get(cid, dict(case_id=cid, status='missing', metrics={}))) for rows in observations] for cid in members}
    success = failed = unknown = missing_count = violation_count = 0
    missing_unknown = violation_unknown = False
    for rows in saved.values():
        for row in rows:
            value = row.get('metrics', {}).get(primary)
            if row.get('status') in ('error', 'missing'):
                is_failed = True
            elif value is None:
                unknown += 1
                continue
            elif type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError('Primary scores must be finite in [0, 1] or unknown')
            else:
                is_failed = value < threshold
            if is_failed:
                failed += 1
                missing_unknown |= not _observable(missing, row)
                violation_unknown |= not _observable(violation, row)
                missing_count += _matches(missing, row)
                violation_count += _matches(violation, row)
            else:
                success += 1
    total = len(members) * len(executions)
    q = success / total if total and not unknown else None
    m = missing_count / failed if failed and not missing_unknown else None
    v = violation_count / failed if failed and not violation_unknown else None
    return dict(members=members, n_cases=len(members), n_attempts=total, n_failed=failed, n_unknown=unknown,
                n_success=success, n_missing=missing_count, n_violation=violation_count, q=q, m=m, v=v,
                retrieval_ceiling=(1-q)*m if q is not None and m is not None else 0 if q == 1 else None,
                verification_ceiling=(1-q)*v if q is not None and v is not None else 0 if q == 1 else None,
                observations=saved, primary=primary, success_threshold=threshold,
                executions=[e['execution_id'] for e in executions])


def source_measurements(candidate):
    """The source's terminal base measurements: the executions a cluster is scanned over."""
    from .comparisons import ComparisonService
    return ComparisonService.terminal(ComparisonService.scoped(candidate, None))


def search_observations(views, measurements, search):
    """Observed search rows per execution, in measurement order (``scan`` input)."""
    return [dict(execution_id=m['execution_id'], cases=[views.observation(r.id, cid)
                 for r in views.execution_runs(m['execution_id']) for cid in r.case_ids if cid in search])
            for m in measurements]


def _value(observation, field):
    value = observation
    for part in field.split('.'):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _clip(value, limit):
    return value[:limit] + '…' if isinstance(value, str) and len(value) > limit else value


EVIDENCE_COLUMNS = ('question', 'status', 'metrics.<primary>', 'prediction', 'response.grounded', 'response.evidence_level',
    'intent_preview.needs_clarification', 'deterministic.evidence_hit', 'deterministic.citation_valid_count',
    'deterministic.citation_invalid_count', 'stage_metrics.gold_evidence_recall.value',
    'stage_metrics.context_precision.value', 'stage_metrics.citation_precision.value', 'usage.total_tokens', 'contexts')
# Opt-in through --fields: usually the same text as prediction.
EVIDENCE_OPTIONAL = ('response.answer',)


def evidence(executions, primary, threshold, *, columns=None, failed_only=False, answer_chars=300, question_chars=200):
    """A compact per-case table over the same observations a cluster scan reads (no annotations).

    One row per case: its attempts (one per execution, in order) as value lists in ``columns`` order,
    the mean primary score over measured attempts, and whether any attempt failed under the scan's
    rule (error, missing, or primary below the success threshold).
    """
    name = lambda column: f'metrics.{primary}' if column == 'metrics.<primary>' else column
    allowed = [name(c) for c in EVIDENCE_COLUMNS + EVIDENCE_OPTIONAL]
    chosen = list(EVIDENCE_COLUMNS) if columns is None else list(dict.fromkeys(columns))
    if unknown := [c for c in chosen if name(c) not in allowed]:
        raise ValueError('Unknown evidence fields: ' + ', '.join(unknown) + '; choose from ' + ', '.join(allowed))
    chosen = ['metrics.<primary>' if c == f'metrics.{primary}' else c for c in chosen]
    with_question = 'question' in chosen
    attempt_columns = [c for c in chosen if c != 'question']
    by_case = {}
    for index, execution in enumerate(executions):
        for row in execution['cases']:
            by_case.setdefault(row['case_id'], {})[index] = row
    rows = []
    for cid in sorted(by_case):
        attempts, values, failed, question = [], [], False, None
        for index in range(len(executions)):
            obs = by_case[cid].get(index) or dict(case_id=cid, status='missing', metrics={})
            question = question or obs.get('question')
            value = (obs.get('metrics') or {}).get(primary)
            value = value if type(value) in (int, float) and math.isfinite(value) else None
            if obs.get('status') in ('error', 'missing'):
                failed = True
            elif value is not None:
                failed |= value < threshold
            if value is not None:
                values.append(value)
            cells = []
            for column in attempt_columns:
                if column == 'metrics.<primary>':
                    cells.append(value)
                elif column == 'contexts':
                    blocks = obs.get('retrieval_context')
                    cells.append(len(blocks) if isinstance(blocks, list) else None)
                elif column in ('prediction', 'response.answer'):
                    cells.append(_clip(_value(obs, column), answer_chars))
                else:
                    cells.append(_value(obs, column))
            attempts.append(cells)
        if failed_only and not failed:
            continue
        row = dict(case_id=cid, mean=sum(values) / len(values) if values else None, failed=failed)
        if with_question:
            row['question'] = _clip(question, question_chars)
        row['attempts'] = attempts
        rows.append(row)
    return dict(columns=[name(c) for c in attempt_columns], cases=rows)


def initialize_clusters(repository):
    with repository.connect(write=True) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS clusters(
            iteration_id TEXT NOT NULL, cluster_id TEXT NOT NULL, description TEXT NOT NULL,
            signature_json TEXT NOT NULL, missing_signature_json TEXT NOT NULL, violation_signature_json TEXT NOT NULL,
            frozen_from_execution TEXT NOT NULL, proposer TEXT NOT NULL, frozen_at TEXT NOT NULL, scan_json TEXT NOT NULL,
            PRIMARY KEY(iteration_id,cluster_id))''')


def _record(row):
    out = dict(row)
    for key in ('signature', 'missing_signature', 'violation_signature', 'scan'):
        out[key] = json.loads(out.pop(key + '_json'))
    out['frozen_from_execution'] = json.loads(out['frozen_from_execution'])
    return out


def referenced_cluster(card):
    top, contract = card.get('cluster_id'), (card.get('contract') or {}).get('cluster_id')
    if top and contract and top != contract:
        raise ValueError('Card and contract must reference the same cluster')
    return top or contract


def candidate_cluster(repository, task_id, candidate_id):
    """Read ownership without initializing services inside a message transaction."""
    with repository.connect() as conn:
        row = conn.execute('SELECT h.card FROM candidates c JOIN iterations i ON i.id=c.iteration_id '
            'JOIN hypotheses h ON h.id=c.proposal_id WHERE c.id=? AND i.task_id=?', (candidate_id, task_id)).fetchone()
    if row is None:
        raise ValueError('Implementer needs its own candidate proposal')
    return referenced_cluster(json.loads(row['card']))


def member_evidence(record):
    """Only observed outputs, question, numeric diagnostics and primary score; no annotations."""
    result = []
    primary = record['scan']['primary']
    ids = record['scan']['members']
    def text(value):
        if not isinstance(value, str):
            return None
        for cid in sorted(record['scan'].get('redaction_case_ids', ids), key=len, reverse=True):
            value = value.replace(cid, '[case]')
        return value
    def number(value):
        return value if type(value) in (int, float) and math.isfinite(value) else None
    for index, cid in enumerate(ids, 1):
        observations = record['scan']['observations'][cid]
        attempts = []
        for i, obs in enumerate(observations, 1):
            attempts.append(dict(attempt=i, status=text(obs.get('status')), sn_answer=text(obs.get('prediction')),
                context_blocks=[dict(text=text(b.get('text'))) for b in obs.get('retrieval_context', [])],
                stage_metrics={k: dict(value=number((obs.get('stage_metrics', {}).get(k) or {}).get('value')))
                    for k in ('gold_evidence_recall', 'context_precision', 'citation_precision')},
                official_primary=dict(metric=primary, value=number(obs.get('metrics', {}).get(primary)))))
        result.append(dict(member=f'm{index:02d}', question=next((text(r.get('question')) for r in observations if r.get('question')), None),
                           attempts=attempts))
    return result


class ClusterStore:
    def __init__(self, repository, iterations=None, run_views=None, *, initialize=True):
        self.repository, self.iterations, self.views = repository, iterations, run_views
        if initialize:
            initialize_clusters(repository)

    def get(self, task_id, cluster_id):
        with self.repository.connect() as conn:
            row = conn.execute('SELECT c.* FROM clusters c JOIN iterations i ON i.id=c.iteration_id '
                               'WHERE i.task_id=? AND c.cluster_id=?', (task_id, cluster_id)).fetchone()
        if row is None:
            raise UnknownIdentity('Unknown cluster in this task')
        record = _record(row)
        from .splits import Policy
        policy = Policy(self.repository, task_id)
        for cid in record['scan']['members']:
            policy.refuse_sealed(cid)
        return record

    def list(self, task_id, *, implementer_candidate=None):
        if implementer_candidate is not None:
            cluster_id = candidate_cluster(self.repository, task_id, implementer_candidate)
            return [self.show(task_id, cluster_id, implementer_candidate=implementer_candidate)] if cluster_id else []
        with self.repository.connect() as conn:
            ids = [r[0] for r in conn.execute('SELECT c.cluster_id FROM clusters c JOIN iterations i ON i.id=c.iteration_id '
                'WHERE i.task_id=? ORDER BY c.frozen_at,c.cluster_id', (task_id,))]
        return [self.show(task_id, cid) for cid in ids]

    def show(self, task_id, cluster_id, *, implementer_candidate=None):
        if implementer_candidate is not None and candidate_cluster(self.repository, task_id, implementer_candidate) != cluster_id:
            raise ValueError('Implementer may read only its own cluster')
        record = self.get(task_id, cluster_id)
        out = {k: record[k] for k in ('cluster_id', 'description', 'signature', 'missing_signature', 'violation_signature')}
        out.update({k: v for k, v in record['scan'].items() if k not in ('observations', 'executions', 'members', 'redaction_case_ids')})
        out['members'] = member_evidence(record) if implementer_candidate is not None else record['scan']['members']
        if implementer_candidate is None:
            out.update(frozen_from_execution=record['frozen_from_execution'], frozen_at=record['frozen_at'], proposer=record['proposer'])
        return out

    def _evaluate(self, candidate_id, card):
        """Validate a cluster card and scan the source's search observations; writes nothing."""
        from .hypotheses import benchmark_material, scan_text
        from .run_views import PRIMARY_METRIC
        if not isinstance(card, dict) or not {'cluster_id', 'description', 'signature'} <= set(card) or set(card) - {
                'cluster_id', 'description', 'signature', 'missing_signature', 'violation_signature'}:
            raise ValueError('Cluster needs cluster_id, description, signature and optional attribution signatures')
        if type(card['cluster_id']) is not str or re.fullmatch('[A-Za-z][A-Za-z0-9_-]{0,63}', card['cluster_id']) is None:
            raise ValueError('Invalid cluster identity')
        description = _text(card['description'], 'cluster description', limit=4000)
        candidate = self.iterations.candidate(candidate_id)
        iteration = self.iterations.iteration(candidate['iteration_id'])
        protocol = iteration.get('protocol') or {}
        if 'splits' not in protocol or (protocol.get('input') or {}).get('suite') not in PRIMARY_METRIC:
            raise ValueError('Clusters require a frozen search protocol and a supported primary metric')
        primary = PRIMARY_METRIC[protocol['input']['suite']]
        normalized = dict(card, primary=primary)
        for key, default in (('signature', None), ('missing_signature', DEFAULT_MISSING), ('violation_signature', DEFAULT_VIOLATION)):
            normalized[key] = validate_signature(card.get(key, default), primary)
        measurements = source_measurements(candidate)
        if not measurements:
            raise ValueError('Clusters require terminal source executions')
        run_ids = [r.id for m in measurements for r in self.views.execution_runs(m['execution_id'])]
        material = benchmark_material(self.views, run_ids)
        if scan_text(json.dumps({k: normalized[k] for k in ('description', 'signature', 'missing_signature', 'violation_signature')}), material):
            raise ValueError('Cluster description or signature overlaps benchmark text')
        search = set(iteration['protocol']['splits']['search'])
        scanned = scan(iteration, normalized, search_observations(self.views, measurements, search))
        scanned['redaction_case_ids'] = sorted(material[2] | search | set(iteration['protocol']['splits'].get('sealed', [])))
        return iteration, description, normalized, scanned

    def preview(self, candidate_id, card):
        """What ``propose`` would freeze for this card, without writing or freezing anything."""
        iteration, description, normalized, scanned = self._evaluate(candidate_id, card)
        with self.repository.connect() as conn:
            taken = conn.execute('SELECT 1 FROM clusters WHERE iteration_id=? AND cluster_id=?',
                                 (iteration['id'], card['cluster_id'])).fetchone() is not None
        out = dict(cluster_id=card['cluster_id'], description=description,
                   **{k: normalized[k] for k in ('signature', 'missing_signature', 'violation_signature')})
        out.update({k: v for k, v in scanned.items() if k not in ('observations', 'executions', 'members', 'redaction_case_ids')})
        out.update(members=scanned['members'], preview=True, cluster_id_taken=taken)
        return out

    def propose(self, candidate_id, card, *, actor):
        actor = _text(actor, 'actor', limit=200)
        iteration, description, normalized, scanned = self._evaluate(candidate_id, card)
        with self.repository.connect(write=True) as conn:
            if conn.execute('SELECT 1 FROM clusters WHERE iteration_id=? AND cluster_id=?',
                            (iteration['id'], card['cluster_id'])).fetchone():
                raise Conflict('Cluster identity is already frozen')
            conn.execute('INSERT INTO clusters VALUES(?,?,?,?,?,?,?,?,?,?)', (iteration['id'], card['cluster_id'], description,
                json.dumps(normalized['signature']), json.dumps(normalized['missing_signature']), json.dumps(normalized['violation_signature']),
                json.dumps(scanned['executions']), actor, now(), json.dumps(scanned, ensure_ascii=False)))
            self.repository._event(conn, iteration['task_id'], 'cluster.proposed', card['cluster_id'],
                                   dict(candidate_id=candidate_id, executions=scanned['executions'], actor=actor))
        return self.show(iteration['task_id'], card['cluster_id'])
