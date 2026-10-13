"""Offline tests for the E5-S3 readiness input/receipt boundary."""
import builtins
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest

from rag_eval.identity import fingerprint


def api():
    from rag_eval import s3_readiness
    return s3_readiness


def model_identity():
    from rag_eval import runtime_model_identity
    return runtime_model_identity


class Settings:
    model_bindings_strict = True
    openai_compat_timeout_seconds = 17
    openai_compat_max_retries = 3
    openai_compat_max_tokens = 321
    answer_max_tokens = 654
    embed_dim = 8
    embed_batch_size = 4
    embed_rate_limit_retries = 2
    embed_rate_limit_base_delay = 0.5
    notebook_metadata_batch_chars = 4096
    notebook_metadata_batch_sources = 2


class Service:
    id = 'chat'
    kind = 'chat'
    protocol = 'openai'
    base_url = 'https://private.invalid/v1?private-token=do-not-emit'
    model = 'synthetic_a'
    api_key_env = 'SYNTHETIC_KEY'
    max_concurrency = 2
    top_p = None
    fingerprint = 'a' * 64

    @property
    def api_key(self):
        raise AssertionError('credential access forbidden')


class Registry:
    def __init__(self, missing=()):
        self.missing = set(missing)

    def services(self):
        return (Service(),)

    def service_for(self, workload):
        return None if workload in self.missing else Service()

    def thinking_mode_for(self, workload):
        return 'disabled'


class Provider:
    def __init__(self, missing=()):
        self.registry = Registry(missing)
        self._owns_registry = True


CATALOG = {
    'ask_answer': SimpleNamespace(id='ask_answer', kind='chat'),
    'external_evidence_answer': SimpleNamespace(id='external_evidence_answer', kind='chat'),
    'gap_consult_query': SimpleNamespace(id='gap_consult_query', kind='chat'),
}
DECLARED_SHA = 'd' * 64
_UNSET = object()


def loaded(settings=None, missing=()):
    return model_identity().loaded_model_identity(
        settings or Settings(), Provider(missing), workloads=CATALOG,
        declared_config_sha256=DECLARED_SHA)


def reseal(record):
    record['fingerprint'] = fingerprint({key: value for key, value in record.items()
                                         if key != 'fingerprint'})
    return record


def scope_fixture(**overrides):
    value = {
        'case_id': 'qasper:synthetic-case',
        'partition_id': 'qasper-group-synthetic',
        'request_revision': 'notebook-request-v3',
        'bundle_id': 'b' * 64,
        'index_id': 'e' * 64,
        'sn_source_manifest_sha256': '7' * 64,
    }
    value.update(overrides)
    return value


def declared_fixture(**overrides):
    value = {
        'source': 'e3b-declared-config-projection',
        'config_sha256': DECLARED_SHA,
        'service_count': 1,
        'credential_variable_names_only': True,
        'endpoint_plaintext_recorded': False,
        'missing_catalog_bindings': [],
        'strict_registry_exemption': False,
        'effective_settings_observed': True,
        'credential_resolution_observed': True,
        'reachability_probed': True,
    }
    value.update(overrides)
    return value


def credentials_fixture(status='configured'):
    return [{'variable_name': 'SYNTHETIC_KEY', 'status': status}]


def reachability_fixture(status='reachable'):
    return [{'service_id': 'chat', 'status': status}]


def readiness_input(*, identity=_UNSET, credentials=_UNSET, reachability=_UNSET,
                    declared=_UNSET, scope=_UNSET):
    return {
        'format': api().INPUT_FORMAT,
        'scope': scope_fixture() if scope is _UNSET else scope,
        'declared_config': declared_fixture() if declared is _UNSET else declared,
        'loaded_model_identity': loaded() if identity is _UNSET else identity,
        'credentials': credentials_fixture() if credentials is _UNSET else credentials,
        'reachability': reachability_fixture() if reachability is _UNSET else reachability,
    }


def codes(receipt):
    return [(reason['code'], reason['subject']) for reason in receipt['blocking_reasons']]


def test_ready_positive_receipt_is_sealed_and_retains_evidence():
    identity = loaded()
    receipt = api().decide_readiness(readiness_input(identity=identity))
    assert receipt['format'] == api().RECEIPT_FORMAT
    assert receipt['status'] == 'ready'
    assert receipt['blocking_reasons'] == []
    assert receipt['scope'] == scope_fixture()
    assert receipt['declared_config'] == declared_fixture()
    assert receipt['loaded_identity_status'] == 'observed'
    assert receipt['loaded_identity_fingerprint'] == identity['fingerprint']
    assert receipt['effective_settings'] == identity['effective_settings']
    assert receipt['model_bindings_strict'] is True
    assert receipt['binding_gaps'] == []
    assert receipt['catalog_evidence'] == {
        'required': ['external_evidence_answer', 'gap_consult_query'],
        'bound': ['external_evidence_answer', 'gap_consult_query'],
        'unverified': [],
    }
    assert receipt['credentials'] == credentials_fixture()
    assert receipt['reachability'] == reachability_fixture()
    assert receipt['limits']['official_score'] == 'not_claimed'
    assert receipt['fingerprint'] == fingerprint(
        {key: value for key, value in receipt.items() if key != 'fingerprint'})
    assert api().validate_readiness_receipt(json.loads(json.dumps(receipt))) == receipt


@pytest.mark.parametrize('name', ['external_evidence_answer', 'gap_consult_query'])
def test_declared_missing_catalog_evidence_blocks(name):
    receipt = api().decide_readiness(
        readiness_input(declared=declared_fixture(missing_catalog_bindings=[name])))
    assert receipt['status'] == 'blocked'
    assert ('missing_catalog_evidence', name) in codes(receipt)


def test_unobserved_catalog_evidence_is_unknown_until_observed_missing():
    receipt = api().decide_readiness(readiness_input(identity=None))
    assert receipt['status'] == 'unknown'
    assert ('catalog_evidence_unverified', 'external_evidence_answer') in codes(receipt)
    assert ('catalog_evidence_unverified', 'gap_consult_query') in codes(receipt)

    catalog = {key: value for key, value in CATALOG.items() if key != 'gap_consult_query'}
    identity = model_identity().loaded_model_identity(
        Settings(), Provider(), workloads=catalog, declared_config_sha256=DECLARED_SHA)
    receipt = api().decide_readiness(readiness_input(identity=identity))
    assert receipt['status'] == 'blocked'
    assert ('missing_catalog_evidence', 'gap_consult_query') in codes(receipt)


def test_absent_effective_settings_are_unknown_not_ready():
    receipt = api().decide_readiness(readiness_input(identity=None))
    assert receipt['status'] == 'unknown'
    assert ('settings_unknown', None) in codes(receipt)
    assert receipt['effective_settings'] is None
    assert receipt['loaded_identity_status'] == 'absent'
    assert receipt['binding_gaps'] == []


def test_invalid_effective_settings_block_without_leaking_the_record():
    identity = loaded()
    identity['effective_settings']['answer_max_tokens'] = 'many'
    reseal(identity)
    receipt = api().decide_readiness(readiness_input(identity=identity))
    assert receipt['status'] == 'blocked'
    assert ('loaded_identity_invalid', None) in codes(receipt)
    assert receipt['loaded_model_identity'] is None
    assert receipt['loaded_identity_status'] == 'invalid'
    assert receipt['effective_settings'] is None


def test_strict_binding_gap_blocks_and_is_retained():
    receipt = api().decide_readiness(
        readiness_input(identity=loaded(missing={'gap_consult_query'})))
    assert receipt['status'] == 'blocked'
    assert receipt['binding_gaps'] == ['gap_consult_query']
    assert ('binding_gap', 'gap_consult_query') in codes(receipt)
    assert ('missing_catalog_evidence', 'gap_consult_query') in codes(receipt)


def test_disabled_strict_bindings_block_ready():
    settings = Settings()
    settings.model_bindings_strict = False
    receipt = api().decide_readiness(readiness_input(identity=loaded(settings=settings)))
    assert receipt['status'] == 'blocked'
    assert ('strict_bindings_disabled', None) in codes(receipt)
    assert receipt['model_bindings_strict'] is False


@pytest.mark.parametrize('state, expected, code', [
    ('unknown', 'unknown', 'credential_unknown'),
    ('absent', 'blocked', 'credential_absent'),
])
def test_credential_unknown_and_absent_states(state, expected, code):
    receipt = api().decide_readiness(
        readiness_input(credentials=[{'variable_name': 'SYNTHETIC_KEY', 'status': state}]))
    assert receipt['status'] == expected
    assert (code, 'SYNTHETIC_KEY') in codes(receipt)


def test_unobserved_credential_variable_is_unknown_and_extra_variable_blocks():
    receipt = api().decide_readiness(readiness_input(credentials=[]))
    assert receipt['status'] == 'unknown'
    assert ('credential_unknown', 'SYNTHETIC_KEY') in codes(receipt)

    receipt = api().decide_readiness(readiness_input(credentials=[
        {'variable_name': 'OTHER_KEY', 'status': 'configured'},
        {'variable_name': 'SYNTHETIC_KEY', 'status': 'configured'},
    ]))
    assert receipt['status'] == 'blocked'
    assert ('credential_variable_unknown', 'OTHER_KEY') in codes(receipt)


@pytest.mark.parametrize('state, expected, code', [
    ('unknown', 'unknown', 'reachability_unknown'),
    ('unreachable', 'blocked', 'service_unreachable'),
])
def test_reachability_unknown_and_unreachable_states(state, expected, code):
    receipt = api().decide_readiness(
        readiness_input(reachability=[{'service_id': 'chat', 'status': state}]))
    assert receipt['status'] == expected
    assert (code, 'chat') in codes(receipt)


def test_unobserved_reachability_is_unknown_and_unknown_service_blocks():
    receipt = api().decide_readiness(readiness_input(reachability=[]))
    assert receipt['status'] == 'unknown'
    assert ('reachability_unknown', 'chat') in codes(receipt)

    receipt = api().decide_readiness(readiness_input(reachability=[
        {'service_id': 'chat', 'status': 'reachable'},
        {'service_id': 'other', 'status': 'reachable'},
    ]))
    assert receipt['status'] == 'blocked'
    assert ('reachability_service_unknown', 'other') in codes(receipt)


@pytest.mark.parametrize('overrides, code', [
    ({'credential_variable_names_only': False}, 'credential_values_declared'),
    ({'endpoint_plaintext_recorded': True}, 'endpoint_plaintext_declared'),
    ({'strict_registry_exemption': True}, 'strict_registry_exempted'),
    ({'config_sha256': 'e' * 64}, 'declared_config_mismatch'),
    ({'service_count': 2}, 'service_count_mismatch'),
])
def test_declared_safety_and_cross_checks_block(overrides, code):
    receipt = api().decide_readiness(readiness_input(declared=declared_fixture(**overrides)))
    assert receipt['status'] == 'blocked'
    assert (code, None) in codes(receipt)


def test_blocked_evidence_dominates_unknown_evidence():
    receipt = api().decide_readiness(readiness_input(
        credentials=[{'variable_name': 'SYNTHETIC_KEY', 'status': 'unknown'}],
        reachability=[{'service_id': 'chat', 'status': 'unreachable'}],
    ))
    assert receipt['status'] == 'blocked'
    assert ('credential_unknown', 'SYNTHETIC_KEY') in codes(receipt)
    assert ('service_unreachable', 'chat') in codes(receipt)


def test_readiness_input_is_not_mutated():
    value = readiness_input()
    before = copy.deepcopy(value)
    api().decide_readiness(value)
    assert value == before


def test_secret_material_is_never_accepted_or_emitted():
    identity = loaded()
    assert 'do-not-emit' not in json.dumps(identity)
    receipt = api().decide_readiness(readiness_input(identity=identity))
    serialized = json.dumps(receipt, sort_keys=True)
    assert 'private.invalid' not in serialized
    assert 'do-not-emit' not in serialized
    assert 'sk-' not in serialized

    secret = 'sk-' + 'A' * 24
    bad = readiness_input()
    bad['credentials'] = [{'variable_name': 'SYNTHETIC_KEY', 'status': 'configured',
                           'secret': secret}]
    with pytest.raises(ValueError) as caught:
        api().decide_readiness(bad)
    assert secret not in str(caught.value)

    bad = readiness_input()
    bad['declared_config'] = declared_fixture(source=secret)
    with pytest.raises(ValueError) as caught:
        api().decide_readiness(bad)
    assert secret not in str(caught.value)


def _invalid_input_cases():
    cases = []

    def add(name, mutate):
        cases.append((name, mutate))

    add('format', lambda value: value.__setitem__('format', 'sn-e5-s3-readiness-input-v2'))
    add('extra_top_key', lambda value: value.__setitem__('extra', True))
    add('missing_top_key', lambda value: value.pop('reachability'))
    add('scope_extra', lambda value: value['scope'].__setitem__('extra', 'x'))
    add('scope_hash', lambda value: value['scope'].__setitem__('bundle_id', 'not-a-hash'))
    add('declared_extra', lambda value: value['declared_config'].__setitem__('extra', 1))
    add('declared_flag_type', lambda value: value['declared_config'].__setitem__(
        'endpoint_plaintext_recorded', 'no'))
    add('declared_count_bool', lambda value: value['declared_config'].__setitem__(
        'service_count', True))
    add('declared_missing_text', lambda value: value['declared_config'].__setitem__(
        'missing_catalog_bindings', 'ask_answer'))
    add('declared_missing_duplicate', lambda value: value['declared_config'].__setitem__(
        'missing_catalog_bindings', ['ask_answer', 'ask_answer']))
    add('credential_extra', lambda value: value.__setitem__('credentials', [
        {'variable_name': 'SYNTHETIC_KEY', 'status': 'configured', 'value': 'x'}]))
    add('credential_status', lambda value: value['credentials'][0].__setitem__(
        'status', 'maybe'))
    add('credential_order', lambda value: value.__setitem__('credentials', [
        {'variable_name': 'ZSECOND', 'status': 'configured'},
        {'variable_name': 'AFIRST', 'status': 'configured'},
    ]))
    add('credential_duplicate', lambda value: value.__setitem__('credentials', [
        {'variable_name': 'SYNTHETIC_KEY', 'status': 'configured'},
        {'variable_name': 'SYNTHETIC_KEY', 'status': 'configured'},
    ]))
    add('credentials_type', lambda value: value.__setitem__('credentials', None))
    add('reachability_status', lambda value: value['reachability'][0].__setitem__(
        'status', 'maybe'))
    add('reachability_key', lambda value: value['reachability'][0].__setitem__(
        'endpoint', 'https://private.invalid'))
    add('identity_type', lambda value: value.__setitem__('loaded_model_identity', 'record'))
    return cases


_INVALID_CASES = _invalid_input_cases()


@pytest.mark.parametrize('name, mutate', _INVALID_CASES, ids=[name for name, _ in _INVALID_CASES])
def test_input_schema_is_closed_and_rejected(name, mutate):
    value = readiness_input()
    mutate(value)
    with pytest.raises(ValueError):
        api().decide_readiness(value)


def test_blocked_and_unknown_receipts_round_trip_offline():
    cases = [
        readiness_input(identity=None),
        readiness_input(credentials=[{'variable_name': 'SYNTHETIC_KEY', 'status': 'absent'}]),
        readiness_input(reachability=[{'service_id': 'chat', 'status': 'unreachable'}]),
    ]
    for value in cases:
        receipt = api().decide_readiness(value)
        assert receipt['status'] != 'ready'
        assert api().validate_readiness_receipt(json.loads(json.dumps(receipt))) == receipt


def test_receipt_tampering_and_unknown_format_are_rejected():
    receipt = api().decide_readiness(readiness_input())
    sealed = copy.deepcopy(receipt)
    sealed['status'] = 'blocked'
    with pytest.raises(ValueError, match='fingerprint'):
        api().validate_readiness_receipt(sealed)

    inconsistent = copy.deepcopy(receipt)
    inconsistent['blocking_reasons'] = [
        {'code': 'binding_gap', 'severity': 'blocked', 'subject': 'ask_answer'}]
    reseal(inconsistent)
    with pytest.raises(ValueError, match='inconsistent'):
        api().validate_readiness_receipt(inconsistent)

    wrong = copy.deepcopy(receipt)
    wrong['format'] = 'sn-e5-s3-readiness-receipt-v2'
    with pytest.raises(ValueError, match='format'):
        api().validate_readiness_receipt(wrong)

    extra = copy.deepcopy(receipt)
    extra['extra'] = True
    with pytest.raises(ValueError, match='schema'):
        api().validate_readiness_receipt(extra)


def test_decision_and_validation_touch_no_environment_process_or_network(monkeypatch):
    class ForbiddenEnvironment(dict):
        def __getitem__(self, key):
            pytest.fail('environment read')

        def get(self, *args):
            pytest.fail('environment read')

        def __iter__(self):
            pytest.fail('environment read')

    def forbidden(*args, **kwargs):
        pytest.fail('host side effect')

    value = readiness_input()
    with monkeypatch.context() as offline:
        offline.setattr(os, 'environ', ForbiddenEnvironment())
        offline.setattr(os, 'getenv', forbidden)
        offline.setattr(subprocess, 'Popen', forbidden)
        offline.setattr(subprocess, 'run', forbidden)
        offline.setattr(socket, 'socket', forbidden)
        offline.setattr(builtins, 'open', forbidden)
        offline.setattr(Path, 'open', forbidden)
        receipt = api().decide_readiness(value)
        api().validate_readiness_receipt(json.loads(json.dumps(receipt)))
    assert receipt['status'] == 'ready'


def test_module_does_not_import_host_capabilities():
    module = api()
    for name in ('os', 'socket', 'subprocess', 'requests', 'httpx', 'dotenv'):
        assert name not in vars(module)
    source = Path(module.__file__).read_text(encoding='utf-8')
    for forbidden in ('import os', 'import socket', 'import subprocess', 'import requests',
                      'import httpx', 'os.environ', 'getenv', 'dotenv'):
        assert forbidden not in source
