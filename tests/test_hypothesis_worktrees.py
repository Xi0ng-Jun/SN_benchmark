"""Card isolation contracts, using real Git worktrees and no Paseo daemon."""
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import threading

import pytest

from rag_eval.artifact_store import ArtifactStore
from rag_eval.workbench.hypotheses import HypothesisStore
from rag_eval.workbench.repository import Conflict, WorkbenchRepository
from rag_eval.workbench.task_worktree import TaskWorktreeService, initialize_task_worktrees


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args],
                                   stderr=subprocess.DEVNULL, text=True).strip()


class Creator:
    def __init__(self, directory):
        self.directory = directory
        self.dispatched = []
        self.workspaces = []
        self.failure = None
        self.after_create = None

    def create(self, project_root, branch, base_revision):
        self.dispatched.append((str(project_root), branch, base_revision))
        path = self.directory / branch
        git(project_root, 'worktree', 'add', '-qb', branch, str(path), base_revision)
        workspace = {'workspace_id': 'workspace-' + branch, 'path': str(path)}
        self.workspaces.append(workspace)
        if self.after_create:
            self.after_create(path)
        if self.failure:
            raise self.failure
        return workspace

    def list(self):
        return self.workspaces


def _process_create(state_root, project_root, artifact_root, directory, task, hypothesis, log, start, queue):
    from rag_eval.workbench.hypothesis_worktrees import HypothesisWorktreeService
    repo = WorkbenchRepository(state_root)
    trees = TaskWorktreeService(repo, ArtifactStore(artifact_root), project_root=project_root)
    creator = Creator(Path(directory))
    original = creator.create
    def logged_create(*args):
        with Path(log).open('a') as stream:
            stream.write('dispatched\n')
        return original(*args)
    creator.create = logged_create
    service = HypothesisWorktreeService(repo, trees, creator=creator)
    start.wait(timeout=5)
    queue.put(service.create(task, hypothesis, actor='human'))


@pytest.fixture
def setup(tmp_path):
    project = tmp_path / 'project'
    source = project / 'backend/app'
    source.mkdir(parents=True)
    (source / 'main.py').write_text('VALUE = 1\n')
    git(project, 'init', '-q')
    git(project, 'add', '.')
    git(project, '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-qm', 'fixture')
    repository = WorkbenchRepository(tmp_path / 'state')
    workspace = repository.register_workspace('Eval', str(tmp_path / 'evaluation'))
    task = repository.create_task(workspace['id'], 'Task', '', request_key='task')['id']
    initialize_task_worktrees(repository)
    trees = TaskWorktreeService(repository, ArtifactStore(tmp_path / 'artifacts'), project_root=project)
    cards = HypothesisStore(repository)
    creator = Creator(tmp_path)
    module = importlib.import_module('rag_eval.workbench.hypothesis_worktrees')
    service = module.HypothesisWorktreeService(repository, trees, creator=creator)
    return project, repository, task, trees, cards, creator, service


def card(setup, *, approved=True, task_id=None):
    _, _, task, _, cards, _, _ = setup
    item = cards.draft(task_id or task, dict(title='Change', component='retrieval', mechanism='Reason',
                       predictions=[dict(case_id='private-case', expect='improve')],
                       falsification='No improvement', implementer_brief='Change one helper'), author='analyst')
    if approved:
        cards.decide(task_id or task, item['id'], 'approve', actor='human')
    return item['id']


def test_creation_pins_committed_source_without_dirty_original_or_activation(setup):
    project, repo, task, trees, _, creator, service = setup
    hypothesis = card(setup)
    base = git(project, 'rev-parse', 'HEAD')
    (project / 'backend/app/main.py').write_text('PRIVATE_DIRTY = 2\n')
    (project / 'untracked').write_text('PRIVATE_UNTRACKED\n')
    record = service.create(task, hypothesis, actor='human')
    assert record['status'] == 'ready'
    assert record['base_revision'] == base
    binding = record['binding']
    assert binding['branch'] == 'sn-hyp-' + hypothesis
    assert binding['base_revision'] == base
    assert binding['start_snapshot']['dirty'] == []
    assert (Path(binding['worktree_path']) / 'backend/app/main.py').read_text() == 'VALUE = 1\n'
    assert not (Path(binding['worktree_path']) / 'untracked').exists()
    assert trees.binding(task) is None
    assert creator.dispatched == [(str(project), 'sn-hyp-' + hypothesis, base)]
    assert service.get(task, hypothesis) == record
    with repo.connect() as conn:
        from rag_eval.workbench.hypothesis_worktrees import hypothesis_binding
        assert hypothesis_binding(conn, task, hypothesis) == binding


def test_unapproved_and_cross_task_cards_never_dispatch(setup):
    _, repo, task, _, _, creator, service = setup
    draft = card(setup, approved=False)
    with pytest.raises(Conflict):
        service.create(task, draft, actor='human')
    other = repo.create_task(repo.task(task)['workspace_id'], 'Other', '', request_key='other')['id']
    approved = card(setup)
    for action in (service.create, service.activate):
        with pytest.raises(ValueError):
            action(other, approved, actor='human')
    assert creator.dispatched == []


def test_repeated_ready_preserves_snapshot_after_edits_and_new_project_head(setup):
    project, _, task, _, _, creator, service = setup
    hypothesis = card(setup)
    original = service.create(task, hypothesis, actor='human')
    Path(original['binding']['worktree_path'], 'backend/app/main.py').write_text('VALUE = 3\n')
    (project / 'backend/app/main.py').write_text('VALUE = 4\n')
    git(project, 'add', '.')
    git(project, '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'new')
    assert service.create(task, hypothesis, actor='human') == original
    assert len(creator.dispatched) == 1
    assert '+VALUE = 3' in service.view(task, hypothesis).read(task)['files'][0]['diff']


def test_selection_preserves_legacy_and_both_cards_and_session_cwd(setup):
    project, repo, task, trees, _, _, service = setup
    legacy_path = project.parent / 'legacy'
    git(project, 'worktree', 'add', '-qb', 'legacy', str(legacy_path))
    legacy = trees.bind(task, legacy_path, paseo_workspace_id='legacy')
    with repo.connect(write=True) as conn:
        conn.execute('INSERT INTO task_sessions(id,task_id,agent_id,cwd) VALUES(?,?,?,?)',
                     ('session', task, 'agent', str(legacy_path)))
    first, second = card(setup), card(setup)
    a = service.create(task, first, actor='human')['binding']
    b = service.create(task, second, actor='human')['binding']
    service.activate(task, first, actor='human')
    assert trees.binding(task)['worktree_path'] == a['worktree_path']
    assert service.activate(task, second, actor='human') == b
    assert trees.binding(task)['worktree_path'] == b['worktree_path']
    assert service.view(task, first).binding(task) == a
    with repo.connect() as conn:
        from rag_eval.workbench.hypothesis_worktrees import historical_bindings
        paths = {item['worktree_path'] for item in historical_bindings(conn, task)}
        assert paths == {str(legacy_path), a['worktree_path'], b['worktree_path']}
        assert conn.execute('SELECT cwd FROM task_sessions').fetchone()[0] == str(legacy_path)
    with pytest.raises(ValueError):
        service.view(task, first).binding('other-task')


@pytest.mark.parametrize('prior_binding', [True, False])
def test_activation_freezes_legacy_null_session_cwd_before_switching(setup, prior_binding):
    project, repo, task, trees, _, _, service = setup
    if prior_binding:
        legacy_path = project.parent / 'legacy-null'
        git(project, 'worktree', 'add', '-qb', 'legacy-null', str(legacy_path))
        trees.bind(task, legacy_path, paseo_workspace_id='legacy-null')
        expected = str(legacy_path)
    else:
        expected = next(row['location'] for row in repo.workspaces()
                        if row['id'] == repo.task(task)['workspace_id'])
    with repo.connect(write=True) as conn:
        conn.execute('INSERT INTO task_sessions(id,task_id,agent_id) VALUES(?,?,?)',
                     ('old-null-session', task, 'old-null-agent'))
        conn.execute('INSERT INTO task_sessions(id,task_id,agent_id,cwd) VALUES(?,?,?,?)',
                     ('explicit-session', task, 'explicit-agent', '/tmp/preserved-explicit'))
        session = conn.execute('SELECT * FROM task_sessions WHERE id=?', ('old-null-session',)).fetchone()
        task_record = repo._require(conn, 'tasks', task)
        assert repo.session_cwd(conn, session, task_record) == expected
    hypothesis = card(setup)
    new = service.create(task, hypothesis, actor='human')['binding']
    service.activate(task, hypothesis, actor='human')
    assert trees.binding(task)['worktree_path'] == new['worktree_path'] != expected
    with repo.connect() as conn:
        session = conn.execute('SELECT * FROM task_sessions WHERE id=?', ('old-null-session',)).fetchone()
        assert session['cwd'] == expected
        assert repo.session_cwd(conn, session, task_record) == expected
        assert conn.execute('SELECT cwd FROM task_sessions WHERE id=?', ('explicit-session',)).fetchone()[0] == '/tmp/preserved-explicit'


def test_timeout_after_actual_creation_reconciles_without_second_dispatch(setup):
    _, _, task, _, _, creator, service = setup
    hypothesis = card(setup)
    creator.failure = TimeoutError('SECRET service location')
    first = service.create(task, hypothesis, actor='human')
    assert first['status'] == 'unknown'
    assert 'SECRET' not in json.dumps(first)
    creator.failure = None
    second = service.create(task, hypothesis, actor='human')
    assert second['status'] == 'ready'
    assert second['base_revision'] == first['base_revision']
    assert len(creator.dispatched) == 1


def test_uncertain_absence_never_redispatches(setup):
    _, _, task, _, _, creator, service = setup
    hypothesis = card(setup)
    def absent(*args):
        creator.dispatched.append(args)
        raise TimeoutError('unknown')
    creator.create = absent
    assert service.create(task, hypothesis, actor='human')['status'] == 'unknown'
    assert service.create(task, hypothesis, actor='human')['status'] == 'unknown'
    assert len(creator.dispatched) == 1


def test_reconciliation_keeps_identity_returned_by_creation_instead_of_adopting_another_workspace(setup):
    _, _, task, _, _, creator, service = setup
    hypothesis = card(setup)
    original = creator.create
    def mismatched(*args):
        row = original(*args)
        return dict(row, workspace_id='wrong-returned-identity')
    creator.create = mismatched
    assert service.create(task, hypothesis, actor='human')['status'] == 'unknown'
    assert service.create(task, hypothesis, actor='human')['status'] == 'unknown'
    assert len(creator.dispatched) == 1


@pytest.mark.parametrize('change', ['wrong_branch', 'wrong_head', 'dirty', 'wrong_repository', 'unregistered_workspace'])
def test_invalid_external_result_is_sanitized_and_never_bound(setup, change, tmp_path):
    project, _, task, trees, _, creator, service = setup
    hypothesis = card(setup)
    def mutate(path):
        if change == 'wrong_branch':
            git(path, 'checkout', '-qb', 'wrong')
        elif change == 'wrong_head':
            (path / 'backend/app/main.py').write_text('VALUE = 9\n')
            git(path, 'add', '.')
            git(path, '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'wrong')
        elif change == 'dirty':
            (path / 'backend/app/main.py').write_text('VALUE = 9\n')
        elif change == 'unregistered_workspace':
            creator.workspaces.clear()
        else:
            other = tmp_path / 'other-repo'
            subprocess.check_call(['git', 'clone', '-q', str(project), str(other)])
            git(other, 'checkout', '-qb', 'sn-hyp-' + hypothesis)
            creator.workspaces[0]['path'] = str(other)
    creator.after_create = mutate
    result = service.create(task, hypothesis, actor='human')
    assert result['status'] == 'unknown'
    assert result['binding'] is None
    assert trees.binding(task) is None
    with pytest.raises(Conflict):
        service.activate(task, hypothesis, actor='human')


def test_concurrent_requests_only_dispatch_one_creation(setup):
    _, _, task, _, _, creator, service = setup
    hypothesis = card(setup)
    start = threading.Barrier(2)
    def run():
        start.wait(timeout=5)
        return service.create(task, hypothesis, actor='human')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert all(result['status'] == 'ready' for result in results)
    assert results[0]['binding'] == results[1]['binding']
    assert len(creator.dispatched) == 1


def test_creation_intent_is_committed_before_external_operation(setup):
    _, _, task, _, _, creator, service = setup
    hypothesis = card(setup)
    original = creator.create
    def inspect(project_root, branch, base_revision):
        saved = service.get(task, hypothesis)
        assert saved['status'] == 'unknown'
        assert saved['base_revision'] == base_revision
        return original(project_root, branch, base_revision)
    creator.create = inspect
    assert service.create(task, hypothesis, actor='human')['status'] == 'ready'


def test_card_with_existing_legacy_implementer_cannot_change_its_start_tree(setup):
    _, repo, task, _, _, creator, service = setup
    hypothesis = card(setup)
    with repo.connect(write=True) as conn:
        conn.execute('INSERT INTO task_sessions(id,task_id,agent_id,role,cwd,hypothesis_id) VALUES(?,?,?,?,?,?)',
                     ('legacy-session', task, 'old-agent', 'implementer', '/tmp/old-tree', hypothesis))
    with pytest.raises(Conflict):
        service.create(task, hypothesis, actor='human')
    assert service.get(task, hypothesis) is None
    assert creator.dispatched == []


def test_separate_processes_share_one_durable_dispatch(setup, tmp_path):
    import multiprocessing
    project, repo, task, trees, _, _, _ = setup
    hypothesis = card(setup)
    log = tmp_path / 'dispatches'
    context = multiprocessing.get_context('spawn')
    start, queue = context.Event(), context.Queue()
    args = (str(repo.root), str(project), str(trees.store.root), str(tmp_path), task,
            hypothesis, str(log), start, queue)
    processes = [context.Process(target=_process_create, args=args) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        start.set()
        results = [queue.get(timeout=10) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
                process.join()
        queue.close()
    assert [row['status'] for row in results] == ['ready', 'ready']
    assert results[0]['binding'] == results[1]['binding']
    assert log.read_text() == 'dispatched\n'


def test_crash_retains_pinned_intent_for_recovery_after_project_head_moves(setup):
    project, _, task, _, _, creator, service = setup
    hypothesis = card(setup)
    base = git(project, 'rev-parse', 'HEAD')
    creator.failure = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        service.create(task, hypothesis, actor='human')
    saved = service.get(task, hypothesis)
    assert saved['status'] == 'unknown'
    assert 'binding_json' not in saved
    (project / 'backend/app/main.py').write_text('VALUE = 10\n')
    git(project, 'add', '.')
    git(project, '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'new')
    result = service.create(task, hypothesis, actor='human')
    assert result['status'] == 'ready'
    assert result['binding']['base_revision'] == base
    assert len(creator.dispatched) == 1


def test_preexisting_deterministic_branch_cannot_be_adopted_as_fresh_workspace(setup):
    project, _, task, _, _, creator, service = setup
    hypothesis = card(setup)
    branch = 'sn-hyp-' + hypothesis
    path = project.parent / branch
    git(project, 'worktree', 'add', '-qb', branch, str(path))
    creator.workspaces.append(dict(workspace_id='old-workspace', path=str(path)))
    for _ in range(2):
        with pytest.raises(Conflict):
            service.create(task, hypothesis, actor='human')
    assert creator.dispatched == []
    assert service.get(task, hypothesis) is None


def test_cli_adapter_uses_frozen_argv_and_local_target_without_daemon(tmp_path, monkeypatch):
    from rag_eval.workbench.hypothesis_worktrees import PaseoWorkspaceCreator
    binary = tmp_path / 'paseo'
    log = tmp_path / 'argv.json'
    binary.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                      'assert "PASEO_HOST" not in os.environ\n'
                      f'open({str(log)!r},"w").write(json.dumps(sys.argv[1:]))\n'
                      'row={"workspaceId":"trusted-id","cwd":"/tmp/created","isolation":"worktree"}\n'
                      'print(json.dumps([row] if "ls" in sys.argv else row))\n')
    binary.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path) + ':' + __import__('os').environ['PATH'])
    monkeypatch.setenv('PASEO_HOST', 'remote-untrusted')
    adapter = PaseoWorkspaceCreator()
    assert adapter.create(Path('/tmp/trusted-project'), 'sn-hyp-abc', '1234') == {
        'workspace_id': 'trusted-id', 'path': '/tmp/created'}
    assert json.loads(log.read_text()) == ['workspace', 'create', '--isolation', 'worktree', '--mode',
        'branch-off', '--path', '/tmp/trusted-project', '--new-branch', 'sn-hyp-abc', '--base', '1234',
        '--worktree-slug', 'sn-hyp-abc', '--json']
    assert adapter.list() == [dict(workspace_id='trusted-id', path='/tmp/created')]


def test_cli_adapter_rejects_unbounded_output_and_finishes_on_timeout(tmp_path, monkeypatch):
    import os
    import time
    module = importlib.import_module('rag_eval.workbench.hypothesis_worktrees')
    binary = tmp_path / 'paseo'
    binary.write_text('#!/usr/bin/env python3\nprint("x" * 2100000)\n')
    binary.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path) + ':' + os.environ['PATH'])
    with pytest.raises(ValueError, match='limit'):
        module.PaseoWorkspaceCreator().list()
    binary.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n')
    monkeypatch.setattr(module, '_CLI_TIMEOUT', .1)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        module.PaseoWorkspaceCreator().list()
    assert time.monotonic() - start < 2
