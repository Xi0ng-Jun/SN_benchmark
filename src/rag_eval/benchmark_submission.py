"""Frozen prediction submissions shared by SN, reference methods and scorers.

This boundary records observations and scope. It neither runs models nor decides
that two different methods are controlled experiments merely because IDs match.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

from .artifacts import digest, save_json
from .identity import fingerprint
from .scoring_projection import PROTOCOL as PROJECTION_PROTOCOL, project_record, validate_public_json, validate_record_labels

FORMAT = 'benchmark-submission-v1'
STATUSES = ('success', 'no_answer', 'clarification', 'error', 'missing')
METHOD_FIELDS = {'name', 'kind', 'citation_style', 'model_identity', 'input_policy', 'configuration'}


def _public_json(value):
    """Reject accidental credentials and invalid JSON at the public boundary."""
    validate_public_json(value)


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
    all_id_set = set(all_ids)
    if not all_ids or len(all_id_set) != len(all_ids):
        raise ValueError('Frozen bundle must contain distinct case IDs')
    if case_ids is None:
        selected = all_ids
    else:
        if (not isinstance(case_ids, (list, tuple)) or not case_ids
                or any(not isinstance(c, str) for c in case_ids)
                or len(set(case_ids)) != len(case_ids) or not set(case_ids) <= all_id_set):
            raise ValueError('Invalid submission case scope')
        requested = set(case_ids)
        selected = [cid for cid in all_ids if cid in requested]
    selected_ids = set(selected)
    if not isinstance(predictions, list):
        raise ValueError('Predictions must be a list')
    from .oracle_track import require_formal_submission
    require_formal_submission(method, predictions)
    by_id = {}
    for row in predictions:
        _public_json(row)
        if not isinstance(row, dict) or row.get('case_id') not in selected_ids:
            raise ValueError('Prediction has unknown or out-of-scope case ID')
        if 'record' in row:
            validate_record_labels(row['record'])
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


def write_submission(bundle_directory, output, *, method, predictions, case_ids=None, context=None):
    from .run_reader import RunReadContext
    bundle_directory, output = Path(bundle_directory).resolve(), Path(output).resolve()
    if output.is_relative_to(bundle_directory) or bundle_directory.is_relative_to(output):
        raise ValueError('Submission output must be outside input bundle')
    context = context or RunReadContext()
    from .artifact_store import reject_shared_output
    reject_shared_output(output, store_roots=context.artifact_roots)
    submission = build_submission(context.bundle(bundle_directory), method=method,
                                  predictions=predictions, case_ids=case_ids)
    output.mkdir(parents=True, exist_ok=False)
    save_json(output / 'submission.json', submission)
    return output / 'submission.json'


def drop_constant_identity_fields(identity):
    """Identity without fields that never distinguished runs.

    ``audits_sha256`` was always the fingerprint of an empty audit list and is
    no longer written; ``code.actual_manifest`` is represented by
    ``code.source_manifest_sha256`` (checked to agree before it is dropped).
    Old and new runs of the same conditions therefore project identically.
    """
    from .source_identity import compact_code_identity
    config = dict(identity)
    if config.get('audits_sha256') == fingerprint([]):
        config.pop('audits_sha256')
    if isinstance(config.get('code'), dict):
        config['code'] = compact_code_identity(config['code'])
    return config


def sn_submission_identity(identity):
    """Keep answer conditions together; retain execution details on each run."""
    config = deepcopy(drop_constant_identity_fields(identity))
    config.pop('product_bundle', None)
    config.pop('preparation_policy', None)
    if isinstance(config.get('code'), dict):
        config['code'].pop('benchmark_source_hashes', None)
    if isinstance(config.get('runtime_dependencies'), dict):
        # Startup hashes include timestamps and evaluator checkout/search paths.
        # load_run validates the original observation; dependency declarations
        # and code.versions still distinguish the environment used for answers.
        for key in ('observation_sha256', 'environment_sha256', 'run_coordinate'):
            config['runtime_dependencies'].pop(key, None)
    if isinstance(config.get('notebook_context'), dict):
        config['notebook_context'].pop('partition_id', None)
        config['notebook_context'].pop('case_ids', None)
    return config


def export_sn_runs(bundle_directory, run_directories, output, *, case_ids=None, qasper_evidence=False,
                   context=None):
    """``context`` lets a caller exporting many partitions reuse one read cache."""
    from .run_reader import RunReadContext
    from .run_report import load_run
    context = context or RunReadContext()
    bundle_directory = Path(bundle_directory).resolve()
    bundle = context.bundle(bundle_directory)
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
    preparation_policies = set()
    evaluator_sources = set()
    for path in paths:
        # Diagnostic provenance must be checked before the formal run reader or
        # any answer projection, including an otherwise valid Notebook run.
        manifest_path = path / 'manifest.json'
        if manifest_path.is_file():
            saved_manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            from .oracle_track import require_formal_run
            require_formal_run(saved_manifest)
        outputs_path = path / 'outputs.jsonl'
        outputs_hash = digest(outputs_path) if outputs_path.exists() else None
        run = load_run(path, context=context, include_model_events=False)
        if outputs_hash != (digest(outputs_path) if outputs_path.exists() else None):
            raise ValueError('SN output ledger changed during export; retry a stable observation')
        manifest = run['manifest']
        if not manifest or manifest.get('mode') not in {'chunk', 'reasoning'}:
            raise ValueError('SN chunk/reasoning run required')
        identity = manifest['identity']
        if identity.get('notebook_context', {}).get('request_revision') != 'notebook-request-v3':
            raise ValueError('Official SN submissions require gold-free notebook-request-v3 runs')
        if identity['source'] != bundle['manifest']:
            raise ValueError('SN run uses a different frozen bundle')
        config = sn_submission_identity(identity)
        preparation_policy = identity.get('preparation_policy', 'per-source-metadata-v1')
        evaluator_hashes = identity.get('code', {}).get('benchmark_source_hashes')
        evaluator_source = fingerprint(evaluator_hashes) if evaluator_hashes is not None else None
        provenance = dict(identity_sha256=fingerprint(identity), preparation_policy=preparation_policy,
                          evaluator_source_sha256=evaluator_source)
        if manifest.get('prepared_seed') is not None:
            # Answers were asked again on a copied prepared notebook.
            provenance['prepared_seed'] = deepcopy(manifest['prepared_seed'])
        if manifest.get('prepared_from') is not None:
            # The prepared notebook came from the content-addressed cache.
            provenance['prepared_from'] = deepcopy(manifest['prepared_from'])
        preparation_policies.add(preparation_policy)
        if evaluator_source is not None:
            evaluator_sources.add(evaluator_source)
        evidence_policy = identity.get('qasper_evidence')
        recovering = qasper_evidence and evidence_policy is None
        if recovering:
            from .qasper_evidence import identity as evidence_identity
            evidence_policy = evidence_identity()
            config['qasper_evidence_recovery'] = dict(origin='saved-run-readonly-export', **evidence_policy)
        current = fingerprint({'identity': config, 'mode': manifest['mode']})
        if family is not None and family != current:
            raise ValueError('SN submissions cannot mix modes, models, requests or SN source revisions')
        family = current
        protocol_info = identity.get('reasoning_protocol')
        method_name = 'sn-' + (
            protocol_info.get('name') if isinstance(protocol_info, dict) else manifest['mode']
        )
        method_configuration = {'mode': manifest['mode'], 'identity': config,
                                'scoring_projection': PROJECTION_PROTOCOL,
                                'comparison_category': 'controlled-rerun'}
        if isinstance(protocol_info, dict):
            method_configuration['reasoning_protocol'] = deepcopy(protocol_info)
        method = dict(name=method_name, kind='sn', citation_style='sn',
                      model_identity={'service_config_sha256': identity['product_services'],
                                      'runtime_settings_sha256': identity['runtime_settings'],
                                      'verification': 'configuration-hashes; not matched to reference model'},
                      input_policy='frozen-source-documents',
                      configuration=method_configuration)
        if evidence_policy is not None:
            method['configuration']['qasper_evidence'] = evidence_policy
        if identity.get('hotpot_evidence') is not None:
            method['configuration']['hotpot_evidence'] = identity['hotpot_evidence']
        if identity.get('multihop_retrieval') is not None:
            method['configuration']['multihop_retrieval'] = identity['multihop_retrieval']
            method['configuration']['retrieval_contract'] = identity['multihop_retrieval']['policy']
        for row in run['outputs']:
            status = row['status']
            record = project_record(bundle['manifest']['suite'], row.get('product_record', {}))
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
                                    record=record, run_id=manifest['run_id'], outputs_sha256=outputs_hash,
                                    run_provenance=provenance))
    method['configuration']['preparation_policies'] = sorted(preparation_policies)
    method['configuration']['evaluator_source_sha256s'] = sorted(evaluator_sources)
    return write_submission(bundle_directory, output, method=method,
                            predictions=predictions, case_ids=case_ids, context=context)
