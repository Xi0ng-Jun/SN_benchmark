"""Task-local SN worktree binding, observed diffs, and captured argv fragments.

Integration: call initialize_task_worktrees(repository) during explicit schema
setup, then instantiate TaskWorktreeService(repository, ArtifactStore(...)).
bind() accepts an existing registered linked worktree, never creates one. The
optional Paseo id is an opaque caller-supplied association, not a verified SDK
workspace. binding() returns None for an unbound Task; read()/run_argv() reject
it. Missing Tasks use UnknownIdentity; replacing a binding uses Conflict.

read() is read-only and reports net changes, not edit history or authorship.
Each file has independent preexisting_dirty and changed_since_start flags:
both can be true. Private paths expose names only, with unknown change flags.
Observed concurrent changes fail explicitly; there is no writer lock or atomic
snapshot guarantee. Bounded Git output never silently drops metadata; only
display patches/stat/file lists may be truncated, with explicit flags.

run_argv() captures afresh and returns only source/config argv pairs. Callers
must supply bundle/index, selection, fresh run-dir, dependency requirements,
runtime expectation, budget approval and invocation. No process is started.
Captured bytes are unbound until the existing runner actually loads them.
"""
from __future__ import annotations

import codecs
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import selectors
import stat
import subprocess
import tempfile
import time

from ..artifact_store import ArtifactStore
from ..sn_source_capture import SourceChangedDuringCapture, capture_sn_source
from .repository import Conflict, _text, now


SN_PROJECT_ROOT = Path('/home/wabiwabi/silicon-notebook/project')
_METADATA_LIMIT = 2 * 1024 * 1024
_GIT_TIMEOUT = 15


def initialize_task_worktrees(repository) -> None:
    """Add only the task_worktrees table; do not change schema/user_version."""
    with repository.connect(write=True) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS task_worktrees(
            task_id TEXT PRIMARY KEY REFERENCES tasks(id),
            paseo_workspace_id TEXT,
            worktree_path TEXT NOT NULL,
            branch TEXT,
            base_revision TEXT NOT NULL,
            start_snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )''')


def _git(root, *args, limit=_METADATA_LIMIT, display=False, allowed=(0,)):
    """Bound both stdout and duration; never include Git stderr in errors."""
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', LC_ALL='C')
    command = ['git', '--no-pager', '--literal-pathspecs', '-C', str(root),
               '-c', 'core.fsmonitor=false', '-c', 'core.quotePath=true',
               '-c', 'diff.external=', *args]
    try:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              env=env) as process, selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            data = bytearray()
            deadline = time.monotonic() + _GIT_TIMEOUT
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise ValueError('Worktree Git inspection timed out')
                    block = os.read(process.stdout.fileno(), min(65536, limit + 1 - len(data)))
                    if not block:
                        break
                    data.extend(block)
                    if len(data) > limit:
                        process.kill()
                        process.wait()
                        if display:
                            return bytes(data[:limit]), True
                        raise ValueError('Worktree Git metadata exceeds inspection limit')
                if process.wait(timeout=max(0.01, deadline - time.monotonic())) not in allowed:
                    raise ValueError('Worktree Git inspection failed')
            except BaseException:
                if process.poll() is None:
                    process.kill()
                process.wait()
                raise
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('Worktree Git inspection unavailable') from None
    return bytes(data), False


def _text_git(root, *args):
    try:
        return _git(root, *args)[0].decode('utf-8').rstrip('\n')
    except UnicodeError:
        raise ValueError('Worktree metadata must be UTF-8') from None


def _validate_worktree(project, worktree):
    project, worktree = Path(project).resolve(), Path(worktree).resolve()
    if worktree == project:
        raise ValueError('Original project tree cannot be bound')
    for root in (project, worktree):
        if _text_git(root, 'rev-parse', '--show-toplevel') != str(root):
            raise ValueError('Path must be a Git worktree root')
    original_common = Path(_text_git(project, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
    common = Path(_text_git(worktree, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
    if common != original_common:
        raise ValueError('Worktree belongs to a different project repository')
    raw = _git(project, 'worktree', 'list', '--porcelain', '-z')[0]
    registered = {item[9:].decode('utf-8') for item in raw.split(b'\0')
                  if item.startswith(b'worktree ')}
    if str(worktree) not in registered:
        raise ValueError('Path is not a registered project worktree')
    return worktree


def _status(root):
    # -z preserves spaces/newlines and reverses rename pair order to new,old.
    parts = _git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all',
                 '--ignore-submodules=none')[0].split(b'\0')
    result = []
    index = 0
    while index < len(parts) and parts[index]:
        item = parts[index].decode('utf-8')
        entry = {'status': item[:2], 'path': item[3:]}
        if 'R' in item[:2] or 'C' in item[:2]:
            index += 1
            entry['original_path'] = parts[index].decode('utf-8')
        result.append(entry)
        index += 1
    return result


def _paths(dirty):
    return {entry[key] for entry in dirty for key in ('path', 'original_path') if key in entry}


def _private(path):
    parts = PurePosixPath(path).parts
    names = {'model-services.toml', 'model-config.json', 'model-config.yaml',
             'model-config.yml', 'model_config.json', 'model_config.yaml', 'model_config.yml',
             'credentials.json', 'secret.json', 'secrets.json', 'service-account.json',
             'id_rsa', 'id_ed25519'}
    return any(part == '.env' or part.startswith('.env.') or part in names
               or part.endswith(('.pem', '.key', '.p12', '.pfx'))
               or part in {'.git', '.venv', 'node_modules', 'var', 'storage', 'logs', 'database',
                           'databases', 'runtime', 'state'} for part in parts)


def _file_state(root, path):
    """Hash ordinary files without following links; private contents stay unread."""
    pure = PurePosixPath(path)
    if pure.is_absolute() or '..' in pure.parts or not pure.parts:
        raise ValueError('Invalid worktree file path')
    if _private(path):
        return {'kind': 'private'}
    target = root
    for part in pure.parts[:-1]:
        target = target / part
        if target.is_symlink():
            return {'kind': 'symlink_ancestor'}
    target = target / pure.name
    try:
        info = target.lstat()
    except FileNotFoundError:
        return {'kind': 'missing'}
    if stat.S_ISLNK(info.st_mode):
        return {'kind': 'symlink', 'sha256': hashlib.sha256(os.fsencode(os.readlink(target))).hexdigest()}
    if not stat.S_ISREG(info.st_mode):
        return {'kind': 'nonregular'}
    descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    digest = hashlib.sha256()
    binary = False
    decoder = codecs.getincrementaldecoder('utf-8')()
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise SourceChangedDuringCapture('Worktree file changed while opening')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            for block in iter(lambda: stream.read(65536), b''):
                digest.update(block)
                binary |= any(byte < 32 and byte not in {9, 10, 13} for byte in block)
                if not binary:
                    try:
                        decoder.decode(block)
                    except UnicodeError:
                        binary = True
            if not binary:
                try:
                    decoder.decode(b'', final=True)
                except UnicodeError:
                    binary = True
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    signature = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_size,
                               value.st_mtime_ns, value.st_ctime_ns)
    if signature(info) != signature(opened) or signature(opened) != signature(after) or signature(after) != signature(target.lstat()):
        raise SourceChangedDuringCapture('Worktree file changed while reading')
    return {'kind': 'file', 'sha256': digest.hexdigest(), 'executable': bool(info.st_mode & 0o111),
            'binary': binary}


def _branch(root):
    # Detached HEAD is a supported state and is represented truthfully by None.
    branch = _text_git(root, 'rev-parse', '--abbrev-ref', 'HEAD')
    return None if branch == 'HEAD' else branch


def _decode_row(row):
    result = dict(row)
    result['start_snapshot'] = json.loads(result.pop('start_snapshot_json'))
    return result


class TaskWorktreeService:
    def __init__(self, repository, store: ArtifactStore, *, project_root=SN_PROJECT_ROOT):
        # A configurable trusted root permits isolated tests; HTTP must not take
        # project_root from request input. Production defaults to the SN tree.
        self.repository = repository
        self.store = store
        self.project_root = Path(project_root).resolve()
        if (store.root.is_relative_to(self.project_root)
                or self.project_root.is_relative_to(store.root)):
            raise ValueError('Artifact store must be isolated from original project')

    def binding(self, task_id: str) -> dict | None:
        """Read the saved binding only; no Git/capture/schema writes."""
        task_id = _text(task_id, 'task identity', limit=200)
        with self.repository.connect() as conn:
            self.repository._require(conn, 'tasks', task_id)
            row = conn.execute('SELECT * FROM task_worktrees WHERE task_id=?', (task_id,)).fetchone()
        return _decode_row(row) if row is not None else None

    def _bound(self, task_id):
        binding = self.binding(task_id)
        if binding is None:
            raise ValueError('Task is not bound to an SN worktree')
        return binding, _validate_worktree(self.project_root, binding['worktree_path'])

    def _ref_file(self, ref):
        directory = self.store.root / 'source-refs'
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink():
            raise ValueError('Source ref directory cannot be a symlink')
        path = directory / (ref['id'] + '.json')
        payload = (json.dumps(ref, sort_keys=True) + '\n').encode()
        # Publish a complete file without replacing an earlier ref. A crash or
        # simultaneous capture must not expose a partially written JSON ref.
        with tempfile.NamedTemporaryFile(dir=directory, prefix='.source-ref-') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            try:
                os.link(stream.name, path)
            except FileExistsError:
                if path.is_symlink():
                    raise ValueError('Existing source ref file differs') from None
                with path.open('rb') as existing:
                    if existing.read(len(payload) + 1) != payload:
                        raise ValueError('Existing source ref file differs') from None
        return str(path)

    def bind(self, task_id: str, worktree_path: Path, *, paseo_workspace_id: str | None = None) -> dict:
        """Capture before recording. Same-association retries preserve the start."""
        task_id = _text(task_id, 'task identity', limit=200)
        if paseo_workspace_id is not None:
            paseo_workspace_id = _text(paseo_workspace_id, 'Paseo workspace identity', limit=200)
        prior = self.binding(task_id)
        root = _validate_worktree(self.project_root, worktree_path)
        def match(record):
            if record['worktree_path'] != str(root) or record['paseo_workspace_id'] != paseo_workspace_id:
                raise Conflict('Task already has a different worktree binding')
            return record
        if prior is not None:
            return match(prior)
        binding = self.snapshot_binding(task_id, root, paseo_workspace_id=paseo_workspace_id)
        with self.repository.connect(write=True) as conn:
            self.repository._require(conn, 'tasks', task_id)
            existing = conn.execute('SELECT * FROM task_worktrees WHERE task_id=?', (task_id,)).fetchone()
            if existing is not None:
                return match(_decode_row(existing))
            conn.execute('INSERT INTO task_worktrees VALUES(?,?,?,?,?,?,?)',
                         (task_id, paseo_workspace_id, str(root), binding['branch'], binding['base_revision'],
                          json.dumps(binding['start_snapshot'], ensure_ascii=False), binding['created_at']))
        return self.binding(task_id)

    def snapshot_binding(self, task_id, worktree_path, *, paseo_workspace_id=None):
        """Capture an immutable start without choosing the Task's active tree."""
        root = _validate_worktree(self.project_root, worktree_path)
        head = _text_git(root, 'rev-parse', 'HEAD')
        branch = _branch(root)
        dirty = _status(root)
        states = {path: _file_state(root, path) for path in sorted(_paths(dirty))}
        capture = capture_sn_source(root, self.store)
        if (capture.head_revision != head or _text_git(root, 'rev-parse', 'HEAD') != head
                or _branch(root) != branch or _status(root) != dirty
                or any(_file_state(root, path) != value for path, value in states.items())):
            raise SourceChangedDuringCapture('Worktree changed during binding')
        snapshot = {'head_revision': head, 'branch': branch, 'dirty': dirty,
                    'dirty_file_states': states, 'source_ref': capture.ref,
                    'source_ref_file': self._ref_file(capture.ref),
                    'source_manifest_sha256': capture.source_manifest_sha256,
                    'capture_status': capture.capture_status}
        return dict(task_id=task_id, paseo_workspace_id=paseo_workspace_id, worktree_path=str(root),
                    branch=branch, base_revision=head, start_snapshot=snapshot, created_at=now())

    def read(self, task_id: str, *, max_diff_bytes=131072, max_file_diff_bytes=32768,
             max_files=200) -> dict:
        """Read base-to-current tracked diff plus untracked files and start flags.

        A reverted/deleted starting dirty file remains in files even when absent
        from base diff. File patches are relative to base, not starting bytes.
        diff_stat is Git's tracked --stat; untracked additions are in files.
        """
        for value, ceiling in ((max_diff_bytes, 131072), (max_file_diff_bytes, 32768), (max_files, 200)):
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError('Invalid worktree diff display limit')
        binding, root = self._bound(task_id)
        head = _text_git(root, 'rev-parse', 'HEAD')
        dirty = _status(root)
        base = binding['base_revision']
        options = ('diff', '--no-ext-diff', '--no-textconv', '--no-renames')
        paths_raw = _git(root, *options, '--name-only', '-z', base, '--')[0]
        base_changed = {path.decode('utf-8') for path in paths_raw.split(b'\0') if path}
        start = binding['start_snapshot']['dirty_file_states']
        candidates = sorted(base_changed | _paths(dirty) | set(start))
        states = {path: _file_state(root, path) for path in candidates}
        stat_raw, stat_truncated = _git(root, *options, '--stat', base, '--', limit=16384, display=True)
        files = []
        budget = max_diff_bytes
        for path in candidates[:max_files]:
            state = states[path]
            private = state['kind'] == 'private'
            binary = state.get('binary', False)
            if not private and path in base_changed:
                numbers = _git(root, *options, '--numstat', '-z', base, '--', path)[0]
                binary |= numbers.startswith(b'-\t-\t')
            changed = (state != start[path] if path in start else
                       path in base_changed or any(e['path'] == path and e['status'] == '??' for e in dirty))
            entry = {'path': path, 'preexisting_dirty': path in start,
                     'changed_since_start': None if state['kind'] in {
                         'private', 'nonregular', 'symlink_ancestor'} else changed,
                     'private': private, 'binary': binary, 'kind': state['kind'],
                     'diff': None, 'truncated': False}
            if not private and not binary and state['kind'] in {'file', 'missing'}:
                if path in base_changed:
                    args = (*options, '--patch', base, '--', path)
                    allowed = (0,)
                elif state['kind'] == 'file' and any(e['path'] == path and e['status'] == '??' for e in dirty):
                    args = ('diff', '--no-ext-diff', '--no-textconv', '--no-index', '--', '/dev/null', str(root / path))
                    allowed = (0, 1)
                else:
                    args = None
                if args is not None:
                    raw, truncated = _git(root, *args, limit=min(budget, max_file_diff_bytes),
                                          display=True, allowed=allowed)
                    if b'Binary files ' in raw:
                        entry['binary'] = True
                    else:
                        # Ignore an incomplete UTF-8 codepoint at the cap; this
                        # keeps the encoded displayed size within the byte cap.
                        entry['diff'] = raw.decode('utf-8', errors='ignore')
                        budget -= len(entry['diff'].encode())
                    entry['truncated'] = truncated
            files.append(entry)
        if (_text_git(root, 'rev-parse', 'HEAD') != head or _status(root) != dirty
                or any(_file_state(root, path) != value for path, value in states.items())):
            raise SourceChangedDuringCapture('Worktree changed during diff inspection')
        return {'binding': binding, 'current_head': head, 'current_dirty': dirty,
                'diff_stat': stat_raw.decode('utf-8', errors='ignore'),
                'diff_stat_truncated': stat_truncated, 'files': files,
                'file_count': len(candidates), 'files_truncated': len(candidates) > max_files,
                'observed_at': now()}

    def capture_source(self, task_id: str) -> dict:
        """Capture the currently bound worktree into the trusted SN store."""
        binding, root = self._bound(task_id)
        capture = capture_sn_source(root, self.store)
        association = dict(task_id=task_id, worktree_path=str(root),
                           paseo_workspace_id=binding['paseo_workspace_id'], source_ref=capture.ref['id'])
        from ..identity import fingerprint
        directory = self.repository.root / 'task-captures'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (fingerprint(association) + '.json')
        payload = json.dumps(association, ensure_ascii=False, sort_keys=True)
        try:
            with path.open('x') as stream:
                stream.write(payload)
        except FileExistsError:
            if path.is_symlink() or path.read_text() != payload:
                raise ValueError('Existing task capture association differs') from None
        return capture.artifact_ref

    def run_argv(self, task_id: str, *, model_config: Path) -> list[str]:
        """Capture afresh; return argv pairs without reading config or executing."""
        binding, root = self._bound(task_id)
        config = Path(model_config).absolute()
        capture = capture_sn_source(root, self.store)
        association = dict(task_id=task_id, worktree_path=str(root),
                           paseo_workspace_id=binding['paseo_workspace_id'], source_ref=capture.ref['id'])
        from ..identity import fingerprint
        directory = self.repository.root / 'task-captures'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (fingerprint(association) + '.json')
        payload = json.dumps(association, ensure_ascii=False, sort_keys=True)
        try:
            with path.open('x') as stream:
                stream.write(payload)
        except FileExistsError:
            if path.is_symlink() or path.read_text() != payload:
                raise ValueError('Existing task capture association differs') from None
        return ['--project-root', str(self.project_root), '--model-config', str(config),
                '--artifact-root', str(self.store.root), '--sn-source-ref', self._ref_file(capture.ref),
                '--task-worktree-ref', str(path)]
