"""Card worktrees stay distinct through API, Agent briefs, and leak checks."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_task_worktree import setup, git
from test_workbench_roles_hypotheses import CARD, created


class Creator:
    def __init__(self, root):
        self.root, self.rows = root, []

    def create(self, project_root, branch, base_revision):
        path = self.root / branch
        git(project_root, 'worktree', 'add', '-qb', branch, str(path), base_revision)
        row = {'workspace_id': 'wks-' + branch, 'path': str(path)}
        self.rows.append(row)
        return row

    def list(self):
        return self.rows


@pytest.fixture
def app_setup(setup):
    from rag_eval.workbench.api import create_app
    from rag_eval.workbench.hypotheses import HypothesisStore
    project, tree, repo, task, store, service = setup
    service.bind(task, tree, paseo_workspace_id='legacy-workspace')
    config = {'workspace': {'title': 'Evaluation', 'location': str(project.parent / 'evaluation')},
              'sources': [], 'task_worktrees': {'project_root': str(project), 'artifact_root': str(store.root),
                                               'create_from_hypothesis': True},
              'agent_creation': {'workspaceId': 'eval-workspace', 'profiles': {'sol': {'provider': 'codex/model'}}}}
    app = create_app(repo.root, config, access_token='test-token', hypothesis_worktree_creator=Creator(project.parent))
    client = TestClient(app)
    client.headers['Authorization'] = 'Bearer test-token'
    client.headers['X-Workbench'] = '1'
    cards = HypothesisStore(repo)
    return client, cards, repo, task, service, config


def approve(cards, task):
    card = cards.draft(task, CARD, author='agent:analyst')
    cards.decide(task, card['id'], 'approve', actor='human')
    return card['id']


def test_api_creates_selects_and_recovers_same_card(app_setup):
    client, cards, repo, task, service, config = app_setup
    card = cards.draft(task, CARD, author='analyst')
    url = f'/api/tasks/{task}/hypotheses/{card["id"]}/worktree'
    assert client.post(url, json={}).status_code in (409, 422)
    cards.decide(task, card['id'], 'approve', actor='human')
    response = client.post(url, json={})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved['status'] == 'ready'
    assert client.post(url, json={}).json()['binding'] == saved['binding']
    assert client.post(url + '/activate', json={}).status_code == 200
    assert service.binding(task)['worktree_path'] == saved['binding']['worktree_path']
    assert client.get(f'/api/tasks/{task}/hypotheses').json()[0]['worktree']['status'] == 'ready'
    assert client.get('/api/bootstrap').json()['capabilities']['hypothesis_worktrees'] is True
    assert client.post(url, json={'path': '/tmp/arbitrary'}).status_code == 422


def test_card_agent_and_brief_keep_their_tree_after_switch(app_setup):
    from rag_eval.workbench.agent_creations import AgentCreationStore
    from rag_eval.workbench.context import resolve_context
    client, cards, repo, task, service, config = app_setup
    ids = [approve(cards, task), approve(cards, task)]
    saved = []
    for identity in ids:
        url = f'/api/tasks/{task}/hypotheses/{identity}/worktree'
        saved.append(client.post(url, json={}).json()['binding'])
        assert client.post(url + '/activate', json={}).status_code == 200
    workspace = repo.task(task)['workspace_id']
    creations = AgentCreationStore(repo, {workspace: {'workspaceId': 'eval-workspace',
                                                     'profiles': {'sol': {'provider': 'codex/model'}}}})
    # Current task points at card 2, but creating card 1's Agent must use card 1.
    result = created(creations, task, 'implementer', ids[0], key='card-1-agent')
    assert result['command']['expectedCwd'] == saved[0]['worktree_path']
    brief = resolve_context(repo, task, {'kind': 'brief', 'session_id': result['session_id']})['text']
    assert saved[0]['worktree_path'] in brief
    assert saved[1]['worktree_path'] not in brief


def test_card_leak_scan_uses_saved_tree_and_requires_scope(app_setup):
    from rag_eval.workbench.hypotheses import leak_check
    client, cards, repo, task, service, config = app_setup
    ids = [approve(cards, task), approve(cards, task)]
    saved = []
    for identity in ids:
        url = f'/api/tasks/{task}/hypotheses/{identity}/worktree'
        saved.append(client.post(url, json={}).json()['binding'])
        client.post(url + '/activate', json={})
    (Path(saved[0]['worktree_path']) / 'backend/app/main.py').write_text('CASE = "qasper:abc"\n')

    class Views:
        _worktree_service = service
        def _load(self, run):
            return {'_bundle': {'cases': [{'case_id': 'qasper:abc'}]}}
        def _binding_read(self, task_id):
            return service.read(task_id), 'task_worktree'

    assert leak_check(Views(), task, ['run'], hypothesis_id=ids[0])['status'] == 'suspect'
    assert leak_check(Views(), task, ['run'], hypothesis_id=ids[1])['status'] == 'clean'
    with pytest.raises(ValueError):
        leak_check(Views(), task, ['run'], hypothesis_id='different-card')


def test_unknown_card_creation_cannot_use_legacy_tree(app_setup):
    from rag_eval.workbench.agent_creations import AgentCreationStore
    from rag_eval.workbench.hypothesis_worktrees import HypothesisWorktreeService
    from rag_eval.workbench.repository import Conflict
    client, cards, repo, task, service, config = app_setup
    identity = approve(cards, task)

    class Unavailable:
        def create(self, *args):
            raise TimeoutError()
        def list(self):
            return []

    trees = HypothesisWorktreeService(repo, service, creator=Unavailable())
    assert trees.create(task, identity, actor='human')['status'] == 'unknown'
    workspace = repo.task(task)['workspace_id']
    creations = AgentCreationStore(repo, {workspace: {'workspaceId': 'eval-workspace',
                                                     'profiles': {'sol': {'provider': 'codex/model'}}}})
    with pytest.raises(Conflict, match='not confirmed'):
        creations.prepare(task, 'sol', actor='human', request_key='uncertain', role='implementer', hypothesis_id=identity)


def test_saved_capture_run_survives_selecting_a_new_card_tree(app_setup, tmp_path):
    import json
    from rag_eval.workbench.run_views import RunViews
    client, cards, repo, task, service, config = app_setup
    old = service.binding(task)
    run = tmp_path / 'old-run'
    run.mkdir()
    (run / 'manifest.json').write_text(json.dumps({'identity': {'task_worktree': {
        'task_id': task, 'worktree_path': old['worktree_path'], 'source_ref': 'observed-source'}}}))
    views = RunViews.from_config({'runs': [{'id': 'old', 'run_dir': str(run)}]}, base=tmp_path, worktree_service=service)
    # Only score parsing is stubbed: run association reads the real saved manifest
    # and real binding history. A run from a different source must still fail.
    def loaded(identity):
        return {'provenance': {'source': {'sn_source_ref': 'observed-source'}}, 'title': 'old',
                'suite': 'qasper', 'mode': 'chunk', 'state': {}, 'official': {}, 'attention': {'count': 0}}
    views._load = loaded
    identity = approve(cards, task)
    url = f'/api/tasks/{task}/hypotheses/{identity}/worktree'
    assert client.post(url, json={}).json()['status'] == 'ready'
    client.post(url + '/activate', json={})
    assert [r['id'] for r in views._runs_for_binding(service.binding(task))] == ['old']
    payload = json.loads((run / 'manifest.json').read_text())
    payload['identity']['task_worktree']['source_ref'] = 'unrelated-source'
    (run / 'manifest.json').write_text(json.dumps(payload))
    assert views._runs_for_binding(service.binding(task)) == []


def test_actual_attempt_dispatches_prepared_card_after_another_card_is_selected(app_setup):
    from rag_eval.workbench.agent_creations import AgentCreationStore
    from test_workbench_agent_creations import Bridge, created as envelope
    client, cards, repo, task, service, _ = app_setup
    a, b = approve(cards, task), approve(cards, task)
    a_url = f'/api/tasks/{task}/hypotheses/{a}/worktree'
    a_binding = client.post(a_url, json={}).json()['binding']
    client.post(a_url + '/activate', json={})
    workspace = repo.task(task)['workspace_id']
    store = AgentCreationStore(repo, {workspace: {'workspaceId': 'eval-workspace',
                                   'profiles': {'sol': {'provider': 'codex/model-example'}}}})
    intent = store.prepare(task, 'sol', actor='human', request_key='dispatch-a',
                           role='implementer', hypothesis_id=a)
    b_url = f'/api/tasks/{task}/hypotheses/{b}/worktree'
    b_binding = client.post(b_url, json={}).json()['binding']
    client.post(b_url + '/activate', json={})
    bridge = Bridge(envelope)
    result = store.attempt(task, intent['id'], actor='human', bridge=bridge)
    assert result['status'] == 'created'
    assert bridge.commands == [intent['command']]
    with repo.connect() as conn:
        session = conn.execute('SELECT * FROM task_sessions WHERE id=?', (result['session_id'],)).fetchone()
        assert session['cwd'] == a_binding['worktree_path'] != b_binding['worktree_path']
        assert session['hypothesis_id'] == a


def legacy_implementer_then_new_selection(app_setup):
    from rag_eval.workbench.agent_creations import AgentCreationStore
    client, cards, repo, task, service, _ = app_setup
    original = service.binding(task)
    # Save a distinct preexisting dirty file in the original immutable snapshot.
    Path(original['worktree_path'], 'backend/app/legacy-start.py').write_text('LEGACY = 1\n')
    original = service.snapshot_binding(task, original['worktree_path'],
                                        paseo_workspace_id=original['paseo_workspace_id'])
    import json
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE task_worktrees SET start_snapshot_json=? WHERE task_id=?',
                     (json.dumps(original['start_snapshot']), task))
    a, b = approve(cards, task), approve(cards, task)
    workspace = repo.task(task)['workspace_id']
    creations = AgentCreationStore(repo, {workspace: {'workspaceId': 'eval-workspace',
                                        'profiles': {'sol': {'provider': 'codex/model'}}}})
    result = created(creations, task, 'implementer', a, key='legacy-a')
    b_url = f'/api/tasks/{task}/hypotheses/{b}/worktree'
    new = client.post(b_url, json={}).json()['binding']
    client.post(b_url + '/activate', json={})
    return a, original, result['session_id'], new


def test_legacy_brief_keeps_original_snapshot_after_card_selection_changes(app_setup):
    from rag_eval.workbench.context import resolve_context
    _, _, repo, task, _, _ = app_setup
    a, original, session_id, new = legacy_implementer_then_new_selection(app_setup)
    brief = resolve_context(repo, task, {'kind': 'brief', 'session_id': session_id})['text']
    assert original['worktree_path'] in brief
    assert new['worktree_path'] not in brief
    assert 'backend/app/legacy-start.py' in brief


def test_legacy_leak_check_reads_original_card_diff_after_selection_changes(app_setup):
    from rag_eval.workbench.hypotheses import leak_check
    _, _, repo, task, service, _ = app_setup
    a, original, _, _ = legacy_implementer_then_new_selection(app_setup)
    Path(original['worktree_path'], 'backend/app/main.py').write_text('CASE = "qasper:legacy-leak"\n')
    class Views:
        _worktree_service = service
        def _load(self, run):
            return {'_bundle': {'cases': [{'case_id': 'qasper:legacy-leak'}]}}
        def _binding_read(self, task_id):
            return service.read(task_id), 'task_worktree'
    result = leak_check(Views(), task, ['run'], hypothesis_id=a)
    assert result['status'] == 'suspect'
    assert result['findings'] == [{'kind': 'case_id', 'case_id': 'qasper:legacy-leak',
                                   'source': 'backend/app/main.py'}]


def test_legacy_multiple_frozen_directories_are_rejected_instead_of_using_current_tree(app_setup):
    from rag_eval.workbench.hypothesis_worktrees import hypothesis_binding
    from rag_eval.workbench.repository import Conflict
    _, _, repo, task, _, _ = app_setup
    a, _, _, new = legacy_implementer_then_new_selection(app_setup)
    with repo.connect(write=True) as conn:
        conn.execute('INSERT INTO task_sessions(id,task_id,agent_id,role,cwd,hypothesis_id) VALUES(?,?,?,?,?,?)',
                     ('ambiguous-session', task, 'ambiguous-agent', 'implementer', new['worktree_path'], a))
    with repo.connect() as conn, pytest.raises(Conflict, match='ambiguous'):
        hypothesis_binding(conn, task, a)


def test_unknown_creation_never_falls_back_to_a_legacy_session_binding(app_setup):
    from rag_eval.workbench.hypothesis_worktrees import HypothesisWorktreeService, hypothesis_binding
    from rag_eval.workbench.repository import Conflict
    from rag_eval.workbench.context import resolve_context
    _, cards, repo, task, service, _ = app_setup
    identity = approve(cards, task)
    class Unavailable:
        def create(self, *args):
            raise TimeoutError()
        def list(self):
            return []
    trees = HypothesisWorktreeService(repo, service, creator=Unavailable())
    assert trees.create(task, identity, actor='human')['status'] == 'unknown'
    with repo.connect(write=True) as conn:
        conn.execute('INSERT INTO task_sessions(id,task_id,agent_id,role,cwd,hypothesis_id) VALUES(?,?,?,?,?,?)',
                     ('old-session', task, 'old-agent', 'implementer', service.binding(task)['worktree_path'], identity))
    with repo.connect() as conn, pytest.raises(Conflict, match='not confirmed'):
        hypothesis_binding(conn, task, identity)
    with pytest.raises(Conflict, match='not confirmed'):
        resolve_context(repo, task, {'kind': 'brief', 'session_id': 'old-session'})
