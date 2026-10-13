"""Offline SN feature facts and the three comparison identity tiers.

No SN imports, environment reads, model calls or file writes occur here. Effective
Settings take precedence over declared runner overrides. Old runtime receipts
only have overrides; ``sources`` explicitly distinguishes that weaker evidence.
Neither feature switches nor equal startup service generations prove execution
or remote weights. Existing bundle/scorer/status/denominator validation remains
with the caller; these functions do not grant formal score admission.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

from .identity import fingerprint

FORMAT = 'sn-feature-set-v1'
COMPARISON_FORMAT = 'sn-comparison-identity-tiers-v1'
_BOOL_FIELDS = (
    'chunk_kg_overlay_enabled', 'kg_auto_extract', 'agent_profile_enabled',
    'user_search_profile_enabled', 'retrieval_experience_enabled',
    'retrieval_experience_inject_enabled', 'reasoning_consult_memory_enabled',
    'llm_cache_enabled',
)
_MODE_FIELD = 'generated_question_index_mode'
FEATURE_SETTING_FIELDS = (*_BOOL_FIELDS, _MODE_FIELD)
_FEATURE_FIELDS = (*FEATURE_SETTING_FIELDS, 'thinking_modes')
_INDEX_MODES = ('off', 'shadow', 'on')
_THINKING_MODES = ('enabled', 'disabled', 'provider_default')
_SOURCES = ('effective_settings', 'loaded_model_services.effective_settings',
            'overrides', 'loaded_model_services.bindings', 'unknown')
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_ID = re.compile(r'[a-z][a-z0-9_]{0,63}\Z')
_LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,127}\Z')
_SECRET = re.compile(r'(?i)(?:(?:sk|rk|pk|ak)-|ghp_|hf_|xox|AIza)[A-Za-z0-9_-]{8,}')


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError('Invalid ' + label + ' object')
    return value


def _digest(value):
    try:
        return fingerprint(value)
    except (TypeError, ValueError):
        raise ValueError('Invalid identity serialization') from None


def _optional_hash(value):
    if value is not None and (not isinstance(value, str) or not _HASH.fullmatch(value)):
        raise ValueError('Invalid identity hash')
    return value


def _label(value):
    if value is not None and (not isinstance(value, str) or not _LABEL.fullmatch(value)
                              or _SECRET.search(value) or '//' in value or '..' in value):
        raise ValueError('Invalid public identity label')
    return value


def _model_observation(runtime):
    if 'loaded_model_services' not in runtime:
        return {}
    loaded = _object(runtime['loaded_model_services'], 'loaded model services')
    return _object(loaded.get('observation', {}), 'loaded model observation')


def _feature_value(field, value, *, override=False):
    if field in _BOOL_FIELDS:
        if override and isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in ('true', '1', 'yes', 'on'):
                return True
            if normalized in ('false', '0', 'no', 'off'):
                return False
        if type(value) is bool:
            return value
    elif isinstance(value, str) and value in _INDEX_MODES:
        return value
    raise ValueError('Invalid feature observation: ' + field)


def _thinking(observation):
    if 'bindings' not in observation:
        return None
    bindings = observation['bindings']
    if not isinstance(bindings, list):
        raise ValueError('Invalid thinking bindings')
    modes = {}
    for binding in bindings:
        binding = _object(binding, 'thinking binding')
        name, mode = binding.get('workload_id'), binding.get('thinking_mode')
        if (not isinstance(name, str) or not _ID.fullmatch(name) or name in modes
                or not isinstance(mode, str) or mode not in _THINKING_MODES
                or binding.get('status') not in ('bound', 'missing')):
            raise ValueError('Invalid/duplicate thinking binding')
        modes[name] = mode if binding['status'] == 'bound' else None
    return dict(sorted(modes.items()))


def _project(runtime_identity, effective_settings):
    runtime = _object(runtime_identity, 'runtime identity')
    overrides = _object(runtime.get('overrides', {}), 'runtime overrides')
    observed = _model_observation(runtime)
    effective = _object(runtime.get('effective_settings', {}), 'effective settings')
    nested = _object(observed.get('effective_settings', {}), 'loaded effective settings')
    if effective_settings is not None:
        effective = _object(effective_settings, 'effective settings')
    features, sources = {}, {}
    for field in FEATURE_SETTING_FIELDS:
        if field in effective:
            features[field] = _feature_value(field, effective[field])
            sources[field] = 'effective_settings'
            if field in nested and _feature_value(field, nested[field]) != features[field]:
                raise ValueError('Conflicting effective feature observations: ' + field)
        elif field in nested:
            features[field] = _feature_value(field, nested[field])
            sources[field] = 'loaded_model_services.effective_settings'
        elif field.upper() in overrides:
            features[field] = _feature_value(field, overrides[field.upper()], override=True)
            sources[field] = 'overrides'
        else:
            features[field], sources[field] = None, 'unknown'
    features['thinking_modes'] = _thinking(observed)
    sources['thinking_modes'] = ('unknown' if features['thinking_modes'] is None
                                 else 'loaded_model_services.bindings')
    return features, sources


def derive_feature_set(runtime_identity: dict, *, effective_settings: dict | None = None) -> dict:
    """Return fixed component switches and workload thinking modes.

    ``None`` means unobserved, not disabled. Index modes off/shadow/on remain
    distinct. A missing service binding has no active thinking mode. Supplied
    effective Settings are a mapping of already observed values, never an env
    lookup; only the allowlisted feature fields are consumed.
    """
    return _project(runtime_identity, effective_settings)[0]


def build_feature_identity(runtime_identity: dict, *, effective_settings: dict | None = None) -> dict:
    """Build the detached record for ``manifest.identity['feature_set']``.

    Persist the same allowlisted effective Settings in runtime-identity.json
    when passing them separately, so a reader can reproduce this record.
    Fingerprint includes observation sources; equal declarations and actual
    observations are deliberately distinguishable in disclosure.
    """
    features, sources = _project(runtime_identity, effective_settings)
    record = {'format': FORMAT, 'feature_set': features, 'sources': sources}
    return {**record, 'fingerprint': _digest(record)}


def validate_feature_identity(record: dict) -> dict:
    """Check a saved record offline; malformed facts raise a redacted ValueError."""
    _object(record, 'feature identity')
    if set(record) != {'format', 'feature_set', 'sources', 'fingerprint'} or record['format'] != FORMAT:
        raise ValueError('Invalid feature identity schema')
    if _optional_hash(record['fingerprint']) != _digest({k: v for k, v in record.items() if k != 'fingerprint'}):
        raise ValueError('Feature identity fingerprint mismatch')
    features, sources = record['feature_set'], record['sources']
    if (not isinstance(features, dict) or set(features) != set(_FEATURE_FIELDS)
            or not isinstance(sources, dict) or set(sources) != set(_FEATURE_FIELDS)):
        raise ValueError('Invalid feature identity fields')
    for name, value in features.items():
        source = sources[name]
        if not isinstance(source, str) or source not in _SOURCES or ((source == 'unknown') != (value is None)):
            raise ValueError('Inconsistent feature observation source')
        if name == 'thinking_modes':
            if source not in ('unknown', 'loaded_model_services.bindings'):
                raise ValueError('Invalid thinking observation source')
            if value is not None and (not isinstance(value, dict) or any(
                not isinstance(k, str) or not _ID.fullmatch(k)
                or (v is not None and (not isinstance(v, str) or v not in _THINKING_MODES))
                for k, v in value.items())):
                raise ValueError('Invalid thinking modes')
        elif value is not None:
            if source == 'loaded_model_services.bindings':
                raise ValueError('Invalid feature observation source')
            _feature_value(name, value)
    return deepcopy(record)


def build_comparison_identity(manifest: dict, *, case_ids: list[str], scorer_identity: dict,
                              runtime_identity: dict | None = None) -> dict:
    """Project a validated run into required/disclosed/unverifiable tiers.

    ``case_ids`` is the complete selected set, including unsuccessful cases;
    scorer_identity comes from the validated score artifact, not its label.
    Call after load_run/score validation. This does not validate file bindings,
    score eligibility, diagnostic-track admission or denominator equality.
    Missing optional facts stay null; malformed present facts raise ValueError.
    """
    identity = _object(_object(manifest, 'manifest').get('identity'), 'manifest identity')
    bundle = _object(identity.get('source'), 'bundle identity')
    _object(scorer_identity, 'scorer identity')
    if not bundle or not scorer_identity:
        raise ValueError('Bundle and scorer identities are required')
    if (not isinstance(case_ids, list) or not case_ids
            or any(not isinstance(cid, str) or not cid.strip() for cid in case_ids)
            or len(set(case_ids)) != len(case_ids)):
        raise ValueError('A nonempty unique case set is required')
    runtime = {} if runtime_identity is None else _object(runtime_identity, 'runtime identity')
    code = _object(identity.get('code', {}), 'source code identity')
    source = None if not code else {
        'revision': _label(code.get('head_revision', code.get('product_revision'))),
        'source_manifest_sha256': _optional_hash(code.get('source_manifest_sha256')),
        'product_archive_sha256': _optional_hash(code.get('product_archive_sha256')),
        'identity_sha256': _digest({k: v for k, v in code.items() if k != 'versions'}),
    }
    dotenv = _object(runtime.get('dotenv_snapshot', {}), 'dotenv snapshot')
    configuration = {
        'runtime_settings_sha256': _optional_hash(identity.get('runtime_settings')),
        'product_services_sha256': _optional_hash(identity.get('product_services')),
        'settings_sha256': _optional_hash(runtime.get('comparable_settings_sha256')),
        'dotenv_sha256': _optional_hash(dotenv.get('sha256')),
        # Request/mode/evidence policies also affect an experiment. Retain a
        # digest of them without copying unbounded/private configuration into
        # reports. Known source, feature, dependency and remote-generation facts
        # belong to their own tiers, not this catch-all configuration digest.
        'run_configuration_sha256': _digest({
            'mode': manifest.get('mode'),
            'identity': {k: v for k, v in identity.items() if k not in {
                'source', 'code', 'feature_set', 'runtime_dependencies', 'runtime_model_services',
            }},
        }),
    }
    feature = identity.get('feature_set')
    if feature is not None:
        feature = validate_feature_identity(feature)
    if runtime_identity is not None:
        actual = build_feature_identity(runtime)
        if feature is not None and feature != actual:
            raise ValueError('Manifest features differ from saved runtime observations')
        feature = actual
    dependency = identity.get('runtime_dependencies')
    dependencies = None
    if dependency is not None:
        dependency = _object(dependency, 'dependency identity')
        dependencies = {key: _optional_hash(dependency.get(key))
                        for key in ('environment_sha256', 'requirements_sha256')}
    elif code.get('versions') is not None:
        dependencies = {'versions_sha256': _digest(_object(code['versions'], 'dependency versions'))}
    observed = _model_observation(runtime)
    remote_models = None
    if 'services' in observed:
        services = observed['services']
        if not isinstance(services, list):
            raise ValueError('Invalid remote service observations')
        remote_models = []
        for service in services:
            service = _object(service, 'remote service observation')
            name = service.get('service_id')
            if not isinstance(name, str) or not _ID.fullmatch(name):
                raise ValueError('Invalid remote service id')
            generation = _optional_hash(service.get('generation'))
            remote_models.append({'service_id': name, 'generation': generation})
        if len({s['service_id'] for s in remote_models}) != len(remote_models):
            raise ValueError('Duplicate remote service observation')
        remote_models.sort(key=lambda s: s['service_id'])
    elif identity.get('runtime_model_services') is not None:
        binding = _object(identity['runtime_model_services'], 'model service binding')
        remote_models = {'observation_sha256': _optional_hash(binding.get('observation_sha256'))}
    return {'format': COMPARISON_FORMAT,
            'must_equal': {'bundle': _digest(bundle),
                           'case_ids': {'sha256': _digest(sorted(case_ids)), 'count': len(case_ids)},
                           'scorer': _digest(scorer_identity)},
            'disclose': {'source': source, 'configuration': configuration,
                         'feature_set': feature, 'dependencies': dependencies},
            'unverifiable': {'remote_models': remote_models}}


def compare_identity_tiers(left: dict, right: dict) -> dict:
    """Return admission differences and a report block; only required facts block.

    Remote generations are retained side by side with not_compared, without
    equal/different claims. Even identical generations cannot prove weights.
    This result is only an identity check, not formal benchmark score admission.
    """
    for value in (left, right):
        _object(value, 'comparison identity')
        if set(value) != {'format', 'must_equal', 'disclose', 'unverifiable'} or value['format'] != COMPARISON_FORMAT:
            raise ValueError('Invalid comparison identity schema')
        for tier, fields in (
            ('must_equal', {'bundle', 'case_ids', 'scorer'}),
            ('disclose', {'source', 'configuration', 'feature_set', 'dependencies'}),
            ('unverifiable', {'remote_models'}),
        ):
            if not isinstance(value[tier], dict) or set(value[tier]) != fields:
                raise ValueError('Invalid comparison identity tier')
        if any(v is None for v in value['must_equal'].values()):
            raise ValueError('Required comparison identity is unknown')
        for field in ('bundle', 'scorer'):
            _optional_hash(value['must_equal'][field])
        cases = _object(value['must_equal']['case_ids'], 'case set identity')
        if (set(cases) != {'sha256', 'count'} or cases['sha256'] is None
                or type(cases['count']) is not int or cases['count'] <= 0):
            raise ValueError('Invalid case set identity')
        _optional_hash(cases['sha256'])
        if value['disclose']['feature_set'] is not None:
            validate_feature_identity(value['disclose']['feature_set'])
        _digest(value)
    def differences(tier):
        return {key: {'left': deepcopy(left[tier][key]), 'right': deepcopy(right[tier][key])}
                for key in left[tier] if left[tier][key] != right[tier][key]}
    required = differences('must_equal')
    return {'format': 'sn-identity-disclosure-v1', 'comparable': not required,
            'must_equal_differences': required, 'disclosure_differences': differences('disclose'),
            'unverifiable': {'remote_models': {
                'left': deepcopy(left['unverifiable']['remote_models']),
                'right': deepcopy(right['unverifiable']['remote_models']),
                'comparison': 'not_compared',
                'reason': 'Startup service generations do not identify remote model weights.'}}}


def disclosure_markdown(comparison: dict) -> str:
    """Render the extra report section; append before hashing the final report."""
    _object(comparison, 'identity disclosure')
    differences = _object(comparison.get('disclosure_differences'), 'disclosure differences')
    lines = ['**披露差异**', '', '披露档差异不阻止同题同 scorer 比较。', '']
    if differences:
        lines += ['```json', json.dumps(differences, ensure_ascii=False, sort_keys=True, indent=2), '```', '']
    else:
        lines += ['未观察到披露档差异；缺失观测仍为 unknown。', '']
    lines += ['远端模型代际只记录，不据此判断权重相同或不同。', '',
              '```json', json.dumps(comparison.get('unverifiable'), ensure_ascii=False, sort_keys=True, indent=2), '```']
    return '\n'.join(lines)
