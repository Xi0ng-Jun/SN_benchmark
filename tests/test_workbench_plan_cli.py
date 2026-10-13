import json
import sys
import subprocess
from pathlib import Path

import pytest

from test_benchmark_comparison import examples
from rag_eval.identity import fingerprint
from rag_eval.workbench.repository import WorkbenchRepository
from rag_eval.workbench import cli


def invoke(monkeypatch, capsys, state, *arguments):
    monkeypatch.setattr(sys, 'argv', ['workbench', '--state', str(state), *map(str, arguments)])
    cli.main()
    return json.loads(capsys.readouterr().out)


def test_cli_save_list_fixed_version_receipt_survive_source_deletion(tmp_path, monkeypatch, capsys):
    bundle, _, _, _ = examples(tmp_path)
    state = tmp_path / 'state'
    repo = WorkbenchRepository(state)
    ws = repo.register_workspace('SN', tmp_path / 'workspace')
    task = repo.create_task(ws['id'], 'Draft without result', '', request_key='task')
    config = tmp_path / 'local.json'
    config.write_text(json.dumps({'workspace': {'title': 'SN', 'location': 'workspace'},
                                 'inputs': [{'id': 'canonical', 'bundle': 'bundle',
                                             'bundle_id': fingerprint(bundle['manifest'])}]}))
    payload = {'title': 'Draft', 'input_id': 'canonical', 'expected_bundle_id': fingerprint(bundle['manifest']),
               'case_ids': [bundle['cases'][0]['case_id']], 'mode': 'reasoning',
               'plan_id': None, 'base_revision': None, 'request_key': 'save'}
    path = tmp_path / 'command.json'
    path.write_text(json.dumps(payload))
    saved = invoke(monkeypatch, capsys, state, 'plan-save', '--config', config, '--task', task['id'],
                   '--actor', 'alice', '--input', path)
    assert saved['actor'] == 'alice'
    assert saved['execution'] is False
    assert saved['content']['execution_intent']['mode'] == 'reasoning'
    (tmp_path / 'bundle').rename(tmp_path / 'removed-bundle')
    assert invoke(monkeypatch, capsys, state, 'plan-list', '--task', task['id']) == [saved]
    assert invoke(monkeypatch, capsys, state, 'plan-show', '--task', task['id'], '--plan', saved['plan_id'],
                  '--revision', saved['revision']) == saved
    assert invoke(monkeypatch, capsys, state, 'plan-receipt', '--task', task['id'], '--actor', 'alice',
                  '--request-key', 'save') == saved
    assert invoke(monkeypatch, capsys, state, 'plan-save', '--config', config, '--task', task['id'],
                  '--actor', 'alice', '--input', path) == saved


@pytest.mark.parametrize('field', ['actor', 'workspace_id', 'task_id', 'content', 'partition_ids'])
def test_cli_rejects_payload_identity_override(tmp_path, monkeypatch, capsys, field):
    state = tmp_path / 'state'
    repo = WorkbenchRepository(state)
    ws = repo.register_workspace('SN', tmp_path)
    task = repo.create_task(ws['id'], 'Draft', '', request_key='task')
    config = tmp_path / 'local.json'
    config.write_text(json.dumps({'workspace': {'title': 'SN', 'location': '.'}}))
    path = tmp_path / 'command.json'
    path.write_text(json.dumps({field: 'forged'}))
    with pytest.raises(SystemExit) as error:
        invoke(monkeypatch, capsys, state, 'plan-save', '--config', config, '--task', task['id'],
               '--actor', 'alice', '--input', path)
    assert error.value.code == 2
    assert repo.task(task['id'])['history'][-1]['kind'] == 'task.created'


@pytest.mark.parametrize('payload', ['{}', '{"title":"only-title"}', '[]', '{broken-json'])
def test_cli_malformed_command_has_safe_diagnostic_and_no_intent_write(tmp_path, payload):
    state = tmp_path / 'state'
    repo = WorkbenchRepository(state)
    ws = repo.register_workspace('SN', tmp_path)
    task = repo.create_task(ws['id'], 'Draft', '', request_key='task')
    before = repo.task(task['id'])
    config = tmp_path / 'local.json'
    config.write_text(json.dumps({'workspace': {'title': 'SN', 'location': '.'}}))
    command = tmp_path / 'private-command.json'
    command.write_text(payload)
    result = subprocess.run([sys.executable, '-m', 'rag_eval.workbench.cli', '--state', str(state),
                             'plan-save', '--config', str(config), '--task', task['id'],
                             '--actor', 'alice', '--input', str(command)], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'error:' in result.stderr
    assert 'Traceback' not in result.stderr
    assert str(Path.cwd()) not in result.stderr
    assert str(tmp_path) not in result.stderr
    assert repo.task(task['id']) == before


@pytest.mark.parametrize('failure', ['unknown_input', 'missing_source', 'bundle_conflict', 'stale_base', 'unknown_plan'])
def test_cli_known_domain_refusals_have_safe_json_without_traceback(tmp_path, monkeypatch, capsys, failure):
    bundle, _, _, _ = examples(tmp_path)
    state = tmp_path / 'state'
    repo = WorkbenchRepository(state)
    ws = repo.register_workspace('Synthetic CLI', tmp_path)
    task = repo.create_task(ws['id'], 'Draft', '', request_key='task')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'workspace': {'title': 'Synthetic CLI', 'location': '.'},
                                 'inputs': [{'id': 'canonical', 'bundle': 'bundle',
                                             'bundle_id': fingerprint(bundle['manifest'])}]}))
    command = tmp_path / 'command.json'
    payload = dict(title='Draft', input_id='canonical', expected_bundle_id=fingerprint(bundle['manifest']),
                   case_ids=[bundle['cases'][0]['case_id']], mode='chunk', request_key='original-key')
    arguments = ['plan-save', '--config', str(config), '--task', task['id'], '--actor', 'alice', '--input', str(command)]
    if failure == 'unknown_input':
        payload['input_id'] = 'unregistered'
    elif failure == 'missing_source':
        (tmp_path / 'bundle').rename(tmp_path / 'removed-source')
    elif failure == 'bundle_conflict':
        payload['expected_bundle_id'] = '0' * 64
    elif failure == 'stale_base':
        command.write_text(json.dumps(payload))
        saved = invoke(monkeypatch, capsys, state, *arguments)
        payload.update(plan_id=saved['plan_id'], base_revision='outdated', request_key='next-key')
    else:
        arguments = ['plan-show', '--task', task['id'], '--plan', 'unknown-plan']
    command.write_text(json.dumps(payload))
    before = repo.task(task['id'])
    result = subprocess.run([sys.executable, '-m', 'rag_eval.workbench.cli', '--state', str(state), *arguments],
                            capture_output=True, text=True)
    assert result.returncode == 2
    diagnostic = json.loads(result.stderr)
    assert diagnostic['code'] == ('PLAN_READ_REJECTED' if failure == 'unknown_plan' else 'PLAN_SAVE_REJECTED')
    assert diagnostic['request_key'] == (None if failure == 'unknown_plan' else payload['request_key'])
    assert diagnostic['error']
    assert result.stdout == ''
    assert 'Traceback' not in result.stderr
    assert str(Path.cwd()) not in result.stderr
    assert str(tmp_path) not in result.stderr
    assert repo.task(task['id']) == before


@pytest.mark.parametrize('error_type', [TypeError, RuntimeError])
def test_cli_does_not_hide_store_programming_exceptions(tmp_path, monkeypatch, error_type):
    from rag_eval.workbench.evaluation_plans import PlanDraftStore
    def broken(_self, _task_id):
        raise error_type('deliberate programming exception')
    monkeypatch.setattr(PlanDraftStore, 'list', broken)
    monkeypatch.setattr(sys, 'argv', ['workbench', '--state', str(tmp_path / 'state'), 'plan-list', '--task', 'task'])
    with pytest.raises(error_type, match='deliberate programming exception'):
        cli.main()
