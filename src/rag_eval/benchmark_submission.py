"""Frozen prediction submissions shared by SN, reference methods and scorers.

This boundary records observations and scope. It neither runs models nor decides
that two different methods are controlled experiments merely because IDs match.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

from .artifacts import save_json
from .starter_protocol import fingerprint

FORMAT = 'benchmark-submission-v1'
STATUSES = ('success', 'no_answer', 'clarification', 'error', 'missing')
METHOD_FIELDS = {'name', 'kind', 'citation_style', 'model_identity', 'input_policy', 'configuration'}


def _public_json(value):
    """Reject accidental credentials and invalid JSON at the public boundary."""
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError('Submission must contain finite JSON values') from exc
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError('Submission JSON object keys must be strings')
            if key.lower() in {'api_key', 'apikey', 'authorization', 'access_token', 'password', 'base_url'}:
                raise ValueError('Submission must not contain credentials or endpoint addresses')
            _public_json(item)
    elif isinstance(value, list):
        for item in value:
            _public_json(item)


def build_submission(bundle, *, method, predictions, case_ids=None):
    _public_json(method)
    if (not isinstance(method, dict) or set(method) != METHOD_FIELDS
            or method['kind'] not in {'sn', 'reference', 'published'}
            or method['citation_style'] not in {'sn', 'numeric', 'none'}
            or not isinstance(method['model_identity'], dict) or not method['model_identity']
            or not isinstance(method['configuration'], dict)
            or any(not isinstance(method[k], str) or not method[k].strip() for k in ('name', 'input_policy'))):
        raise ValueError('Explicit public method, model identity and input policy required')
    manifest = bundle['manifest']
    cases = bundle['cases']
    all_ids = [c['case_id'] for c in cases]
    if not all_ids or len(set(all_ids)) != len(all_ids):
        raise ValueError('Frozen bundle must contain distinct case IDs')
    if case_ids is None:
        selected = all_ids
    else:
        if (not isinstance(case_ids, (list, tuple)) or not case_ids
                or any(not isinstance(c, str) for c in case_ids)
                or len(set(case_ids)) != len(case_ids) or not set(case_ids) <= set(all_ids)):
            raise ValueError('Invalid submission case scope')
        selected = [cid for cid in all_ids if cid in set(case_ids)]
    if not isinstance(predictions, list):
        raise ValueError('Predictions must be a list')
    by_id = {}
    for row in predictions:
        _public_json(row)
        if not isinstance(row, dict) or row.get('case_id') not in selected:
            raise ValueError('Prediction has unknown or out-of-scope case ID')
        cid = row['case_id']
        if cid in by_id:
            raise ValueError('Duplicate prediction; choose an attempt explicitly')
        if row.get('status') not in STATUSES or not isinstance(row.get('prediction'), str):
            raise ValueError('Invalid prediction status or text')
        if row['status'] == 'success' and not row['prediction'].strip():
            raise ValueError('Successful prediction must have nonempty text')
        if row['status'] in {'missing', 'no_answer', 'clarification'} and row['prediction'].strip():
            raise ValueError('Unanswered status cannot carry a scored answer; retain observations separately')
        by_id[cid] = deepcopy(row)
    ordered = [by_id.get(cid, dict(case_id=cid, status='missing', prediction='')) for cid in selected]
    counts = Counter(row['status'] for row in ordered)
    coverage = {'planned': len(selected), **{s: counts[s] for s in STATUSES},
                'generation_complete': not (counts['missing'] or counts['error'])}
    return dict(format=FORMAT, suite=manifest['suite'], bundle_id=fingerprint(manifest),
                scope='full' if selected == all_ids else 'subset', case_ids=selected,
                method=deepcopy(method), method_id=fingerprint(method),
                predictions=ordered, coverage=coverage)


def validate_submission(bundle, submission):
    if submission.get('format') != FORMAT:
        raise ValueError('Unsupported submission format')
    if submission.get('bundle_id') != fingerprint(bundle['manifest']):
        raise ValueError('Submission refers to a different frozen bundle')
    rebuilt = build_submission(bundle, method=submission.get('method'),
                               predictions=submission.get('predictions'), case_ids=submission.get('case_ids'))
    for key in rebuilt:
        if submission.get(key) != rebuilt[key]:
            raise ValueError('Submission ' + key + ' differs from its reconstructed identity or coverage')
    if set(submission) != set(rebuilt):
        raise ValueError('Unexpected submission fields')
    return rebuilt


def write_submission(bundle_directory, output, *, method, predictions, case_ids=None):
    from .notebook_bundle import load_bundle
    bundle_directory, output = Path(bundle_directory).resolve(), Path(output).resolve()
    if output.is_relative_to(bundle_directory) or bundle_directory.is_relative_to(output):
        raise ValueError('Submission output must be outside input bundle')
    submission = build_submission(load_bundle(bundle_directory), method=method,
                                  predictions=predictions, case_ids=case_ids)
    output.mkdir(parents=True, exist_ok=False)
    save_json(output / 'submission.json', submission)
    return output / 'submission.json'


def export_sn_runs(bundle_directory, run_directories, output, *, case_ids=None, qasper_evidence=False):
    from .notebook_bundle import load_bundle
    from .starter_report import load_run
    bundle = load_bundle(bundle_directory)
    if qasper_evidence and not bundle.get('qasper_evidence_catalogues'):
        raise ValueError('Evidence recovery requires a QASPER v3 bundle')
    cases = {c['case_id']: c for c in bundle['cases']}
    documents = {d['id']: d for d in bundle['documents']}
    paths = [Path(p).resolve() for p in run_directories]
    if not paths or len(paths) != len(set(paths)):
        raise ValueError('Distinct nonempty SN run directories required')
    output = Path(output).resolve()
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in paths):
        raise ValueError('Submission output must be outside all input runs')
    family = None
    method = None
    predictions = []
    for path in paths:
        run = load_run(path)
        manifest = run['manifest']
        if not manifest or manifest.get('mode') not in {'chunk', 'reasoning'}:
            raise ValueError('SN chunk/reasoning run required')
        identity = manifest['identity']
        if identity.get('notebook_context', {}).get('request_revision') != 'notebook-request-v3':
            raise ValueError('Official SN submissions require gold-free notebook-request-v3 runs')
        if identity['source'] != bundle['manifest']:
            raise ValueError('SN run uses a different frozen bundle')
        config = deepcopy(identity)
        config.pop('product_bundle', None)
        context = config['notebook_context']
        context.pop('partition_id', None)
        context.pop('case_ids', None)
        evidence_policy = identity.get('qasper_evidence')
        recovering = qasper_evidence and evidence_policy is None
        if recovering:
            from .qasper_evidence import identity as evidence_identity
            evidence_policy = evidence_identity()
            config['qasper_evidence_recovery'] = dict(origin='saved-run-readonly-export', **evidence_policy)
        current = fingerprint({'identity': config, 'mode': manifest['mode']})
        if family is not None and family != current:
            raise ValueError('SN submissions cannot mix modes, models, requests or source revisions')
        family = current
        method = dict(name='sn-' + manifest['mode'], kind='sn', citation_style='sn',
                      model_identity={'service_config_sha256': identity['product_services'],
                                      'runtime_settings_sha256': identity['runtime_settings'],
                                      'verification': 'configuration-hashes; not matched to reference model'},
                      input_policy='frozen-source-documents',
                      configuration={'mode': manifest['mode'], 'identity': config})
        if evidence_policy is not None:
            method['configuration']['qasper_evidence'] = evidence_policy
        for row in run['outputs']:
            status = row['status']
            record = deepcopy(row.get('product_record', {}))
            if recovering:
                from .qasper_evidence import recover_saved_evidence
                if 'qasper_evidence' in record:
                    raise ValueError('Unclaimed evidence snapshot cannot be overwritten by recovery')
                did, = cases[row['case_id']]['material_document_ids']
                captured = recover_saved_evidence(path, record, documents[did], bundle['qasper_evidence_catalogues'][did])
                record['qasper_evidence'] = captured
                record.pop('predicted_evidence', None)
                if captured['status'] == 'complete':
                    record['predicted_evidence'] = captured['projection']['predicted_evidence']
            text = row.get('prediction', '')
            predictions.append(dict(case_id=row['case_id'], status=status,
                                    prediction=text if status in {'success', 'error'} else '',
                                    record=record, run_id=manifest['run_id']))
    return write_submission(bundle_directory, output, method=method,
                            predictions=predictions, case_ids=case_ids)
