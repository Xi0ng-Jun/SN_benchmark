"""Read-only comparison of two task-linked, independently verified snapshots.

Canonical paths are trusted caller configuration, never discovered from import
COMMIT sources. Snapshot case/document projections are reading targets, not
complete canonical bundles. All comparison admission and estimates stay in the
shared benchmark comparison core.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re

from ..benchmark_comparison import compare_frozen_submissions, validate_scored_submission
from ..benchmark_official import validate_metrics
from ..identity import fingerprint
from ..notebook_bundle import load_bundle
from .ingest import read_snapshot


def _reason(code, message, detail):
    return {'code': code, 'message': message, 'detail': detail}


def _detail(error):
    # OSError.__str__ includes private filenames; strerror retains the actual
    # failure. Validator messages can also contain data-derived absolute paths.
    text = error.strerror if isinstance(error, OSError) else str(error)
    return re.sub(r'(?<!\w)(?:[A-Za-z]:)?/[^\s,;]+', '[private path]', text or type(error).__name__)


def _fields(value, fields, label):
    """Guard persisted object structure before any reader indexes into it."""
    if not isinstance(value, dict):
        raise ValueError(f'Snapshot {label} must be an object')
    for name, kind in fields.items():
        if not isinstance(value.get(name), kind):
            raise ValueError(f'Snapshot {label}.{name} is missing or has an invalid type')


def _strings(value, label):
    if (not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value)
            or len(set(value)) != len(value)):
        raise ValueError(f'Snapshot {label} must contain distinct nonempty strings')


def _denominators(value, metrics, count, label):
    if (not isinstance(value, dict) or set(value) != set(metrics)
            or any(type(number) is not int or not 0 < number <= count for number in value.values())):
        raise ValueError(f'Snapshot {label} denominators must cover exactly the metrics within the case scope')


def _snapshot_structure(snapshot):
    """Validate reading shapes, not scoring or comparison admission.

    A new content-addressed object can still contain malformed facts. Missing
    fields are never guessed from the repository summary or normalized beyond
    the shared score validator's absent/null optional-field semantics.
    """
    _fields(snapshot, {'bundle_manifest': dict, 'bundle_case_count': int, 'submission': dict,
                       'scores': dict, 'cases': list, 'documents': list, 'denominators': dict,
                       'eligible': dict, 'per_metrics': list, 'batch_only': list, 'source_hashes': dict}, 'result')
    if snapshot.get('format') != 'sn-workbench-result-v1':
        raise ValueError('Unsupported snapshot result format')
    sub, scores, manifest = snapshot['submission'], snapshot['scores'], snapshot['bundle_manifest']
    _fields(sub, {'suite': str, 'scope': str, 'case_ids': list, 'method': dict, 'coverage': dict,
                  'bundle_id': str, 'predictions': list}, 'submission')
    _fields(scores, {'submission_id': str, 'scores_id': str, 'scorer_id': str, 'profile': str,
                     'metrics': dict, 'pending_metrics': list}, 'scores')
    _fields(manifest, {'source': dict, 'selected_cases': int}, 'bundle_manifest')
    if fingerprint(manifest) != sub['bundle_id']:
        raise ValueError('Snapshot bundle_manifest identity differs from submission bundle_id')
    if (type(snapshot['bundle_case_count']) is not int or snapshot['bundle_case_count'] <= 0
            or snapshot['bundle_case_count'] != manifest['selected_cases']):
        raise ValueError('Snapshot bundle_case_count differs from complete bundle manifest')
    _strings(sub['case_ids'], 'submission.case_ids')
    if not sub['case_ids'] or len(sub['case_ids']) > snapshot['bundle_case_count']:
        raise ValueError('Snapshot submission case count is outside the complete bundle')
    for field in ('cases', 'documents'):
        if any(not isinstance(row, dict) for row in snapshot[field]):
            raise ValueError(f'Snapshot {field} must contain objects')
    metrics = validate_metrics(scores['metrics'])
    count = len(sub['case_ids'])
    if scores.get('metric_denominators') is not None:
        _denominators(scores['metric_denominators'], metrics, count, 'scores.metric_denominators')
    _denominators(snapshot['denominators'], metrics, count, 'denominators')
    _strings(scores['pending_metrics'], 'scores.pending_metrics')
    if scores.get('batch_only_metrics') is not None:
        _strings(scores['batch_only_metrics'], 'scores.batch_only_metrics')
    for field in ('per_metrics', 'batch_only'):
        _strings(snapshot[field], field)
    if set(snapshot['eligible']) != set(metrics):
        raise ValueError('Snapshot eligible projection must cover exactly the scored metrics')
    for metric, ids in snapshot['eligible'].items():
        _strings(ids, 'eligible.' + metric)
        if len(ids) != snapshot['denominators'][metric] or not set(ids) <= set(sub['case_ids']):
            raise ValueError('Snapshot eligible IDs differ from scope or denominators')
    hashes = snapshot['source_hashes']
    if (set(hashes) != {'submission', 'scores'}
            or any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)
                   for value in hashes.values())):
        raise ValueError('Invalid snapshot source hashes')


def _canonical_projection(snapshot, bundle):
    """Bind all frozen reading projections to the real, complete canonical."""
    if snapshot['bundle_manifest'] != bundle['manifest']:
        raise ValueError('Snapshot bundle_manifest differs from complete canonical bundle')
    sub, scores = snapshot['submission'], snapshot['scores']
    per_metrics, denominators, eligible, batch_only = validate_scored_submission(bundle, sub, scores)
    scope = set(sub['case_ids'])
    cases = [case for case in bundle['cases'] if case['case_id'] in scope]
    document_ids = {did for case in cases for did in case['material_document_ids']}
    expected = {'bundle_case_count': len(bundle['cases']), 'cases': cases,
                'documents': [doc for doc in bundle['documents'] if doc['id'] in document_ids],
                'denominators': denominators, 'eligible': eligible,
                'per_metrics': sorted(per_metrics), 'batch_only': sorted(batch_only)}
    for field, value in expected.items():
        if snapshot[field] != value:
            raise ValueError(f'Snapshot {field} projection differs from validated complete canonical scope')


def _unavailable_side(view, error):
    view.update(status='unavailable', summary=None,
                reason=_reason('snapshot_unavailable', '结果快照缺失或校验失败。', _detail(error)))


def _snapshot_failure(result):
    failures = [f"{name}: {result[name]['reason']['detail']}" for name in ('left', 'right')
                if result[name]['reason'] is not None]
    result['reason'] = _reason('snapshot_unavailable', '所选结果中有快照不可用，已保留各侧读取状态。',
                               '; '.join(failures))
    return result


def _summary(snapshot, result_id):
    """Project official facts from the verified object, never the SQLite cache."""
    sub, scores = snapshot['submission'], snapshot['scores']
    denominators = scores.get('metric_denominators')
    batch_only = scores.get('batch_only_metrics')
    if batch_only is None:  # Same absent/null meaning as the shared validator.
        batch_only = []
    return {
        'id': result_id, 'suite': sub['suite'], 'scope': sub['scope'],
        'method': deepcopy(sub['method']), 'case_ids': list(sub['case_ids']),
        'case_count': len(sub['case_ids']), 'bundle_case_count': snapshot['bundle_case_count'],
        'submission_id': scores['submission_id'], 'scores_id': scores['scores_id'],
        'scorer_id': scores['scorer_id'], 'profile': scores['profile'],
        'coverage': deepcopy(sub['coverage']),
        'metrics': {name: {'value': value,
                           'denominator': denominators[name] if denominators is not None else len(sub['case_ids']),
                           'kind': 'batch-only' if name in batch_only else 'per-case'}
                    for name, value in scores['metrics'].items()},
        'pending_metrics': list(scores['pending_metrics']),
        'source_hashes': deepcopy(snapshot['source_hashes']),
        'source': deepcopy(snapshot['bundle_manifest']['source']),
    }


def _side(repository, result_id):
    view = {'result_id': result_id, 'status': 'unavailable', 'summary': None,
            'read_reference': {'kind': 'result', 'result_id': result_id}, 'reason': None}
    try:
        snapshot = read_snapshot(repository.root, result_id)
        _snapshot_structure(snapshot)
    except (OSError, ValueError) as error:
        _unavailable_side(view, error)
        return view, None
    # Projection code is outside the artifact-validation exception boundary:
    # a reader programming error must propagate rather than hide the other side.
    view.update(status='readable', summary=_summary(snapshot, result_id))
    return view, snapshot


def compare_task_results(repository, task_id, left_result_id, right_result_id, *,
                         canonical_bundle_directory=None):
    """Return comparable/not_comparable/unavailable, with each readable side.

    Unknown task, unlinked results and duplicate selection raise ValueError as
    input errors. Artifact availability and comparison eligibility are separate:
    failures preserve the other side's summary and reading reference. ``report``
    is the unmodified shared-core report and is present only when comparable.
    No artifacts, database records, scores or temporary submissions are written.

    The optional canonical path must come from trusted application configuration;
    this function does not authorize paths supplied by a browser or COMMIT file.
    """
    if any(not isinstance(value, str) or not value.strip()
           for value in (task_id, left_result_id, right_result_id)):
        raise ValueError('Task and result identities must be nonempty strings')
    if left_result_id == right_result_id:
        raise ValueError('Choose two different task results')
    # Direct associations remain authoritative even if cached summaries are
    # stale or malformed. This SELECT-only scope check consumes no history.
    with repository.connect() as conn:
        if not conn.execute('SELECT 1 FROM tasks WHERE id=?', (task_id,)).fetchone():
            raise ValueError('Unknown task identity')
        attached = {row['result_id'] for row in conn.execute(
            'SELECT result_id FROM task_results WHERE task_id=? AND result_id IN (?,?)',
            (task_id, left_result_id, right_result_id))}
    if attached != {left_result_id, right_result_id}:
        raise ValueError('Result is not attached to this task')

    left, left_snapshot = _side(repository, left_result_id)
    right, right_snapshot = _side(repository, right_result_id)
    result = {'task_id': task_id, 'left_result_id': left_result_id, 'right_result_id': right_result_id,
              'left': left, 'right': right, 'status': 'unavailable', 'reason': None, 'report': None}
    if left_snapshot is None or right_snapshot is None:
        return _snapshot_failure(result)
    if canonical_bundle_directory is None:
        result['reason'] = _reason('canonical_bundle_required', '需要配置可信的完整基准数据才能比较。',
                                   'No trusted complete canonical bundle was supplied by the caller')
        return result

    # Invalid caller path types are programming errors, outside the artifact
    # failure boundary. Only explicit trusted paths reach the canonical loader.
    canonical_bundle_directory = Path(canonical_bundle_directory).resolve()
    try:
        bundle = load_bundle(canonical_bundle_directory)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        # Only the external canonical loader has this structural-error boundary;
        # core/summary programming exceptions are not caught by it.
        result['reason'] = _reason('canonical_bundle_unavailable', '完整基准数据缺失或校验失败。', _detail(error))
        return result
    canonical_id = fingerprint(bundle['manifest'])
    entries = []
    for view, snapshot in ((left, left_snapshot), (right, right_snapshot)):
        # Internally valid results from a different bundle remain readable.
        # Their incompatibility is rejected by the unchanged shared core.
        if snapshot['submission']['bundle_id'] == canonical_id:
            try:
                _canonical_projection(snapshot, bundle)
            except ValueError as error:
                _unavailable_side(view, error)
        entries.append({'submission': snapshot['submission'], 'scores': snapshot['scores'],
                        'source': {'snapshot_id': view['result_id'], 'source_hashes': snapshot['source_hashes']}})
    if left['status'] == 'unavailable' or right['status'] == 'unavailable':
        return _snapshot_failure(result)
    try:
        report = compare_frozen_submissions(canonical_bundle_directory, entries)
    except OSError as error:
        result['reason'] = _reason('canonical_bundle_unavailable', '完整基准数据无法读取。', _detail(error))
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        # Diagnose actual canonical contents when the core's second read fails.
        # A healthy reread must not turn a core programming error into a result.
        try:
            load_bundle(canonical_bundle_directory)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as canonical_error:
            result['reason'] = _reason('canonical_bundle_unavailable', '完整基准数据校验失败。',
                                       _detail(canonical_error))
        else:
            if not isinstance(error, ValueError):
                raise
            result.update(status='not_comparable', reason=_reason(
                'comparison_rejected', '所选结果不满足同题比较条件，请查看校验详情。', _detail(error)))
    else:
        result.update(status='comparable', report=report)
    return result
