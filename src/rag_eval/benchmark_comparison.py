"""Read-only, identity-checked comparisons of complete benchmark submissions.

Batch estimates are copied from score artifacts. Paired means describe a
different estimator, especially for the original Perl ROUGE bootstrap output.
No model execution, scoring, significance claims or cross-suite totals occur.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path
import re

from .artifacts import save_json
from .benchmark_official import prepare_inputs, scorer_identity, validate_metrics
from .benchmark_submission import validate_submission
from .notebook_bundle import load_bundle
from .starter_protocol import fingerprint


FORMAT = 'benchmark-comparison-v1'


def _nonnegative_observation(value, field):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f'{field} observation must be finite, numeric and nonnegative')
    return value


def _observed_cost(submission):
    """Preserve recorded cost boundaries; absent quantities never become zeros."""
    rows = submission['predictions']
    boundaries, observations, groups = {}, [], {}
    aggregate_fields = {'call_count', 'calls_with_usage', 'prompt_tokens', 'completion_tokens', 'total_tokens'}
    def validate_usage(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if item is not None and (key in aggregate_fields or key in {'latency_ms', 'cost'} or key.endswith('_tokens')):
                    _nonnegative_observation(item, 'usage.' + key)
                validate_usage(item)
        elif isinstance(value, list):
            for item in value:
                validate_usage(item)
        elif type(value) in (int, float) and not math.isfinite(value):
            raise ValueError('usage observations must be finite')
    for row in rows:
        record = row.get('record') or {}
        if not isinstance(record, dict):
            raise ValueError('Prediction record must be an object for observed cost extraction')
        for field, value in [('record.latency_seconds', record.get('latency_seconds')),
                             ('prediction.latency_seconds', row.get('latency_seconds'))]:
            if value is not None:
                _nonnegative_observation(value, field)
                boundaries.setdefault(field, []).append({'case_id': row['case_id'], 'seconds': value})
        usage = record.get('usage')
        if usage is None or usage == {}:
            continue
        if not isinstance(usage, dict):
            raise ValueError('usage observation must be an object')
        validate_usage(usage)
        observations.append({'case_id': row['case_id'], 'usage': deepcopy(usage)})
        supported = {key: value for key, value in usage.items() if key in aggregate_fields and value is not None}
        # This is the only measured schema whose aggregation boundary is defined
        # by this repository's usage_capture.py. Other schemas remain raw.
        if usage.get('coverage') != 'provider_logged_events' or not supported:
            continue
        calls = usage.get('calls', [])
        if not isinstance(calls, list) or any(not isinstance(call, dict) for call in calls):
            raise ValueError('usage.calls must be a list of objects')
        model_values = [call.get('model') for call in calls if call.get('model') is not None]
        if any(not isinstance(model, str) for model in model_values):
            raise ValueError('usage call model identity must be text')
        models = sorted(set(model_values))
        identity = {'coverage': usage['coverage'], 'fields': sorted(supported), 'models': models}
        group = groups.setdefault(fingerprint(identity), {**identity, 'case_ids': [], 'totals': {key: 0 for key in supported}})
        group['case_ids'].append(row['case_id'])
        for key, value in supported.items():
            group['totals'][key] += value
            _nonnegative_observation(group['totals'][key], 'usage total.' + key)
    latency = []
    for field, values in boundaries.items():
        total = _nonnegative_observation(sum(x['seconds'] for x in values), field + ' total')
        latency.append({'field': field, 'measured_cases': len(values), 'case_count': len(rows),
                        'total_seconds': total, 'mean_seconds': total / len(values), 'observations': values})
    aggregated = sum(len(group['case_ids']) for group in groups.values())
    return {'case_count': len(rows),
            'latency': {'status': 'observed' if latency else 'unavailable', 'boundaries': latency},
            'usage': {'status': 'observed' if observations else 'unavailable', 'measured_cases': len(observations),
                      'observations': observations, 'unaggregated_cases': len(observations) - aggregated,
                      'aggregation_groups': [{**group, 'measured_cases': len(group['case_ids'])} for group in groups.values()]},
            'monetary_cost': {'status': 'unavailable', 'reason': 'No validated currency/pricing conversion; recorded usage.cost stays raw.'},
            'interpretation': 'Latencies retain their recorded field boundaries. Provider-logged calls/tokens may have partial coverage; '
                              'they are not inferred HTTP totals. Different boundaries do not support causal speed or cost comparisons.'}


def _hash(value, field):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError(f'{field} must be a SHA256 identity')


def _read(path):
    path = Path(path).resolve()
    payload = path.read_bytes()
    def distinct_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate JSON field in {path}: {key}')
            result[key] = value
        return result
    value = json.loads(payload, object_pairs_hook=distinct_pairs)
    if not isinstance(value, dict):
        raise ValueError(f'Expected JSON object: {path}')
    return value, {'path': str(path), 'sha256': hashlib.sha256(payload).hexdigest()}


def _validate_scores(bundle, submission, scores):
    if scores.get('format') != 'benchmark-scored-submission-v1':
        raise ValueError('Unsupported benchmark score format')
    checksum = scores.get('scores_id')
    _hash(checksum, 'scores_id')
    content = {key: value for key, value in scores.items() if key != 'scores_id'}
    if checksum != fingerprint(content):
        raise ValueError('Score artifact content hash differs from scores_id')
    for key in ('suite', 'bundle_id', 'scope', 'case_ids', 'method', 'method_id', 'coverage'):
        if scores.get(key) != submission[key]:
            raise ValueError(f'Score {key} differs from reconstructed submission')
    if scores.get('submission_id') != fingerprint(submission):
        raise ValueError('Score submission_id differs from reconstructed submission')
    prepared = prepare_inputs(bundle, submission)
    if scores.get('prepared_id') != fingerprint(prepared):
        raise ValueError('Score prepared_id differs from reconstructed scoring inputs')
    if scores.get('profile') != prepared['profile']:
        raise ValueError('Score profile differs from reconstructed scoring inputs')
    dependencies = scores.get('dependencies')
    if not isinstance(dependencies, dict) or not dependencies:
        raise ValueError('Scorer dependency identity is missing')
    bridge = dependencies.get('bridge')
    if not isinstance(bridge, dict) or not bridge:
        raise ValueError('Scorer bridge source hashes are missing')
    for name, digest in bridge.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Invalid scorer bridge filename')
        _hash(digest, 'bridge source hash')
    if not isinstance(dependencies.get('python'), str) or not dependencies['python'].strip():
        raise ValueError('Scorer Python version is missing')
    if not isinstance(dependencies.get('packages'), dict):
        raise ValueError('Scorer package identities are missing')
    if any(not isinstance(name, str) or not name.strip() or not isinstance(version, str) or not version.strip()
           for name, version in dependencies['packages'].items()):
        raise ValueError('Scorer package names and versions must be nonempty strings')
    sources = dependencies.get('sources', {})
    if not isinstance(sources, dict):
        raise ValueError('Scorer source identities must be an object')
    for name, source in sources.items():
        if not isinstance(source, dict) or not isinstance(source.get('url'), str) or not source['url'].strip():
            raise ValueError(f'Invalid saved upstream source identity: {name}')
        _hash(source.get('sha256'), 'upstream source hash')
    identity = scorer_identity(scores['profile'], dependencies)
    if scores.get('scorer_identity') != identity or scores.get('scorer_id') != fingerprint(identity):
        raise ValueError('Saved scorer identity differs from recorded source/dependency identity')
    metrics = validate_metrics(scores.get('metrics'))
    denominators = scores.get('metric_denominators')
    if denominators is not None:
        if (not isinstance(denominators, dict) or set(denominators) != set(metrics)
                or any(type(count) is not int or not 0 < count <= len(submission['case_ids'])
                       for count in denominators.values())):
            raise ValueError('Metric denominators must cover all batch metrics and be positive integers within the case scope')
    else:
        # Original v1 score artifacts predate per-metric denominators. Only the
        # ordinary all-case text/distribution metrics can use that legacy scope.
        if any(name.startswith(('upstream_hits', 'upstream_map', 'upstream_mrr')) for name in metrics):
            raise ValueError('Retrieval metrics require explicit eligible-case denominators')
        denominators = {name: len(submission['case_ids']) for name in metrics}
    metric_cases = scores.get('metric_case_ids')
    scope_ids = submission['case_ids']
    if metric_cases is not None:
        if not isinstance(metric_cases, dict) or set(metric_cases) != set(metrics):
            raise ValueError('metric_case_ids must cover exactly all scored metrics')
        for metric, ids in metric_cases.items():
            if (not isinstance(ids, list) or not ids or any(not isinstance(cid, str) for cid in ids)
                    or len(set(ids)) != len(ids) or not set(ids) <= set(scope_ids)
                    or ids != [cid for cid in scope_ids if cid in set(ids)]
                    or len(ids) != denominators[metric]):
                raise ValueError(f'metric_case_ids must be ordered unique eligible-case subsets matching denominators: {metric}')
    else:
        if any(denominators[name] != len(scope_ids)
               or name.startswith(('upstream_hits', 'upstream_map', 'upstream_mrr', 'citation_', 'autoais_'))
               for name in metrics):
            raise ValueError('Conditional metrics require explicit metric_case_ids; eligible-case IDs cannot be inferred from counts')
        metric_cases = {name: list(scope_ids) for name in metrics}
    pending = scores.get('pending_metrics')
    if (not isinstance(pending, list) or any(not isinstance(x, str) or not x.strip() for x in pending)
            or len(pending) != len(set(pending))):
        raise ValueError('pending_metrics must contain distinct nonempty descriptions')
    if any(item.split(':', 1)[0].strip() in metrics for item in pending):
        raise ValueError('Metric cannot be scored and pending simultaneously')
    rows = scores.get('per_case')
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Missing per-case coverage records')
    if [row.get('case_id') for row in rows] != submission['case_ids']:
        raise ValueError('Per-case coverage/order differs from frozen case IDs or contains duplicates')
    cases = {c['case_id']: c for c in bundle['cases']}
    per_case_metrics = None
    for row, prediction in zip(rows, submission['predictions']):
        case = cases[row['case_id']]
        for key, expected in [('status', prediction['status']), ('group_id', case['group_id']), ('task', case['task'])]:
            if row.get(key) != expected:
                raise ValueError(f'Per-case {key} differs from source/submission: {row["case_id"]}')
        keys = set(validate_metrics(row.get('metrics')))
        if not keys <= set(metrics):
            raise ValueError('Per-case metric has no corresponding batch metric')
        if per_case_metrics is not None and keys != per_case_metrics:
            raise ValueError('Per-case metric coverage is inconsistent; partial denominators are not comparable')
        if any(denominators[key] != len(rows) for key in keys):
            raise ValueError('Per-case metric denominator differs from its complete row coverage')
        per_case_metrics = keys
    if not submission['coverage']['generation_complete']:
        raise ValueError('Main comparison requires complete generation; missing/error cases remain incomplete')
    return per_case_metrics or set(), denominators, metric_cases


def compare_submissions(bundle_directory, entries):
    """Validate local artifacts and compare two or more distinct selected methods.

    ``entries`` is a list of {submission: path, scores: path}. All methods must
    have exactly the same frozen scope and scorer content identity. Different
    generation models/configurations are disclosed, not silently equated.
    """
    if not isinstance(entries, (list, tuple)) or len(entries) < 2:
        raise ValueError('At least two scored submissions are required')
    bundle_directory = Path(bundle_directory).resolve()
    bundle = load_bundle(bundle_directory)
    seen_submissions, seen_methods, loaded = set(), set(), []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'submission', 'scores'}:
            raise ValueError('Each comparison entry requires submission and scores file paths')
        saved, submission_file = _read(entry['submission'])
        submission = validate_submission(bundle, saved)
        submission_id = fingerprint(submission)
        if submission_id in seen_submissions or submission['method_id'] in seen_methods:
            raise ValueError('Duplicate submission/method attempt; explicitly select one attempt per method')
        scores, score_file = _read(entry['scores'])
        per_metrics, denominators, metric_cases = _validate_scores(bundle, submission, scores)
        if loaded:
            reference = loaded[0]['scores']
            for key in ('bundle_id', 'suite', 'scope', 'case_ids', 'profile', 'scorer_id', 'scorer_identity'):
                if scores[key] != reference[key]:
                    raise ValueError(f'Cannot compare different {key}; freeze the same data, scope and scorer')
        seen_submissions.add(submission_id)
        seen_methods.add(submission['method_id'])
        loaded.append({'submission': submission, 'scores': scores, 'per_metrics': per_metrics,
                       'denominators': denominators,
                       'metric_case_ids': metric_cases,
                       'files': {'submission': submission_file, 'scores': score_file}})
    common = set.intersection(*(set(item['scores']['metrics']) for item in loaded))
    if not common:
        raise ValueError('No common scored metrics; pending metrics cannot produce a main comparison')
    for key in common:
        if len({item['denominators'][key] for item in loaded}) != 1:
            raise ValueError(f'Cannot compare different eligible-case denominators for {key}')
        if any(item['metric_case_ids'][key] != loaded[0]['metric_case_ids'][key] for item in loaded[1:]):
            raise ValueError(f'Cannot compare different eligible-case IDs for {key}, even with equal denominators')
    paired = common.intersection(*(item['per_metrics'] for item in loaded))
    union = set.union(*(set(item['scores']['metrics']) for item in loaded))
    methods = []
    for item in loaded:
        scores = item['scores']
        methods.append({'method_id': scores['method_id'], 'method': deepcopy(scores['method']),
                        'submission_id': scores['submission_id'], 'scores_id': scores['scores_id'],
                        'prepared_id': scores['prepared_id'], 'coverage': deepcopy(scores['coverage']),
                        'metrics': deepcopy(scores['metrics']), 'pending_metrics': list(scores['pending_metrics']),
                        'metric_denominators': dict(item['denominators']),
                        'metric_case_ids': deepcopy(item['metric_case_ids']),
                        'metric_case_ids_source': ('explicit' if 'metric_case_ids' in scores else 'inferred_from_full_case_scope'),
                        'denominator_source': ('explicit' if 'metric_denominators' in scores else 'inferred_from_full_case_scope'),
                        'unavailable_metrics': sorted(union - set(scores['metrics'])),
                        'unshared_metrics': sorted(set(scores['metrics']) - common),
                        'batch_only_metrics': sorted(set(scores['metrics']) - item['per_metrics']),
                        'observed_cost': _observed_cost(item['submission']),
                        'files': item['files']})
    differences = {}
    for field in ('kind', 'citation_style', 'model_identity', 'input_policy', 'configuration'):
        values = [m['method'][field] for m in methods]
        if any(value != values[0] for value in values[1:]):
            differences[field] = [{'method_id': m['method_id'], 'value': deepcopy(value)}
                                  for m, value in zip(methods, values)]
    comparisons = []
    for left, right in combinations(loaded, 2):
        a, b = left['scores'], right['scores']
        rows, groups = [], {}
        for ar, br in zip(a['per_case'], b['per_case']):
            row = {'case_id': ar['case_id'], 'group_id': ar['group_id'], 'task': ar['task'],
                   'left_status': ar['status'], 'right_status': br['status'],
                   'left': {k: ar['metrics'][k] for k in sorted(paired)},
                   'right': {k: br['metrics'][k] for k in sorted(paired)},
                   'differences': {k: br['metrics'][k] - ar['metrics'][k] for k in sorted(paired)}}
            rows.append(row)
            groups.setdefault(row['group_id'], []).append(row)
        def means(items):
            return {key: sum(row['differences'][key] for row in items) / len(items) for key in sorted(paired)}
        comparisons.append({'left_method_id': a['method_id'], 'right_method_id': b['method_id'],
                            'direction': 'right minus left',
                            'batch_differences': {k: b['metrics'][k] - a['metrics'][k] for k in sorted(common)},
                            'paired': rows, 'paired_mean_differences': means(rows),
                            'group_differences': [{'group_id': group, 'case_count': len(items),
                                                   'mean_differences': means(items)} for group, items in groups.items()]})
    first = loaded[0]['scores']
    report = {'format': FORMAT, 'suite': first['suite'], 'bundle_id': first['bundle_id'],
              'source': deepcopy(bundle['manifest']['source']),
              'adaptation_revision': bundle['manifest']['adaptation_revision'],
              'scope': first['scope'], 'case_ids': first['case_ids'], 'case_count': len(first['case_ids']),
              'profile': first['profile'], 'scorer_id': first['scorer_id'],
              'scorer_identity': first['scorer_identity'], 'common_metrics': sorted(common),
              'metric_denominators': {key: loaded[0]['denominators'][key] for key in sorted(common)},
              'metric_case_ids': {key: list(loaded[0]['metric_case_ids'][key]) for key in sorted(common)},
              'conditional_metric_scopes': {
                  key: {'case_count': loaded[0]['denominators'][key], 'case_ids': list(loaded[0]['metric_case_ids'][key])}
                  for key in sorted(common) if loaded[0]['metric_case_ids'][key] != first['case_ids']},
              'paired_metrics': sorted(paired), 'methods': methods, 'method_differences': differences,
              'comparisons': comparisons,
              'uncertainty': {'computed': False, 'reason': 'No confidence intervals or significance test computed.'},
              'interpretation': [
                  'Batch metrics are preserved from each validated score artifact; no per-case recomputation.',
                  'Paired/group means describe per-case differences, a different estimator from Perl batch ROUGE.',
                  'Model, input policy, citation handling and full configurations are disclosed; unspecified budgets remain unknown.',
                  'Observed differences do not establish causality or statistical significance.',
                  'This report covers one suite and scope; there is no cross-suite total or causal ranking.',
              ]}
    report['report_id'] = fingerprint(report)
    return report


def _markdown(report):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ').replace('`', '&#96;')
    lines = [f'**Benchmark comparison: {cell(report["suite"])}**', '',
             f'Scope: {report["scope"]}; cases: {report["case_count"]}. Profile: {cell(report["profile"])}.', '',
             'Batch scores retain the original scorer estimates (native metric units).', '',
             '| Method | ' + ' | '.join(cell(k) for k in report['common_metrics']) + ' |',
             '| --- | ' + ' | '.join('---:' for _ in report['common_metrics']) + ' |']
    for entry in report['methods']:
        lines.append('| ' + cell(entry['method']['name']) + ' | ' +
                     ' | '.join(f'{entry["metrics"][k]:.6f}' for k in report['common_metrics']) + ' |')
    lines += ['', 'Metric denominators: ' + ', '.join(f'{cell(key)}={count}'
                                                   for key, count in report['metric_denominators'].items()) + '.']
    for metric, scope in report['conditional_metric_scopes'].items():
        lines += ['', f'Conditional metric scope for {cell(metric)}: {scope["case_count"]}/{report["case_count"]} cases; '
                       'the same explicitly identified cases were used by every method. IDs are listed in report.json.']
    for entry in report['methods']:
        lines += ['', f'**{cell(entry["method"]["name"])}** (`{entry["method_id"][:12]}`)', '',
                  'Model, input policy, configuration and supplied budgets:', '',
                  '```json', json.dumps(entry['method'], ensure_ascii=False, indent=2), '```', '',
                  f'Pending: {cell(", ".join(entry["pending_metrics"]) or "none")}.',
                  f'Unavailable scored metrics: {cell(", ".join(entry["unavailable_metrics"]) or "none")}.',
                  f'Batch-only metrics: {cell(", ".join(entry["batch_only_metrics"]) or "none")}.']
        observed = entry['observed_cost']
        lines += ['', 'Observed cost (separate measurement boundaries):']
        if observed['latency']['boundaries']:
            for boundary in observed['latency']['boundaries']:
                lines += ['', f'- {cell(boundary["field"])}: measured {boundary["measured_cases"]}/{observed["case_count"]} cases, '
                               f'mean {boundary["mean_seconds"]:.3f}s, observed sum {boundary["total_seconds"]:.3f}s.']
        else:
            lines += ['', '- Latency unavailable; missing measurements are not zero.']
        lines += ['', f'- Usage measured on {observed["usage"]["measured_cases"]}/{observed["case_count"]} cases; '
                       'compatible provider fields are aggregated by schema/model in report.json.',
                  '- Monetary cost unavailable. Raw usage observations and coverage are retained in report.json.',
                  '', observed['interpretation']]
    names = {m['method_id']: m['method']['name'] for m in report['methods']}
    for comparison in report['comparisons']:
        left, right = names[comparison['left_method_id']], names[comparison['right_method_id']]
        lines += ['', f'**Difference: {cell(right)} minus {cell(left)}**', '',
                  '| Metric | Batch difference | Mean per-case difference |', '| --- | ---: | ---: |']
        for metric, value in comparison['batch_differences'].items():
            per_case = comparison['paired_mean_differences'].get(metric)
            lines.append(f'| {cell(metric)} | {value:+.6f} | ' +
                         (f'{per_case:+.6f}' if per_case is not None else 'unavailable') + ' |')
    lines += ['', 'Per-case and group differences, source identity and artifact hashes are in [report.json](report.json).', '']
    lines += [f'- {message}' for message in report['interpretation']]
    lines += ['- No uncertainty interval or significance test was computed.', '']
    return '\n'.join(lines)


def write_comparison(bundle_directory, entries, output_dir):
    output = Path(output_dir).resolve()
    bundle = Path(bundle_directory).resolve()
    if output.exists():
        raise FileExistsError(f'Comparison output must be a fresh directory: {output}')
    inputs = [bundle] + [Path(e[k]).resolve() for e in entries for k in ('submission', 'scores')]
    if output.is_relative_to(bundle) or any(path.is_relative_to(output) for path in inputs):
        raise ValueError('Comparison output must not overlap its inputs')
    report = compare_submissions(bundle, entries)
    markdown = _markdown(report)
    output.mkdir(parents=True, exist_ok=False)
    save_json(output / 'report.json', report)
    (output / 'report.md').write_text(markdown, encoding='utf-8')
    return report
