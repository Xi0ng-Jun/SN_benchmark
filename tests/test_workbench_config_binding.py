"""Agent commands stay bound to their server even if Paseo inherits another config."""
import json
import shlex

from rag_eval.workbench.api import create_app
from rag_eval.workbench.configuration import load_config
from rag_eval.workbench.context import resolve_context


def test_server_brief_pins_loaded_config_over_stale_daemon_environment(tmp_path, monkeypatch):
    config_path = tmp_path / 'new-config.json'
    config_path.write_text(json.dumps({'workspace': {'title': 'isolated', 'location': str(tmp_path / 'checkout')}}))
    monkeypatch.setenv('SN_WORKBENCH_CONFIG', str(tmp_path / 'old-config.json'))
    config = load_config(config_path)
    app = create_app(tmp_path / 'state', config)
    ops = app.state.round_service.operations
    task = ops.repository.create_task(ops.api.workspace['id'], 'Test', '', request_key='test')
    # Observe exactly the command handed to the real role brief renderer.
    monkeypatch.setattr('rag_eval.workbench.briefs.resolve_brief',
                        lambda *args, tool, **kwargs: ('brief', tool, {}))
    snapshot = resolve_context(ops.repository, task['id'], {'kind': 'brief', 'session_id': 'session'},
                               run_views=ops.api.views)
    argv = shlex.split(snapshot['text'])
    assert '--config' in argv
    assert argv[argv.index('--config') + 1] == str(config_path.resolve())
    assert argv[argv.index('--state') + 1] == str((tmp_path / 'state').resolve())
    assert str(tmp_path / 'old-config.json') not in argv
