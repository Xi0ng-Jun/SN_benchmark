import json

import pytest


def load(path):
    from rag_eval.workbench.configuration import load_config
    return load_config(path)


def test_registered_inputs_resolve_from_config_without_result_registration(tmp_path, monkeypatch):
    directory = tmp_path / 'configuration'
    directory.mkdir()
    path = directory / 'local.json'
    original = {'workspace': {'title': 'SN', 'location': '../workspace'},
                'inputs': [{'id': 'evaluation-input', 'title': 'Input',
                            'description': 'Independent of results', 'bundle': '../canonical',
                            'bundle_id': 'a' * 64}]}
    path.write_text(json.dumps(original))
    monkeypatch.chdir(tmp_path.parent)
    config = load(path)
    assert config['workspace']['location'] == str(tmp_path / 'workspace')
    assert config['inputs'][0]['bundle'] == str(tmp_path / 'canonical')
    assert config['inputs'][0]['bundle_id'] == 'a' * 64
    assert config.get('sources', []) == []
    assert json.loads(path.read_text()) == original


def test_existing_result_sources_and_bridge_configuration_are_preserved(tmp_path):
    path = tmp_path / 'config.json'
    config = {'workspace': {'title': 'SN', 'location': '/workspace'},
              'sources': [{'id': 'old', 'title': 'Old', 'description': 'Recorded',
                           'bundle': 'bundle', 'submission': 'sub.json', 'scores': 'scores.json'}],
              'agent_bridge': {'url': 'ws://127.0.0.1:6767/ws'}}
    path.write_text(json.dumps(config))
    actual = load(path)
    assert actual['agent_bridge'] == config['agent_bridge']
    for key in ('bundle', 'submission', 'scores'):
        assert actual['sources'][0][key] == str(tmp_path / config['sources'][0][key])


@pytest.mark.parametrize('inputs', [
    [{'id': 'same', 'bundle': 'a'}, {'id': 'same', 'bundle': 'b'}],
    [{'id': 'bad id', 'bundle': 'a'}],
    [{'id': 'one', 'bundle': 'a', 'bundle_id': 'not-a-hash'}],
    [{'id': 'one', 'bundle': ''}],
    [{'id': 'one', 'bundle': 'a', 'bundle_id': int('1' * 64)}],
    [{'id': 'one', 'bundle': 'a', 'shell': 'do not run'}],
])
def test_invalid_input_registration_fails_without_loading_any_bundle(tmp_path, inputs):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'workspace': {'title': 'SN', 'location': '.'}, 'inputs': inputs}))
    with pytest.raises(ValueError):
        load(path)


def test_execution_interpreter_preserves_venv_symlink_invocation(tmp_path):
    binary = tmp_path / 'python-real'
    binary.write_text('binary')
    invocation = tmp_path / 'venv/bin/python'
    invocation.parent.mkdir(parents=True)
    invocation.symlink_to(binary)
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(workspace=dict(title='SN', location='.'),
        job_execution=dict(mode='notebook', output_root='runs', python='venv/bin/python'))))
    assert load(config)['job_execution']['python'] == str(invocation)
