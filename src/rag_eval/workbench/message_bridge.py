"""Server-only single send; process uncertainty never authorizes a retry.

Pure validators copy the closed public protocol. Task authorization and frozen
intent persistence belong to the caller; canonical directory checks belong to
the owned Node bridge. SDK resolution is acknowledgement, not model completion.
Config travels only on stdin, stdout is bounded to 2 MiB, stderr is discarded.
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
_PRECHECK_CODES = _BRIDGE_CODES | {'AGENT_NOT_FOUND', 'AGENT_ARCHIVED', 'INCOMPLETE_SNAPSHOT',
                                 'PERMISSION_REQUIRED', 'AGENT_UNAVAILABLE', 'BRIDGE_INVALID_COMMAND', 'BRIDGE_UNAVAILABLE'}
_UNKNOWN_CAUSES = _BRIDGE_CODES | _ADAPTER_CAUSES | {'SEND_FAILED'}


def _record(value, required, optional=()):
    if type(value) is not dict or not set(required) <= value.keys() or not value.keys() <= set(required) | set(optional):
        raise ValueError('Invalid message record')
    return value


def _text(value, limit, *, opaque=True, empty=False):
    if (type(value) is not str or (not value and not empty) or '\0' in value
            or (opaque and value.strip(_JS_WHITESPACE) != value)
            or sum(2 if ord(char) > 0xffff else 1 for char in value) > limit):
        raise ValueError('Invalid message string')
    return value


def _uuid(value):
    if type(value) is not str or _UUID.fullmatch(value) is None:
        raise ValueError('Invalid message UUID')
    return value


def validate_send_command(command):
    """Return a new closed command, preserving paths/text/IDs without IO."""
    command = _record(command, {'op', 'agentId', 'expectedCwd', 'text', 'messageId', 'activeTurnBehavior'})
    if command['op'] != 'sendAgent':
        raise ValueError('Invalid message operation')
    cwd = _text(command['expectedCwd'], 8192, opaque=False)
    if not os.path.isabs(cwd):
        raise ValueError('Invalid message path')
    text = _text(command['text'], 16384, opaque=False)
    # Node Buffer.byteLength replaces unpaired UTF-16 surrogates with U+FFFD
    # and joins paired surrogates. Only measure this view; retain original text.
    encoded = text.encode('utf-16-le', 'surrogatepass').decode('utf-16-le', 'replace').encode('utf-8')
    if not text.strip(_JS_WHITESPACE) or len(encoded) > 32768:
        raise ValueError('Invalid message text')
    behavior = command['activeTurnBehavior']
    if type(behavior) is not str or behavior not in ('interrupt', 'steer'):
        raise ValueError('Invalid message behavior')
    return {'op': 'sendAgent', 'agentId': _uuid(command['agentId']), 'expectedCwd': cwd,
            'text': text, 'messageId': _uuid(command['messageId']), 'activeTurnBehavior': behavior}


def _code(value, allowed):
    if type(value) is not str or value not in allowed:
        raise ValueError('Invalid message code')
    return value


def validate_send_envelope(envelope, command):
    """Copy validated intent outcome and release independently, without IO."""
    command = validate_send_command(command)
    try:
        envelope = _record(envelope, {'result', 'release'})
        result = _record(envelope['result'], {'status'}, {'code', 'cause', 'agentId', 'messageId', 'observed'})
        status = result['status']
        if status == 'not_attempted':
            _record(result, {'status', 'code'}, {'agentId', 'messageId'})
            safe = {'status': status, 'code': _code(result['code'], _PRECHECK_CODES)}
            for key in ('agentId', 'messageId'):
                if key in result:
                    if _uuid(result[key]) != command[key]:
                        raise ValueError('Invalid message identity')
                    safe[key] = result[key]
        elif status in ('unknown', 'acknowledged'):
            _record(result, {'status', 'agentId', 'messageId'} | ({'code', 'cause'} if status == 'unknown' else set()),
                    {'observed'} if status == 'unknown' else ())
            if any(_uuid(result[key]) != command[key] for key in ('agentId', 'messageId')):
                raise ValueError('Invalid message identity')
            safe = {'status': status, 'agentId': result['agentId'], 'messageId': result['messageId']}
            if status == 'unknown':
                if result['code'] != 'SEND_OUTCOME_UNKNOWN':
                    raise ValueError('Invalid message code')
                safe.update(code='SEND_OUTCOME_UNKNOWN', cause=_code(result['cause'], _UNKNOWN_CAUSES))
                if 'observed' in result:
                    observed = _record(result['observed'], {'sdkResolved'})
                    if observed['sdkResolved'] is not True:
                        raise ValueError('Invalid message observation')
                    safe['observed'] = {'sdkResolved': True}
        else:
            raise ValueError('Invalid message result')
        release = _record(envelope['release'], {'status'}, {'code'})
        if release['status'] == 'unavailable':
            _record(release, {'status', 'code'})
            released = {'status': 'unavailable', 'code': _code(release['code'], {'CLOSE_FAILED', 'BRIDGE_RELEASE_UNKNOWN'})}
        else:
            _record(release, {'status'})
            if release['status'] != 'ok' and not (release['status'] == 'not_needed' and status == 'not_attempted'):
                raise ValueError('Invalid message release')
            released = {'status': release['status']}
        return {'result': safe, 'release': released}
    except (ValueError, TypeError, KeyError, RecursionError):
        raise InvalidBridgeResponse('Invalid message response') from None


def _not_attempted(code, command):
    result = {'status': 'not_attempted', 'code': code}
    if type(command) is dict:
        for key in ('agentId', 'messageId'):
            try:
                result[key] = _uuid(command.get(key))
            except ValueError:
                pass
    return {'result': result, 'release': {'status': 'not_needed'}}


def _unknown(cause, command):
    return {'result': {'status': 'unknown', 'code': 'SEND_OUTCOME_UNKNOWN', 'cause': cause,
                       'agentId': command['agentId'], 'messageId': command['messageId']},
            'release': {'status': 'unavailable', 'code': 'BRIDGE_RELEASE_UNKNOWN'}}


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise InvalidBridgeResponse('Invalid message response')
        value[key] = item
    return value


def _invalid_constant(_value):
    raise InvalidBridgeResponse('Invalid message response')


def _validate_config(config):
    config = _record(config, {'url'}, {'password', 'authHeader', 'connectTimeoutMs'})
    frozen = {'url': _text(config['url'], 8192)}
    for key in ('password', 'authHeader'):
        if key in config:
            frozen[key] = _text(config[key], 8192, opaque=False, empty=True)
    if 'connectTimeoutMs' in config:
        value = config['connectTimeoutMs']
        if type(value) is not int or not 1 <= value <= 120000:
            raise ValueError('Invalid message connection timeout')
        frozen['connectTimeoutMs'] = value
    return frozen


def validate_send_request(command, config):
    """Pure preflight: return exact stdin bytes or raise ValueError, no child.

    Server callers may check the complete frozen command/config before claiming
    uncertainty. UTF-16/text budgets and JSON escaping are the same as call().
    """
    request = json.dumps({'config': _validate_config(config), 'command': validate_send_command(command)},
                         ensure_ascii=True).encode('utf-8')
    if len(request) > 65536:
        raise ValueError('Invalid message request size')
    return request


class AgentMessageBridgeAdapter:
    """One owned CLI process per call; no reads, retries or remote cancellation."""
    def __init__(self, config, *, command=None, timeout=110):
        frozen = _validate_config(config)
        if command is not None:
            if (not isinstance(command, (tuple, list)) or not command
                    or any(type(arg) is not str or '\0' in arg for arg in command) or not command[0]):
                raise ValueError('Invalid message executable')
        self._transport = AgentBridgeAdapter(frozen, command=command, timeout=timeout)

    def validate_request(self, command):
        """Check the exact request against this adapter's frozen server config."""
        return validate_send_request(command, self._transport._config)

    def call(self, command):
        try:
            frozen = validate_send_command(command)
            request = self.validate_request(frozen)
        except (ValueError, TypeError, RecursionError):
            return _not_attempted('BRIDGE_INVALID_COMMAND', command)
        try:
            returncode, output = _run_bounded(self._transport._command, request, self._transport._timeout)
        except subprocess.TimeoutExpired:
            return _unknown('BRIDGE_TIMEOUT', frozen)
        except InvalidBridgeResponse:
            return _unknown('BRIDGE_INVALID_RESPONSE', frozen)
        except OSError as error:
            # The reader does no path IO after Popen. A matching exec filename
            # confirms startup failed; pipe failures cannot prove no dispatch.
            if error.filename == self._transport._command[0]:
                return _not_attempted('BRIDGE_UNAVAILABLE', frozen)
            return _unknown('BRIDGE_UNAVAILABLE', frozen)
        except Exception:
            return _unknown('BRIDGE_UNAVAILABLE', frozen)
        if returncode != 0:
            return _unknown('BRIDGE_PROCESS_FAILED', frozen)
        try:
            envelope = json.loads(output.decode('utf-8'), object_pairs_hook=_unique_object,
                                  parse_constant=_invalid_constant)
            return validate_send_envelope(envelope, frozen)
        except (ValueError, TypeError, RecursionError):
            return _unknown('BRIDGE_INVALID_RESPONSE', frozen)
