import json
import os
from pathlib import Path
import subprocess
import sys

from rag_eval.workbench.configuration import load_config
from test_notebook_data_v3 import freeze, qasper_raw


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/configure_workbench_server.py'


def invoke(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True)


def test_new_checkout_starts_without_local_paths_or_model_execution(tmp_path):
    config = tmp_path / 'server/config.json'
    result = invoke('init', '--config', config)
    assert result.returncode == 0, result.stderr
    value = load_config(config)
    assert value['inputs'] == []
    assert 'job_execution' not in value
    assert 'agent_creation' not in value
    assert value['task_worktrees']['create_from_hypothesis'] is False
    assert os.stat(config).st_mode & 0o777 == 0o600
    from fastapi.testclient import TestClient
    from rag_eval.workbench.api import create_app
    with TestClient(create_app(tmp_path / 'state', value, access_token='test')) as client:
        assert client.get('/api/bootstrap', headers={'Authorization': 'Bearer test'}).status_code == 200


def test_refuses_overwrite_even_with_malformed_bundle(tmp_path):
    config = tmp_path / 'config.json'
    config.write_text('do not replace')
    result = invoke('init', '--config', config, '--bundle', 'qasper=/missing')
    assert result.returncode != 0
    assert config.read_text() == 'do not replace'


def test_registers_verified_bundle_and_detects_later_tampering(tmp_path):
    freeze(tmp_path / 'input', 'qasper', qasper_raw())
    config = tmp_path / 'server/config.json'
    result = invoke('init', '--config', config, '--bundle', f'qasper={tmp_path}/input/bundle')
    assert result.returncode == 0, result.stderr
    data = load_config(config)
    assert data['inputs'][0]['id'] == 'qasper'
    refs = data['job_preparation']['inputs']['qasper']
    assert set(refs) == {'bundle_ref', 'index_ref'}
    assert invoke('check', '--config', config).returncode == 0
    from rag_eval.artifact_store import ArtifactStore
    copied = ArtifactStore(data['job_preparation']['artifact_root']).resolve(refs['bundle_ref'])
    (copied / 'manifest.json').write_text('{}')
    assert invoke('check', '--config', config).returncode != 0


def test_duplicate_input_ids_do_not_publish_config(tmp_path):
    config = tmp_path / 'config.json'
    result = invoke('init', '--config', config, '--bundle', 'same=/a', '--bundle', 'same=/b')
    assert result.returncode != 0
    assert not config.exists()


def test_execution_readiness_fails_closed_for_starter(tmp_path):
    config = tmp_path / 'config.json'
    assert invoke('init', '--config', config).returncode == 0
    result = invoke('check', '--config', config, '--execution')
    assert result.returncode != 0
    assert 'job_execution' in result.stderr


def test_requires_explicit_sn_root_for_agent_workspace(tmp_path):
    config = tmp_path / 'config.json'
    result = invoke('init', '--config', config, '--workspace-id', 'server-workspace')
    assert result.returncode != 0
    assert not config.exists()


def test_execution_check_validates_interpreter_and_output_isolation(tmp_path):
    config = tmp_path / 'config.json'
    freeze(tmp_path / 'input', 'qasper', qasper_raw())
    assert invoke('init', '--config', config, '--bundle', f'qasper={tmp_path}/input/bundle').returncode == 0
    data = json.loads(config.read_text())
    sn = tmp_path / 'sn'
    (sn / 'backend').mkdir(parents=True)
    (tmp_path / 'scorers').mkdir()
    (tmp_path / 'model.toml').write_text('# private fixture')
    (tmp_path / 'requirements.txt').write_text('pydantic>=2\n')
    expectation = tmp_path / 'runtime.json'
    expectation.write_text(json.dumps(dict(executable=sys.executable, prefix=sys.prefix)))
    data['task_worktrees']['project_root'] = str(sn)
    data['agent_creation'] = {'workspaceId': 'fixture', 'profiles': {'test': {'provider': 'pi/test'}}}
    data['job_execution'] = dict(mode='notebook', python=sys.executable,
        output_root=str(tmp_path / 'executions'), project_root=str(sn),
        artifact_root=data['job_preparation']['artifact_root'],
        model_config=str(tmp_path / 'model.toml'), scorer_sources=str(tmp_path / 'scorers'),
        dependency_requirements=str(tmp_path / 'requirements.txt'), runtime_expectation=str(expectation))
    config.write_text(json.dumps(data))
    result = invoke('check', '--config', config, '--execution')
    assert result.returncode == 0, result.stderr
    expectation.write_text(json.dumps(dict(executable=sys.executable, prefix='/wrong/venv')))
    assert invoke('check', '--config', config, '--execution').returncode != 0
    data['job_execution']['output_root'] = str(sn / 'production')
    config.write_text(json.dumps(data))
    result = invoke('check', '--config', config, '--execution')
    assert result.returncode != 0
    assert 'overlaps' in result.stderr


def test_agent_read_tool_uses_server_defaults_without_local_config(tmp_path, monkeypatch, capsys):
    from rag_eval.workbench.agent_tools import main
    from rag_eval.workbench.repository import WorkbenchRepository
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('SN_WORKBENCH_CONFIG', raising=False)
    monkeypatch.delenv('SN_WORKBENCH_STATE', raising=False)
    config = tmp_path / 'var/sn-workbench-server/config.json'
    assert invoke('init', '--config', config).returncode == 0
    settings = load_config(config)
    repo = WorkbenchRepository(tmp_path / 'var/sn-workbench-server/state')
    ws = repo.register_workspace(settings['workspace']['title'], settings['workspace']['location'])
    task = repo.create_task(ws['id'], 'server task', '', request_key='fixture')
    main(['--task', task['id'], 'task'])
    assert json.loads(capsys.readouterr().out)['task']['id'] == task['id']
