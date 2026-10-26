"""Human round API and thin operator CLI, without any external dispatch."""
import json
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag_eval.workbench.loop_api import RoundAPI, RoundManager
from rag_eval.workbench import operator_client


class Rounds:
    def __init__(self):
        self.rows = {}
        self.calls = []

    def list(self, iteration_id=None):
        return [r for r in self.rows.values() if iteration_id is None or r['iteration_id'] == iteration_id]

    def get(self, rid):
        return self.rows[rid]

    def start(self, iid, **kwargs):
        self.calls.append((iid, kwargs))
        self.rows['r1'] = dict(id='r1', iteration_id=iid, status='running')
        return self.rows['r1']

    def stop(self, rid, **kwargs):
        self.rows[rid]['status'] = 'stopped'
        self.calls.append(('stop', kwargs))
        return self.rows[rid]

    def tick(self, rid):
        self.rows[rid]['status'] = 'completed'
        return self.rows[rid]


def test_round_routes_scope_actor_and_wakeup():
    rounds, checked, wakeups = Rounds(), [], []
    iterations = SimpleNamespace(actor='operator', _write=lambda: checked.append('write'),
                                  _scope=lambda **kw: checked.append(kw))
    api = RoundAPI(iterations, rounds, SimpleNamespace(wake=lambda: wakeups.append(True)))
    app = FastAPI()
    app.include_router(api.router())
    with TestClient(app) as client:
        result = client.post('/api/iterations/i1/rounds', json=dict(request_key='k', rounds=2))
        assert result.status_code == 200
        assert rounds.calls == [('i1', dict(actor='operator', request_key='k', rounds=2))]
        assert checked == ['write', dict(iteration_id='i1')]
        assert wakeups == [True]
        assert client.get('/api/iterations/i1/rounds').json()[0]['id'] == 'r1'
        assert client.get('/api/rounds/r1').json()['status'] == 'running'
        assert client.post('/api/rounds/r1/stop', json={}).json()['status'] == 'stopped'
        assert rounds.calls[-1] == ('stop', dict(actor='operator'))
        assert client.post('/api/iterations/i1/rounds', json=dict(request_key='k', actor='forged')).status_code == 422
        assert client.post('/api/iterations/i1/rounds', json=dict(request_key='k', rounds=0)).status_code == 422


def test_manager_recovers_persisted_active_invocation_and_stops_cleanly():
    rounds = Rounds()
    rounds.start('i1', actor='old', request_key='old')
    manager = RoundManager(rounds, allowed=lambda iid: iid == 'i1', poll_seconds=.01)
    manager.start()
    try:
        deadline = time.monotonic() + 2
        while rounds.get('r1')['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(.01)
        assert rounds.get('r1')['status'] == 'completed'
    finally:
        manager.close()
    assert not manager.thread.is_alive()


def test_round_cli_is_http_only(tmp_path, monkeypatch, capsys):
    (tmp_path / 'access-token').write_text('secret')
    calls = []
    monkeypatch.setattr(operator_client.Client, 'call', lambda self, *args: calls.append(args) or {'id': 'r'})
    operator_client.main(['--state', str(tmp_path), 'iteration', 'round', 'i1', '--rounds', '2', '--request-key', 'stable'])
    assert calls == [('POST', '/api/iterations/i1/rounds', dict(rounds=2, request_key='stable'))]
    assert json.loads(capsys.readouterr().out)['id'] == 'r'
    calls.clear()
    operator_client.main(['--state', str(tmp_path), 'iteration', 'round-show', 'r'])
    assert calls == [('GET', '/api/rounds/r')]
    calls.clear()
    operator_client.main(['--state', str(tmp_path), 'iteration', 'round-stop', 'r'])
    assert calls == [('POST', '/api/rounds/r/stop', {})]


def test_authenticated_app_lifecycle_resumes_round_after_http_disconnect(tmp_path, monkeypatch):
    from rag_eval.workbench.api import create_app
    from rag_eval.workbench.loop import RoundService
    from test_workbench_loop import Ports
    # Keep the real persistence/HTTP/lifespan; replace only external operations.
    monkeypatch.setattr('rag_eval.workbench.loop_services.RoundOperations', lambda *args: Ports())
    config = dict(workspace=dict(title='test', location='/sn'), task_worktrees=dict(create_candidates=True))
    app = create_app(tmp_path / 'state', config, actor='human', access_token='test')
    rounds = app.state.round_service
    # Scope normally comes from real IterationService; this test has fake ports.
    manager = app.state.round_manager
    manager.allowed = lambda iid: True
    manager.poll_seconds = .01
    state = rounds.start('synthetic-iteration', actor='human', request_key='recover')
    with TestClient(app) as client:
        assert client.get('/api/rounds/' + state['id']).status_code == 401
        assert manager.thread.is_alive()
        deadline = time.monotonic() + 5
        while rounds.get(state['id'])['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(.01)
        assert rounds.get(state['id'])['status'] == 'completed'
    assert not manager.thread.is_alive()
    restored = RoundService(rounds.repo, Ports())
    assert restored.get(state['id'])['stop_reason'] == 'rounds_exhausted'


def test_manager_bad_scope_does_not_starve_other_invocations():
    rounds = Rounds()
    rounds.rows['bad'] = dict(id='bad', iteration_id='missing', status='running')
    rounds.start('i1', actor='old', request_key='old')
    def allowed(iid):
        if iid == 'missing':
            raise ValueError('removed workspace')
        return True
    manager = RoundManager(rounds, allowed=allowed, poll_seconds=.01)
    manager.start()
    try:
        deadline = time.monotonic() + 2
        while rounds.get('r1')['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(.01)
        assert rounds.get('r1')['status'] == 'completed'
        assert rounds.get('bad')['status'] == 'running'
    finally:
        manager.close()
