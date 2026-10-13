"""Server-only no-prompt creation; an uncertain process outcome is never retried.

The server must persist and authorize its intent before call(). Pure validators
perform no IO. The owned Node bridge owns canonical cwd checks against workspace
and agent directories; Python checks shape and the other frozen identity fields.
An original absolute cwd is retained, not trimmed or equated lexically to an alias.
The process budget is the read adapter's 2 MiB, independently of the CLI's 4 MiB.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

from .bridge_adapter import AgentBridgeAdapter, InvalidBridgeResponse, _run_bounded


_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', re.I)
_JS_WHITESPACE = ' \t\n\v\f\r\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
_BRIDGE_CODES = frozenset({
    'INVALID_CONFIG', 'NOT_CONNECTED', 'CONNECTION_UNAVAILABLE', 'CONNECT_FAILED',
    'READ_FAILED', 'CATALOG_UNAVAILABLE', 'CLOSED', 'CLOSE_FAILED', 'INVALID_INPUT',
    'IDENTITY_MISMATCH', 'SCOPE_MISMATCH', 'RESPONSE_TOO_LARGE', 'INTERNAL_ERROR',
})
_ADAPTER_CAUSES = frozenset({'BRIDGE_TIMEOUT', 'BRIDGE_PROCESS_FAILED', 'BRIDGE_INVALID_RESPONSE', 'BRIDGE_UNAVAILABLE'})
_PRECHECK_CODES = _BRIDGE_CODES | {'WORKSPACE_NOT_FOUND', 'WORKSPACE_UNAVAILABLE', 'AGENT_ALREADY_EXISTS',
                                  'BRIDGE_INVALID_COMMAND', 'BRIDGE_UNAVAILABLE'}
_UNKNOWN_CAUSES = _BRIDGE_CODES | _ADAPTER_CAUSES | {'CREATE_FAILED', 'AGENT_NOT_FOUND'}
_CAPABILITIES = frozenset({
    'supportsStreaming', 'supportsSessionPersistence', 'supportsSessionListing',
    'supportsDynamicModes', 'supportsMcpServers', 'supportsReasoningStream',
    'supportsToolInvocations', 'supportsRewindConversation', 'supportsRewindFiles', 'supportsRewindBoth',
})


def _record(value, required, optional=()):
    if type(value) is not dict or not set(required) <= value.keys() or not value.keys() <= set(required) | set(optional):
        raise ValueError('Invalid creation record')
    return value


def _text(value, limit=512, *, opaque=True, empty=False):
    if (type(value) is not str or (not value and not empty) or '\0' in value
            or len(value) > limit or (opaque and value.strip(_JS_WHITESPACE) != value)):
        raise ValueError('Invalid creation string')
    # JS counts astral characters as two units and individual surrogates as one.
    if sum(2 if ord(char) > 0xffff else 1 for char in value) > limit:
        raise ValueError('Invalid creation string')
    return value


def _uuid(value):
    value = _text(value, 36)
    if not _UUID.fullmatch(value):
        raise ValueError('Invalid creation UUID')
    return value


def _path(value):
    value = _text(value, 8192, opaque=False)
    if not os.path.isabs(value):
        raise ValueError('Invalid creation path')
    return value


def validate_create_command(command):
    """Copy the closed CLI create command without IO, normalization or authorization."""
    command = _record(command, {'op', 'workspaceId', 'expectedCwd', 'agentId', 'idempotencyKey', 'config'}, {'title'})
    if command['op'] != 'createAgent':
        raise ValueError('Invalid creation operation')
    config = _record(command['config'], {'provider'}, {'modeId', 'thinkingOptionId'})
    provider = _text(config['provider'])
    if '/' not in provider:
        raise ValueError('Invalid creation provider')
    provider_id, model = provider.split('/', 1)
    _text(provider_id)
    _text(model)
    frozen = {
        'op': 'createAgent', 'workspaceId': _text(command['workspaceId']),
        'expectedCwd': _path(command['expectedCwd']), 'agentId': _uuid(command['agentId']),
        'idempotencyKey': _text(command['idempotencyKey']), 'config': {'provider': provider},
    }
    for key in ('modeId', 'thinkingOptionId'):
        if key in config:
            frozen['config'][key] = _text(config[key])
    if 'title' in command:
        frozen['title'] = _text(command['title'], 200)
    return frozen


def _code(value, allowed):
    if type(value) is not str or value not in allowed:
        raise ValueError('Invalid creation code')
    return value


def _snapshot(agent, command):
    agent = _record(agent, {'id', 'workspaceId', 'cwd', 'title', 'provider', 'model', 'capabilities'},
                    {'status', 'archivedAt', 'activeTurn'})
    provider, model = command['config']['provider'].split('/', 1)
    if (_uuid(agent['id']) != command['agentId'] or _text(agent['workspaceId']) != command['workspaceId']
            or agent['provider'] != provider or agent['model'] != model):
        raise ValueError('Invalid creation identity')
    snapshot = {'id': agent['id'], 'workspaceId': agent['workspaceId'], 'cwd': _path(agent['cwd']),
                'title': None if agent['title'] is None else _text(agent['title'], 8192, opaque=False, empty=True),
                'provider': provider, 'model': model}
    capabilities = _record(agent['capabilities'], _CAPABILITIES)
    if any(value is not None and type(value) is not bool for value in capabilities.values()):
        raise ValueError('Invalid creation capabilities')
    snapshot['capabilities'] = dict(capabilities)
    if 'status' in agent:
        snapshot['status'] = _code(agent['status'], {'initializing', 'idle', 'running', 'error', 'closed'})
    if 'archivedAt' in agent:
        snapshot['archivedAt'] = None if agent['archivedAt'] is None else _text(agent['archivedAt'], 8192, opaque=False, empty=True)
    if 'activeTurn' in agent:
        turn = agent['activeTurn']
        if turn is not None:
            turn = _record(turn, {'turnId', 'startedAt'})
            turn = {'turnId': _text(turn['turnId']), 'startedAt': None if turn['startedAt'] is None
                    else _text(turn['startedAt'], 8192, opaque=False, empty=True)}
        snapshot['activeTurn'] = turn
    return snapshot


def _release(value, status):
    value = _record(value, {'status'}, {'code'})
    if value['status'] == 'unavailable':
        _record(value, {'status', 'code'})
        return {'status': 'unavailable', 'code': _code(value['code'], {'CLOSE_FAILED', 'BRIDGE_RELEASE_UNKNOWN'})}
    _record(value, {'status'})
    if value['status'] == 'ok' or (value['status'] == 'not_needed' and status == 'not_attempted'):
        return {'status': value['status']}
    raise ValueError('Invalid creation release')


def validate_create_envelope(envelope, command):
    """Validate and copy facts, never infer creation absence from process failure.

    A valid created DTO relies on the owned producer's canonical cwd check. It
    proves neither effective mode/effort nor future idleness. Unknown observations
    are snapshots made before settlement, not remote absence or reconciliation.
    """
    command = validate_create_command(command)
    try:
        envelope = _record(envelope, {'result', 'release'})
        result = envelope['result']
        if type(result) is not dict:
            raise ValueError('Invalid creation result')
        status = result.get('status')
        if status == 'not_attempted':
            _record(result, {'status', 'code'}, {'agentId'})
            copied = {'status': status, 'code': _code(result['code'], _PRECHECK_CODES)}
            if 'agentId' in result:
                if _uuid(result['agentId']) != command['agentId']:
                    raise ValueError('Invalid creation identity')
                copied['agentId'] = result['agentId']
        elif status == 'unknown':
            _record(result, {'status', 'code', 'cause', 'agentId'}, {'observed'})
            if result['code'] != 'CREATE_OUTCOME_UNKNOWN' or _uuid(result['agentId']) != command['agentId']:
                raise ValueError('Invalid creation identity')
            copied = {'status': status, 'code': 'CREATE_OUTCOME_UNKNOWN',
                      'cause': _code(result['cause'], _UNKNOWN_CAUSES), 'agentId': result['agentId']}
            if 'observed' in result:
                observed = _record(result['observed'], {'handleId'}, {'agent'})
                safe = {'handleId': _uuid(observed['handleId'])}
                if 'agent' in observed:
                    if safe['handleId'] != command['agentId']:
                        raise ValueError('Invalid creation identity')
                    safe['agent'] = _snapshot(observed['agent'], command)
                copied['observed'] = safe
        elif status == 'created':
            _record(result, {'status', 'agentId', 'agent', 'ready', 'readiness'})
            if _uuid(result['agentId']) != command['agentId'] or type(result['ready']) is not bool:
                raise ValueError('Invalid creation identity')
            agent = _snapshot(result['agent'], command)
            readiness = ('incomplete_snapshot' if not {'archivedAt', 'activeTurn', 'status'} <= agent.keys()
                         else 'archived' if agent['archivedAt'] is not None
                         else 'not_idle' if agent['status'] != 'idle' or agent['activeTurn'] is not None else 'idle')
            if result['readiness'] != readiness or result['ready'] != (readiness == 'idle'):
                raise ValueError('Invalid creation readiness')
            copied = {'status': status, 'agentId': result['agentId'], 'agent': agent,
                      'ready': result['ready'], 'readiness': readiness}
        else:
            raise ValueError('Invalid creation result')
        return {'result': copied, 'release': _release(envelope['release'], status)}
    except (ValueError, TypeError, KeyError, RecursionError):
        raise InvalidBridgeResponse('Invalid creation response') from None


def _not_attempted(code, command):
    result = {'status': 'not_attempted', 'code': code}
    if type(command) is dict:
        try:
            result['agentId'] = _uuid(command.get('agentId'))
        except ValueError:
            pass
    return {'result': result, 'release': {'status': 'not_needed'}}


def _unknown(cause, command):
    return {'result': {'status': 'unknown', 'code': 'CREATE_OUTCOME_UNKNOWN',
                       'cause': cause, 'agentId': command['agentId']},
            'release': {'status': 'unavailable', 'code': 'BRIDGE_RELEASE_UNKNOWN'}}


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise InvalidBridgeResponse('Invalid creation response')
        value[key] = item
    return value


class AgentCreationBridgeAdapter:
    """One server-owned createAgent per call; no reads, retries or remote cleanup.

    Server code supplies config/executable and must persist the planned UUID first.
    No process stdout is trusted until both result and release have been validated.
    """
    def __init__(self, config, *, command=None, timeout=110):
        config = _record(config, {'url'}, {'password', 'authHeader', 'connectTimeoutMs'})
        frozen = {'url': _text(config['url'], 8192)}
        for key in ('password', 'authHeader'):
            if key in config:
                frozen[key] = _text(config[key], 8192, opaque=False, empty=True)
        if 'connectTimeoutMs' in config:
            value = config['connectTimeoutMs']
            if type(value) is not int or not 1 <= value <= 120000:
                raise ValueError('Invalid creation connection timeout')
            frozen['connectTimeoutMs'] = value
        if command is not None:
            if not isinstance(command, (tuple, list)) or not command or any(type(arg) is not str or '\0' in arg for arg in command) or not command[0]:
                raise ValueError('Invalid creation executable')
        self._transport = AgentBridgeAdapter(frozen, command=command, timeout=timeout)

    def call(self, command):
        try:
            frozen = validate_create_command(command)
            # ASCII escapes preserve every JS UTF-16 code unit on valid UTF-8 stdin.
            request = json.dumps({'config': self._transport._config, 'command': frozen}, ensure_ascii=True).encode('utf-8')
            if len(request) > 65536:
                raise ValueError('Invalid creation request size')
        except (ValueError, TypeError, RecursionError):
            return _not_attempted('BRIDGE_INVALID_COMMAND', command)

        # Every failure from here is potentially post-dispatch unless the OS
        # explicitly reports exec failure for the executable. _run_bounded has
        # no path IO after Popen: pipe errors cannot supply that filename.
        try:
            returncode, output = _run_bounded(self._transport._command, request, self._transport._timeout)
        except subprocess.TimeoutExpired:
            return _unknown('BRIDGE_TIMEOUT', frozen)
        except InvalidBridgeResponse:
            return _unknown('BRIDGE_INVALID_RESPONSE', frozen)
        except OSError as error:
            if error.filename == self._transport._command[0]:
                return _not_attempted('BRIDGE_UNAVAILABLE', frozen)
            return _unknown('BRIDGE_UNAVAILABLE', frozen)
        except Exception:
            return _unknown('BRIDGE_UNAVAILABLE', frozen)

        if returncode != 0:
            return _unknown('BRIDGE_PROCESS_FAILED', frozen)
        try:
            envelope = json.loads(output, object_pairs_hook=_unique_object)
            return validate_create_envelope(envelope, frozen)
        except (ValueError, TypeError, RecursionError):
            return _unknown('BRIDGE_INVALID_RESPONSE', frozen)
