"""Owned process/closed send boundary; all targets and output are synthetic."""
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


AGENT = 'b7abb649-7b0d-4ead-9368-b2a4c7c58209'
MESSAGE = '2ac576eb-a0ad-4f85-8bf7-53d12a67c080'
OTHER = 'b7abb649-7b0d-4ead-9368-b2a4c7c58210'


def module():
    return importlib.import_module('rag_eval.workbench.message_bridge')


def intent():
    return {'op': 'sendAgent', 'agentId': AGENT, 'messageId': MESSAGE,
            'expectedCwd': '/synthetic/owned ', 'text': '  synthetic message\n',
            'activeTurnBehavior': 'steer'}


def ack():
    return {'result': {'status': 'acknowledged', 'agentId': AGENT, 'messageId': MESSAGE},
            'release': {'status': 'ok'}}


def bridge(tmp_path, script, timeout=2):
    path = tmp_path / 'owned.py'
    path.write_text(script)
    return module().AgentMessageBridgeAdapter(
        {'url': 'ws://synthetic.invalid', 'password': 'synthetic-secret'},
        command=[sys.executable, str(path)], timeout=timeout)


def unknown(envelope, cause):
    assert envelope == {'result': {'status': 'unknown', 'code': 'SEND_OUTCOME_UNKNOWN',
                                  'cause': cause, 'agentId': AGENT, 'messageId': MESSAGE},
                        'release': {'status': 'unavailable', 'code': 'BRIDGE_RELEASE_UNKNOWN'}}
    for secret in ('synthetic-secret', '/synthetic/owned', 'synthetic message'):
        assert secret not in json.dumps(envelope)


def test_exact_stdin_frozen_config_ack_and_close_failure(tmp_path):
    envelope = ack()
    envelope['release'] = {'status': 'unavailable', 'code': 'CLOSE_FAILED'}
    config = {'url': 'ws://synthetic.invalid', 'password': 'synthetic-secret', 'connectTimeoutMs': 100}
    script = tmp_path / 'stdin.py'
    script.write_text('import json,sys\nrequest=json.load(sys.stdin)\n'
                      f'assert request == {dict(config=config,command=intent())!r}\n'
                      "assert 'synthetic-secret' not in repr(sys.argv)\n"
                      f'print({json.dumps(envelope)!r})\n')
    adapter = module().AgentMessageBridgeAdapter(config, command=[sys.executable, str(script)])
    config['password'] = 'mutated'
    assert adapter.call(intent()) == envelope


@pytest.mark.parametrize('result,release', [
    ({'status': 'not_attempted', 'code': 'INVALID_INPUT'}, {'status': 'not_needed'}),
    ({'status': 'not_attempted', 'code': 'PERMISSION_REQUIRED', 'agentId': AGENT, 'messageId': MESSAGE}, {'status': 'ok'}),
    ({'status': 'unknown', 'code': 'SEND_OUTCOME_UNKNOWN', 'cause': 'SEND_FAILED',
      'agentId': AGENT, 'messageId': MESSAGE}, {'status': 'ok'}),
    ({'status': 'unknown', 'code': 'SEND_OUTCOME_UNKNOWN', 'cause': 'RESPONSE_TOO_LARGE',
      'agentId': AGENT, 'messageId': MESSAGE, 'observed': {'sdkResolved': True}},
     {'status': 'unavailable', 'code': 'CLOSE_FAILED'}),
])
def test_preserves_valid_outcomes_and_encoding_fallback(tmp_path, result, release):
    envelope = {'result': result, 'release': release}
    assert bridge(tmp_path, f'print({json.dumps(envelope)!r})').call(intent()) == envelope


def test_pure_validators_copy_without_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('no IO')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(Path, 'resolve', forbidden)
    command = intent()
    frozen = module().validate_send_command(command)
    command['text'] = 'changed'
    assert frozen == intent()
    envelope = ack()
    validated = module().validate_send_envelope(envelope, frozen)
    envelope['release']['status'] = 'changed'
    assert validated == ack()


@pytest.mark.parametrize('change', [
    {'op': 'createAgent'}, {'extra': 1}, {'config': {}}, {'text': ''}, {'text': ' \ufeff\n'},
    {'text': 'x\0y'}, {'text': 1}, {'text': 'a' * 16385}, {'text': '中' * 10923},
    {'text': '😀' * 8193}, {'expectedCwd': 'relative'}, {'expectedCwd': '/x\0'},
    {'expectedCwd': '/' + '😀' * 4096}, {'agentId': 'short'}, {'messageId': OTHER[:8]},
    {'agentId': AGENT.replace('-4ead-', '-0ead-')}, {'messageId': MESSAGE.replace('-8bf7-', '-7bf7-')},
    {'activeTurnBehavior': None}, {'activeTurnBehavior': 'reject'},
])
def test_invalid_command_zero_child(tmp_path, monkeypatch, change):
    adapter = module().AgentMessageBridgeAdapter({'url': 'ws://synthetic.invalid'})
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid input spawned child')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    command = {**intent(), **change}
    with pytest.raises(ValueError):
        module().validate_send_command(command)
    result = adapter.call(command)
    assert result['result']['status'] == 'not_attempted'
    assert result['result']['code'] == 'BRIDGE_INVALID_COMMAND'
    assert result['release'] == {'status': 'not_needed'}


@pytest.mark.parametrize('field', list(intent()))
def test_missing_command_field_rejected(field):
    command = intent()
    del command[field]
    with pytest.raises(ValueError):
        module().validate_send_command(command)


def test_text_utf16_utf8_and_js_whitespace_preserve_original():
    for text in ('  x\n', 'a' * 16384, '😀' * 8192, '\x1c', '\ud800', '\ud83d\ude00'):
        assert module().validate_send_command({**intent(), 'text': text})['text'] == text
    upper = {**intent(), 'agentId': AGENT.upper(), 'messageId': MESSAGE.upper()}
    assert module().validate_send_command(upper) == upper


@pytest.mark.parametrize('mutate', [
    lambda e: e.update(extra=1), lambda e: e.pop('release'),
    lambda e: e['result'].update(agentId=OTHER), lambda e: e['result'].update(messageId=OTHER),
    lambda e: e['result'].update(text='private'), lambda e: e['result'].update(status='ok'),
    lambda e: e['release'].update(status='not_needed'),
    lambda e: e['release'].update(status='unavailable', code='PRIVATE_EXCEPTION'),
    lambda e: e.update(result=None), lambda e: e['result'].update(code='INVALID_INPUT'),
])
def test_bad_dto_unknown_not_acknowledged(tmp_path, mutate):
    envelope = ack()
    mutate(envelope)
    with pytest.raises(ValueError):
        module().validate_send_envelope(envelope, intent())
    unknown(bridge(tmp_path, f'print({json.dumps(envelope)!r})').call(intent()), 'BRIDGE_INVALID_RESPONSE')


@pytest.mark.parametrize('change', [
    {'agentId': OTHER}, {'messageId': OTHER}, {'cause': 'BRIDGE_PRIVATE_ERROR'},
    {'code': 'CREATE_OUTCOME_UNKNOWN'}, {'observed': {'sdkResolved': 1}},
    {'observed': {'sdkResolved': False}}, {'observed': {'sdkResolved': True, 'text': 'private'}},
])
def test_unknown_identity_and_observation_closed(change):
    result = {'status': 'unknown', 'code': 'SEND_OUTCOME_UNKNOWN', 'cause': 'BRIDGE_TIMEOUT',
              'agentId': AGENT, 'messageId': MESSAGE, **change}
    with pytest.raises(ValueError):
        module().validate_send_envelope({'result': result, 'release': {'status': 'ok'}}, intent())


@pytest.mark.parametrize('output', [
    b'{', b'\xff', b'{} {}', b'{"result":NaN,"release":{}}',
    b'{"result":{},"result":{},"release":{}}',
    b'{"result":{"status":"acknowledged","status":"not_attempted"},"release":{}}',
    ('[' * 2000 + '0' + ']' * 2000).encode(),
])
def test_bad_stdout_unknown(tmp_path, output):
    unknown(bridge(tmp_path, f'import sys\nsys.stdout.buffer.write({output!r})').call(intent()),
            'BRIDGE_INVALID_RESPONSE')


@pytest.mark.parametrize('script,cause', [
    ('import sys\nsys.exit(7)', 'BRIDGE_PROCESS_FAILED'),
    (f'import sys\nprint({json.dumps(ack())!r})\nsys.exit(7)', 'BRIDGE_PROCESS_FAILED'),
    ('import sys\nsys.stderr.write("private-exception")', 'BRIDGE_INVALID_RESPONSE'),
    ('import time\ntime.sleep(10)', 'BRIDGE_TIMEOUT'),
    ('import sys,time\nsys.stdout.write("x" * (3*1024*1024))\nsys.stdout.flush()\ntime.sleep(10)', 'BRIDGE_INVALID_RESPONSE'),
])
def test_process_failure_unknown_reaped_no_retry(tmp_path, monkeypatch, script, cause):
    processes = []
    real = subprocess.Popen
    def start(*args, **kwargs):
        process = real(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(subprocess, 'Popen', start)
    unknown(bridge(tmp_path, script, timeout=.2).call(intent()), cause)
    assert len(processes) == 1
    process = processes[0]
    assert process.poll() is not None and process.stdin.closed and process.stdout.closed


def test_possible_submission_marker_then_lost_stdout_never_not_attempted(tmp_path):
    marker = tmp_path / 'submitted'
    script = ('import json,sys\nfrom pathlib import Path\nrequest=json.load(sys.stdin)\n'
              f'Path({str(marker)!r}).write_text(request["command"]["messageId"])\n')
    unknown(bridge(tmp_path, script).call(intent()), 'BRIDGE_INVALID_RESPONSE')
    assert marker.read_text() == MESSAGE


def test_request_escaped_config_and_text_budget_before_child(monkeypatch):
    adapter = module().AgentMessageBridgeAdapter({'url': 'ws://synthetic.invalid', 'password': '中' * 8192})
    def forbidden(*args, **kwargs):
        raise AssertionError('oversized request spawned child')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    command = {**intent(), 'text': '中' * 10922}
    assert module().validate_send_command(command) == command
    result = adapter.call(command)
    assert result == {'result': {'status': 'not_attempted', 'code': 'BRIDGE_INVALID_COMMAND',
                                 'agentId': AGENT, 'messageId': MESSAGE}, 'release': {'status': 'not_needed'}}


def test_missing_executable_confirmed_not_started(tmp_path):
    adapter = module().AgentMessageBridgeAdapter({'url': 'ws://synthetic.invalid'}, command=[str(tmp_path / 'absent')])
    assert adapter.call(intent()) == {'result': {'status': 'not_attempted', 'code': 'BRIDGE_UNAVAILABLE',
                                                'agentId': AGENT, 'messageId': MESSAGE},
                                     'release': {'status': 'not_needed'}}


def test_post_spawn_io_failure_unknown(monkeypatch):
    def fail(*args):
        raise OSError('private-error')
    monkeypatch.setattr(module(), '_run_bounded', fail)
    unknown(module().AgentMessageBridgeAdapter({'url': 'ws://synthetic.invalid'}).call(intent()), 'BRIDGE_UNAVAILABLE')


@pytest.mark.parametrize('config', [{}, {'url': 1}, {'url': 'x', 'extra': 1},
    {'url': 'x', 'connectTimeoutMs': True}, {'url': 'x', 'connectTimeoutMs': 0},
    {'url': 'x', 'password': None}])
def test_bad_config_rejected(config):
    with pytest.raises(ValueError):
        module().AgentMessageBridgeAdapter(config)


def test_real_owned_cli_invalid_config_no_daemon():
    envelope = module().AgentMessageBridgeAdapter({'url': 'invalid-url'}).call(intent())
    assert envelope == {'result': {'status': 'not_attempted', 'code': 'INVALID_CONFIG',
                                  'agentId': AGENT, 'messageId': MESSAGE}, 'release': {'status': 'ok'}}


@pytest.mark.parametrize('timeout', [True, '1', None, 0, -1, float('inf'), float('nan')])
def test_invalid_timeout_before_any_process(monkeypatch, timeout):
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid timeout spawned child')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    with pytest.raises(ValueError):
        module().AgentMessageBridgeAdapter({'url': 'ws://synthetic.invalid'}, timeout=timeout)


@pytest.mark.parametrize('result', [
    {'status': 'not_attempted', 'code': 'PRIVATE_EXCEPTION'},
    {'status': 'not_attempted', 'code': 'INVALID_INPUT', 'agentId': OTHER},
    {'status': 'not_attempted', 'code': 'INVALID_INPUT', 'messageId': OTHER},
    {'status': 'not_attempted', 'code': 'INVALID_INPUT', 'observed': {'sdkResolved': True}},
])
def test_malformed_not_attempted_cannot_hide_possible_side_effect(tmp_path, result):
    envelope = {'result': result, 'release': {'status': 'not_needed'}}
    unknown(bridge(tmp_path, f'print({json.dumps(envelope)!r})').call(intent()), 'BRIDGE_INVALID_RESPONSE')


def test_non_utf8_json_encoding_and_nested_nonfinite_stdout_unknown(tmp_path):
    for output in (json.dumps(ack()).encode('utf-16'),
                   b'{"result":{"status":"not_attempted","code":"INVALID_INPUT","extra":Infinity},"release":{"status":"ok"}}',
                   b'{"result":{"status":"not_attempted","code":"INVALID_INPUT"},"release":{"status":"ok","status":"not_needed"}}'):
        unknown(bridge(tmp_path, f'import sys\nsys.stdout.buffer.write({output!r})').call(intent()),
                'BRIDGE_INVALID_RESPONSE')


def test_adapter_accepts_exact_64k_request_rejects_next_byte(monkeypatch):
    captured = []
    def execute(command, request, timeout):
        captured.append(request)
        return 0, json.dumps(ack()).encode()
    monkeypatch.setattr(module(), '_run_bounded', execute)
    # Valid text; JSON escaping and config determine the separate stdin budget.
    planned = {**intent(), 'text': '中' * 10000}
    config = {'url': 'ws://synthetic.invalid', 'password': ''}
    base_size = len(json.dumps({'config': config, 'command': planned}, ensure_ascii=True).encode())
    config['password'] = 'x' * (65536 - base_size)
    assert module().AgentMessageBridgeAdapter(config).call(planned) == ack()
    assert len(captured[0]) == 65536
    config['password'] += 'x'
    result = module().AgentMessageBridgeAdapter(config).call(planned)
    assert result['result']['status'] == 'not_attempted'
    assert result['result']['code'] == 'BRIDGE_INVALID_COMMAND'
    assert len(captured) == 1


def test_js_surrogate_encoding_uses_node_utf8_budget_not_python_codepoints():
    valid = {**intent(), 'text': '\ud800' * 10922}
    assert module().validate_send_command(valid) == valid
    with pytest.raises(ValueError):
        module().validate_send_command({**intent(), 'text': '\ud800' * 10923})


def test_real_owned_cli_input_rejection_envelope_without_daemon():
    cli = Path(__file__).resolve().parents[1] / 'apps/sn-agent-bridge/dist/cli.js'
    request = json.dumps({'config': {'url': 'invalid-url'}, 'command': {**intent(), 'extra': 1}}).encode()
    returncode, output = module()._run_bounded(('node', str(cli)), request, 3)
    assert returncode == 0
    envelope = module().validate_send_envelope(json.loads(output), intent())
    # IDs are optional for pre-parse rejection: CLI only records planned IDs
    # after the complete command has passed parseRequest.
    assert envelope == {'result': {'status': 'not_attempted', 'code': 'INVALID_INPUT'},
                        'release': {'status': 'not_needed'}}


@pytest.mark.parametrize('failure,cause', [('oversize', 'RESPONSE_TOO_LARGE'), ('cycle', 'INTERNAL_ERROR')])
def test_actual_owned_encoder_fallback_retains_sdk_resolution_and_release(tmp_path, failure, cause):
    protocol = Path(__file__).resolve().parents[1] / 'apps/sn-agent-bridge/dist/cli-protocol.js'
    script = tmp_path / 'encoder.mjs'
    response = ack()
    response['release'] = {'status': 'unavailable', 'code': 'CLOSE_FAILED'}
    script.write_text(f'import {{encodeResponse}} from {json.dumps(protocol.as_uri())};\n'
                      f'const response={json.dumps(response)};\n'
                      + ('response.result.extra="x".repeat(4*1024*1024);\n' if failure == 'oversize'
                         else 'response.result.extra=response.result;\n')
                      + 'process.stdout.write(encodeResponse(response));\n')
    adapter = module().AgentMessageBridgeAdapter({'url': 'ws://synthetic.invalid'}, command=['node', str(script)])
    assert adapter.call(intent()) == {
        'result': {'status': 'unknown', 'code': 'SEND_OUTCOME_UNKNOWN', 'cause': cause,
                   'agentId': AGENT, 'messageId': MESSAGE, 'observed': {'sdkResolved': True}},
        'release': {'status': 'unavailable', 'code': 'CLOSE_FAILED'}}


def test_public_preflight_is_pure_and_exactly_matches_call_bytes(monkeypatch):
    captured = []
    def execute(command, request, timeout):
        captured.append(request)
        return 0, json.dumps(ack()).encode()
    monkeypatch.setattr(module(), '_run_bounded', execute)
    adapter = module().AgentMessageBridgeAdapter({'url': 'ws://synthetic.invalid', 'password': '配置'})
    request = adapter.validate_request(intent())
    assert captured == []
    assert json.loads(request) == {'config': {'url': 'ws://synthetic.invalid', 'password': '配置'}, 'command': intent()}
    assert adapter.call(intent()) == ack() and captured == [request]
    with pytest.raises(ValueError):
        adapter.validate_request({**intent(), 'text': '中' * 10922})
    assert len(captured) == 1
