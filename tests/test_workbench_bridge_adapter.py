"""Python/Node process boundary keeps fixed outcomes and releases distinct."""
import importlib
import json
import sys
import pytest


def adapter(tmp_path, script, *, timeout=2):
    path = tmp_path / 'bridge_stub.py'
    path.write_text(script)
    module = importlib.import_module('rag_eval.workbench.bridge_adapter')
    return module.AgentBridgeAdapter({'url': 'ws://example.invalid/ws', 'password': 'test-secret'},
                                    command=(sys.executable, str(path)), timeout=timeout)


def test_adapter_passes_server_config_on_stdin_and_preserves_release(tmp_path):
    bridge = adapter(tmp_path, """import json,sys
request=json.load(sys.stdin)
assert request['config']['password']=='test-secret'
assert request['command']=={'op':'readAgent','agentId':'agent-1'}
print(json.dumps({'result':{'status':'ok','agent':{'id':'agent-1'}},'release':{'status':'unavailable','code':'CLOSE_FAILED'}}))
""")
    result = bridge.call({'op': 'readAgent', 'agentId': 'agent-1'})
    assert result['result']['agent']['id'] == 'agent-1'
    assert result['release'] == {'status': 'unavailable', 'code': 'CLOSE_FAILED'}
    assert 'test-secret' not in json.dumps(result)


def test_adapter_crash_invalid_output_and_timeout_are_fixed_unavailable(tmp_path):
    for script, code in (("import sys;sys.stderr.write('test-secret');sys.exit(1)", 'BRIDGE_PROCESS_FAILED'),
                         ("print('test-secret')", 'BRIDGE_INVALID_RESPONSE'), ("print('{}')", 'BRIDGE_INVALID_RESPONSE'),
                         ("import time;time.sleep(10)", 'BRIDGE_TIMEOUT')):
        bridge = adapter(tmp_path, script, timeout=.05)
        result = bridge.call({'op': 'readAgent', 'agentId': 'agent-1'})
        assert result['result']['status'] == 'unavailable'
        assert result['release']['status'] == 'unavailable'
        assert result['result']['code'] == code
        assert result['release']['code'] == 'BRIDGE_RELEASE_UNKNOWN'
        assert 'test-secret' not in json.dumps(result)


def test_adapter_bounds_stream_before_completion_and_rejects_deep_json(tmp_path):
    bridge = adapter(tmp_path, "import sys,time;sys.stdout.write('x'*3000000);sys.stdout.flush();time.sleep(10)", timeout=2)
    result = bridge.call({'op': 'readAgent', 'agentId': 'agent-1'})
    assert result['result']['code'] == 'BRIDGE_INVALID_RESPONSE'  # Stop on size, before sleep timeout.
    bridge = adapter(tmp_path, "print('['*2000+'0'+']'*2000)")
    assert bridge.call({'op': 'readAgent', 'agentId': 'agent-1'})['result']['code'] == 'BRIDGE_INVALID_RESPONSE'


def test_adapter_rejects_command_expansion_without_starting_process(tmp_path):
    bridge = adapter(tmp_path, "raise AssertionError('must not run')")
    result = bridge.call({'op': 'sendMessage', 'agentId': 'agent-1', 'text': 'No'})
    assert result['result'] == {'status': 'unavailable', 'code': 'BRIDGE_INVALID_COMMAND'}


@pytest.mark.parametrize('timeout', ['2', None, True, 0, -1, float('nan'), float('inf')])
def test_adapter_rejects_invalid_timeout_before_process_start(monkeypatch, timeout):
    module = importlib.import_module('rag_eval.workbench.bridge_adapter')
    starts = []
    def forbidden_start(*args, **kwargs):
        starts.append(True)
        raise AssertionError('invalid timeout must not start a child')
    monkeypatch.setattr(module.subprocess, 'Popen', forbidden_start)
    with pytest.raises(ValueError, match='finite positive'):
        bridge = module.AgentBridgeAdapter({'url': 'ws://example.invalid'}, timeout=timeout)
        bridge.call({'op': 'readAgent', 'agentId': 'a'})
    assert starts == []


def test_adapter_preserves_missing_agent_without_accepting_missing_release(tmp_path):
    envelope = {'result': {'status': 'missing', 'code': 'AGENT_NOT_FOUND'}, 'release': {'status': 'ok'}}
    bridge = adapter(tmp_path, 'print(' + repr(json.dumps(envelope)) + ')')
    assert bridge.call({'op': 'readAgent', 'agentId': 'unknown'}) == envelope
    for invalid in ({'result': {'status': 'missing', 'code': 'OTHER'}, 'release': {'status': 'ok'}},
                    {'result': {'status': 'ok'}, 'release': {'status': 'missing', 'code': 'AGENT_NOT_FOUND'}}):
        bridge = adapter(tmp_path, 'print(' + repr(json.dumps(invalid)) + ')')
        assert bridge.call({'op': 'readAgent', 'agentId': 'unknown'})['result']['code'] == 'BRIDGE_INVALID_RESPONSE'


def test_adapter_preserves_cli_input_rejection_without_inventing_release_unknown(tmp_path):
    envelope = {'result': {'status': 'unavailable', 'code': 'INVALID_INPUT'},
                'release': {'status': 'not_needed'}}
    bridge = adapter(tmp_path, 'print(' + repr(json.dumps(envelope)) + ')')
    assert bridge.call({'op': 'readAgent', 'agentId': 'unknown'}) == envelope
    invalid = {'result': {'status': 'ok'}, 'release': {'status': 'not_needed'}}
    bridge = adapter(tmp_path, 'print(' + repr(json.dumps(invalid)) + ')')
    assert bridge.call({'op': 'readAgent', 'agentId': 'a'})['result']['code'] == 'BRIDGE_INVALID_RESPONSE'
    for result in (None, [], 'invalid'):
        invalid = {'release': {'status': 'not_needed'}, 'result': result}
        bridge = adapter(tmp_path, 'print(' + repr(json.dumps(invalid)) + ')')
        assert bridge.call({'op': 'readAgent', 'agentId': 'a'})['result']['code'] == 'BRIDGE_INVALID_RESPONSE'
