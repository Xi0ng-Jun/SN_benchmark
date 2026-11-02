"""An iteration bound to an automatic run: read route, manual lock, Agent command limits."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from rag_eval.workbench import auto_runs
from rag_eval.workbench.agent_tools import ANALYST_COMMANDS, IMPLEMENTER_COMMANDS
from rag_eval.workbench.api import create_app
from rag_eval.workbench.repository import Conflict, WorkbenchRepository

WRITE = {'X-Workbench': '1'}
NOW = '2026-11-02T00:00:00+00:00'


def _seed_iteration(repository, workspace_id, iteration_id='it1'):
    task_id = 'task-' + iteration_id
    with repository.connect(write=True) as conn:
        conn.execute('INSERT INTO tasks(id,workspace_id,title,goal,created_at) VALUES(?,?,?,?,?)',
                     (task_id, workspace_id, 't', 'g', NOW))
        conn.execute('INSERT INTO iterations(id,task_id,origin,title,root_candidate_id,unknown_json,created_by,created_at) '
                     'VALUES(?,?,?,?,?,?,?,?)', (iteration_id, task_id, 'native', 't', 'root-' + iteration_id, '{}', 'a', NOW))
        conn.execute('INSERT INTO candidates(id,iteration_id,origin,status,integrated_from_json,inspired_by_json,'
                     'unknown_json,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                     ('root-' + iteration_id, iteration_id, 'root', 'frozen', '[]', '[]', '{}', 'a', NOW, NOW))
    return task_id


@pytest.fixture
def app_env(tmp_path):
    app = create_app(tmp_path / 'state', dict(workspace=dict(title='SN', location=str(tmp_path / 'sn'))),
                     actor='alice', access_token='test-access')
    repository = WorkbenchRepository(tmp_path / 'state')
    with TestClient(app) as client:
        client.post('/api/session', json={'token': 'test-access'})
        workspace_id = client.get('/api/bootstrap').json()['workspaces'][0]['id']
        yield client, repository, _seed_iteration(repository, workspace_id), tmp_path


MANUAL_CANDIDATE_ROUTES = ['derive', 'freeze', 'run', 'fresh-run', 'cluster-run', 'adopt', 'decision', 'proposals', 'provision', 'check']
BODIES = {
    'derive': dict(request_key='k', note='n'), 'run': dict(budget={}, request_key='k'), 'fresh-run': dict(budget={}, request_key='k'),
    'adopt': dict(budget={}, request_key='k'), 'cluster-run': dict(cluster_id='c', budget={}, request_key='k'),
    'decision': dict(decision='adopted'), 'proposals': dict(card={}),
}


def test_a_manual_iteration_is_untouched(app_env):
    client, repository, task_id, _ = app_env
    assert auto_runs.binding(repository, 'it1') is None
    assert auto_runs.task_binding(repository, task_id) is None
    assert client.get('/api/iterations').json()[0]['auto'] is None
    assert client.get('/api/iterations/it1/auto-run').status_code == 422  # UnknownIdentity, as everywhere in this API
    assert auto_runs.agent_refusal(repository, task_id, 'analyst', 'iteration ledger') is None


@pytest.mark.parametrize('route', MANUAL_CANDIDATE_ROUTES)
def test_manual_candidate_writes_are_refused(app_env, route):
    client, repository, _, tmp_path = app_env
    auto_runs.register(repository, 'it1', run_id='r1', state_dir=str(tmp_path), actor='a')
    response = client.post(f'/api/candidates/root-it1/{route}', json=BODIES.get(route, {}), headers=WRITE)
    assert response.status_code == 409, response.text
    assert 'r1' in response.json()['error']


def test_people_cannot_talk_to_the_runs_agents_or_decide_its_cards(app_env):
    client, repository, task_id, tmp_path = app_env
    auto_runs.register(repository, 'it1', run_id='r1', state_dir=str(tmp_path), actor='a')
    key = str(uuid.uuid4())
    for path, body in ((f'/api/tasks/{task_id}/hypotheses/h1/decision', dict(decision='approve', note='')),
                       (f'/api/tasks/{task_id}/hypotheses/h1/worktree', dict(request_key=key))):
        response = client.post(path, json=body, headers=WRITE)
        assert response.status_code in (409, 422), response.text
    assert client.post(f'/api/tasks/{task_id}/hypotheses/h1/decision', json=dict(decision='approve', note=''),
                       headers=WRITE).status_code == 409
    with pytest.raises(Conflict, match='r1'):
        auto_runs.refuse_manual_task(repository, task_id)


def test_read_route_projects_the_bound_run(app_env):
    client, repository, _, tmp_path = app_env
    run_dir = tmp_path / 'run'
    run_dir.mkdir()
    (run_dir / 'cli-run.json').write_text(json.dumps(dict(run_id='r1', iteration_id='it1')))
    auto_runs.register(repository, 'it1', run_id='r1', state_dir=str(run_dir), actor='a')
    assert client.get('/api/iterations').json()[0]['auto'] == dict(run_id='r1', alive=False)
    view = client.get('/api/iterations/it1/auto-run').json()
    assert view['run']['run_id'] == 'r1' and view['run']['status'] == 'not_started'


def test_binding_is_idempotent_and_exclusive(app_env):
    _, repository, _, tmp_path = app_env
    first = auto_runs.register(repository, 'it1', run_id='r1', state_dir=str(tmp_path), actor='a')
    assert auto_runs.register(repository, 'it1', run_id='r1', state_dir=str(tmp_path), actor='b')['actor'] == first['actor']
    with pytest.raises(Conflict, match='bound to run r1'):
        auto_runs.register(repository, 'it1', run_id='r2', state_dir=str(tmp_path), actor='a')
    with pytest.raises(Conflict, match='bound to run r1'):
        auto_runs.register(repository, 'it1', run_id='r1', state_dir=str(tmp_path / 'other'), actor='a')


def test_an_active_manual_round_blocks_binding(app_env):
    _, repository, task_id, tmp_path = app_env
    with repository.connect(write=True) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS iteration_round_invocations(id TEXT PRIMARY KEY, iteration_id TEXT NOT NULL, '
                     'record TEXT NOT NULL, stop_actor TEXT, stop_at REAL)')
        conn.execute('INSERT INTO iteration_round_invocations(id,iteration_id,record) VALUES(?,?,?)',
                     ('inv', 'it1', json.dumps(dict(status='running'))))
    with pytest.raises(Conflict, match='active manual round'):
        auto_runs.register(repository, 'it1', run_id='r1', state_dir=str(tmp_path), actor='a')


def test_agents_of_an_automatic_iteration_cannot_read_scores_or_request_actions(app_env):
    _, repository, task_id, tmp_path = app_env
    auto_runs.register(repository, 'it1', run_id='r1', state_dir=str(tmp_path), actor='a')
    for role, commands, allowed in (('analyst', ANALYST_COMMANDS, auto_runs.ANALYST_ALLOWED),
                                    ('implementer', IMPLEMENTER_COMMANDS, auto_runs.IMPLEMENTER_ALLOWED)):
        assert allowed <= set(commands), 'an allowed command that does not exist'
        for command in commands:
            refusal = auto_runs.agent_refusal(repository, task_id, role, command)
            assert (refusal is None) == (command in allowed), (role, command)
    for leaking in ('iteration ledger', 'iteration show', 'node list', 'node show', 'compare', 'runs', 'cases', 'flips'):
        assert auto_runs.agent_refusal(repository, task_id, 'analyst', leaking)


def test_agent_command_line_refuses_before_reading(app_env, tmp_path, monkeypatch, capsys):
    from rag_eval.workbench import agent_tools
    _, repository, task_id, run_dir = app_env
    auto_runs.register(repository, 'it1', run_id='r1', state_dir=str(run_dir), actor='a')
    monkeypatch.setattr(agent_tools, 'build', lambda state, config: (repository, object()))
    with pytest.raises(SystemExit, match='automatic run r1'):
        agent_tools.main(['--state', 'x', '--task', task_id, '--role', 'analyst', 'iteration', 'ledger'])
