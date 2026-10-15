"""Selecting an SN registration is explicit and does not change existing bindings."""
from fastapi.testclient import TestClient

from rag_eval.workbench.api import create_app
from test_task_worktree import setup, git


def test_bootstrap_describes_each_registration_and_binding_uses_selected_id(setup, tmp_path):
    project, first, repo, task, store, service = setup
    second = tmp_path / 'second-sn'
    git(project, 'worktree', 'add', '-qb', 'second', str(second))
    config = dict(workspace={'title': 'Evaluation', 'location': str(tmp_path / 'evaluation')},
        task_worktrees={'project_root': str(project), 'artifact_root': str(store.root),
            'registrations': [
                {'id': 'first', 'path': str(first), 'paseo_workspace_id': 'ws-first'},
                {'id': 'second', 'title': 'Server SN', 'path': str(second),
                 'paseo_workspace_id': 'ws-second'}]})
    with TestClient(create_app(repo.root, config, access_token='test')) as client:
        client.headers.update({'Authorization': 'Bearer test', 'X-Workbench': '1'})
        assert client.get('/api/bootstrap').json()['execution_worktrees'] == [
            {'id': 'first', 'title': 'first', 'path': str(first)},
            {'id': 'second', 'title': 'Server SN', 'path': str(second)}]
        endpoint = f'/api/tasks/{task}/worktree'
        assert client.post(endpoint, json={'worktree_id': 'unregistered'}).status_code == 404
        selected = client.post(endpoint, json={'worktree_id': 'second'})
        assert selected.status_code == 200, selected.text
        assert selected.json()['worktree_path'] == str(second)
        assert selected.json()['paseo_workspace_id'] == 'ws-second'
        assert client.post(endpoint, json={'worktree_id': 'first'}).status_code == 409
        assert service.binding(task)['worktree_path'] == str(second)
