"""Deterministic stage diagnostics from saved observations; never calls a model.

Recall uses the union of distinct gold evidence texts across annotations and
full evidence containment after whitespace folding. Partial paragraphs do not
count as recovered. This is a diagnostic, not the official evidence scorer.
Context blocks retain multiplicity; final context wins over its capture copy.
Sectioned synthesis uses all successful captures instead. Costs are separate
observations: provider logs and scheduled submissions must never be added.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import json
import math
from pathlib import Path

from .artifacts import digest, save_json

FORMAT = 'sn-stage-metrics-v1'
EVIDENCE_METRICS = ('gold_evidence_recall', 'context_precision', 'citation_precision')


def _text(value):
    return ' '.join(value.split())


def _ratio(n=None, d=None, *, status='available', reason=None):
    if d == 0:
        status, reason = 'not_applicable', reason or 'empty_denominator'
    return dict(value=n / d if status == 'available' and d else None,
                numerator=n, denominator=d, status=status, reason=reason)


def _unavailable(reason, *, applicable=True):
    return _ratio(status='unavailable' if applicable else 'not_applicable', reason=reason)


def _gold(case, documents):
    """Evaluator-only evidence atoms. No answer text is used for relevance."""
    suite, gold = case['suite'], case.get('gold', {})
    if suite == 'qasper':
        if 'annotations' not in gold:
            return None
        atoms = [(None, _text(e), None) for a in gold['annotations'] for e in a.get('evidence', []) if e.strip()]
    elif suite == 'multihop_rag':
        if 'evidence' not in gold:
            return None
        atoms = [(e['document_id'], _text(e['fact']), None) for e in gold['evidence']]
    elif suite == 'hotpotqa':
        if 'supporting_facts' not in gold:
            return None
        units = {(d['id'], u['id']): u['text'] for d in documents for u in d.get('source_units', [])}
        if any((e['document_id'], e['source_unit_id']) not in units for e in gold['supporting_facts']):
            return None
        atoms = [(e['document_id'], _text(units[(e['document_id'], e['source_unit_id'])]),
                  e['sent_id']) for e in gold['supporting_facts']]
        # Keep published but unmappable labels in the denominator, never drop them.
        atoms += [(e['title'], '', e['sent_id']) for e in gold.get('unmapped_supporting_facts', [])]
    else:
        return None
    return list(dict.fromkeys(atoms))


def _blocks(record):
    """Return delivered blocks and ownership, or None for absent observation."""
    owners = record.get('source_to_document', {})
    if record.get('context_supported') is True:
        contexts = record.get('retrieval_context')
        if not isinstance(contexts, list) or any(not isinstance(c, str) for c in contexts):
            return None
        sources, handles = record.get('source_ids', []), record.get('handles', [])
        # capture_context may prepend a block with no source handle.
        offset = len(contexts) - len(sources)
        return [dict(text=c, document_id=owners.get(sources[i - offset])
                     if 0 <= i - offset < len(sources) else None,
                     key=handles[i - offset] if 0 <= i - offset < len(handles) else None)
                for i, c in enumerate(contexts)]
    captures = record.get('captures')
    if not isinstance(captures, list):
        return None
    from .protocol import capture_context
    result, observed = [], False
    for c in captures:
        if not c.get('succeeded') or not isinstance(c.get('context_block'), str) or not isinstance(c.get('id_map'), dict):
            continue
        observed = True
        projected = capture_context(c)
        result.extend(_blocks(dict(projected, context_supported=True, source_to_document=owners)))
    return result if observed else None


def _hits(atom, block):
    did, text, _ = atom
    return bool(text and (did is None or did == block['document_id']) and text in _text(block['text']))


def _citation_metric(case, record, atoms, documents, catalogues):
    anchors = record.get('response', {}).get('anchors')
    if not isinstance(anchors, list):
        return _unavailable('anchors_not_observed')
    if not anchors:
        return _ratio(0, 0, reason='no_anchors')
    keys = [a.get('key') for a in anchors]
    if any(not isinstance(k, str) or not k for k in keys) or len(set(keys)) != len(keys):
        return _unavailable('ambiguous_anchor_keys')
    suite = case['suite']
    try:
        if suite == 'qasper':
            from .qasper_evidence import verify_evidence
            did, = case['material_document_ids']
            document, = [d for d in documents if d['id'] == did]
            catalogue = catalogues[did]
            verify_evidence(record, document, catalogue)
            audits = record['qasper_evidence']['projection']['citations']
            unit_text = {u['id']: _text(u['text']) for u in catalogue['units']}
            relevant = {a['key'] for a in audits if not a.get('invalid') and
                        any(unit_text.get(uid) == text for uid in a.get('unit_ids', []) for _, text, _ in atoms)}
        elif suite == 'hotpotqa':
            from .hotpot_evidence import verify_evidence
            verify_evidence(record, documents)
            audits = record['hotpot_evidence']['projection']['citations']
            relevant = {a['key'] for a in audits if not a.get('invalid') and
                        any(a.get('document_id') == did and sent in a.get('sentence_ids', [])
                            for did, _, sent in atoms)}
        else:
            # MultiHop has fact text and captured handle/source ownership, no
            # official sentence projector. Never use truncated anchor snippets.
            blocks = _blocks(record)
            captures = record.get('captures', [])
            relevant = set()
            for anchor in anchors:
                matches = [c['id_map'][anchor['key']] for c in captures
                           if c.get('succeeded') and anchor['key'] in c.get('id_map', {})]
                if len(matches) != 1 or any(anchor.get(k) != matches[0].get(k)
                                          for k in ('object_id', 'source_id', 'object_type')):
                    return _unavailable('anchor_capture_mapping_unavailable')
                candidates = [b for b in blocks or [] if b['key'] == anchor['key']]
                if len(candidates) != 1:
                    return _unavailable('anchor_context_unavailable')
                if any(_hits(atom, candidates[0]) for atom in atoms):
                    relevant.add(anchor['key'])
        return _ratio(sum(k in relevant for k in keys), len(keys))
    except (ValueError, KeyError, TypeError):
        return _unavailable('evidence_projection_unverifiable')


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _usage(record):
    usage = record.get('usage', {})
    calls = usage.get('calls')
    observed = isinstance(calls, list)
    times = [c['latency_ms'] / 1000 for c in calls or [] if _finite(c.get('latency_ms'))]
    latency = record.get('latency_seconds')
    return dict(provider_logged_call_count=len(calls) if observed else None,
                provider_coverage=usage.get('coverage'),
                provider_latency_seconds_sum=sum(times) if times else None,
                provider_latency_count=len(times) if observed else None,
                ask_latency_seconds=latency if _finite(latency) else None,
                total_tokens=usage.get('total_tokens'), cost=usage.get('cost'),
                scheduled_call_count=None, scheduled_latency_seconds_sum=None,
                scheduled_latency_count=None, scheduled_pending_count=None,
                history_exhaustive=False)


def case_metrics(case, output, *, documents=(), catalogues=None, missing_status='missing'):
    """Preserve output status; missing observations produce null diagnostics."""
    status = output['status'] if output else missing_status
    # A retained record cannot turn an explicitly unobserved output into an
    # observation. Error/no_answer may still carry real partial observations.
    record = output.get('product_record', {}) if output and status not in {'missing', 'pending', 'not_applicable'} else {}
    result = dict(case_id=case['case_id'], suite=case['suite'], status=status,
                  model_usage=_usage(record))
    if status == 'not_applicable':
        result.update({k: _unavailable('output_not_applicable', applicable=False) for k in EVIDENCE_METRICS})
        return result
    supported = case['suite'] in {'qasper', 'hotpotqa', 'multihop_rag'}
    atoms = _gold(case, documents)
    blocks = _blocks(record)
    if not supported or atoms is None:
        reason = 'suite_has_no_supported_evidence_units' if not supported else 'gold_evidence_fields_unavailable'
        result.update({k: _unavailable(reason, applicable=supported) for k in EVIDENCE_METRICS})
        return result
    if not atoms:
        result.update({k: _unavailable('no_gold_evidence', applicable=False) for k in EVIDENCE_METRICS})
        return result
    if blocks is None:
        result.update({k: _unavailable('context_not_observed') for k in EVIDENCE_METRICS[:2]})
    elif case['suite'] != 'qasper' and any(b['document_id'] is None for b in blocks):
        result.update({k: _unavailable('context_document_ownership_unavailable') for k in EVIDENCE_METRICS[:2]})
    else:
        result['gold_evidence_recall'] = _ratio(sum(any(_hits(a, b) for b in blocks) for a in atoms), len(atoms))
        result['context_precision'] = _ratio(sum(any(_hits(a, b) for a in atoms) for b in blocks), len(blocks))
    result['citation_precision'] = _citation_metric(case, record, atoms, documents, catalogues or {})
    return result


def _rows(path):
    if not path.exists():
        return None
    rows = []
    for i, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError(f'Malformed {path.name} row {i}') from None
        if not isinstance(row, dict):
            raise ValueError(f'Invalid {path.name} row {i}')
        rows.append(row)
    return rows


def _time(value):
    try:
        dt = datetime.fromisoformat(value)
        return dt.timestamp() if dt.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def _scheduled(events, attempts, cases):
    """Attribute by saved Ask windows; the current journal has no case IDs."""
    if events is None:
        return dict(status='unavailable', submission_count=None, unattributed_call_count=None, history_exhaustive=False)
    from .scheduled_generation_audit import audit_scheduled_generation
    audit = audit_scheduled_generation(events, journal_present=True)
    windows, active = [], {}
    for row in attempts or []:
        cid, t = row.get('case_id'), _time(row.get('recorded_at'))
        if t is None:
            continue
        if row.get('event') == 'started':
            if cid in active:
                raise ValueError('Ambiguous repeated case attempt')
            active[cid] = t
        elif row.get('event') == 'finished' and cid in active:
            start = active.pop(cid)
            if t < start:
                raise ValueError('Invalid case attempt interval')
            windows.append((cid, start, t))
    starts, terminals = {}, {}
    for row in events:
        if row.get('kind') == 'model_service_submission_attempt':
            target = starts
        elif row.get('kind') == 'model_service_future_terminal':
            target = terminals
        else:
            continue
        call_id = row.get('call_id')
        if type(call_id) is not int or call_id in target:
            raise ValueError('Invalid or duplicate model-event call ID')
        target[call_id] = row
    counts, latencies, pending = Counter(), defaultdict(list), Counter()
    unattributed, kinds = 0, Counter()
    for call_id, start in starts.items():
        kinds[start.get('service', {}).get('kind', 'unknown')] += 1
        stamp = _time(start.get('recorded_at'))
        cid = None
        if stamp is not None:
            matches = [c for c, a, b in windows if a <= stamp <= b]
            cid = matches[0] if len(matches) == 1 else None
        if cid not in cases:
            unattributed += 1
            continue
        counts[cid] += 1
        terminal = terminals.get(call_id)
        if terminal is None:
            pending[cid] += 1
        else:
            end = _time(terminal.get('recorded_at'))
            if stamp is not None and end is not None and end >= stamp:
                latencies[cid].append(end - stamp)
    # A file with no scheduled observations is not evidence of zero calls.
    attributable = set(c for c, _, _ in windows) | set(counts)
    if any(_time(row.get('recorded_at')) is None for row in starts.values()):
        attributable = set()
    # Overlapping windows cannot establish exclusive ownership of calls.
    ambiguous = {c for c, a, b in windows for other, x, y in windows
                 if other != c and max(a, x) <= min(b, y)}
    attributable -= ambiguous
    for cid, row in cases.items():
        if starts and cid in attributable:
            row['model_usage'].update(scheduled_call_count=counts[cid], scheduled_pending_count=pending[cid],
                scheduled_latency_seconds_sum=sum(latencies[cid]) if latencies[cid] else None,
                scheduled_latency_count=len(latencies[cid]), scheduled_attribution='saved_ask_window')
    return dict(status='observed' if starts else 'unavailable', submission_count=len(starts) if starts else None,
                terminal_count=len(terminals), unattributed_call_count=unattributed if starts else None,
                service_kind_counts=dict(kinds), history_exhaustive=False,
                journal_pair_state=audit['completeness']['pair_state'],
                latency_semantics='submission-to-future-terminal wall time; sums may overlap')


def summarize_cases(cases):
    result = dict(planned=len(cases), status_counts=dict(Counter(c['status'] for c in cases)))
    # Refusals are an observed no_answer outcome. Clarification is distinct;
    # missing/pending/not_applicable have no observed refusal decision.
    observed = [c for c in cases if c['status'] in {'success', 'no_answer', 'clarification', 'error'}]
    result['refusal_rate'] = dict(_ratio(sum(c['status'] == 'no_answer' for c in observed), len(observed)),
                                  planned=len(cases), unobserved=len(cases) - len(observed))
    for key in EVIDENCE_METRICS:
        values = [c[key] for c in cases if c[key]['value'] is not None]
        result[key] = dict(_ratio(sum(v['numerator'] for v in values), sum(v['denominator'] for v in values)),
            eligible_cases=len(values), planned=len(cases),
            mean_over_observed=sum(v['value'] for v in values) / len(values) if values else None,
            observation_status_counts=dict(Counter(c[key]['status'] for c in cases)))
        if not values and any(c[key]['status'] == 'unavailable' for c in cases):
            result[key].update(status='unavailable', reason='no_available_observations')
    result['model_usage'] = {}
    for key in ('provider_logged_call_count', 'provider_latency_seconds_sum', 'ask_latency_seconds',
                'scheduled_call_count', 'scheduled_latency_seconds_sum', 'scheduled_pending_count', 'total_tokens'):
        values = [c['model_usage'][key] for c in cases if _finite(c['model_usage'].get(key))]
        result['model_usage'][key] = dict(sum_over_observed=sum(values) if values else None,
            mean_over_observed=sum(values) / len(values) if values else None,
            observed_cases=len(values), planned=len(cases))
    return result


def compute_stage_metrics(run, *, context=None):
    """Read immutable input plus saved observations. Does not mutate the run."""
    from .run_reader import RunReadContext
    run = Path(run).resolve()
    manifest = json.loads((run / 'manifest.json').read_text(encoding='utf-8'))
    state = json.loads((run / 'state.json').read_text(encoding='utf-8'))
    context = context or RunReadContext(partition_only=True)
    bundle = context.bundle_for_run(run)
    planned = _rows(run / 'planned.jsonl')
    scope = list(dict.fromkeys(p['case_id'] for p in planned or []))
    notebook = manifest['identity']['notebook_context']
    try:
        partition = next(p for p in bundle['partitions'] if p['partition_id'] == notebook['partition_id'])
    except StopIteration:
        raise ValueError('Manifest scope is outside frozen partitions') from None
    expected_scope = notebook.get('case_ids', partition['case_ids'])
    if not expected_scope or len(set(expected_scope)) != len(expected_scope) or not set(expected_scope) <= set(partition['case_ids']):
        raise ValueError('Invalid manifest case scope')
    if scope and set(scope) != set(expected_scope):
        raise ValueError('Saved plan differs from manifest case scope')
    if not scope:
        scope = list(expected_scope)
    by_case = {c['case_id']: c for c in bundle['cases']}
    if not set(scope) <= by_case.keys():
        raise ValueError('Planned scope is outside frozen bundle')
    outputs = {}
    for row in _rows(run / 'outputs.jsonl') or []:
        cid = row['case_id']
        if cid in outputs:
            raise ValueError('Duplicate saved output')
        if cid not in scope:
            raise ValueError('Saved output outside planned scope')
        outputs[cid] = row
    rows = [case_metrics(by_case[cid], outputs.get(cid), documents=bundle['documents'],
                        catalogues=bundle.get('qasper_evidence_catalogues', {}),
                        missing_status='not_applicable' if state['phase'] == 'not_applicable' else 'missing' if state['phase'] in
                            {'finished', 'finished_with_errors', 'failed', 'interrupted', 'cancelled'} else 'pending')
            for cid in scope]
    events = _scheduled(_rows(run / 'model-events.jsonl'), _rows(run / 'product-artifacts/attempts.jsonl'),
                        {c['case_id']: c for c in rows})
    return dict(format=FORMAT, metric_role='diagnostic', run_path=str(run), suite=manifest['suite'],
                mode=manifest.get('mode'), state=state, cases=rows, summary=summarize_cases(rows), model_events=events,
                inputs_sha256={name: digest(run / name) for name in
                    ('outputs.jsonl', 'model-events.jsonl', 'product-artifacts/attempts.jsonl', 'manifest.json', 'planned.jsonl')
                    if (run / name).is_file()},
                definitions=dict(gold='distinct evidence union across annotations',
                    context='full evidence text containment after whitespace folding; no fuzzy or partial match',
                    citations='fraction of observed anchors with at least one gold unit/fact; not entailment',
                    refusal='no_answer / observed output statuses; errors retained in denominator',
                    costs='provider logs and scheduled journal are separate partial observations'))


def write_stage_metrics(run_dirs, *, summary_output=None, write_runs=True):
    """Write each stage-metrics.json and optionally aggregate by suite and mode."""
    paths = [Path(p).resolve() for p in run_dirs]
    if not paths or len(set(paths)) != len(paths):
        raise ValueError('Provide distinct run directories')
    from .run_reader import RunReadContext
    context = RunReadContext(partition_only=True)
    runs = [compute_stage_metrics(p, context=context) for p in paths]
    groups = defaultdict(list)
    for run in runs:
        groups[(run['suite'], run['mode'])].extend(run['cases'])
        if write_runs:
            save_json(Path(run['run_path']) / 'stage-metrics.json', run)
    report = dict(format=FORMAT, metric_role='diagnostic', runs=runs,
                  groups=[dict(suite=s, mode=m, **summarize_cases(rows)) for (s, m), rows in groups.items()])
    if summary_output is not None:
        save_json(Path(summary_output), report)
    return report
