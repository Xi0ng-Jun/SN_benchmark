"""Creation process failures retain possible side effects and the planned UUID."""
import copy
import importlib
import json
from pathlib import Path
import sys

import pytest


AGENT_ID = 'b7abb649-7b0d-4ead-9368-b2a4c7c58209'
OTHER_ID = 'b7abb649-7b0d-4ead-9368-b2a4c7c58210'
CAPABILITIES = {
    'supportsStreaming': True, 'supportsSessionPersistence': True,
    'supportsSessionListing': None, 'supportsDynamicModes': False,
    'supportsMcpServers': False, 'supportsReasoningStream': True,
    'supportsToolInvocations': True, 'supportsRewindConversation': None,
    'supportsRewindFiles': None, 'supportsRewindBoth': None,
}


def module():
    return importlib.import_module('rag_eval.workbench.creation_bridge')


def intent():
    return {'op': 'createAgent', 'workspaceId': 'workspace-example',
            'expectedCwd': '/example/owned ', 'agentId': AGENT_ID,
            'idempotencyKey': 'intent-1', 'config': {'provider': 'codex/model/example',
                'modeId': 'mode-example', 'thinkingOptionId': 'effort-example'}, 'title': 'Example'}


def created():
    return {'result': {'status': 'created', 'agentId': AGENT_ID, 'ready': True, 'readiness': 'idle',
            'agent': {'id': AGENT_ID, 'workspaceId': 'workspace-example', 'cwd': '/example/owned ',
                      'provider': 'codex', 'model': 'model/example', 'status': 'idle', 'title': 'Example',
                      'archivedAt': None, 'activeTurn': None, 'capabilities': copy.deepcopy(CAPABILITIES)}},
            'release': {'status': 'ok'}}


def adapter(tmp_path, script, *, timeout=2):
    path = tmp_path / 'creation_stub.py'
    path.write_text(script)
    return module().AgentCreationBridgeAdapter(
        {'url': 'ws://example.invalid/ws', 'password': 'private-test-secret'},
        command=(sys.executable, str(path)), timeout=timeout)


def responder(tmp_path, envelope):
    return adapter(tmp_path, 'print(' + repr(json.dumps(envelope)) + ')')


def assert_unknown(envelope, cause):
    assert envelope == {'result': {'status': 'unknown', 'code': 'CREATE_OUTCOME_UNKNOWN',
                                  'cause': cause, 'agentId': AGENT_ID},
                        'release': {'status': 'unavailable', 'code': 'BRIDGE_RELEASE_UNKNOWN'}}
    assert 'private-test-secret' not in json.dumps(envelope)


def test_creation_preserves_created_and_release_failure_and_exact_frozen_stdin(tmp_path):
    response = created()
    response['release'] = {'status': 'unavailable', 'code': 'CLOSE_FAILED'}
    planned = intent()
    server_config = {'url': 'ws://example.invalid/ws', 'password': 'private-test-secret'}
    script = ('import json,sys\nrequest=json.load(sys.stdin)\n'
              f'assert request == {dict(config=server_config, command=planned)!r}\n'
              "assert 'private-test-secret' not in repr(sys.argv)\n"
              f'print({json.dumps(response)!r})\n')
    path = tmp_path / 'creation_stub.py'
    path.write_text(script)
    bridge = module().AgentCreationBridgeAdapter(server_config, command=(sys.executable, str(path)))
    server_config['password'] = 'mutated'
    output = bridge.call(planned)
    assert output == response
    assert 'private-test-secret' not in json.dumps(output)


def test_pure_validators_copy_nested_parameters_and_observations_without_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('pure validation must not perform IO')
    monkeypatch.setattr('subprocess.Popen', forbidden)
    monkeypatch.setattr(Path, 'resolve', forbidden)
    original = intent()
    frozen = module().validate_create_command(original)
    original['config']['provider'] = 'mutated/model'
    assert frozen == intent() and frozen is not original
    envelope = created()
    output = module().validate_create_envelope(envelope, frozen)
    envelope['result']['agent']['capabilities']['supportsStreaming'] = False
    assert output == created()
    # Different original cwd is allowed: the owned bridge verifies realpath/stat.
    envelope = created()
    envelope['result']['agent']['cwd'] = '/example/canonical '
    assert module().validate_create_envelope(envelope, frozen) == envelope


@pytest.mark.parametrize('result,release', [
    ({'status': 'not_attempted', 'code': 'INVALID_INPUT'}, {'status': 'not_needed'}),
    ({'status': 'not_attempted', 'code': 'CONNECT_FAILED', 'agentId': AGENT_ID}, {'status': 'ok'}),
    ({'status': 'not_attempted', 'code': 'WORKSPACE_UNAVAILABLE', 'agentId': AGENT_ID},
     {'status': 'unavailable', 'code': 'CLOSE_FAILED'}),
    ({'status': 'unknown', 'code': 'CREATE_OUTCOME_UNKNOWN', 'cause': 'READ_FAILED', 'agentId': AGENT_ID,
      'observed': {'handleId': AGENT_ID, 'agent': created()['result']['agent']}}, {'status': 'ok'}),
    ({'status': 'unknown', 'code': 'CREATE_OUTCOME_UNKNOWN', 'cause': 'IDENTITY_MISMATCH', 'agentId': AGENT_ID,
      'observed': {'handleId': OTHER_ID}}, {'status': 'unavailable', 'code': 'CLOSE_FAILED'}),
])
def test_creation_preserves_complete_legitimate_outcomes(tmp_path, result, release):
    envelope = {'result': result, 'release': release}
    assert responder(tmp_path, envelope).call(intent()) == envelope
    copied = module().validate_create_envelope(envelope, intent())
    assert copied == envelope and copied is not envelope and copied['result'] is not result


@pytest.mark.parametrize('agent_changes,readiness,ready', [
    ({'status': 'running'}, 'not_idle', False),
    ({'activeTurn': {'turnId': 'turn-1', 'startedAt': None}}, 'not_idle', False),
    ({'archivedAt': '2026-10-11T00:00:00Z'}, 'archived', False),
    ({'archivedAt': '__omit__'}, 'incomplete_snapshot', False),
    ({'activeTurn': '__omit__'}, 'incomplete_snapshot', False),
    ({'status': '__omit__'}, 'incomplete_snapshot', False),
    ({'archivedAt': '2026-10-11', 'activeTurn': '__omit__'}, 'incomplete_snapshot', False),
])
def test_readiness_preserves_incomplete_and_non_idle_facts(tmp_path, agent_changes, readiness, ready):
    envelope = created()
    for key, value in agent_changes.items():
        if value == '__omit__':
            del envelope['result']['agent'][key]
        else:
            envelope['result']['agent'][key] = value
    envelope['result'].update(readiness=readiness, ready=ready)
    assert responder(tmp_path, envelope).call(intent()) == envelope


@pytest.mark.parametrize('change', [
    {'op': 'readAgent'}, {'op': 'sendMessage'}, {'prompt': 'private-test-secret'}, {'env': {}},
    {'parent': AGENT_ID}, {'agentId': 'b7abb649'}, {'agentId': 'b7abb649-7b0d-0ead-9368-b2a4c7c58209'},
    {'workspaceId': ' w'}, {'workspaceId': 'w\0'}, {'idempotencyKey': ''}, {'title': 'x' * 201},
    {'expectedCwd': 'relative'}, {'expectedCwd': '/bad\0'}, {'expectedCwd': ''},
    {'config': {'provider': 'codex'}}, {'config': {'provider': 'codex/'}},
    {'config': {'provider': 'codex/ model'}}, {'config': {'provider': 'codex/model', 'options': {}}},
    {'config': {'provider': 'codex/model', 'modeId': None}},
    {'config': {'provider': 'codex/model', 'thinkingOptionId': 'x' * 513}},
    {'title': '\U0001f600' * 101},
])
def test_invalid_creation_input_never_starts_process(monkeypatch, change):
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid input must not start a process')
    monkeypatch.setattr('subprocess.Popen', forbidden)
    command = {**intent(), **change}
    with pytest.raises(ValueError):
        module().validate_create_command(command)
    output = module().AgentCreationBridgeAdapter({'url': 'ws://example.invalid/ws'}).call(command)
    assert output['result']['status'] == 'not_attempted'
    assert output['result']['code'] == 'BRIDGE_INVALID_COMMAND'
    assert output['release'] == {'status': 'not_needed'}


def test_utf16_bounds_and_case_preserving_full_uuid():
    command = intent()
    command['agentId'] = AGENT_ID.upper()
    command['title'] = '\U0001f600' * 100
    assert module().validate_create_command(command) == command


@pytest.mark.parametrize('script,cause', [
    ('import time;time.sleep(10)', 'BRIDGE_TIMEOUT'),
    ("import sys;sys.stderr.write('private-test-secret');sys.exit(1)", 'BRIDGE_PROCESS_FAILED'),
    ("print('private-test-secret')", 'BRIDGE_INVALID_RESPONSE'),
    ("print('{}')", 'BRIDGE_INVALID_RESPONSE'),
    ("print('['*2000+'0'+']'*2000)", 'BRIDGE_INVALID_RESPONSE'),
    ("import sys,time;sys.stdout.write('x'*3000000);sys.stdout.flush();time.sleep(10)", 'BRIDGE_INVALID_RESPONSE'),
])
def test_started_process_failures_are_unknown_and_never_retry(tmp_path, script, cause):
    marker = tmp_path / 'attempts'
    script = f"from pathlib import Path\np=Path({str(marker)!r})\np.write_text(p.read_text()+'x' if p.exists() else 'x')\n" + script
    output = adapter(tmp_path, script, timeout=.5).call(intent())
    assert_unknown(output, cause)
    assert marker.read_text() == 'x'


def test_submission_marker_then_missing_stdout_keeps_possible_creation(tmp_path):
    marker = tmp_path / 'submitted'
    bridge = adapter(tmp_path, f"import json,sys\nfrom pathlib import Path\nr=json.load(sys.stdin)\nPath({str(marker)!r}).write_text(r['command']['agentId'])")
    assert_unknown(bridge.call(intent()), 'BRIDGE_INVALID_RESPONSE')
    assert marker.read_text() == AGENT_ID


@pytest.mark.parametrize('target,key,value', [
    ('result', 'agentId', OTHER_ID), ('agent', 'id', OTHER_ID), ('agent', 'workspaceId', 'foreign'),
    ('agent', 'provider', 'other'), ('agent', 'model', 'other'), ('agent', 'cwd', 'relative'),
    ('agent', 'cwd', '/bad\0'), ('agent', 'metadata', {'secret': 'private-test-secret'}),
    ('result', 'config', {'secret': 'private-test-secret'}), ('result', 'ready', 1),
    ('result', 'readiness', 'not_idle'), ('result', 'ready', False),
    ('agent', 'activeTurn', {'turnId': 'turn-1', 'startedAt': None, 'metadata': {}}),
    ('agent', 'capabilities', {'supportsStreaming': True}),
    ('agent', 'status', 'invented'), ('agent', 'archivedAt', False),
])
def test_invalid_created_dto_is_unknown_not_success(tmp_path, target, key, value):
    envelope = created()
    obj = envelope['result'] if target == 'result' else envelope['result']['agent']
    obj[key] = value
    with pytest.raises(ValueError):
        module().validate_create_envelope(envelope, intent())
    assert_unknown(responder(tmp_path, envelope).call(intent()), 'BRIDGE_INVALID_RESPONSE')


@pytest.mark.parametrize('envelope', [
    {'result': created()['result']},
    {**created(), 'config': {'password': 'private-test-secret'}},
    {**created(), 'release': {'status': 'not_needed'}},
    {**created(), 'release': {'status': 'unavailable', 'code': 'SECRET'}},
    {**created(), 'release': {'status': 'ok', 'message': 'private-test-secret'}},
    {'result': {'status': 'unknown', 'code': 'CREATE_OUTCOME_UNKNOWN', 'cause': 'SECRET', 'agentId': AGENT_ID}, 'release': {'status': 'ok'}},
    {'result': {'status': 'unknown', 'code': 'CREATE_OUTCOME_UNKNOWN', 'cause': 'CREATE_FAILED', 'agentId': AGENT_ID}, 'release': {'status': 'not_needed'}},
    {'result': {'status': 'not_attempted', 'code': 'SECRET', 'agentId': AGENT_ID}, 'release': {'status': 'ok'}},
    {'result': {'status': 'not_attempted', 'code': 'CONNECT_FAILED', 'agentId': OTHER_ID}, 'release': {'status': 'ok'}},
    {'result': {'status': 'unknown', 'code': 'CREATE_OUTCOME_UNKNOWN', 'cause': 'IDENTITY_MISMATCH', 'agentId': AGENT_ID,
                'observed': {'handleId': 'alias'}}, 'release': {'status': 'ok'}},
    {'result': {'status': 'unknown', 'code': 'CREATE_OUTCOME_UNKNOWN', 'cause': 'READ_FAILED', 'agentId': AGENT_ID,
                'observed': {'handleId': AGENT_ID, 'config': {'secret': 'private-test-secret'}}}, 'release': {'status': 'ok'}},
    {'release': {'status': 'not_needed'}, 'result': None},
])
def test_invalid_envelope_cannot_erase_possible_side_effects(tmp_path, envelope):
    assert_unknown(responder(tmp_path, envelope).call(intent()), 'BRIDGE_INVALID_RESPONSE')


def test_confirmed_missing_executable_is_not_attempted(tmp_path):
    bridge = module().AgentCreationBridgeAdapter({'url': 'ws://example.invalid/ws'},
                                               command=(str(tmp_path / 'absent-executable'),))
    assert bridge.call(intent()) == {'result': {'status': 'not_attempted', 'code': 'BRIDGE_UNAVAILABLE', 'agentId': AGENT_ID},
                                     'release': {'status': 'not_needed'}}


def test_real_owned_cli_rejects_invalid_config_without_daemon():
    # Syntactically legal input, transport configuration explicitly rejected before connect.
    bridge = module().AgentCreationBridgeAdapter({'url': 'http://example.invalid/ws'}, timeout=3)
    assert bridge.call(intent()) == {'result': {'status': 'not_attempted', 'code': 'INVALID_CONFIG', 'agentId': AGENT_ID},
                                     'release': {'status': 'ok'}}


def test_existing_read_adapter_still_rejects_create(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('read adapter must not create')
    monkeypatch.setattr('subprocess.Popen', forbidden)
    from rag_eval.workbench.bridge_adapter import AgentBridgeAdapter
    assert AgentBridgeAdapter({'url': 'ws://example.invalid/ws'}).call(intent())['result']['code'] == 'BRIDGE_INVALID_COMMAND'


def test_duplicate_json_outcome_cannot_replace_unknown_with_not_attempted(tmp_path):
    raw = ('{"result":{"status":"unknown","code":"CREATE_OUTCOME_UNKNOWN",'
           '"cause":"CREATE_FAILED","agentId":' + json.dumps(AGENT_ID) + '},'
           '"result":{"status":"not_attempted","code":"INVALID_INPUT"},'
           '"release":{"status":"not_needed"}}')
    assert_unknown(adapter(tmp_path, f'print({raw!r})').call(intent()), 'BRIDGE_INVALID_RESPONSE')


@pytest.mark.parametrize('field,value,readiness', [
    ('archivedAt', '', 'archived'),
    ('activeTurn', {'turnId': 'turn-1', 'startedAt': ''}, 'not_idle'),
])
def test_snapshot_string_fields_follow_sdk_nullable_string_shape(tmp_path, field, value, readiness):
    envelope = created()
    envelope['result']['agent'][field] = value
    envelope['result'].update(ready=False, readiness=readiness)
    assert responder(tmp_path, envelope).call(intent()) == envelope


def test_real_owned_cli_input_rejection_shape_is_accepted_without_daemon():
    command = {**intent(), 'prompt': 'forbidden-test-prompt'}
    cli = Path(__file__).resolve().parents[1] / 'apps/sn-agent-bridge/dist/cli.js'
    request = json.dumps({'config': {'url': 'ws://example.invalid/ws'}, 'command': command}).encode()
    returncode, output = module()._run_bounded(('node', str(cli)), request, 3)
    assert returncode == 0
    expected = {'result': {'status': 'not_attempted', 'code': 'INVALID_INPUT'}, 'release': {'status': 'not_needed'}}
    assert module().validate_create_envelope(json.loads(output), intent()) == expected


def test_post_spawn_io_error_is_unknown_and_validator_accepts_adapter_cause(monkeypatch):
    def fail_after_possible_dispatch(*args, **kwargs):
        raise OSError('private-test-secret')
    monkeypatch.setattr(module(), '_run_bounded', fail_after_possible_dispatch)
    bridge = module().AgentCreationBridgeAdapter({'url': 'ws://example.invalid/ws'})
    output = bridge.call(intent())
    assert_unknown(output, 'BRIDGE_UNAVAILABLE')
    assert module().validate_create_envelope(output, intent()) == output


def test_request_byte_limit_rejects_before_spawn(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('overlarge request must not start a child')
    monkeypatch.setattr('subprocess.Popen', forbidden)
    config = {'url': '\U0001f600' * 4096, 'password': '\U0001f600' * 4096, 'authHeader': '\U0001f600' * 4096}
    command = intent()
    command['expectedCwd'] = '/' + '\U0001f600' * 4095
    output = module().AgentCreationBridgeAdapter(config).call(command)
    assert output == {'result': {'status': 'not_attempted', 'code': 'BRIDGE_INVALID_COMMAND', 'agentId': AGENT_ID},
                      'release': {'status': 'not_needed'}}


def test_opaque_whitespace_matches_js_trim_not_python_strip(monkeypatch):
    command = intent()
    command['title'] = '\u0085Example\u001c'  # Python strips these; JS trim does not.
    assert module().validate_create_command(command) == command
    command['title'] = '\ufeffExample'  # JS trims BOM; Python strip does not.
    with pytest.raises(ValueError):
        module().validate_create_command(command)
    def forbidden(*args, **kwargs):
        raise AssertionError('JS-invalid frozen intent must not start a child')
    monkeypatch.setattr('subprocess.Popen', forbidden)
    assert module().AgentCreationBridgeAdapter({'url': 'ws://example.invalid/ws'}).call(command)['result']['status'] == 'not_attempted'


def test_js_utf16_code_units_can_be_json_escaped_without_invalid_utf8():
    command = intent()
    command['title'] = '\ud800'  # Legal JS code unit; JSON must escape it on UTF-8 stdin.
    assert module().validate_create_command(command) == command
    bridge = module().AgentCreationBridgeAdapter({'url': 'http://example.invalid/ws'}, timeout=3)
    assert bridge.call(command) == {'result': {'status': 'not_attempted', 'code': 'INVALID_CONFIG', 'agentId': AGENT_ID},
                                    'release': {'status': 'ok'}}
