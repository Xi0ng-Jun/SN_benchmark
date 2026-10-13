"""Server-only, one-command Node adapter. Credentials travel only on stdin."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import selectors
import subprocess
import time


_READ_OPERATIONS = {'listProviders', 'listModels', 'listWorkspaces', 'readAgent', 'readTimeline'}
_MAX_OUTPUT_BYTES = 2 * 1024 * 1024


def _failure(code):
    return {'result': {'status': 'unavailable', 'code': code},
            'release': {'status': 'unavailable', 'code': 'BRIDGE_RELEASE_UNKNOWN'}}


class InvalidBridgeResponse(ValueError):
    pass


def _run_bounded(command, request, timeout):
    """Multiplex the two pipes so oversized output cannot accumulate or deadlock."""
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + timeout
    output = bytearray()
    written = 0
    try:
        with selectors.DefaultSelector() as selector:
            for stream, event in ((process.stdin, selectors.EVENT_WRITE), (process.stdout, selectors.EVENT_READ)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, event)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                for key, _ in selector.select(remaining):
                    stream = key.fileobj
                    if stream is process.stdin:
                        try:
                            written += os.write(stream.fileno(), request[written:written + 16384])
                        except BrokenPipeError:
                            written = len(request)
                        if written == len(request):
                            selector.unregister(stream)
                            stream.close()
                    else:
                        chunk = os.read(stream.fileno(), min(65536, _MAX_OUTPUT_BYTES + 1 - len(output)))
                        if not chunk:
                            selector.unregister(stream)
                        else:
                            output.extend(chunk)
                            if len(output) > _MAX_OUTPUT_BYTES:
                                raise InvalidBridgeResponse()
            return process.wait(timeout=max(0, deadline - time.monotonic())), bytes(output)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdin.close()
        process.stdout.close()


class AgentBridgeAdapter:
    """Internal adapter; HTTP commands never supply config or executable arguments.

    The owned CLI validates operation fields and projects SDK responses. HTTP
    consumers must still check Task/workspace scope before returning a snapshot.
    """
    def __init__(self, config, *, command=None, timeout=110):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('bridge timeout must be a finite positive number')
        self._config = {k: v for k, v in config.items()
                        if k in {'url', 'password', 'authHeader', 'connectTimeoutMs'}}
        cli = Path(__file__).resolve().parents[3] / 'apps/sn-agent-bridge/dist/cli.js'
        self._command = tuple(command) if command is not None else ('node', str(cli))
        self._timeout = timeout

    def call(self, command):
        if (not isinstance(command, dict) or not isinstance(command.get('op'), str)
                or command['op'] not in _READ_OPERATIONS):
            return _failure('BRIDGE_INVALID_COMMAND')
        try:
            request = json.dumps({'config': self._config, 'command': command}, ensure_ascii=False).encode('utf-8')
            if len(request) > 65536:
                return _failure('BRIDGE_INVALID_COMMAND')
            returncode, output = _run_bounded(self._command, request, self._timeout)
            if returncode != 0:
                return _failure('BRIDGE_PROCESS_FAILED')
            try:
                envelope = json.loads(output)
            except (ValueError, RecursionError):
                raise InvalidBridgeResponse() from None
            if not isinstance(envelope, dict) or set(envelope) != {'result', 'release'}:
                return _failure('BRIDGE_INVALID_RESPONSE')
            for name, value in envelope.items():
                allowed = {'ok', 'unavailable', 'missing'} if name == 'result' else {'ok', 'unavailable', 'not_needed'}
                if (not isinstance(value, dict) or value.get('status') not in allowed
                        or (value['status'] == 'missing' and value.get('code') != 'AGENT_NOT_FOUND')
                        or (value['status'] == 'not_needed' and
                            (not isinstance(envelope['result'], dict) or envelope['result'].get('status') != 'unavailable'))
                        or (value['status'] == 'unavailable' and
                            (not isinstance(value.get('code'), str)
                             or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', value['code'])))):
                    return _failure('BRIDGE_INVALID_RESPONSE')
            return envelope
        except subprocess.TimeoutExpired:
            # The bounded reader kills and waits for this owned Node process. The
            # daemon's read demand ends with that connection; no Agent is stopped.
            return _failure('BRIDGE_TIMEOUT')
        except InvalidBridgeResponse:
            return _failure('BRIDGE_INVALID_RESPONSE')
        except (OSError, ValueError, TypeError):
            return _failure('BRIDGE_UNAVAILABLE')
