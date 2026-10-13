"""Job registration is observable through real HTTP and CLI, never dispatch."""
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

from rag_eval.workbench.api import create_app
from rag_eval.workbench.repository import WorkbenchRepository
from test_workbench_plans_comparison_api import HEADERS, command, setup


def fixture(tmp_path):
    client, config, task, bundle = setup(tmp_path)
    saved = client.post(f"/api/tasks/{task['id']}/plans", json=command(bundle), headers=HEADERS).json()['record']
    payload = dict(plan_id=saved['plan_id'], revision=saved['revision'],
                   expected_content_hash=saved['content_hash'], request_key=' prepare ')
    return client, config, task, saved, payload


def test_http_fixed_revision_receipt_cancel_and_restart(tmp_path):
    client, config, task, plan, payload = fixture(tmp_path)
    path = f"/api/tasks/{task['id']}/jobs"
    boot = client.get('/api/bootstrap', headers=HEADERS).json()
    assert boot['capabilities']['job_registration'] is True
    assert boot['capabilities']['execution'] is False
    response = client.post(path, json=payload, headers=HEADERS)
    assert response.status_code == 200
    envelope = response.json(); job = envelope['record']
    assert envelope == {'operation': 'prepare', 'request_key': ' prepare ', 'record': job}
    assert job['state'] == 'not_dispatched' and job['executable'] is False
    assert job['plan_revision'] == plan['revision'] and job['execution_spec'] is None
    cancel = {'expected_state_version': job['state_version'], 'request_key': 'cancel'}
    stopped = client.post(path + '/' + job['job_id'] + '/cancel', json=cancel, headers=HEADERS).json()['record']
    assert stopped['state'] == 'cancelled'
    assert client.post(path, json=payload, headers=HEADERS).json() == envelope
    receipt = dict(operation='prepare', request_key=' prepare ')
    assert client.get(path + '/receipt', params=receipt, headers=HEADERS).json() == envelope
    assert client.get(path + '/receipt', params={**receipt, 'operation': 'cancel'}, headers=HEADERS).json()['record'] is None
    assert client.get(path + '/' + job['job_id'], headers=HEADERS).json() == stopped
    assert str(tmp_path) not in json.dumps(job)
    with TestClient(create_app(tmp_path / 'state', config, actor='alice', access_token='test-access')) as reopened:
        assert reopened.get(path, headers=HEADERS).json() == [stopped]
        assert reopened.get(path + '/receipt', params=receipt, headers=HEADERS).json() == envelope


def test_http_capture_preparation_uses_trusted_input_binding_and_fresh_source(tmp_path, monkeypatch):
    client, config, task, plan, payload = fixture(tmp_path)
    bundle_ref = {'kind': 'bundle', 'id': 'a' * 64, 'manifest_sha256': 'b' * 64}
    index_ref = {'kind': 'index', 'id': 'c' * 64, 'manifest_sha256': 'd' * 64}
    config['job_preparation'] = {
        'artifact_root': str(tmp_path / 'state' / 'artifacts'),
        'inputs': {plan['content']['input']['input_id']: {'bundle_ref': bundle_ref, 'index_ref': index_ref}},
    }
    from rag_eval.workbench.evaluation_jobs import EvaluationJobStore
    from rag_eval.workbench.task_worktree import TaskWorktreeService
    source_ref = {'kind': 'sn', 'id': 'e' * 64, 'manifest_sha256': 'f' * 64}
    calls = {}
    monkeypatch.setattr(TaskWorktreeService, 'capture_source', lambda self, task_id: source_ref)
    def capture(self, **kwargs):
        calls.update(kwargs)
        return {'preparation': {'preparation_id': '1' * 64}}
    monkeypatch.setattr(EvaluationJobStore, 'capture_inputs_source', capture)
    client = TestClient(create_app(tmp_path / 'state', config, actor='alice', access_token='test-access'))
    headers = HEADERS
    job = client.post('/api/tasks/' + task['id'] + '/jobs', json=payload, headers=headers).json()['record']

    response = client.post(f"/api/tasks/{task['id']}/jobs/{job['job_id']}/preparations", json={
        'expected_state_version': job['state_version'], 'request_key': 'capture',
    }, headers=headers)

    assert response.status_code == 200, response.text
    assert calls['task_id'] == task['id'] and calls['job_id'] == job['job_id']
    assert calls['bundle_ref'] == bundle_ref and calls['index_ref'] == index_ref and calls['sn_ref'] == source_ref


@pytest.mark.parametrize('extra', [dict(actor='forged'), dict(cwd='/secret/path'), dict(argv=['run']),
                                  dict(state='completed'), dict(executable=True), dict(execution_spec={})])
def test_http_forbids_execution_and_server_fields_without_echo(tmp_path, extra):
    client, _, task, _, payload = fixture(tmp_path)
    path = f"/api/tasks/{task['id']}/jobs"
    response = client.post(path, json={**payload, **extra}, headers=HEADERS)
    assert response.status_code == 422
    assert response.json() == {'error': 'Invalid Job request'}
    assert client.get(path, headers=HEADERS).json() == []


def test_http_auth_rejection_cas_and_true_programming_errors(tmp_path, monkeypatch):
    client, _, task, _, payload = fixture(tmp_path)
    path = f"/api/tasks/{task['id']}/jobs"
    assert client.post(path, json=payload).status_code == 401
    assert client.post(path, json=payload, headers={**HEADERS, 'Origin': 'https://other.invalid'}).status_code == 403
    refused = client.post(path, json={**payload, 'expected_content_hash': '0' * 64}, headers=HEADERS)
    assert refused.status_code in (409, 422)
    assert refused.json()['code'] == 'JOB_COMMAND_REJECTED'
    assert refused.json()['request_key'] == payload['request_key']
    assert 'record' not in refused.json()
    assert client.get(path + '/receipt', params={'request_key': payload['request_key'], 'operation': 'prepare'}, headers=HEADERS).json()['record'] is None
    from rag_eval.workbench.evaluation_jobs import EvaluationJobStore
    def failure(*args, **kwargs):
        raise TypeError('genuine program bug')
    monkeypatch.setattr(EvaluationJobStore, 'prepare', failure)
    with pytest.raises(TypeError, match='genuine program bug'):
        client.post(path, json=payload, headers=HEADERS)


def cli(tmp_path, *args):
    return subprocess.run([sys.executable, '-B', '-m', 'rag_eval.workbench.cli', '--state',
                           str(tmp_path / 'state'), *args], capture_output=True, text=True,
                          env={**os.environ, 'PYTHONPATH': str(Path('src').resolve()), 'PYTHONDONTWRITEBYTECODE': '1'})


def test_real_cli_register_read_cancel_original_receipt(tmp_path):
    _, _, task, plan, payload = fixture(tmp_path)
    args = ('job-prepare', '--task', task['id'], '--plan', plan['plan_id'], '--revision', plan['revision'],
            '--content-hash', plan['content_hash'], '--actor', 'operator', '--request-key', ' prepare ')
    result = cli(tmp_path, *args)
    assert result.returncode == 0, result.stderr
    envelope = json.loads(result.stdout); job = envelope['record']
    assert job['state'] == 'not_dispatched' and job['executable'] is False
    stopped = cli(tmp_path, 'job-cancel', '--task', task['id'], '--job', job['job_id'],
                  '--state-version', str(job['state_version']), '--actor', 'operator', '--request-key', 'cancel')
    assert stopped.returncode == 0, stopped.stderr
    assert json.loads(stopped.stdout)['record']['state'] == 'cancelled'
    assert json.loads(cli(tmp_path, *args).stdout) == envelope
    receipt = cli(tmp_path, 'job-receipt', '--task', task['id'], '--actor', 'operator',
                  '--request-key', ' prepare ', '--operation', 'prepare')
    assert receipt.returncode == 0 and json.loads(receipt.stdout) == envelope
    read = cli(tmp_path, 'job-show', '--task', task['id'], '--job', job['job_id'])
    assert read.returncode == 0 and json.loads(read.stdout)['state'] == 'cancelled'


def test_real_cli_domain_rejection_has_safe_json_and_no_writes(tmp_path):
    _, _, task, plan, _ = fixture(tmp_path)
    result = cli(tmp_path, 'job-prepare', '--task', task['id'], '--plan', plan['plan_id'],
                 '--revision', 'missing', '--content-hash', plan['content_hash'],
                 '--actor', 'operator', '--request-key', 'original')
    assert result.returncode == 2
    assert json.loads(result.stderr) == {'error': 'Job command rejected; check the original receipt and current record',
                                       'code': 'JOB_COMMAND_REJECTED', 'operation': 'prepare', 'request_key': 'original'}
    assert 'Traceback' not in result.stderr and str(tmp_path) not in result.stderr
    with WorkbenchRepository(tmp_path / 'state').connect() as conn:
        assert conn.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0


@pytest.mark.parametrize('error', [TypeError('bug'), RuntimeError('bug'),
                                  ValueError('bug'), FileNotFoundError('bug')])
def test_cli_only_catches_known_domain_rejections(tmp_path, monkeypatch, error):
    from rag_eval.workbench.cli import main
    from rag_eval.workbench.evaluation_jobs import EvaluationJobStore
    _, _, task, plan, _ = fixture(tmp_path)
    def failure(*args, **kwargs):
        raise error
    monkeypatch.setattr(EvaluationJobStore, 'prepare', failure)
    monkeypatch.setattr(sys, 'argv', ['workbench', '--state', str(tmp_path / 'state'),
        'job-prepare', '--task', task['id'], '--plan', plan['plan_id'], '--revision', plan['revision'],
        '--content-hash', plan['content_hash'], '--actor', 'operator', '--request-key', 'original'])
    with pytest.raises(type(error)) as caught:
        main()
    assert caught.value is error


@pytest.mark.parametrize('error', [ValueError('/private/program-failure'), FileNotFoundError('/private/program-failure')])
def test_http_internal_value_and_file_errors_propagate_without_raw_response(tmp_path, monkeypatch, error):
    client, config, task, _, payload = fixture(tmp_path)
    def failure(*args, **kwargs):
        raise error
    monkeypatch.setattr(WorkbenchRepository, '_event', failure)
    path = f"/api/tasks/{task['id']}/jobs"
    with pytest.raises(type(error)) as caught:
        client.post(path, json=payload, headers=HEADERS)
    assert caught.value is error
    # Actual HTTP clients receive a server failure, without a rejection code
    # or the exception string. TestClient's raised object remains available.
    with TestClient(create_app(tmp_path / 'state', config, actor='alice', access_token='test-access'),
                    raise_server_exceptions=False) as wire:
        response = wire.post(path, json=payload, headers=HEADERS)
        assert response.status_code == 500
        assert '/private/program-failure' not in response.text and 'JOB_COMMAND_REJECTED' not in response.text


@pytest.mark.parametrize('operation', ['list', 'get', 'receipt'])
def test_http_job_read_program_error_is_safe_500_and_propagates(tmp_path, monkeypatch, operation):
    from rag_eval.workbench.evaluation_jobs import EvaluationJobStore
    client, config, task, _, _ = fixture(tmp_path)
    error = ValueError('/private/read-error')
    def failure(*args, **kwargs):
        raise error
    monkeypatch.setattr(EvaluationJobStore, operation, failure)
    path = f"/api/tasks/{task['id']}/jobs"
    if operation == 'get':
        path += '/unknown'
    elif operation == 'receipt':
        path += '/receipt?operation=prepare&request_key=read'
    with pytest.raises(ValueError) as caught:
        client.get(path, headers=HEADERS)
    assert caught.value is error
    with TestClient(create_app(tmp_path / 'state', config, actor='alice', access_token='test-access'),
                    raise_server_exceptions=False) as wire:
        response = wire.get(path, headers=HEADERS)
        assert response.status_code == 500 and '/private/read-error' not in response.text


@pytest.mark.parametrize('option', ['--cwd', '--argv', '--sourcepaths', '--clientactor', '--state-field'])
def test_cli_forbidden_options_have_fixed_parser_diagnostic(tmp_path, option):
    _, _, task, plan, _ = fixture(tmp_path)
    result = cli(tmp_path, 'job-prepare', '--task', task['id'], '--plan', plan['plan_id'],
                 '--revision', plan['revision'], '--content-hash', plan['content_hash'],
                 '--actor', 'operator', '--request-key', ' original ', option, '/private/forbidden')
    assert result.returncode == 2
    assert '/private/forbidden' not in result.stderr and 'Traceback' not in result.stderr
    assert json.loads(result.stderr)['request_key'] == ' original '


@pytest.mark.parametrize('style', ['abbreviated', 'repeated', 'equals'])
@pytest.mark.parametrize('early_error', [False, True])
@pytest.mark.parametrize('key', [' effective original key ', '-1'])
def test_job_parser_rejection_uses_effective_original_key(tmp_path, style, early_error, key):
    _, _, task, plan, _ = fixture(tmp_path)
    flags = {'abbreviated': ['--request-ke', key],
             'repeated': ['--request-key', 'stale key', '--request-key', key],
             'equals': ['--request-key=' + key]}[style]
    if early_error:
        args = ['job-cancel', '--task', task['id'], '--job', 'synthetic', '--actor', 'operator',
                '--state-version', '/private/bad-version', *flags]
    else:
        args = ['job-prepare', '--task', task['id'], '--plan', plan['plan_id'],
                '--revision', plan['revision'], '--content-hash', plan['content_hash'],
                '--actor', 'operator', *flags]
        accepted = cli(tmp_path, *args)
        assert accepted.returncode == 0, accepted.stderr
        assert json.loads(accepted.stdout)['request_key'] == key
        args += ['--cwd', '/private/forbidden']
    from rag_eval.workbench.repository import WorkbenchRepository
    repo = WorkbenchRepository(tmp_path / 'state')
    def rows():
        with repo.connect() as conn:
            return {table: [tuple(row) for row in conn.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                    for table in ('jobs', 'history', 'requests')}
    before = rows()
    rejected = cli(tmp_path, *args)
    assert rejected.returncode == 2
    assert '/private/' not in rejected.stderr and 'Traceback' not in rejected.stderr
    assert json.loads(rejected.stderr)['request_key'] == key
    assert rows() == before


def test_http_actor_receipt_isolation_and_cross_task_job_scope(tmp_path):
    client, config, task, _, payload = fixture(tmp_path)
    path = f"/api/tasks/{task['id']}/jobs"
    response = client.post(path, json=payload, headers=HEADERS)
    assert response.status_code == 200
    record = response.json()['record']
    other = client.post('/api/tasks', json={'workspace_id': task['workspace_id'], 'title': 'Other',
                        'request_key': 'other-task'}, headers=HEADERS).json()
    other_path = f"/api/tasks/{other['id']}/jobs"
    assert client.get(other_path + '/' + record['job_id'], headers=HEADERS).status_code == 422
    assert client.post(other_path, json=payload, headers=HEADERS).status_code == 422
    assert client.get(other_path, headers=HEADERS).json() == []
    with TestClient(create_app(tmp_path / 'state', config, actor='bob', access_token='test-access')) as bob:
        assert bob.get(path + '/receipt', params={'operation': 'prepare', 'request_key': payload['request_key']},
                       headers=HEADERS).json()['record'] is None
        assert bob.get(path + '/' + record['job_id'], headers=HEADERS).json() == record


@pytest.mark.parametrize('change', [dict(revision=['revision']), dict(expected_content_hash=True),
                                   dict(plan_id=12), dict(request_key=None)])
def test_http_registration_true_type_and_receipt_operation(tmp_path, change):
    client, _, task, _, payload = fixture(tmp_path)
    path = f"/api/tasks/{task['id']}/jobs"
    response = client.post(path, json={**payload, **change}, headers=HEADERS)
    assert response.status_code == 422 and response.json() == {'error': 'Invalid Job request'}
    assert client.get(path + '/receipt', params={'request_key': 'key'}, headers=HEADERS).status_code == 422
    assert client.get(path + '/receipt', params={'request_key': 'key', 'operation': 'run'}, headers=HEADERS).status_code == 422


def test_notebook_http_retry_recovers_original_receipt_without_runtime_files(tmp_path, monkeypatch):
    from test_workbench_notebook_job import prepared, settings
    from rag_eval.workbench.jobs_api import JobAPI
    from fastapi import FastAPI
    jobs, job, prep, args = prepared(tmp_path)
    config = settings(tmp_path, args)
    app = FastAPI()
    adapter = JobAPI(jobs.repository, job['workspace_id'], actor='alice', execution_config=config)
    monkeypatch.setattr(adapter.execution, '_start', lambda record: None)
    app.include_router(adapter.router())
    command = dict(expected_state_version=1, preparation_id=prep['preparation_id'],
        budget=dict(confirmed=True, limit_cny=1, timeout_seconds=10), request_key='same-run')
    with TestClient(app) as client:
        url = f"/api/tasks/{job['task_id']}/jobs/{job['job_id']}/run"
        first = client.post(url, json=command)
        assert first.status_code == 200
        Path(config['model_config']).unlink()
        retry = client.post(url, json=command)
        assert retry.status_code == 200 and retry.json() == first.json()
        changed = client.post(url, json={**command, 'budget': dict(confirmed=True,limit_cny=2,timeout_seconds=10)})
        assert changed.status_code == 409


def test_serve_cli_loads_configuration_and_starts_http_server(tmp_path, monkeypatch):
    from rag_eval.workbench.cli import main
    import uvicorn
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(workspace=dict(title='serve fixture', location=str(tmp_path)))))
    monkeypatch.setattr(sys, 'argv', ['workbench', '--state', str(tmp_path / 'state'),
        'serve', '--config', str(config), '--port', '18098'])
    started = []
    monkeypatch.setattr(uvicorn, 'run', lambda app, **options: started.append(options))
    main()
    assert started == [dict(host='127.0.0.1', port=18098)]
