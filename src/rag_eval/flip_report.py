"""Offline run flips over benchmark_comparison's admission and paired rows.

There is no synthetic method identity, rescoring, status ordering, or total
across metrics. Directions are per metric (positive official quality deltas
are better). Batch-only metrics remain in the embedded comparison. Failed
admission still returns independently readable sides with no paired claims.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from . import benchmark_comparison as core
from .artifacts import digest, save_json
from .artifact_store import reject_shared_output
from .identity import fingerprint
from .notebook_bundle import load_bundle
from .oracle_track import require_formal_run
from .run_reader import RunReadContext

FORMAT = 'sn-run-flips-v1'


def _reason(exc):
    # Admission errors must not reproduce a private URL/credential from input.
    text = re.sub(r'(?i)\b(?:https?|wss?)://\S+', '[redacted-address]', str(exc))
    return re.sub(r'(?i)\b(?:authorization|api[_-]?key|token|password|secret|credential)\b[^\n]*',
                  '[redacted-credential]', text)[:1024]


def _run_path(value):
    if isinstance(value, dict):
        value = value.get('run_dir')
    if not isinstance(value, (str, Path)):
        raise ValueError('A run directory or ledger row with run_dir is required')
    return Path(value).expanduser().resolve()


def _read_side(value, scores_path, submission_path):
    side = dict(readable=False, admitted=False, cases=[], reason=None)
    entry = None
    try:
        run = _run_path(value)
        side['run_dir'] = str(run)
        manifest, _ = core._read(run / 'manifest.json')
        side['run_id'] = manifest.get('run_id', run.name)
        side['diagnostic'] = bool(manifest.get('diagnostic') or manifest.get('kind') == 'diagnostic/oracle')
        outputs = []
        outputs_path = run / 'outputs.jsonl'
        output_bytes = outputs_path.read_bytes() if outputs_path.exists() else b''
        output_hash = hashlib.sha256(output_bytes).hexdigest() if outputs_path.exists() else None
        for line in output_bytes.decode('utf-8').splitlines():
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get('case_id'), str):
                raise ValueError('Invalid saved run output row')
            outputs.append(row)
        if len({r['case_id'] for r in outputs}) != len(outputs):
            raise ValueError('Duplicate saved output case; select one run attempt')
        # These are saved observations, independent of score admission. Never
        # copy private full product records/answers into the flip summary.
        side['cases'] = [dict(case_id=r['case_id'], status=r.get('status'), metrics=None) for r in outputs]
        side['readable'] = True
        if (run / 'planned.jsonl').exists():
            state, _ = core._read(run / 'state.json') if (run / 'state.json').exists() else ({}, None)
            pending = state.get('phase') not in {
                'finished', 'finished_with_errors', 'failed', 'interrupted', 'cancelled', 'not_applicable'}
            observed = {r['case_id'] for r in outputs}
            for line in (run / 'planned.jsonl').read_text(encoding='utf-8').splitlines():
                planned = json.loads(line)
                if not isinstance(planned, dict):
                    raise ValueError('Invalid planned run case')
                cid = planned.get('case_id')
                if not isinstance(cid, str):
                    raise ValueError('Invalid planned run case')
                if cid not in observed:
                    side['cases'].append(dict(case_id=cid, status='pending' if pending else 'missing', metrics=None))
                    observed.add(cid)
        require_formal_run(manifest)
        if scores_path is None and isinstance(value, dict):
            scores_path = value.get('official_scores_path') or value.get('scores_path')
        score_path = Path(scores_path).expanduser().resolve() if scores_path else run / 'scores.json'
        submission_path = (Path(submission_path).expanduser().resolve() if submission_path
                           else score_path.parent / 'submission.json')
        submission, submission_file = core._read(submission_path)
        scores, score_file = core._read(score_path)
        method = submission['method']
        if method.get('kind') == 'sn':
            from .benchmark_submission import sn_submission_identity
            configuration = method['configuration']
            if configuration.get('mode') != manifest.get('mode') or manifest.get('mode') not in {'chunk', 'reasoning'}:
                raise ValueError('Submission mode differs from input SN run')
            identity = manifest.get('identity')
            if not isinstance(identity, dict):
                raise ValueError('Input SN run identity unavailable')
            expected = sn_submission_identity(identity)
            claimed = configuration.get('identity')
            if isinstance(claimed, dict):
                claimed = sn_submission_identity(claimed)
            if isinstance(claimed, dict) and 'qasper_evidence_recovery' in claimed and identity.get('qasper_evidence') is None:
                from .qasper_evidence import identity as evidence_identity
                expected['qasper_evidence_recovery'] = dict(origin='saved-run-readonly-export', **evidence_identity())
            if claimed != expected:
                raise ValueError('Submission method identity differs from input SN run')
        # Bind the supplied official artifacts to this run, not just to some
        # other admissible answer sheet. Partial/multi-run submissions can
        # cover a subset here, but every selected case must belong to this run.
        by_id = {r['case_id']: r for r in outputs}
        for row in submission['predictions']:
            saved = by_id.get(row['case_id'])
            if saved is None:
                if row['status'] == 'missing':
                    continue  # shared admission will reject incomplete generation
                raise ValueError('Submission case is not present in this run')
            if saved.get('status') != row['status']:
                raise ValueError('Submission status differs from saved run output')
            text = saved.get('prediction', '') if row['status'] in {'success', 'error'} else ''
            if text != row['prediction']:
                raise ValueError('Submission answer differs from saved run output')
            if 'run_id' in row and row['run_id'] != side['run_id']:
                raise ValueError('Submission run_id differs from input run')
            if 'outputs_sha256' in row and row['outputs_sha256'] != output_hash:
                raise ValueError('Submission output ledger hash differs from input run')
        if output_hash != (digest(outputs_path) if outputs_path.exists() else None):
            raise ValueError('Run output ledger changed during comparison; retry a stable observation')
        entry = dict(submission=submission, scores=scores,
                     source={'submission': submission_file, 'scores': score_file})
        side['scores_id'] = scores.get('scores_id')
        side['submission_id'] = scores.get('submission_id')
        side['_manifest'] = manifest
    except (OSError, ValueError, KeyError, TypeError) as exc:
        side['reason'] = _reason(exc)
    return side, entry


def _admit_side(bundle, side, entry):
    if entry is None or side['reason']:
        return None
    try:
        identity = side['_manifest'].get('identity')
        if not isinstance(identity, dict) or identity.get('source') != bundle['manifest']:
            raise ValueError('Run manifest uses a different frozen bundle')
        # The one-entry core performs the exact same formal admission as a
        # pair, including complete generation and observed-cost validation.
        admitted = core._compare_objects(bundle, [entry])
        side['admitted'] = True
        side['cases'] = [dict(case_id=r['case_id'], status=r['status'], metrics=r['metrics'])
                         for r in entry['scores']['per_case']]
        side['pending_metrics'] = list(entry['scores']['pending_metrics'])
        return admitted
    except (ValueError, KeyError, TypeError) as exc:
        side['reason'] = _reason(exc)
        return None


def compare_runs_flips(before, after, *, before_scores=None, after_scores=None,
                       before_submission=None, after_submission=None, bundle_directory=None):
    """Return a report for two run paths or run-ledger rows; never raises admission errors.

    Scores default to run/scores.json, submissions to scores.parent/submission.json.
    Ledger aggregates are not score artifacts; pass their official scores paths
    explicitly if they live elsewhere. With no --bundle, resolve the before
    run's immutable input through RunReadContext. A self comparison validates
    the original artifact once and projects its reflexive per-case identity;
    it neither renames methods nor modifies score hashes. Nonidentical attempts
    with the same method use the explicit flip comparison option.
    """
    left, a = _read_side(before, before_scores, before_submission)
    right, b = _read_side(after, after_scores, after_submission)
    report = dict(format=FORMAT, before=left, after=right, self_comparison=False,
                  admission={'status': 'not_admitted', 'reason': None}, cases=[], summary={}, comparison=None,
                  interpretation='Directions describe each official quality metric independently; '
                                 'statuses have no invented ranking; unavailable values are not zeros.')
    try:
        if bundle_directory is None:
            bundle_directory = RunReadContext().input_directory(_run_path(before))
        bundle = load_bundle(Path(bundle_directory).resolve())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report['admission']['reason'] = 'Frozen bundle unavailable: ' + _reason(exc)
    else:
        left_admission = _admit_side(bundle, left, a)
        right_admission = _admit_side(bundle, right, b)
        if not left_admission or not right_admission:
            report['admission']['reason'] = 'One or both runs failed formal admission; see before/after reason.'
        else:
            try:
                identical = a['submission'] == b['submission'] and a['scores'] == b['scores']
                if identical:
                    comparison = left_admission
                    report['self_comparison'] = True
                    metrics = comparison['paired_metrics']
                    paired = [dict(case_id=r['case_id'], group_id=r['group_id'], task=r['task'],
                                   left_status=r['status'], right_status=r['status'],
                                   left={k: r['metrics'][k] for k in metrics if k in r['metrics']},
                                   right={k: r['metrics'][k] for k in metrics if k in r['metrics']},
                                   differences={k: 0.0 for k in metrics if k in r['metrics']})
                              for r in a['scores']['per_case']]
                else:
                    comparison = core._compare_objects(bundle, [a, b], allow_method_attempts=True)
                    metrics = comparison['paired_metrics']
                    paired = comparison['comparisons'][0]['paired']
                report['admission'] = dict(status='admitted', reason=None)
                report['comparison'] = comparison
                report['summary'] = {k: dict(better=0, worse=0, unchanged=0, unavailable=0) for k in metrics}
                for row in paired:
                    flips = {}
                    for metric in metrics:
                        delta = row['differences'].get(metric)
                        direction = ('unavailable' if delta is None else
                                     'better' if delta > 0 else 'worse' if delta < 0 else 'unchanged')
                        flips[metric] = dict(before=row['left'].get(metric), after=row['right'].get(metric),
                                             difference=delta, direction=direction)
                        report['summary'][metric][direction] += 1
                    report['cases'].append(dict(case_id=row['case_id'], group_id=row['group_id'], task=row['task'],
                                                before_status=row['left_status'], after_status=row['right_status'],
                                                metrics=flips))
            except (ValueError, KeyError, TypeError) as exc:
                report['admission']['reason'] = _reason(exc)
    left.pop('_manifest', None)
    right.pop('_manifest', None)
    report['report_id'] = fingerprint(report)
    return report


def _cell(value):
    return str(value if value is not None else '—').replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')


def render_markdown(report):
    lines = ['# Run flips', '', f"Admission: {report['admission']['status']}", '']
    if report['admission']['reason']:
        lines.extend([_cell(report['admission']['reason']), ''])
    lines.extend(['| Metric | 变好 | 变差 | 不变 | 不可用 |', '|---|---:|---:|---:|---:|'])
    for metric, counts in report['summary'].items():
        lines.append('| ' + ' | '.join(map(_cell, [metric, *[counts[k] for k in ('better', 'worse', 'unchanged', 'unavailable')]])) + ' |')
    lines.extend(['', '| Case | Before status | After status | Metric | Before | After | Delta | Direction |',
                  '|---|---|---|---|---:|---:|---:|---|'])
    for row in report['cases']:
        for metric, flip in row['metrics'].items():
            lines.append('| ' + ' | '.join(map(_cell, [row['case_id'], row['before_status'], row['after_status'],
                                metric, flip['before'], flip['after'], flip['difference'], flip['direction']])) + ' |')
        if not row['metrics']:
            lines.append('| ' + ' | '.join(map(_cell, [row['case_id'], row['before_status'], row['after_status'],
                                                       None, None, None, None, 'unavailable'])) + ' |')
    for name in ('before', 'after'):
        side = report[name]
        lines.extend(['', f'## {name}', '', f"Readable: {side['readable']}; admitted: {side['admitted']}", ''])
        if side['reason']:
            lines.extend([_cell(side['reason']), ''])
        lines.extend(['| Case | Saved status | Validated metrics |', '|---|---|---|'])
        for row in side['cases']:
            lines.append('| ' + ' | '.join(map(_cell, [row['case_id'], row['status'], row['metrics']])) + ' |')
    lines.extend(['', report['interpretation'], ''])
    return '\n'.join(lines)


def write_flip_report(before, after, output_dir, **kwargs):
    """Write JSON and Markdown to a fresh directory outside all input artifacts."""
    output = Path(output_dir).resolve()
    inputs = [_run_path(before), _run_path(after)]
    for source in inputs:
        reject_shared_output(output, source=source)
    for key in ('bundle_directory', 'before_scores', 'after_scores', 'before_submission', 'after_submission'):
        if kwargs.get(key) is not None:
            artifact = Path(kwargs[key]).expanduser().resolve()
            inputs.append(artifact)
            if key != 'bundle_directory':
                inputs.append(artifact.parent)
    for value in (before, after):
        if isinstance(value, dict):
            score_path = value.get('official_scores_path') or value.get('scores_path')
            if score_path:
                artifact = Path(score_path).expanduser().resolve()
                inputs.extend([artifact, artifact.parent])
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in inputs):
        raise ValueError('Flip report output must be outside input artifacts')
    report = compare_runs_flips(before, after, **kwargs)
    output.mkdir(parents=True, exist_ok=False)
    save_json(output / 'report.json', report)
    (output / 'report.md').write_text(render_markdown(report), encoding='utf-8')
    return report
