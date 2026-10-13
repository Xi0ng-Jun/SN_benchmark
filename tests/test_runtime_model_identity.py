"""Supplied-object/Future witnesses; no SN, credentials or live model work."""
import builtins
import copy
from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from rag_eval.identity import fingerprint


def api():
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

    def __getattr__(self, name):
        raise AssertionError('unallowlisted Settings attribute')

    def model_dump(self):
        raise AssertionError('model_dump forbidden')


class Service:
    id = 'chat'
    kind = 'chat'
    protocol = 'openai'
    base_url = 'https://private.invalid/v1?private-token=do-not-emit'
    model = 'synthetic_a'
    api_key_env = 'SYNTHETIC_KEY'
    max_concurrency = 2
    top_p = 0.7
    fingerprint = 'a' * 64

    @property
    def api_key(self):
        raise AssertionError('credential access forbidden')


class Registry:
    def __init__(self, service=None):
        self.service = service or Service()
        self.missing = set()

    def services(self):
        return (self.service,)

    def service_for(self, workload):
        return None if workload in self.missing else self.service

    def thinking_mode_for(self, workload):
        return 'disabled' if workload == 'ask_answer' else 'provider_default'


class Provider:
    def __init__(self):
        self.registry = Registry()
        self._owns_registry = True
        self.calls = []
        self.next = Future()
        self.raise_submission = None

    def _submit_scheduled(self, runtime, *, context, invoke):
        self.calls.append((runtime, context, invoke))
        if self.raise_submission is not None:
            raise self.raise_submission
        return self.next


CATALOG = {
    'ask_answer': SimpleNamespace(id='ask_answer', kind='chat'),
    'gap_consult_query': SimpleNamespace(id='gap_consult_query', kind='chat'),
    'external_evidence_answer': SimpleNamespace(id='external_evidence_answer', kind='chat'),
}


def loaded(provider=None, settings=None, digest='d' * 64):
    return api().loaded_model_identity(settings or Settings(), provider or Provider(),
                                       workloads=CATALOG, declared_config_sha256=digest)


def context(support='mdl-test'):
    class Context:
        workload_id = 'ask_answer'
        support_id = support
        priority = 'interactive'

        def __getattr__(self, name):
            raise AssertionError('unallowlisted context attribute')
    return Context()


def runtime(service=None):
    return SimpleNamespace(service=service or Service())


def reseal(record):
    record['fingerprint'] = fingerprint({k: v for k, v in record.items() if k != 'fingerprint'})
    return record


def test_loaded_effective_allowlist_declared_distinction_full_catalog_and_no_secrets(monkeypatch):
    class ForbiddenEnvironment(dict):
        def __getitem__(self, key):
            pytest.fail('environment read')
        def get(self, *args):
            pytest.fail('environment read')
        def __iter__(self):
            pytest.fail('environment copy')

    with monkeypatch.context() as offline:
        offline.setattr(os, 'environ', ForbiddenEnvironment())
        value = loaded()
    assert value['format'] == 'sn-loaded-model-services-v1'
    assert value['status'] == 'observed'
    assert value['declared_config_sha256'] == 'd' * 64
    assert value['effective_settings']['model_bindings_strict'] is True
    assert value['effective_settings']['openai_compat_max_tokens'] == 321
    assert len(value['effective_settings']) == 11
    assert value['provider_owns_registry'] is True
    assert [item['workload_id'] for item in value['workloads']] == sorted(CATALOG)
    assert [item['workload_id'] for item in value['bindings']] == sorted(CATALOG)
    assert value['missing_workloads'] == []
    service = value['services'][0]
    assert service['generation'] == 'a' * 64
    assert service['endpoint_sha256'] == hashlib.sha256(Service.base_url.encode()).hexdigest()
    assert service['credential_variable_name'] == 'SYNTHETIC_KEY'
    assert service['credential_variable_configured'] is True
    assert value['bindings'][0]['thinking_mode'] == 'disabled'
    assert 'private.invalid' not in json.dumps(value)
    assert 'do-not-emit' not in json.dumps(value)
    assert loaded() == value
    assert loaded(digest='e' * 64)['fingerprint'] != value['fingerprint']
    effective = Settings()
    effective.answer_max_tokens = 655
    assert loaded(settings=effective)['fingerprint'] != value['fingerprint']


def test_missing_and_external_ownership_are_explicit_and_route_generation_mismatch_rejects():
    provider = Provider()
    provider.registry.missing = {'gap_consult_query', 'external_evidence_answer'}
    provider._owns_registry = False
    value = loaded(provider)
    assert value['provider_owns_registry'] is False
    assert value['missing_workloads'] == ['external_evidence_answer', 'gap_consult_query']
    assert [entry['status'] for entry in value['bindings']] == ['bound', 'missing', 'missing']
    assert value['bindings'][1]['service_id'] is None
    assert value['bindings'][1]['service_generation'] is None
    other = Service()
    other.fingerprint = 'b' * 64
    provider.registry.service_for = lambda workload: other
    with pytest.raises(ValueError, match='generation|route|service'):
        loaded(provider)


def test_offline_validation_checks_complete_closed_routes_hashes_and_types(monkeypatch):
    record = loaded()
    def forbidden(*args, **kwargs):
        pytest.fail('offline host read')
    with monkeypatch.context() as offline:
        offline.setattr(Path, 'open', forbidden)
        offline.setattr(builtins, 'open', forbidden)
        offline.setattr(api(), 'loaded_model_identity', forbidden)
        assert api().validate_loaded_model_identity(json.loads(json.dumps(record))) == record
    for change in ('omission', 'generation', 'ownership_type', 'unknown', 'unsafe_label'):
        bad = copy.deepcopy(record)
        if change == 'omission':
            bad['bindings'].pop()
        elif change == 'generation':
            bad['bindings'][0]['service_generation'] = 'b' * 64
        elif change == 'ownership_type':
            bad['provider_owns_registry'] = 1
        elif change == 'unknown':
            bad['secret'] = 'not permitted'
        else:
            bad['services'][0]['model'] = 'https://secret.invalid/private'
        with pytest.raises(ValueError):
            api().validate_loaded_model_identity(reseal(bad))
    bad = copy.deepcopy(record)
    bad['effective_settings']['answer_max_tokens'] += 1
    with pytest.raises(ValueError, match='fingerprint'):
        api().validate_loaded_model_identity(bad)


def test_pending_generation_A_completes_as_A_after_registry_changes_to_B():
    provider, events = Provider(), []
    first = provider.next
    first_runtime, first_context = runtime(), context()
    invoke = object()
    with api().capture_model_service_generations(provider, events.append):
        assert provider._submit_scheduled(first_runtime, context=first_context, invoke=invoke) is first
        service_b = Service()
        service_b.fingerprint, service_b.model = 'b' * 64, 'synthetic_b'
        provider.registry = Registry(service_b)
        provider.next = second = Future()
        assert provider._submit_scheduled(runtime(service_b), context=context('mdl-b'), invoke=invoke) is second
        second.set_result('private answer B')
        first.set_result('private answer A')
    assert provider.calls[0] == (first_runtime, first_context, invoke)
    attempts = [event for event in events if event['kind'] == 'model_service_submission_attempt']
    terminals = [event for event in events if event['kind'] == 'model_service_future_terminal']
    assert len(attempts) == len(terminals) == 2
    assert terminals[0]['service']['generation'] == 'b' * 64
    assert terminals[1]['service']['generation'] == 'a' * 64
    assert terminals[1]['call_id'] == attempts[0]['call_id']
    assert terminals[1]['snapshot_sha256'] == attempts[0]['snapshot_sha256']
    assert terminals[1]['context'] == {'workload_id': 'ask_answer', 'support_id': 'mdl-test', 'priority': 'interactive'}
    assert terminals[1]['status'] == 'completed'
    assert 'private answer' not in json.dumps(events)
    assert 'private.invalid' not in json.dumps(events)
    assert '_submit_scheduled' not in provider.__dict__


def test_concurrent_immediate_failed_and_cancelled_futures_preserve_objects_and_single_terminals():
    provider, events = Provider(), []
    futures = [Future() for _ in range(4)]
    futures[0].set_result(object())
    error = RuntimeError('private exception body')
    futures[1].set_exception(error)
    with api().capture_model_service_generations(provider, events.append):
        for index, future in enumerate(futures):
            provider.next = future
            assert provider._submit_scheduled(runtime(), context=context(f'mdl-{index}'), invoke=object()) is future
        with ThreadPoolExecutor(max_workers=2) as pool:
            completed = pool.submit(futures[2].set_result, object())
            cancelled = pool.submit(futures[3].cancel)
            assert completed.result() is None
            assert cancelled.result() is True
    assert futures[1].exception() is error
    terminals = [event for event in events if event['kind'] == 'model_service_future_terminal']
    assert len(terminals) == 4
    assert len({event['call_id'] for event in terminals}) == 4
    assert sorted(event['status'] for event in terminals) == ['cancelled', 'completed', 'completed', 'error']
    assert next(event for event in terminals if event['status'] == 'error')['error_type'] == 'RuntimeError'
    assert 'private exception body' not in json.dumps(events)


def test_submission_exception_and_own_attribute_are_preserved_and_safe():
    provider, events = Provider(), []
    error = ValueError('secret synchronous failure')
    def original(runtime, *, context, invoke):
        raise error
    provider._submit_scheduled = original
    with api().capture_model_service_generations(provider, events.append):
        with pytest.raises(ValueError) as caught:
            provider._submit_scheduled(runtime(), context=context(), invoke=object())
        assert caught.value is error
    assert provider.__dict__['_submit_scheduled'] is original
    assert [row['kind'] for row in events] == ['model_service_submission_attempt', 'model_service_future_terminal']
    assert events[-1]['phase'] == 'submission'
    assert events[-1]['error_type'] == 'ValueError'
    assert 'secret synchronous failure' not in json.dumps(events)


def test_attempt_sink_failure_and_duplicate_callback_do_not_change_future_behavior():
    class DuplicateCallbackFuture(Future):
        def add_done_callback(self, callback):
            super().add_done_callback(callback)
            super().add_done_callback(callback)
    provider = Provider()
    provider.next = future = DuplicateCallbackFuture()
    events = []
    def failing_attempt(event):
        if event['kind'] == 'model_service_submission_attempt':
            raise OSError('private attempt sink error')
        events.append(event)
    with pytest.raises(RuntimeError, match='capture'):
        with api().capture_model_service_generations(provider, failing_attempt):
            assert provider._submit_scheduled(runtime(), context=context(), invoke=object()) is future
            answer = object()
            future.set_result(answer)
            assert future.result() is answer
    assert len(events) == 1
    assert events[0]['phase'] == 'future'
    assert events[0]['status'] == 'completed'
    assert '_submit_scheduled' not in provider.__dict__


def test_capture_failures_surface_after_cleanup_without_masking_body_failure():
    provider = Provider()
    future = provider.next
    def terminal_failure(event):
        if event['kind'] == 'model_service_future_terminal':
            raise OSError('secret journal failure')
    with pytest.raises(RuntimeError, match='capture') as failure:
        with api().capture_model_service_generations(provider, terminal_failure):
            assert provider._submit_scheduled(runtime(), context=context(), invoke=object()) is future
            future.set_result(object())
    assert 'secret journal failure' not in str(failure.value)
    assert '_submit_scheduled' not in provider.__dict__
    body_error = ValueError('original body error')
    with pytest.raises(ValueError) as caught:
        with api().capture_model_service_generations(provider, lambda event: (_ for _ in ()).throw(OSError('secret'))):
            provider._submit_scheduled(runtime(), context=context(), invoke=object())
            raise body_error
    assert caught.value is body_error
    assert '_submit_scheduled' not in provider.__dict__


def test_undrained_future_is_not_silently_complete_and_original_method_restores():
    provider = Provider()
    future = provider.next
    with pytest.raises(RuntimeError, match='capture|pending|unfinished'):
        with api().capture_model_service_generations(provider, [].append):
            assert provider._submit_scheduled(runtime(), context=context(), invoke=object()) is future
    assert '_submit_scheduled' not in provider.__dict__
    future.cancel()
