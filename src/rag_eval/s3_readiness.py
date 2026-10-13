"""Offline readiness decision for the E5-S3 single-case smoke gate.

This module replays already-observed facts.  It never imports Silicon
Notebook, resolves credentials, inspects the host environment, opens a socket,
starts a process or writes Job/UI state.  A supplied loaded model identity is
checked through the existing
``runtime_model_identity.validate_loaded_model_identity`` boundary instead of
re-parsing registry or Settings.

Input format ``sn-e5-s3-readiness-input-v1`` carries only a closed projection:
scope refs, the declared config projection, the supplied loaded identity (or
``None`` when it was not observed), credential variable names with existence
states and service reachability states.  Credential values and plaintext
endpoints are not accepted.

Receipt format ``sn-e5-s3-readiness-receipt-v1`` retains the declared config,
the validated effective Settings, strict registry binding gaps, credential
variable names/existence, reachability, blocking reasons and a fingerprint.
Decision precedence is ``blocked`` over ``unknown`` over ``ready``.  A receipt
is ``ready`` only when catalog evidence, effective Settings, complete strict
bindings, credential existence and necessary reachability are all explicitly
observed and satisfied.  The declared observation flags are retained as
declaration-stage facts; they never promote absent or failing concrete
evidence to ``ready``.
"""
from __future__ import annotations

import copy
import re

from .identity import fingerprint
from .runtime_model_identity import validate_loaded_model_identity


INPUT_FORMAT = 'sn-e5-s3-readiness-input-v1'
RECEIPT_FORMAT = 'sn-e5-s3-readiness-receipt-v1'
CATALOG_EVIDENCE = ('external_evidence_answer', 'gap_consult_query')

_INPUT_KEYS = (
    'format', 'scope', 'declared_config', 'loaded_model_identity', 'credentials', 'reachability',
)
_SCOPE_KEYS = (
    'case_id', 'partition_id', 'request_revision', 'bundle_id', 'index_id',
    'sn_source_manifest_sha256',
)
_DECLARED_KEYS = (
    'source', 'config_sha256', 'service_count', 'credential_variable_names_only',
    'endpoint_plaintext_recorded', 'missing_catalog_bindings', 'strict_registry_exemption',
    'effective_settings_observed', 'credential_resolution_observed', 'reachability_probed',
)
_CREDENTIAL_KEYS = ('variable_name', 'status')
_REACHABILITY_KEYS = ('service_id', 'status')
_CREDENTIAL_STATES = ('configured', 'absent', 'unknown')
_REACHABILITY_STATES = ('reachable', 'unreachable', 'unknown')
_LOADED_STATES = ('observed', 'absent', 'invalid')
_STATUSES = ('ready', 'unknown', 'blocked')
_REASON_SEVERITIES = ('blocked', 'unknown')
_LIMITS = {
    'model_quality': 'not_claimed',
    'official_score': 'not_claimed',
    'job_executable': 'not_claimed',
    'live_probe': 'not_performed',
    'credential_values': 'never_accepted_or_emitted',
    'environment_read': 'not_read',
}
_RECEIPT_KEYS = (
    'format', 'status', 'scope', 'declared_config', 'loaded_identity_status',
    'loaded_identity_fingerprint', 'loaded_model_identity', 'effective_settings',
    'model_bindings_strict', 'binding_gaps', 'catalog_evidence', 'credentials',
    'reachability', 'blocking_reasons', 'limits', 'fingerprint',
)
_EVIDENCE_KEYS = ('required', 'bound', 'unverified')

_HASH = re.compile(r'[a-f0-9]{64}\Z')
_ID = re.compile(r'[a-z][a-z0-9_]{0,63}\Z')
_ENV = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
_LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}\Z')
_SECRET = re.compile(r'(?i)(?:(?:sk|rk|pk|ak)-|ghp_|hf_|xox|AIza)[A-Za-z0-9_-]{8,}')


def _keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError('Invalid ' + label + ' schema')


def _hash(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError('Invalid readiness hash')


def _label(value, name):
    if not isinstance(value, str) or not _LABEL.fullmatch(value) or _SECRET.search(value):
        raise ValueError('Invalid ' + name)


def _validate_scope(value):
    _keys(value, _SCOPE_KEYS, 'readiness scope')
    for name in ('case_id', 'partition_id', 'request_revision'):
        _label(value[name], 'readiness scope label')
    for name in ('bundle_id', 'index_id', 'sn_source_manifest_sha256'):
        _hash(value[name])
    return {name: value[name] for name in _SCOPE_KEYS}


def _validate_declared(value):
    _keys(value, _DECLARED_KEYS, 'declared config projection')
    _label(value['source'], 'declared config source')
    _hash(value['config_sha256'])
    count = value['service_count']
    if type(count) is not int or count < 1:
        raise ValueError('Invalid declared service count')
    for name in ('credential_variable_names_only', 'endpoint_plaintext_recorded',
                 'strict_registry_exemption'):
        if type(value[name]) is not bool:
            raise ValueError('Invalid declared config flag')
    for name in ('effective_settings_observed', 'credential_resolution_observed',
                 'reachability_probed'):
        if type(value[name]) is not bool:
            raise ValueError('Invalid declared observation flag')
    missing = value['missing_catalog_bindings']
    if (not isinstance(missing, list)
            or any(not isinstance(name, str) or not _ID.fullmatch(name) for name in missing)
            or missing != sorted(set(missing))):
        raise ValueError('Invalid declared missing catalog bindings')
    return {name: (list(value[name]) if name == 'missing_catalog_bindings' else value[name])
            for name in _DECLARED_KEYS}


def _validate_credentials(value):
    if not isinstance(value, list):
        raise ValueError('Invalid credential observation list')
    entries, seen = [], set()
    for entry in value:
        _keys(entry, _CREDENTIAL_KEYS, 'credential observation')
        name = entry['variable_name']
        if not isinstance(name, str) or not _ENV.fullmatch(name):
            raise ValueError('Invalid credential variable name')
        if entry['status'] not in _CREDENTIAL_STATES:
            raise ValueError('Invalid credential existence status')
        if name in seen:
            raise ValueError('Duplicate credential variable observation')
        seen.add(name)
        entries.append({'variable_name': name, 'status': entry['status']})
    if [entry['variable_name'] for entry in entries] != sorted(seen):
        raise ValueError('Unsorted credential observations')
    return entries


def _validate_reachability(value):
    if not isinstance(value, list):
        raise ValueError('Invalid reachability observation list')
    entries, seen = [], set()
    for entry in value:
        _keys(entry, _REACHABILITY_KEYS, 'reachability observation')
        service_id = entry['service_id']
        if not isinstance(service_id, str) or not _ID.fullmatch(service_id):
            raise ValueError('Invalid reachability service id')
        if entry['status'] not in _REACHABILITY_STATES:
            raise ValueError('Invalid reachability status')
        if service_id in seen:
            raise ValueError('Duplicate reachability service observation')
        seen.add(service_id)
        entries.append({'service_id': service_id, 'status': entry['status']})
    if [entry['service_id'] for entry in entries] != sorted(seen):
        raise ValueError('Unsorted reachability observations')
    return entries


def _reason(code, severity, subject=None):
    return {'code': code, 'severity': severity, 'subject': subject}


def _classify(declared, identity, credentials, reachability, loaded_status):
    """Classify validated facts; ``identity`` is a validated record or ``None``."""
    reasons = []
    if not declared['credential_variable_names_only']:
        reasons.append(_reason('credential_values_declared', 'blocked'))
    if declared['endpoint_plaintext_recorded']:
        reasons.append(_reason('endpoint_plaintext_declared', 'blocked'))
    if declared['strict_registry_exemption']:
        reasons.append(_reason('strict_registry_exempted', 'blocked'))
    declared_missing = set(declared['missing_catalog_bindings'])
    for name in CATALOG_EVIDENCE:
        if name in declared_missing:
            reasons.append(_reason('missing_catalog_evidence', 'blocked', name))
    if loaded_status == 'invalid':
        reasons.append(_reason('loaded_identity_invalid', 'blocked'))

    identity_fingerprint = None
    effective_settings = None
    strict = None
    binding_gaps = []
    bound, unverified = [], []
    if identity is not None:
        identity_fingerprint = identity['fingerprint']
        effective_settings = dict(identity['effective_settings'])
        strict = effective_settings['model_bindings_strict']
        binding_gaps = list(identity['missing_workloads'])
        if not strict:
            reasons.append(_reason('strict_bindings_disabled', 'blocked'))
        if identity['declared_config_sha256'] != declared['config_sha256']:
            reasons.append(_reason('declared_config_mismatch', 'blocked'))
        if len(identity['services']) != declared['service_count']:
            reasons.append(_reason('service_count_mismatch', 'blocked'))
        for name in binding_gaps:
            reasons.append(_reason('binding_gap', 'blocked', name))
        workloads = {workload['workload_id'] for workload in identity['workloads']}
        for name in CATALOG_EVIDENCE:
            if name in workloads and name not in binding_gaps:
                bound.append(name)
            elif name not in declared_missing:
                unverified.append(name)
                reasons.append(_reason('missing_catalog_evidence', 'blocked', name))
    else:
        if loaded_status == 'absent':
            reasons.append(_reason('settings_unknown', 'unknown'))
        for name in CATALOG_EVIDENCE:
            if name not in declared_missing:
                unverified.append(name)
                reasons.append(_reason('catalog_evidence_unverified', 'unknown', name))

    observed_names = {entry['variable_name'] for entry in credentials}
    if identity is not None:
        required_names = {service['credential_variable_name'] for service in identity['services']
                          if service['credential_variable_name'] is not None}
        for name in sorted(required_names - observed_names):
            reasons.append(_reason('credential_unknown', 'unknown', name))
        for name in sorted(observed_names - required_names):
            reasons.append(_reason('credential_variable_unknown', 'blocked', name))
    for entry in credentials:
        if entry['status'] == 'unknown':
            reasons.append(_reason('credential_unknown', 'unknown', entry['variable_name']))
        elif entry['status'] == 'absent':
            reasons.append(_reason('credential_absent', 'blocked', entry['variable_name']))

    observed_services = {entry['service_id'] for entry in reachability}
    if identity is not None:
        required_services = {service['service_id'] for service in identity['services']}
        for service_id in sorted(required_services - observed_services):
            reasons.append(_reason('reachability_unknown', 'unknown', service_id))
        for service_id in sorted(observed_services - required_services):
            reasons.append(_reason('reachability_service_unknown', 'blocked', service_id))
    for entry in reachability:
        if entry['status'] == 'unknown':
            reasons.append(_reason('reachability_unknown', 'unknown', entry['service_id']))
        elif entry['status'] == 'unreachable':
            reasons.append(_reason('service_unreachable', 'blocked', entry['service_id']))

    unique = {(reason['code'], reason['subject']): reason for reason in reasons}
    reasons = [unique[key] for key in sorted(unique, key=lambda item: (item[0], item[1] or ''))]
    status = ('blocked' if any(reason['severity'] == 'blocked' for reason in reasons)
              else 'unknown' if reasons else 'ready')
    return {
        'status': status,
        'loaded_identity_status': loaded_status,
        'loaded_identity_fingerprint': identity_fingerprint,
        'effective_settings': effective_settings,
        'model_bindings_strict': strict,
        'binding_gaps': binding_gaps,
        'catalog_evidence': {
            'required': list(CATALOG_EVIDENCE),
            'bound': sorted(bound),
            'unverified': sorted(unverified),
        },
        'blocking_reasons': reasons,
    }


def _evaluate(value):
    _keys(value, _INPUT_KEYS, 'readiness input')
    if value['format'] != INPUT_FORMAT:
        raise ValueError('Unknown readiness input format')
    scope = _validate_scope(value['scope'])
    declared = _validate_declared(value['declared_config'])
    credentials = _validate_credentials(value['credentials'])
    reachability = _validate_reachability(value['reachability'])
    identity_input = value['loaded_model_identity']
    if identity_input is not None and not isinstance(identity_input, dict):
        raise ValueError('Invalid loaded model identity schema')
    loaded_status, identity = 'absent', None
    if identity_input is not None:
        try:
            identity = copy.deepcopy(validate_loaded_model_identity(identity_input))
            loaded_status = 'observed'
        except (TypeError, ValueError):
            loaded_status = 'invalid'
    return scope, declared, credentials, reachability, identity, _classify(
        declared, identity, credentials, reachability, loaded_status)


def decide_readiness(value) -> dict:
    """Validate a closed readiness input and return a sealed receipt.

    The decision is derived only from the supplied projection.  It does not
    observe a host, resolve a credential, call a service or execute a Job.
    """
    scope, declared, credentials, reachability, identity, result = _evaluate(value)
    receipt = {
        'format': RECEIPT_FORMAT,
        'status': result['status'],
        'scope': scope,
        'declared_config': declared,
        'loaded_identity_status': result['loaded_identity_status'],
        'loaded_identity_fingerprint': result['loaded_identity_fingerprint'],
        'loaded_model_identity': identity,
        'effective_settings': result['effective_settings'],
        'model_bindings_strict': result['model_bindings_strict'],
        'binding_gaps': result['binding_gaps'],
        'catalog_evidence': result['catalog_evidence'],
        'credentials': credentials,
        'reachability': reachability,
        'blocking_reasons': result['blocking_reasons'],
        'limits': dict(_LIMITS),
    }
    receipt['fingerprint'] = fingerprint(receipt)
    return validate_readiness_receipt(receipt)


def _validate_receipt_reasons(reasons):
    if not isinstance(reasons, list):
        raise ValueError('Invalid blocking reasons')
    for reason in reasons:
        _keys(reason, ('code', 'severity', 'subject'), 'blocking reason')
        if not isinstance(reason['code'], str) or not _ID.fullmatch(reason['code']):
            raise ValueError('Invalid blocking reason code')
        if reason['severity'] not in _REASON_SEVERITIES:
            raise ValueError('Invalid blocking reason severity')
        subject = reason['subject']
        if subject is not None and (not isinstance(subject, str)
                                    or not (_ID.fullmatch(subject) or _ENV.fullmatch(subject))
                                    or _SECRET.search(subject)):
            raise ValueError('Invalid blocking reason subject')


def validate_readiness_receipt(record) -> dict:
    """Recheck a saved receipt offline without observing a host."""
    _keys(record, _RECEIPT_KEYS, 'readiness receipt')
    if record['format'] != RECEIPT_FORMAT or record['status'] not in _STATUSES:
        raise ValueError('Unknown readiness receipt format/status')
    _hash(record['fingerprint'])
    try:
        digest = fingerprint({key: item for key, item in record.items() if key != 'fingerprint'})
    except (TypeError, ValueError):
        raise ValueError('Invalid readiness receipt serialization') from None
    if digest != record['fingerprint']:
        raise ValueError('Readiness receipt fingerprint mismatch')
    if record['limits'] != _LIMITS:
        raise ValueError('Invalid readiness observation limits')
    if record['loaded_identity_status'] not in _LOADED_STATES:
        raise ValueError('Unknown loaded identity observation status')

    scope = _validate_scope(record['scope'])
    declared = _validate_declared(record['declared_config'])
    credentials = _validate_credentials(record['credentials'])
    reachability = _validate_reachability(record['reachability'])
    _validate_receipt_reasons(record['blocking_reasons'])

    identity = record['loaded_model_identity']
    loaded_status = record['loaded_identity_status']
    if loaded_status == 'observed':
        if not isinstance(identity, dict):
            raise ValueError('Observed loaded identity record is missing')
        try:
            identity = validate_loaded_model_identity(identity)
        except (TypeError, ValueError):
            raise ValueError('Invalid embedded loaded identity record') from None
    else:
        if identity is not None:
            raise ValueError('Unobserved loaded identity cannot carry a record')

    evidence = record['catalog_evidence']
    _keys(evidence, _EVIDENCE_KEYS, 'catalog evidence')
    if evidence['required'] != list(CATALOG_EVIDENCE):
        raise ValueError('Invalid required catalog evidence')

    result = _classify(declared, identity, credentials, reachability, loaded_status)
    expected = {
        'status': result['status'],
        'scope': scope,
        'declared_config': declared,
        'loaded_identity_status': result['loaded_identity_status'],
        'loaded_identity_fingerprint': result['loaded_identity_fingerprint'],
        'loaded_model_identity': identity,
        'effective_settings': result['effective_settings'],
        'model_bindings_strict': result['model_bindings_strict'],
        'binding_gaps': result['binding_gaps'],
        'catalog_evidence': result['catalog_evidence'],
        'credentials': credentials,
        'reachability': reachability,
        'blocking_reasons': result['blocking_reasons'],
    }
    for name, expected_value in expected.items():
        if record[name] != expected_value:
            raise ValueError('Readiness receipt facts are inconsistent')
    return record
