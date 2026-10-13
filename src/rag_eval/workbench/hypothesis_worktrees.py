"""Immutable per-card worktrees; external uncertainty is never retried blindly.

Only trusted configuration supplies the project and creator. The CLI adapter
uses the local daemon and a fixed command; HTTP chooses only a card identity.
Dispatch is durably recorded before the call, guarded by a per-card flock.
Crashes release the lock but retain unknown, so later calls only reconcile.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time

from .repository import Conflict, _text, now
from .task_worktree import TaskWorktreeService, _branch, _decode_row, _status, _text_git, _validate_worktree

_UNKNOWN = 'Workspace creation is uncertain; retry performs read-only reconciliation.'
_CLI_TIMEOUT = 45


def initialize_hypothesis_worktrees(repository):
    with repository.connect(write=True) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS hypothesis_worktrees(
            task_id TEXT NOT NULL REFERENCES tasks(id),
            hypothesis_id TEXT NOT NULL REFERENCES hypotheses(id),
            status TEXT NOT NULL, branch TEXT NOT NULL, base_revision TEXT NOT NULL,
            project_common_dir TEXT NOT NULL, binding_json TEXT, workspace_json TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(task_id,hypothesis_id))''')
        if 'workspace_json' not in {r['name'] for r in conn.execute('PRAGMA table_info(hypothesis_worktrees)')}:
            conn.execute('ALTER TABLE hypothesis_worktrees ADD COLUMN workspace_json TEXT')
        conn.execute('''CREATE TABLE IF NOT EXISTS historical_task_worktrees(
            task_id TEXT NOT NULL REFERENCES tasks(id), identity TEXT NOT NULL,
            binding_json TEXT NOT NULL, PRIMARY KEY(task_id,identity))''')


def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def hypothesis_binding(conn, task_id, hypothesis_id):
    """Read a card start, or its legacy session's preserved immutable binding.

    A known unconfirmed creation never falls back to another directory. Legacy
    sessions must agree on one frozen cwd with a saved start snapshot; current
    filesystem contents cannot reconstruct the missing historical start.
    """
    if _has_table(conn, 'hypothesis_worktrees'):
        row = conn.execute('SELECT status,binding_json FROM hypothesis_worktrees WHERE task_id=? AND hypothesis_id=?',
                           (task_id, hypothesis_id)).fetchone()
        if row is not None:
            if row['status'] != 'ready' or not row['binding_json']:
                raise Conflict('Hypothesis worktree creation is not confirmed')
            return json.loads(row['binding_json'])
    sessions = conn.execute("SELECT cwd FROM task_sessions WHERE task_id=? AND hypothesis_id=? AND role='implementer'",
                            (task_id, hypothesis_id)).fetchall()
    if not sessions:
        return None
    directories = {row['cwd'] for row in sessions}
    if len(directories) != 1 or None in directories or '' in directories:
        raise Conflict('Legacy hypothesis worktree directory is ambiguous or missing')
    directory = next(iter(directories))
    matches = [binding for binding in historical_bindings(conn, task_id)
               if binding['worktree_path'] == directory
               and binding.get('hypothesis_id') in (None, hypothesis_id)]
    if len(matches) != 1:
        raise Conflict('Legacy hypothesis worktree snapshot is ambiguous or missing')
    return matches[0]


def historical_bindings(conn, task_id):
    """Preserved legacy selections and all ready cards; no filesystem inspection."""
    records = []
    if _has_table(conn, 'historical_task_worktrees'):
        records.extend(json.loads(r['binding_json']) for r in conn.execute(
            'SELECT binding_json FROM historical_task_worktrees WHERE task_id=? ORDER BY identity', (task_id,)))
    if _has_table(conn, 'hypothesis_worktrees'):
        records.extend(json.loads(r['binding_json']) for r in conn.execute(
            "SELECT binding_json FROM hypothesis_worktrees WHERE task_id=? AND status='ready' ORDER BY created_at,hypothesis_id", (task_id,)))
    if _has_table(conn, 'task_worktrees'):
        row = conn.execute('SELECT * FROM task_worktrees WHERE task_id=?', (task_id,)).fetchone()
        if row:
            records.append(_decode_row(row))
    unique = {}
    for binding in records:
        unique.setdefault((binding['worktree_path'], binding['paseo_workspace_id']), binding)
    return list(unique.values())


def _workspace(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid workspace descriptor')
    identity = _text(value.get('workspace_id'), 'workspace identity', limit=200)
    path = value.get('path')
    if not isinstance(path, str) or not path or '\0' in path or not Path(path).is_absolute():
        raise ValueError('Invalid workspace directory')
    return dict(workspace_id=identity, path=str(Path(path).resolve()))


class PaseoWorkspaceCreator:
    """Bounded trusted local CLI adapter; never returns stderr or raw failures."""
    @staticmethod
    def _run(args):
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_') and k != 'PASEO_HOST'}
        with subprocess.Popen(['paseo', 'workspace', *args, '--json'], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, env=env, start_new_session=True) as process:
            deadline = time.monotonic() + _CLI_TIMEOUT
            data = bytearray()
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise TimeoutError('Workspace operation timed out')
                        block = os.read(process.stdout.fileno(), min(65536, 2 * 1024 * 1024 + 1 - len(data)))
                        if not block:
                            break
                        data.extend(block)
                        if len(data) > 2 * 1024 * 1024:
                            raise ValueError('Workspace output exceeds limit')
                if process.wait(timeout=max(.01, deadline - time.monotonic())):
                    raise ValueError('Workspace operation failed')
                return json.loads(data)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()

    @staticmethod
    def _decode(row):
        if not isinstance(row, dict) or row.get('isolation') != 'worktree':
            raise ValueError('Expected a Paseo worktree workspace')
        return _workspace(dict(workspace_id=row.get('workspaceId'), path=row.get('cwd')))

    def create(self, project_root, branch, base_revision):
        return self._decode(self._run(['create', '--isolation', 'worktree', '--mode', 'branch-off',
                                     '--path', str(project_root), '--new-branch', branch,
                                     '--base', base_revision, '--worktree-slug', branch]))

    def list(self):
        rows = self._run(['ls'])
        if not isinstance(rows, list):
            raise ValueError('Invalid workspace list')
        return [self._decode(row) for row in rows if row.get('isolation') == 'worktree']


class _CardWorktreeView(TaskWorktreeService):
    def __init__(self, service, task_id, hypothesis_id):
        trees = service.task_worktree_service
        super().__init__(service.repository, trees.store, project_root=trees.project_root)
        self.service, self.task_id, self.hypothesis_id = service, task_id, hypothesis_id

    def binding(self, task_id):
        if task_id != self.task_id:
            raise ValueError('Card worktree view belongs to a different Task')
        with self.repository.connect() as conn:
            self.service._require_card(conn, self.task_id, self.hypothesis_id)
            return hypothesis_binding(conn, self.task_id, self.hypothesis_id)

    def bind(self, *args, **kwargs):
        raise Conflict('Card worktree bindings are immutable')


class HypothesisWorktreeService:
    def __init__(self, repository, task_worktree_service, *, creator=None):
        self.repository = repository
        self.task_worktree_service = task_worktree_service
        self.creator = creator if creator is not None else PaseoWorkspaceCreator()
        initialize_hypothesis_worktrees(repository)

    def _require_card(self, conn, task_id, hypothesis_id):
        self.repository._require(conn, 'tasks', task_id)
        if not _has_table(conn, 'hypotheses'):
            raise ValueError('Hypothesis card is not in this task')
        row = conn.execute('SELECT status FROM hypotheses WHERE task_id=? AND id=?', (task_id, hypothesis_id)).fetchone()
        if not row:
            raise ValueError('Hypothesis card is not in this task')
        return row['status']

    def get(self, task_id, hypothesis_id):
        with self.repository.connect() as conn:
            self._require_card(conn, task_id, hypothesis_id)
            row = conn.execute('SELECT * FROM hypothesis_worktrees WHERE task_id=? AND hypothesis_id=?',
                               (task_id, hypothesis_id)).fetchone()
        if row is None:
            return None
        record = dict(row)
        record.pop('project_common_dir')
        record.pop('workspace_json')
        payload = record.pop('binding_json')
        record['binding'] = json.loads(payload) if payload else None
        record['detail'] = None if record['status'] == 'ready' else _UNKNOWN
        return record

    @contextmanager
    def _lock(self, task_id, hypothesis_id):
        directory = self.repository.root / 'hypothesis-worktree-locks'
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink():
            raise ValueError('Worktree lock directory cannot be a symlink')
        name = hashlib.sha256(json.dumps([task_id, hypothesis_id]).encode()).hexdigest()
        descriptor = os.open(directory / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def _validate_candidate(self, workspace, intent):
        root = _validate_worktree(self.task_worktree_service.project_root, workspace['path'])
        common = Path(_text_git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
        if (str(common) != intent['project_common_dir'] or _branch(root) != intent['branch']
                or _text_git(root, 'rev-parse', 'HEAD') != intent['base_revision'] or _status(root)):
            raise ValueError('Workspace does not match immutable creation intent')
        return root

    def _reconcile(self, intent, expected=None):
        matches = []
        for row in self.creator.list():
            workspace = _workspace(row)
            if expected is not None and workspace != expected:
                continue
            try:
                root = self._validate_candidate(workspace, intent)
            except (ValueError, OSError):
                continue
            matches.append((workspace, root))
        if len(matches) != 1:
            raise ValueError('No unique verified registered workspace')
        workspace, root = matches[0]
        binding = self.task_worktree_service.snapshot_binding(intent['task_id'], root,
                                                             paseo_workspace_id=workspace['workspace_id'])
        self._validate_candidate(workspace, intent)
        if (binding['base_revision'] != intent['base_revision'] or binding['branch'] != intent['branch']
                or binding['start_snapshot']['dirty']):
            raise ValueError('Starting snapshot differs from creation intent')
        binding['hypothesis_id'] = intent['hypothesis_id']
        with self.repository.connect(write=True) as conn:
            conn.execute("UPDATE hypothesis_worktrees SET status='ready',binding_json=?,updated_at=? "
                         "WHERE task_id=? AND hypothesis_id=? AND status='unknown'",
                         (json.dumps(binding, ensure_ascii=False), now(), intent['task_id'], intent['hypothesis_id']))
            self.repository._event(conn, intent['task_id'], 'hypothesis.worktree.ready', intent['hypothesis_id'],
                                   dict(paseo_workspace_id=workspace['workspace_id'], branch=intent['branch'],
                                        base_revision=intent['base_revision']))

    def create(self, task_id, hypothesis_id, *, actor):
        actor = _text(actor, 'actor', limit=200)
        _text(task_id, 'Task identity', limit=200)
        if not isinstance(hypothesis_id, str) or not re.fullmatch('[a-f0-9]{32}', hypothesis_id):
            raise ValueError('Invalid hypothesis identity')
        with self._lock(task_id, hypothesis_id):
            with self.repository.connect(write=True) as conn:
                status = self._require_card(conn, task_id, hypothesis_id)
                row = conn.execute('SELECT * FROM hypothesis_worktrees WHERE task_id=? AND hypothesis_id=?',
                                   (task_id, hypothesis_id)).fetchone()
                if row is not None and row['status'] == 'ready':
                    existing_ready = True
                    dispatch = False
                else:
                    existing_ready = False
                    if status not in ('approved', 'implementing'):
                        raise Conflict('An approved hypothesis is required to create its worktree')
                    dispatch = row is None
                    if dispatch:
                        if conn.execute("SELECT 1 FROM task_sessions WHERE task_id=? AND hypothesis_id=? "
                                        "AND role='implementer'", (task_id, hypothesis_id)).fetchone():
                            raise Conflict('Hypothesis already has an implementer on its earlier worktree')
                        project = self.task_worktree_service.project_root
                        if _text_git(project, 'rev-parse', '--show-toplevel') != str(project):
                            raise ValueError('Trusted project must be a Git repository root')
                        base = _text_git(project, 'rev-parse', 'HEAD')
                        common = str(Path(_text_git(project, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve())
                        branch = 'sn-hyp-' + hypothesis_id
                        branches = _text_git(project, 'for-each-ref', '--format=%(refname:short)',
                                             'refs/heads/' + branch).splitlines()
                        if branch in branches:
                            raise Conflict('Hypothesis creation branch already exists')
                        created = now()
                        conn.execute('INSERT INTO hypothesis_worktrees(task_id,hypothesis_id,status,branch,base_revision,'
                                     'project_common_dir,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                                     (task_id, hypothesis_id, 'unknown', branch, base, common, created, created))
                        self.repository._event(conn, task_id, 'hypothesis.worktree.requested', hypothesis_id,
                                               dict(actor=actor, branch=branch, base_revision=base))
                    row = conn.execute('SELECT * FROM hypothesis_worktrees WHERE task_id=? AND hypothesis_id=?',
                                       (task_id, hypothesis_id)).fetchone()
            if existing_ready:
                return self.get(task_id, hypothesis_id)
            intent = dict(row)
            try:
                expected = json.loads(intent['workspace_json']) if intent['workspace_json'] else None
                if dispatch:
                    expected = _workspace(self.creator.create(self.task_worktree_service.project_root,
                                                              intent['branch'], intent['base_revision']))
                    with self.repository.connect(write=True) as conn:
                        conn.execute('UPDATE hypothesis_worktrees SET workspace_json=?,updated_at=? '
                                     'WHERE task_id=? AND hypothesis_id=?',
                                     (json.dumps(expected), now(), task_id, hypothesis_id))
                self._reconcile(intent, expected)
            except Exception:
                # Raw errors may contain credentials, paths or daemon addresses.
                # Intent is already committed and every retry is read-only.
                pass
            return self.get(task_id, hypothesis_id)

    def activate(self, task_id, hypothesis_id, *, actor):
        actor = _text(actor, 'actor', limit=200)
        with self.repository.connect(write=True) as conn:
            self._require_card(conn, task_id, hypothesis_id)
            binding = hypothesis_binding(conn, task_id, hypothesis_id)
            if binding is None:
                raise Conflict('Hypothesis worktree is not ready')
            _validate_worktree(self.task_worktree_service.project_root, binding['worktree_path'])
            # Manual/older sessions inherit the Task scope until their cwd is
            # frozen. Resolve that inheritance before changing its selection.
            task = self.repository._require(conn, 'tasks', task_id)
            for session in conn.execute('SELECT * FROM task_sessions WHERE task_id=? AND cwd IS NULL',
                                        (task_id,)).fetchall():
                cwd = self.repository.session_cwd(conn, session, task)
                conn.execute('UPDATE task_sessions SET cwd=? WHERE id=? AND cwd IS NULL', (cwd, session['id']))
            current = conn.execute('SELECT * FROM task_worktrees WHERE task_id=?', (task_id,)).fetchone()
            if current:
                old = _decode_row(current)
                payload = json.dumps(old, sort_keys=True, ensure_ascii=False)
                identity = hashlib.sha256(payload.encode()).hexdigest()
                conn.execute('INSERT OR IGNORE INTO historical_task_worktrees VALUES(?,?,?)', (task_id, identity, payload))
            conn.execute('''INSERT INTO task_worktrees VALUES(?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET
                paseo_workspace_id=excluded.paseo_workspace_id,worktree_path=excluded.worktree_path,
                branch=excluded.branch,base_revision=excluded.base_revision,
                start_snapshot_json=excluded.start_snapshot_json,created_at=excluded.created_at''',
                (task_id, binding['paseo_workspace_id'], binding['worktree_path'], binding['branch'],
                 binding['base_revision'], json.dumps(binding['start_snapshot'], ensure_ascii=False), binding['created_at']))
            self.repository._event(conn, task_id, 'hypothesis.worktree.selected', hypothesis_id, dict(actor=actor))
        return binding

    def view(self, task_id, hypothesis_id):
        with self.repository.connect() as conn:
            self._require_card(conn, task_id, hypothesis_id)
        return _CardWorktreeView(self, task_id, hypothesis_id)
