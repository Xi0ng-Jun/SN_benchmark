"""Offline contract tests using a primary repository and linked worktree."""
import importlib
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

from rag_eval.artifact_store import ArtifactStore
from rag_eval.workbench.repository import Conflict, UnknownIdentity, WorkbenchRepository


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL,
                                   text=True).strip()


@pytest.fixture
def setup(tmp_path):
    project = tmp_path / 'project'
    source = project / 'backend/app'
    source.mkdir(parents=True)
    (source / 'main.py').write_text('VALUE = 1\n')
    (source / 'other.py').write_text('OTHER = 1\n')
    git(project, 'init', '-q')
    git(project, 'add', '.')
    git(project, '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-qm', 'fixture')
    worktree = tmp_path / 'sn-task'
    git(project, 'worktree', 'add', '-qb', 'task-branch', str(worktree))
    repository = WorkbenchRepository(tmp_path / 'state')
    workspace = repository.register_workspace('Evaluation', str(tmp_path / 'evaluation'))
    task = repository.create_task(workspace['id'], 'Investigate', '', request_key='task')
    module = importlib.import_module('rag_eval.workbench.task_worktree')
    with repository.connect() as conn:
        before_schema = {row['name']: row['sql'] for row in conn.execute('SELECT name,sql FROM sqlite_master')}
    module.initialize_task_worktrees(repository)
    module.initialize_task_worktrees(repository)
    with repository.connect() as conn:
        after_schema = {row['name']: row['sql'] for row in conn.execute('SELECT name,sql FROM sqlite_master')}
    assert {key: value for key, value in after_schema.items() if key in before_schema} == before_schema
    assert {key for key in after_schema.keys() - before_schema.keys()
            if not key.startswith('sqlite_autoindex_')} == {'task_worktrees'}
    store = ArtifactStore(tmp_path / 'artifacts')
    service = module.TaskWorktreeService(repository, store, project_root=project)
    return project, worktree, repository, task['id'], store, service


def bind(setup):
    _, worktree, _, task, _, service = setup
    return service.bind(task, worktree, paseo_workspace_id='paseo-workspace')


def test_binding_persists_start_capture_and_only_adds_one_table(setup):
    project, worktree, repo, task, store, service = setup
    (worktree / 'backend/app/main.py').write_text('VALUE = 2\n')
    (worktree / 'backend/app/new.py').write_text('NEW = 1\n')
    before_task = repo.task(task)
    before_status = git(project, 'status', '--porcelain')
    record = bind(setup)
    assert record['task_id'] == task
    assert record['worktree_path'] == str(worktree)
    assert record['paseo_workspace_id'] == 'paseo-workspace'
    assert record['base_revision'] == git(worktree, 'rev-parse', 'HEAD')
    assert record['branch'] == 'task-branch'
    start = record['start_snapshot']
    assert {entry['path'] for entry in start['dirty']} == {
        'backend/app/main.py', 'backend/app/new.py'}
    assert json.loads(Path(start['source_ref_file']).read_text()) == start['source_ref']
    with tarfile.open(store.resolve(start['source_ref']) / 'product-source.tar') as archive:
        assert archive.extractfile('backend/app/main.py').read() == b'VALUE = 2\n'
    assert repo.task(task) == before_task
    assert git(project, 'status', '--porcelain') == before_status
    assert service.binding(task) == record
    assert bind(setup) == record  # retry never resets the starting point
    with repo.connect() as conn:
        assert [row['name'] for row in conn.execute('PRAGMA table_info(task_worktrees)')] == [
            'task_id', 'paseo_workspace_id', 'worktree_path', 'branch', 'base_revision',
            'start_snapshot_json', 'created_at']
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 6


def test_original_tree_unrelated_repo_and_subdirectory_are_rejected(setup, tmp_path):
    project, worktree, _, task, _, service = setup
    unrelated = tmp_path / 'unrelated'
    unrelated.mkdir()
    git(unrelated, 'init', '-q')
    for path in (project, worktree / 'backend', unrelated):
        with pytest.raises(ValueError):
            service.bind(task, path)
    assert service.binding(task) is None


def test_unknown_task_and_rebinding_conflict(setup, tmp_path):
    project, worktree, _, task, _, service = setup
    with pytest.raises(UnknownIdentity):
        service.bind('missing', worktree)
    bind(setup)
    other = tmp_path / 'other-worktree'
    git(project, 'worktree', 'add', '-qb', 'other-branch', str(other))
    with pytest.raises(Conflict):
        service.bind(task, other)
    with pytest.raises(Conflict):
        service.bind(task, worktree, paseo_workspace_id='different')


def test_diff_separates_preexisting_dirty_from_later_edits_in_same_file(setup):
    _, worktree, repo, task, _, service = setup
    main = worktree / 'backend/app/main.py'
    main.write_text('VALUE = 2\n')
    added = worktree / 'backend/app/start new.py'
    added.write_text('NEW = 1\n')
    record = bind(setup)
    first = service.read(task)
    entries = {item['path']: item for item in first['files']}
    assert entries['backend/app/main.py']['preexisting_dirty'] is True
    assert entries['backend/app/main.py']['changed_since_start'] is False
    assert entries['backend/app/start new.py']['changed_since_start'] is False
    main.write_text('VALUE = 3\n')
    added.unlink()
    (worktree / 'backend/app/other.py').write_text('OTHER = 2\n')
    (worktree / 'backend/app/new\nfile.py').write_text('NEW = 2\n')
    before = repo.database.read_bytes()
    result = service.read(task)
    assert repo.database.read_bytes() == before
    assert result['binding'] == record
    assert result['current_head'] == record['base_revision']
    assert 'main.py' in result['diff_stat']
    entries = {item['path']: item for item in result['files']}
    assert '+VALUE = 3' in entries['backend/app/main.py']['diff']
    assert entries['backend/app/main.py']['changed_since_start'] is True
    assert entries['backend/app/start new.py']['changed_since_start'] is True
    assert entries['backend/app/other.py']['preexisting_dirty'] is False
    assert entries['backend/app/other.py']['changed_since_start'] is True
    assert '+NEW = 2' in entries['backend/app/new\nfile.py']['diff']


def test_staged_rename_and_committed_changes_are_visible(setup):
    _, worktree, _, task, _, service = setup
    git(worktree, 'mv', 'backend/app/main.py', 'backend/app/renamed.py')
    bind(setup)
    assert all(not f['changed_since_start'] for f in service.read(task)['files'])
    (worktree / 'backend/app/renamed.py').write_text('VALUE = 4\n')
    git(worktree, 'add', '.')
    git(worktree, '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-qm', 'later change')
    result = service.read(task)
    assert result['current_head'] != result['binding']['base_revision']
    entries = {f['path']: f for f in result['files']}
    assert entries['backend/app/renamed.py']['preexisting_dirty']
    assert entries['backend/app/renamed.py']['changed_since_start']


def test_binary_private_files_and_diff_limits(setup):
    _, worktree, _, task, _, service = setup
    bind(setup)
    (worktree / 'backend/app/data.bin').write_bytes(b'\x00private binary\xff')
    (worktree / '.env').write_text('SECRET=do-not-display\n')
    (worktree / 'model-services.toml').write_text('private-service-address\n')
    (worktree / 'backend/app/main.py').write_text('VALUE = 2\n' * 1000)
    result = service.read(task, max_diff_bytes=100, max_file_diff_bytes=80)
    entries = {f['path']: f for f in result['files']}
    assert entries['backend/app/data.bin']['binary'] is True
    assert entries['backend/app/data.bin']['diff'] is None
    assert entries['.env']['diff'] is None
    assert entries['model-services.toml']['diff'] is None
    assert 'do-not-display' not in json.dumps(result)
    assert 'private-service-address' not in json.dumps(result)
    assert sum(len((f['diff'] or '').encode()) for f in result['files']) <= 100
    assert entries['backend/app/main.py']['truncated'] is True
    limited = service.read(task, max_files=1)
    assert len(limited['files']) == 1
    assert limited['files_truncated'] is True


def test_run_argv_recaptures_bytes_without_starting_runner(setup, tmp_path):
    project, worktree, repo, task, store, service = setup
    baseline = bind(setup)
    config = tmp_path / 'experiment-model.toml'
    config.write_text('private configuration\n')
    argv = service.run_argv(task, model_config=config)
    args = dict(zip(argv[::2], argv[1::2]))
    assert args['--project-root'] == str(project)
    assert args['--model-config'] == str(config)
    assert args['--artifact-root'] == str(store.root)
    assert json.loads(Path(args['--sn-source-ref']).read_text()) == baseline['start_snapshot']['source_ref']
    (worktree / 'backend/app/main.py').write_text('VALUE = 5\n')
    (worktree / 'backend/app/new.py').write_text('NEW = 5\n')
    next_argv = service.run_argv(task, model_config=config)
    next_args = dict(zip(next_argv[::2], next_argv[1::2]))
    changed_ref = json.loads(Path(next_args['--sn-source-ref']).read_text())
    assert changed_ref != baseline['start_snapshot']['source_ref']
    assert service.binding(task) == baseline
    with tarfile.open(store.resolve(changed_ref) / 'product-source.tar') as archive:
        assert archive.extractfile('backend/app/new.py').read() == b'NEW = 5\n'
    assert (project / 'backend/app/main.py').read_text() == 'VALUE = 1\n'


def test_capture_source_returns_a_fresh_trusted_reference_without_running(setup):
    project, worktree, _, task, store, service = setup
    bind(setup)
    (worktree / 'backend/app/main.py').write_text('VALUE = 9\n')

    captured = service.capture_source(task)

    assert captured['kind'] == 'sn'
    assert store.resolve(captured).is_dir()
    assert service.binding(task)['start_snapshot']['source_ref'] != captured


def test_capture_failure_does_not_save_binding(setup, monkeypatch):
    _, _, _, task, _, service = setup
    module = importlib.import_module('rag_eval.workbench.task_worktree')
    def fail(*args, **kwargs):
        raise ValueError('capture rejected')
    monkeypatch.setattr(module, 'capture_sn_source', fail)
    with pytest.raises(ValueError, match='capture rejected'):
        bind(setup)
    assert service.binding(task) is None


def test_removed_worktree_and_unbound_tasks_fail_explicitly(setup):
    project, worktree, _, task, _, service = setup
    with pytest.raises(ValueError, match='bound'):
        service.read(task)
    bind(setup)
    git(project, 'worktree', 'remove', str(worktree))
    with pytest.raises(ValueError):
        service.read(task)
    assert service.binding(task)['worktree_path'] == str(worktree)


def test_read_detects_same_status_content_race(setup, monkeypatch):
    from rag_eval.sn_source_capture import SourceChangedDuringCapture
    _, worktree, _, task, _, service = setup
    main = worktree / 'backend/app/main.py'
    main.write_text('VALUE = 2\n')
    bind(setup)
    module = importlib.import_module('rag_eval.workbench.task_worktree')
    original = module._git
    def mutate(root, *args, **kwargs):
        output = original(root, *args, **kwargs)
        if '--patch' in args:
            main.write_text('VALUE = 3\n')
        return output
    monkeypatch.setattr(module, '_git', mutate)
    with pytest.raises(SourceChangedDuringCapture, match='changed'):
        service.read(task)


def test_bind_rejects_race_without_saving_a_binding(setup, monkeypatch):
    from rag_eval.sn_source_capture import SourceChangedDuringCapture
    _, worktree, _, task, _, service = setup
    main = worktree / 'backend/app/main.py'
    main.write_text('VALUE = 2\n')
    module = importlib.import_module('rag_eval.workbench.task_worktree')
    original = module.capture_sn_source
    def mutate(*args, **kwargs):
        capture = original(*args, **kwargs)
        main.write_text('VALUE = 3\n')
        return capture
    monkeypatch.setattr(module, 'capture_sn_source', mutate)
    with pytest.raises(SourceChangedDuringCapture):
        bind(setup)
    assert service.binding(task) is None


def test_read_disables_external_diff_and_does_not_refresh_index(setup, tmp_path):
    _, worktree, _, task, _, service = setup
    bind(setup)
    marker = tmp_path / 'external-diff-called'
    hook = tmp_path / 'diff-tool'
    hook.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n')
    hook.chmod(0o700)
    git(worktree, 'config', 'diff.external', str(hook))
    (worktree / 'backend/app/main.py').write_text('VALUE = 3\n')
    index_path = Path(git(worktree, 'rev-parse', '--path-format=absolute', '--git-path', 'index'))
    before_index = index_path.read_bytes()
    result = service.read(task)
    assert '+VALUE = 3' in result['files'][0]['diff']
    assert not marker.exists()
    assert index_path.read_bytes() == before_index


def test_deleted_binary_and_invalid_utf8_are_names_only(setup):
    _, worktree, _, task, _, service = setup
    binary = worktree / 'backend/app/old.bin'
    binary.write_bytes(b'\0binary')
    git(worktree, 'add', '.')
    git(worktree, '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-qm', 'binary fixture')
    bind(setup)
    binary.unlink()
    (worktree / 'backend/app/nonutf8.bin').write_bytes(b'\xff')
    entries = {f['path']: f for f in service.read(task, max_file_diff_bytes=1)['files']}
    for path in ('backend/app/old.bin', 'backend/app/nonutf8.bin'):
        assert entries[path]['binary'] is True
        assert entries[path]['diff'] is None


def test_detached_head_supported_and_original_alias_rejected(setup, tmp_path):
    project, worktree, _, task, _, service = setup
    git(worktree, 'checkout', '--detach', '-q')
    assert service.bind(task, worktree)['branch'] is None
    alias = tmp_path / 'original-alias'
    alias.symlink_to(project, target_is_directory=True)
    with pytest.raises(ValueError, match='Original'):
        service.bind(task, alias)


def test_artifact_store_cannot_write_inside_original_project(setup):
    project, _, repo, _, _, _ = setup
    module = importlib.import_module('rag_eval.workbench.task_worktree')
    with pytest.raises(ValueError, match='isolated'):
        module.TaskWorktreeService(repo, ArtifactStore(project / 'artifacts'), project_root=project)


def test_metadata_is_rejected_not_silently_truncated(setup):
    project, _, _, _, _, _ = setup
    module = importlib.import_module('rag_eval.workbench.task_worktree')
    with pytest.raises(ValueError, match='limit'):
        module._git(project, 'rev-parse', 'HEAD', limit=1)
