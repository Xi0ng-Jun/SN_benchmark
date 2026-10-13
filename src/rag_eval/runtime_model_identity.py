"""Observe supplied loaded services and scheduled Futures without importing SN.

Declared bytes, loaded registry and submission generations are distinct facts.
Native ownership/source audit and provider lifetime belong to the caller. This
module neither resolves credentials nor proves requests, responses or freezing.
"""
from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import Future
from contextlib import contextmanager
import hashlib
import math
import re
from threading import Lock

from .identity import fingerprint


FORMAT = 'sn-loaded-model-services-v1'
_SETTINGS = (
    'model_bindings_strict', 'openai_compat_timeout_seconds', 'openai_compat_max_retries',
    'openai_compat_max_tokens', 'answer_max_tokens', 'embed_dim', 'embed_batch_size',
    'embed_rate_limit_retries', 'embed_rate_limit_base_delay',
    'notebook_metadata_batch_chars', 'notebook_metadata_batch_sources',
)
_LIMITS = {
    'registry': 'loaded_observed', 'declared_file_registry_equivalence': 'unverified',
    'credential_resolution': 'not_independently_inspected',
    'reachability': 'unverified', 'provider_responses': 'unverified',
    'request_count': 'unverified', 'request_overrides': 'unverified', 'environment_freeze': 'unverified',
}
_ID = re.compile(r'[a-z][a-z0-9_]{0,63}\Z')
_ENV = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.@+:/-]{0,127}\Z')
_SECRET = re.compile(r'(?i)(?:(?:sk|rk|pk|ak)-|ghp_|hf_|xox|AIza)[A-Za-z0-9_-]{8,}')
_SUPPORT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
_KINDS = ('chat', 'embedding', 'rerank')
_THINKING = ('enabled', 'disabled', 'provider_default')
_SERVICE_KEYS = (
    'service_id', 'kind', 'protocol', 'model', 'endpoint_sha256', 'credential_variable_name',
    'credential_variable_configured', 'max_concurrency', 'top_p', 'generation',
)


def _keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError('Invalid ' + label + ' schema')


def _hash(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError('Invalid model identity hash')


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError('Invalid public service/workload id')


def _settings(value):
    _keys(value, _SETTINGS, 'effective Settings')
    if type(value['model_bindings_strict']) is not bool:
        raise ValueError('Invalid effective strict binding flag')
    for name in _SETTINGS[1:]:
        number = value[name]
        if name == 'embed_rate_limit_base_delay':
            if type(number) not in (int, float) or not math.isfinite(number):
                raise ValueError('Invalid effective retry delay')
        elif type(number) is not int:
            raise ValueError('Invalid effective integer Setting')


def _validate_service(service):
    _keys(service, _SERVICE_KEYS, 'loaded service')
    _id(service['service_id'])
    if service['kind'] not in _KINDS or service['protocol'] not in ('openai', 'dashscope'):
        raise ValueError('Invalid loaded service kind/protocol')
    if service['kind'] == 'chat' and service['protocol'] != 'openai':
        raise ValueError('Invalid chat service protocol')
    model = service['model']
    if (not isinstance(model, str) or not _MODEL.fullmatch(model) or '://' in model
            or '//' in model or '..' in model or _SECRET.search(model)):
        raise ValueError('Unsafe public model label')
    _hash(service['endpoint_sha256'])
    _hash(service['generation'])
    name = service['credential_variable_name']
    if name is not None and (not isinstance(name, str) or not _ENV.fullmatch(name)):
        raise ValueError('Invalid credential variable name')
    if (type(service['credential_variable_configured']) is not bool
            or service['credential_variable_configured'] != (name is not None)):
        raise ValueError('Inconsistent credential variable presence')
    if type(service['max_concurrency']) is not int or service['max_concurrency'] < 1:
        raise ValueError('Invalid loaded concurrency')
    top_p = service['top_p']
    if top_p is not None and (service['kind'] != 'chat' or type(top_p) not in (int, float)
                              or not math.isfinite(top_p) or not 0 <= top_p <= 1):
        raise ValueError('Invalid loaded top_p')


def _service_fact(service):
    endpoint = service.base_url
    if not isinstance(endpoint, str) or not endpoint:
        raise ValueError('Invalid service endpoint')
    variable = service.api_key_env
    if variable == '':
        variable = None
    fact = {
        'service_id': service.id, 'kind': service.kind, 'protocol': service.protocol,
        'model': service.model, 'endpoint_sha256': hashlib.sha256(endpoint.encode('utf-8')).hexdigest(),
        'credential_variable_name': variable, 'credential_variable_configured': variable is not None,
        'max_concurrency': service.max_concurrency, 'top_p': service.top_p,
        'generation': service.fingerprint,
    }
    _validate_service(fact)
    return fact


def loaded_model_identity(settings, provider, *, workloads, declared_config_sha256: str) -> dict:
    """Observe one immutable registry snapshot and explicit full workload input.

    Provider ownership is reported, not certified as native. Missing routes
    remain explicit even when the supplied Settings says strict; only the
    native normal loader enforces that startup policy. No mode exemptions.
    """
    _hash(declared_config_sha256)
    if not isinstance(workloads, Mapping) or not workloads:
        raise ValueError('Explicit full workload catalog is required')
    catalog = []
    for key, workload in workloads.items():
        _id(key)
        if workload.id != key or workload.kind not in _KINDS:
            raise ValueError('Invalid explicit workload catalog')
        catalog.append({'workload_id': key, 'kind': workload.kind})
    catalog.sort(key=lambda item: item['workload_id'])
    effective = {name: getattr(settings, name) for name in _SETTINGS}
    _settings(effective)
    registry = provider.registry  # One snapshot; never re-query the live provider.
    owns = provider._owns_registry
    if type(owns) is not bool:
        raise ValueError('Invalid supplied provider registry ownership')
    services = [_service_fact(service) for service in registry.services()]
    services.sort(key=lambda item: item['service_id'])
    indexed = {service['service_id']: service for service in services}
    if len(indexed) != len(services):
        raise ValueError('Duplicate loaded service id')
    bindings, missing = [], []
    for workload in catalog:
        name = workload['workload_id']
        selected = registry.service_for(name)
        mode = registry.thinking_mode_for(name)
        if selected is None:
            missing.append(name)
            service_id = generation = None
        else:
            fact = _service_fact(selected)
            if indexed.get(fact['service_id']) != fact or fact['kind'] != workload['kind']:
                raise ValueError('Loaded route service/generation differs from registry services')
            service_id, generation = fact['service_id'], fact['generation']
        bindings.append({'workload_id': name, 'status': 'missing' if selected is None else 'bound',
                         'service_id': service_id, 'service_generation': generation, 'thinking_mode': mode})
    record = {
        'format': FORMAT, 'status': 'observed', 'declared_config_sha256': declared_config_sha256,
        'effective_settings': effective, 'provider_owns_registry': owns,
        'workloads': catalog, 'services': services, 'bindings': bindings,
        'missing_workloads': missing, 'limits': dict(_LIMITS),
    }
    record['fingerprint'] = fingerprint(record)
    return validate_loaded_model_identity(record)


def validate_loaded_model_identity(record) -> dict:
    """Validate saved shape, routes and digest offline, without observing a host."""
    _keys(record, ('format', 'status', 'declared_config_sha256', 'effective_settings',
                   'provider_owns_registry', 'workloads', 'services', 'bindings',
                   'missing_workloads', 'limits', 'fingerprint'), 'loaded model identity')
    if record['format'] != FORMAT or record['status'] != 'observed':
        raise ValueError('Unknown loaded model identity format/status')
    _hash(record['declared_config_sha256'])
    _hash(record['fingerprint'])
    try:
        digest = fingerprint({k: v for k, v in record.items() if k != 'fingerprint'})
    except (TypeError, ValueError):
        raise ValueError('Invalid loaded model identity serialization') from None
    if digest != record['fingerprint']:
        raise ValueError('Loaded model identity fingerprint mismatch')
    _settings(record['effective_settings'])
    if type(record['provider_owns_registry']) is not bool:
        raise ValueError('Invalid provider registry ownership')
    workloads = record['workloads']
    if not isinstance(workloads, list) or not workloads:
        raise ValueError('Missing full explicit workload catalog')
    catalog = {}
    for workload in workloads:
        _keys(workload, ('workload_id', 'kind'), 'workload')
        _id(workload['workload_id'])
        if workload['kind'] not in _KINDS or workload['workload_id'] in catalog:
            raise ValueError('Invalid/duplicate catalog workload')
        catalog[workload['workload_id']] = workload['kind']
    if list(catalog) != sorted(catalog):
        raise ValueError('Unsorted workload catalog')
    if not isinstance(record['services'], list):
        raise ValueError('Invalid loaded services')
    services = {}
    for service in record['services']:
        _validate_service(service)
        if service['service_id'] in services:
            raise ValueError('Duplicate loaded service id')
        services[service['service_id']] = service
    if list(services) != sorted(services):
        raise ValueError('Unsorted loaded services')
    bindings = record['bindings']
    if not isinstance(bindings, list) or len(bindings) != len(catalog):
        raise ValueError('Incomplete full catalog bindings')
    missing = []
    for (name, kind), binding in zip(catalog.items(), bindings):
        _keys(binding, ('workload_id', 'status', 'service_id', 'service_generation', 'thinking_mode'), 'binding')
        if binding['workload_id'] != name or binding['thinking_mode'] not in _THINKING:
            raise ValueError('Invalid loaded workload/thinking route')
        if binding['status'] == 'missing':
            if binding['service_id'] is not None or binding['service_generation'] is not None:
                raise ValueError('Missing workload cannot claim a service')
            missing.append(name)
        elif binding['status'] == 'bound':
            _id(binding['service_id'])
            service = services.get(binding['service_id'])
            if service is None or service['kind'] != kind or service['generation'] != binding['service_generation']:
                raise ValueError('Loaded binding service/generation mismatch')
        else:
            raise ValueError('Unknown loaded binding status')
    if record['missing_workloads'] != missing or record['limits'] != _LIMITS:
        raise ValueError('Invalid explicit missing state or observation limits')
    return record


class ModelServiceCaptureError(RuntimeError):
    """Generation observation failed; original provider/Future outcomes survive."""


def _error_type(error):
    name = type(error).__name__
    return name if re.fullmatch(r'[A-Za-z_]\w{0,127}', name, re.ASCII) else 'Exception'


def _submission_snapshot(runtime, context):
    workload, support, priority = context.workload_id, context.support_id, context.priority
    _id(workload)
    if not isinstance(support, str) or not _SUPPORT.fullmatch(support):
        raise ValueError('Unsafe submission support id')
    if not isinstance(priority, str) or priority not in ('interactive', 'report', 'background'):
        raise ValueError('Invalid submission priority')
    snapshot = {'service': _service_fact(runtime.service),
                'context': {'workload_id': workload, 'support_id': support, 'priority': str(priority)}}
    snapshot['snapshot_sha256'] = fingerprint(snapshot)
    return snapshot


@contextmanager
def capture_model_service_generations(provider, sink):
    """Wrap only this instance; caller closes/drains provider before normal exit.

    Terminal status means scheduled invoke completed/failed/cancelled, not a
    request/score count. Journal/observation failures never change submission
    return values or exceptions; they fail a normal context exit after cleanup.
    The body's original failure takes precedence over observation failures.
    """
    original = provider._submit_scheduled
    if not callable(original) or not callable(sink):
        raise ValueError('A scheduled provider method and callable sink are required')
    if getattr(original, '_model_generation_capture', False):
        raise ValueError('Nested capture of the same provider is unsupported')
    absent = object()
    own = provider.__dict__.get('_submit_scheduled', absent)
    lock = Lock()
    failures, counter = 0, 0
    pending = set()

    def failed():
        nonlocal failures
        with lock:
            failures += 1

    def emit(kind, call_id, snapshot, **facts):
        if snapshot is None:
            return
        event = {'kind': kind, 'call_id': call_id, 'service': dict(snapshot['service']),
                 'context': dict(snapshot['context']), 'snapshot_sha256': snapshot['snapshot_sha256'], **facts}
        try:
            sink(event)
        except BaseException:
            failed()

    def submit(runtime, *, context, invoke):
        nonlocal counter
        with lock:
            counter += 1
            call_id = counter
        try:
            snapshot = _submission_snapshot(runtime, context)
        except BaseException:
            failed()
            snapshot = None
        emit('model_service_submission_attempt', call_id, snapshot, phase='submission')
        try:
            future = original(runtime, context=context, invoke=invoke)
        except BaseException as error:
            emit('model_service_future_terminal', call_id, snapshot, phase='submission',
                 status='error', error_type=_error_type(error))
            raise
        if not isinstance(future, Future):
            failed()
            return future
        with lock:
            pending.add(call_id)
        terminal_seen = False

        def terminal(done):
            nonlocal terminal_seen
            with lock:
                if terminal_seen:
                    return
                terminal_seen = True
            try:
                if done.cancelled():
                    status, error_type = 'cancelled', None
                else:
                    error = done.exception()  # Never read a result/answer or stringify an error.
                    status = 'completed' if error is None else 'error'
                    error_type = None if error is None else _error_type(error)
                emit('model_service_future_terminal', call_id, snapshot, phase='future',
                     status=status, error_type=error_type)
            except BaseException:
                failed()
            finally:
                with lock:
                    pending.discard(call_id)
        try:
            future.add_done_callback(terminal)
        except BaseException:
            failed()
        return future

    submit._model_generation_capture = True
    provider._submit_scheduled = submit
    body_failed = False
    try:
        yield
    except BaseException:
        body_failed = True
        raise
    finally:
        if own is absent:
            del provider._submit_scheduled
        else:
            provider._submit_scheduled = own
        with lock:
            incomplete = bool(failures or pending)
        if not body_failed and incomplete:
            raise ModelServiceCaptureError('Model service generation capture failed or has unfinished submissions') from None
