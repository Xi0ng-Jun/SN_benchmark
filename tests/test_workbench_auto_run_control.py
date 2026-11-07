"""Confirmed HTTP actions and real signals against an inert lock-holding child."""
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from rag_eval.optimizer import ledger_io
from rag_eval.optimizer.run_view import run_alive
from rag_eval.workbench import auto_runs
from rag_eval.workbench.auto_run_control import AutoRunControl, Action, Edit, process_identity
from rag_eval.workbench.repository import Conflict
from optimizer.test_v41_calibration_session import calibration_env, env, eight_papers, measure  # noqa: F401


@pytest.fixture
def control(calibration_env, tmp_path):
    service, tree, adapter, root = calibration_env
    measure(calibration_env)
    auto_runs.register(service.repository, tree['id'], run_id='ctl', state_dir=str(root), actor='alice', method='method-v4')
    def scope(*, iteration_id):
        if iteration_id != tree['id']:
            raise ValueError('Unknown iteration identity')
    api = SimpleNamespace(repository=service.repository, service=service, actor='alice', _scope=scope, _write=lambda: None)
    config = tmp_path / 'trusted.json'
    config.write_text('{}')
    controller = AutoRunControl(api, dict(_config_path=str(config), agent_creation=dict(
        workspaceId='test-workspace', profiles={'pi-flash': dict(provider='codex/test')})))
    app = FastAPI()
    app.include_router(controller.router())
    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse(dict(error=str(exc)), status_code=409)
    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse(dict(error=str(exc)), status_code=422)
    with TestClient(app) as client:
        yield controller, client, tree['id'], root


def test_preview_requires_confirmation_and_parameter_edit_invalidates_token(control, monkeypatch):
    ctl, client, iid, root = control
    launches = []
    monkeypatch.setattr(ctl, '_launch', lambda *args: launches.append(args))
    url = f'/api/iterations/{iid}/v4'
    assert client.post(url + '/run', json={}).status_code == 409
    preview = client.post(url + '/run/preview', json={}).json()
    assert preview['incurs_cost'] and 'qasper:' not in json.dumps(preview)
    assert client.patch(url + '/proposal', json=dict(key='max_concurrent_executions', value=2)).status_code == 200
    assert client.post(url + '/run', json=dict(confirmation_token=preview['confirmation_token'])).status_code == 409
    assert launches == []
    preview = client.post(url + '/run/preview', json={}).json()
    confirmed = dict(confirmation_token=preview['confirmation_token'])
    assert client.post(url + '/run', json=confirmed).json()['accepted']
    assert client.post(url + '/run', json=confirmed).status_code == 409
    assert len(launches) == 1
    events = ctl.repository.task(ctl.api.service.iteration_record(iid)['task_id'])['history']
    assert any(e['kind'] == 'v41.control.run.requested' for e in events)


def test_confirmation_binds_actor_action_and_expiry(control, monkeypatch):
    ctl, _, iid, root = control
    preview = ctl.preview(iid, 'run', Action())
    ctl.api.actor = 'bob'
    with pytest.raises(Conflict):
        ctl.execute(iid, 'run', Action(confirmation_token=preview['confirmation_token']))
    ctl.api.actor = 'alice'
    with pytest.raises(Conflict):
        ctl.execute(iid, 'resume', Action(confirmation_token=preview['confirmation_token']))
    saved = ledger_io.read_json(root / 'confirmation.json')
    saved['expires_at'] = 0
    ledger_io.atomic_write_json(root / 'confirmation.json', saved)
    with pytest.raises(Conflict):
        ctl.execute(iid, 'run', Action(confirmation_token=preview['confirmation_token']))


def test_readonly_server_refuses_every_write_before_launch(control, monkeypatch):
    ctl, client, iid, _ = control
    monkeypatch.setattr(ctl.api, '_write', lambda: (_ for _ in ()).throw(ValueError('read only')))
    for route in ('calibrate', 'simulate', 'run/preview', 'run', 'run/resume', 'run/pause', 'run/stop'):
        response = client.post(f'/api/iterations/{iid}/v4/{route}', json={})
        assert response.status_code in (409, 422), response.text
    assert client.patch(f'/api/iterations/{iid}/v4/proposal', json=dict(key='b', value=1)).status_code == 422


def test_calibration_public_reads_and_frozen_edit_protection(control):
    ctl, client, iid, root = control
    response = client.get(f'/api/iterations/{iid}/v4/calibration')
    assert response.status_code == 200 and response.json()['complete']
    assert 'qasper:' not in response.text
    ledger_io.atomic_write_json(root / 'run-config.json', {})
    with pytest.raises(Conflict):
        ctl.edit_proposal(iid, Edit(key='b', value=1))


def test_launcher_uses_only_trusted_config_and_never_starts_endpoint(control, monkeypatch):
    ctl, _, iid, root = control
    ctl.preview(iid, 'run', Action())
    calls = []
    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(pid=123456789)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    ctl._launch(iid, 'ctl', root, 'run', Action())
    argv, opts = calls[-1]
    assert argv[argv.index('--expected-agent-profiles-sha256') + 1] == ledger_io.read_json(root / 'confirmation.json')['agent_profiles_sha256']
    assert '--from-proposal' in argv and '--no-test-set' in argv and '--confirm' not in argv
    assert opts['start_new_session'] is True
    assert argv[argv.index('--config') + 1] == ctl.config['_config_path']
    assert ctl.process(root) is None  # an already exited child has no usable identity


def test_pid_reuse_never_signals_an_unrelated_process(control, monkeypatch):
    ctl, _, iid, root = control
    ledger_io.atomic_write_json(root / 'process.json', dict(pid=9999999, identity='old', action='run'))
    monkeypatch.setattr('rag_eval.workbench.auto_run_control.process_identity', lambda pid: 'new')
    assert ctl.process(root) is None
    with pytest.raises(Conflict, match='owned process'):
        ctl.execute(iid, 'stop', Action())


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('inert child did not reach the expected state')
        time.sleep(.02)


def test_real_mock_process_pause_resume_stop_and_server_restart(control, monkeypatch):
    ctl, _, iid, root = control
    children = []
    script = '''import fcntl,json,pathlib,signal,sys,time
root=pathlib.Path(sys.argv[1])
lock=(root/'run.lock').open('a'); fcntl.flock(lock,fcntl.LOCK_EX)
def stop(sig, frame):
 (root/'state.json').write_text(json.dumps({'halt_reason': 'stopped' if sig==signal.SIGTERM else None}))
 (root/'signal.txt').write_text(str(sig)); sys.exit(0)
signal.signal(signal.SIGINT,stop); signal.signal(signal.SIGTERM,stop)
(root/'ready').write_text('ready')
while True: time.sleep(.1)
'''
    def launch(iteration_id, run_id, directory, action, command):
        (root / 'ready').unlink(missing_ok=True)
        child = subprocess.Popen([sys.executable, '-c', script, str(root)], start_new_session=True)
        children.append(child)
        ledger_io.atomic_write_json(root / 'process.json', dict(pid=child.pid, identity=process_identity(child.pid), action=action))
        wait_until(lambda: (root / 'ready').exists())
    monkeypatch.setattr(ctl, '_launch', launch)
    def confirmed(owner, action):
        p = owner.preview(iid, action, Action())
        return owner.execute(iid, action, Action(confirmation_token=p['confirmation_token']))
    try:
        confirmed(ctl, 'run')
        assert run_alive(root)
        restarted = AutoRunControl(ctl.api, ctl.config)
        assert restarted.busy(root)
        with pytest.raises(Conflict, match='already running'):
            restarted.preview(iid, 'run', Action())
        confirmed(restarted, 'pause')
        children[-1].wait(timeout=5)
        assert not run_alive(root) and (root / 'signal.txt').read_text() == '2'
        confirmed(ctl, 'resume')
        confirmed(restarted, 'stop')
        children[-1].wait(timeout=5)
        assert (root / 'signal.txt').read_text() == '15'
        with pytest.raises(Conflict, match='permanently'):
            ctl.preview(iid, 'resume', Action())
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)


def test_new_operator_cannot_issue_a_fresh_confirmation(control):
    ctl, _, iid, _ = control
    ctl.api.actor = 'bob'
    with pytest.raises(Conflict, match='operator identity'):
        ctl.preview(iid, 'run', Action())


def test_saved_config_change_invalidates_confirmation(control, monkeypatch):
    ctl, _, iid, root = control
    launches = []
    monkeypatch.setattr(ctl, '_launch', lambda *args: launches.append(args))
    preview = ctl.preview(iid, 'run', Action())
    ledger_io.atomic_write_json(root / 'resolved-config.json', {'time_budget_hours': 10})
    with pytest.raises(Conflict, match='configuration changed'):
        ctl.execute(iid, 'run', Action(confirmation_token=preview['confirmation_token']))
    assert not launches


def test_pending_calibration_exposes_original_browser_inputs_for_resume(control):
    ctl, _, iid, root = control
    inputs = dict(time_budget_hours=2, calibration_k=3, adapter=dict(calibrator_profile='review-fast'), test_set_authorized=True)
    ledger_io.atomic_write_json(root / 'launch.json', dict(inputs=inputs, test_set_authorized=True))
    (root / 'calibration.json').unlink()
    response = ctl.calibration(iid)
    assert response['launch_inputs'] == inputs and not response['complete']


@pytest.mark.parametrize('inputs', [
    dict(time_budget_hours=1, spend_budget=10),
    dict(time_budget_hours=1, price_per_1k_tokens=-1),
    dict(time_budget_hours=1, test_set_authorized='yes'),
    dict(time_budget_hours=1, calibration_concurrency=0),
    dict(time_budget_hours=1, calibration_concurrency=17),
    dict(time_budget_hours=1, calibration_concurrency=True),
])
def test_invalid_budget_or_authorization_refused_before_confirmation(control, inputs):
    ctl, _, iid, root = control
    with pytest.raises(ValueError):
        ctl.preview(iid, 'calibrate', Action(inputs=inputs))
    assert not (root / 'confirmation.json').exists()


def test_calibration_concurrency_is_in_confirmed_config(control):
    ctl, _, iid, root = control
    (root / 'calibration.json').unlink()
    preview = ctl.preview(iid, 'calibrate', Action(inputs=dict(time_budget_hours=1, calibration_concurrency=3)))
    assert preview['config']['calibration_concurrency'] == 3
    assert ctl._calibration_input(dict(time_budget_hours=1))['calibration_concurrency'] == 2


def test_resume_calibration_uses_saved_config_and_refuses_input_changes(control):
    ctl, _, iid, root = control
    inputs = dict(time_budget_hours=2, calibration_k=3)
    config = ctl._calibration_input(inputs)
    ledger_io.atomic_write_json(root / 'launch.json', dict(inputs=inputs))
    ledger_io.atomic_write_json(root / 'calibration-input.json', config)
    ctl.config['optimizer_v4'] = dict(adapter=dict(analyst_profile='changed-server-default'))
    preview = ctl.preview(iid, 'calibrate', Action(inputs=inputs))
    assert preview['config'] == config
    with pytest.raises(Conflict, match='original inputs'):
        ctl.preview(iid, 'calibrate', Action(inputs=dict(inputs, calibration_k=4)))


def test_profiles_preflight_resolves_defaults_and_rechecks_confirmation(control, monkeypatch):
    ctl, client, iid, root = control
    (root / 'calibration.json').unlink()
    ctl.config['agent_creation']['profiles'] = {'server-model': dict(provider='codex/test')}
    url = f'/api/iterations/{iid}/v4/calibrate'
    inputs = dict(time_budget_hours=2)
    rejected = client.post(url, json=dict(inputs=inputs))
    assert rejected.status_code == 422
    assert '模型配置' in rejected.json()['error']
    assert not (root / 'confirmation.json').exists()
    inputs['adapter'] = dict(analyst_profile='server-model', implementer_profile='server-model')
    preview = client.post(url, json=dict(inputs=inputs))
    assert preview.status_code == 200
    resolved = preview.json()['config']['adapter']
    assert resolved['calibrator_profile'] == resolved['reviewer_profile'] == 'server-model'
    assert ledger_io.read_json(root / 'confirmation.json')['agent_profiles_sha256']
    launches = []
    monkeypatch.setattr(ctl, '_launch', lambda *a: launches.append(a))
    ctl.config['agent_creation']['profiles']['server-model']['provider'] = 'codex/changed'
    response = client.post(url, json=dict(inputs=inputs, confirmation_token=preview.json()['confirmation_token']))
    assert response.status_code == 409
    ctl.config['agent_creation']['profiles'] = {}
    response = client.post(url, json=dict(inputs=inputs, confirmation_token=preview.json()['confirmation_token']))
    assert response.status_code == 422
    assert launches == []


def test_calibration_recovery_budget_is_confirmed_without_changing_original_input(control, monkeypatch):
    from rag_eval.optimizer.v4 import calibration_session as session
    ctl, client, iid, root = control
    bundle = session._load(root / session.CALIBRATION_FILE)
    bundle['complete'] = False
    session._save(root / session.CALIBRATION_FILE, bundle)
    inputs = dict(time_budget_hours=2, calibration_k=3, adapter={})
    config = ctl._calibration_input(inputs)
    ledger_io.atomic_write_json(root / 'calibration-input.json', config)
    ledger_io.atomic_write_json(root / 'launch.json', dict(inputs=inputs))
    before = (root / 'calibration-input.json').read_bytes()
    launches = []
    monkeypatch.setattr(ctl, '_launch', lambda *args: launches.append(args))
    url = f'/api/iterations/{iid}/v4/calibrate'
    body = dict(inputs=inputs, recovery_execution_timeout_seconds=14400)
    response = client.post(url, json=body)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview['execution_timeout_seconds'] == 14400
    assert client.post(url, json=dict(body, recovery_execution_timeout_seconds=18000,
        confirmation_token=preview['confirmation_token'])).status_code == 409
    assert not launches
    assert client.post(url, json=dict(body, confirmation_token=preview['confirmation_token'])).status_code == 200
    assert (root / 'calibration-input.json').read_bytes() == before
    assert launches[0][-1].recovery_execution_timeout_seconds == 14400


def test_new_calibration_accepts_execution_budget_but_not_connection_settings(control):
    ctl, _, _, _ = control
    config = ctl._calibration_input(dict(time_budget_hours=48,
        adapter=dict(execution_timeout_seconds=14400)))
    assert config['adapter']['execution_timeout_seconds'] == 14400
    with pytest.raises(ValueError):
        ctl._calibration_input(dict(time_budget_hours=48, adapter=dict(server_url='untrusted')))
