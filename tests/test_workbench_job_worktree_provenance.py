"""Notebook dispatch labels the captured source's tree, never Task selection."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from rag_eval.workbench.hypotheses import HypothesisStore
from rag_eval.workbench.hypothesis_worktrees import HypothesisWorktreeService
from rag_eval.workbench.jobs_api import JobAPI
from rag_eval.workbench.task_worktree import TaskWorktreeService, initialize_task_worktrees
from test_hypothesis_worktrees import Creator, git
from test_workbench_job_preparation_records import environment
from test_workbench_notebook_job import settings
from test_workbench_roles_hypotheses import CARD


def captured_job(tmp_path, monkeypatch):
    repo, jobs, job, args = environment(tmp_path)
    task = job['task_id']
    project, legacy = tmp_path / 'sn', tmp_path / 'legacy'
    git(project, 'worktree', 'add', '-qb', 'legacy', str(legacy), 'HEAD')
    (legacy / 'backend/app/__init__.py').write_text('VALUE = "captured A"\n')
    initialize_task_worktrees(repo)
    service = TaskWorktreeService(repo, args['artifact_store'], project_root=project)
    service.bind(task, legacy, paseo_workspace_id='workspace-A')
    args['sn_ref'] = service.capture_source(task)
    prep = jobs.capture_inputs_source(**args)['preparation']
    cards = HypothesisStore(repo)
    card = cards.draft(task, CARD, author='analyst')
    cards.decide(task, card['id'], 'approve', actor='human')
    trees = HypothesisWorktreeService(repo, service, creator=Creator(tmp_path))
    new = trees.create(task, card['id'], actor='human')['binding']
    trees.activate(task, card['id'], actor='human')
    adapter = JobAPI(repo, job['workspace_id'], actor='alice',
                     execution_config=settings(tmp_path, args), worktree_service=service)
    # Preserve actual spec/receipt publication; only process launch is external.
    monkeypatch.setattr(adapter.execution, '_start', lambda record: None)
    app = FastAPI()
    app.include_router(adapter.router())
    command = dict(expected_state_version=1, preparation_id=prep['preparation_id'],
                   budget=dict(confirmed=True, limit_cny=1, timeout_seconds=10), request_key='run-A')
    return TestClient(app), repo, job, args, service, legacy, new, command


def test_capture_from_previous_tree_keeps_its_workspace_at_dispatch(tmp_path, monkeypatch):
    client, repo, job, args, service, legacy, new, command = captured_job(tmp_path, monkeypatch)
    response = client.post(f"/api/tasks/{job['task_id']}/jobs/{job['job_id']}/run", json=command)
    assert response.status_code == 200, response.text
    payload = json.loads(response.json()['record']['argv'][-1])
    assert payload['sn_ref'] == args['sn_ref']
    assert payload['task_worktree'] == dict(task_id=job['task_id'],
        worktree_path=str(legacy), paseo_workspace_id='workspace-A')
    assert service.binding(job['task_id'])['worktree_path'] == new['worktree_path']


@pytest.mark.parametrize('problem', ['missing', 'ambiguous', 'tampered'])
def test_uncertain_source_tree_rejects_dispatch_without_start_receipt(tmp_path, monkeypatch, problem):
    client, repo, job, args, service, legacy, new, command = captured_job(tmp_path, monkeypatch)
    association = next((repo.root / 'task-captures').glob('*.json'))
    if problem == 'missing':
        association.unlink()
    elif problem == 'tampered':
        payload = json.loads(association.read_text())
        payload['worktree_path'] = new['worktree_path']
        payload['paseo_workspace_id'] = new['paseo_workspace_id']
        association.write_text(json.dumps(payload))
    else:
        from pathlib import Path
        (Path(new['worktree_path']) / 'backend/app/__init__.py').write_text('VALUE = "captured A"\n')
        # Same committed HEAD and identical edits produce the same source object,
        # so source identity alone cannot select between its two originating trees.
        assert service.capture_source(job['task_id']) == args['sn_ref']
    response = client.post(f"/api/tasks/{job['task_id']}/jobs/{job['job_id']}/run", json=command)
    assert response.status_code == 422, response.text
    with repo.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM history WHERE kind='job.execution_started'").fetchone()[0] == 0
