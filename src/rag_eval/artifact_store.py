"""Private content-addressed, immutable artifacts; never shared mutable runtime.

References bind both the exact object manifest bytes and its complete content
identity. Every boundary checks the files it consumes. Memoization belongs to
one reader operation, not a persistent 'validated' flag.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

from .artifacts import digest, save_json
from .identity import fingerprint

FORMAT = 'sn-artifact-object-v1'
REF_FORMAT = 'sn-run-artifact-refs-v1'
KINDS = {'bundle', 'index', 'evaluator', 'sn'}
_HEX = re.compile(r'[0-9a-f]{64}')


def _name(name):
    if (not isinstance(name, str) or not name or '\\' in name
            or name.startswith('/') or any(p in {'', '.', '..'} for p in name.split('/'))
            or ':' in name or name == 'object.json'):
        raise ValueError('Invalid artifact relative file name')
    return name


def _no_links(path, root):
    """Require a real descendant file/directory, including all ancestors."""
    path, root = Path(path), Path(root)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError('Artifact path escapes store') from exc
    current = root
    if current.is_symlink():
        raise ValueError('Artifact store cannot be a symlink')
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Artifact path contains a symlink')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Artifact path escapes store')


class ArtifactStore:
    def __init__(self, root):
        # A reference root is deliberately relative to a run, whereas paths
        # inside an object are always fixed content-addressed descendants.
        self.root = Path(root).absolute()
        if self.root.is_symlink():
            raise ValueError('Artifact store cannot be a symlink')
        self.root = self.root.resolve()

    def _path(self, ref):
        if (not isinstance(ref, dict) or set(ref) != {'kind', 'id', 'manifest_sha256'}
                or ref['kind'] not in KINDS
                or any(not isinstance(ref[k], str) or not _HEX.fullmatch(ref[k])
                       for k in ('id', 'manifest_sha256'))):
            raise ValueError('Invalid artifact reference')
        path = self.root / 'objects' / ref['kind'] / ref['id']
        _no_links(path, self.root)
        return path

    def resolve(self, ref, *, files=None):
        path = self._path(ref)
        manifest_path = path / 'object.json'
        _no_links(manifest_path, self.root)
        if not manifest_path.is_file():
            raise ValueError('Missing artifact object')
        if digest(manifest_path) != ref['manifest_sha256']:
            raise ValueError('Artifact manifest hash changed')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if (set(manifest) != {'format', 'kind', 'identity', 'files'}
                or manifest['format'] != FORMAT or manifest['kind'] != ref['kind']
                or not isinstance(manifest['identity'], dict)
                or not isinstance(manifest['files'], dict) or not manifest['files']
                or fingerprint(manifest) != ref['id']):
            raise ValueError('Artifact content identity changed')
        for name, checksum in manifest['files'].items():
            _name(name)
            if not isinstance(checksum, str) or not _HEX.fullmatch(checksum):
                raise ValueError('Invalid artifact file hash')
        selected = list(manifest['files']) if files is None else list(files)
        if any(name not in manifest['files'] for name in selected):
            raise ValueError('Artifact has no requested file')
        for name in selected:
            source = path / _name(name)
            _no_links(source, self.root)
            if not source.is_file() or digest(source) != manifest['files'][name]:
                raise ValueError('Artifact file hash changed: ' + name)
        if files is None:
            actual = set()
            for source in path.rglob('*'):
                _no_links(source, self.root)
                if source.is_file() and source != manifest_path:
                    actual.add(source.relative_to(path).as_posix())
            if actual != set(manifest['files']):
                raise ValueError('Artifact file set changed')
        return path

    def install_files(self, kind, sources, *, identity):
        sources = {_name(name): Path(path) for name, path in sources.items()}
        if any(path.is_symlink() or not path.is_file() for path in sources.values()):
            raise ValueError('Artifact source must be a regular file, not a symlink')
        hashes = {name: digest(path) for name, path in sources.items()}
        def write(stage):
            for name, source in sources.items():
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(source, target)
                os.chmod(target, 0o600)
                if digest(target) != hashes[name]:
                    raise ValueError('Artifact source changed while copying')
        return self._publish(kind, identity, hashes, write)

    def install_bytes(self, kind, contents, *, identity):
        contents = {_name(name): value for name, value in contents.items()}
        if any(not isinstance(value, bytes) for value in contents.values()):
            raise ValueError('Artifact contents must be bytes')
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
        def write(stage):
            for name, data in contents.items():
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                target.write_bytes(data)
                os.chmod(target, 0o600)
        return self._publish(kind, identity, hashes, write)

    def _publish(self, kind, identity, hashes, write):
        if kind not in KINDS or not isinstance(identity, dict) or not hashes:
            raise ValueError('Invalid artifact kind/identity/files')
        manifest = dict(format=FORMAT, kind=kind, identity=identity, files=hashes)
        object_id = fingerprint(manifest)
        # save_json uses this exact stable representation for its private bytes.
        encoded = (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
        ref = dict(kind=kind, id=object_id, manifest_sha256=hashlib.sha256(encoded).hexdigest())
        target = self._path(ref)
        if target.exists():
            self.resolve(ref)
            return ref
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        staging = Path(tempfile.mkdtemp(prefix='.publishing-', dir=target.parent))
        try:
            write(staging)
            (staging / 'object.json').write_bytes(encoded)
            os.chmod(staging / 'object.json', 0o600)
            # File bytes reach stable storage before the complete directory is
            # visible. An interrupted installer never publishes a valid prefix.
            for file in staging.rglob('*'):
                if file.is_file():
                    with file.open('rb') as handle:
                        os.fsync(handle.fileno())
            try:
                staging.rename(target)
            except OSError as exc:
                if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise
                self.resolve(ref)
            descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self.resolve(ref)
            return ref
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def read_run_refs(run):
    run = Path(run).resolve()
    path = run / 'artifact-refs.json'
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError('Run artifact reference cannot be a symlink')
    refs = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(refs, dict) or refs.get('format') != REF_FORMAT
            or not {'format', 'root', 'bundle', 'index'} <= set(refs)
            or set(refs) - {'format', 'root', 'bundle', 'index', 'sources'}
            or not isinstance(refs['root'], str) or not refs['root']
            or Path(refs['root']).is_absolute() or '\\' in refs['root']):
        raise ValueError('Invalid run artifact references')
    sources = refs.get('sources', {})
    if (not isinstance(sources, dict) or set(sources) - {'evaluator', 'sn'}
            or any(not isinstance(ref, dict) or ref.get('kind') != role
                   for role, ref in sources.items())
            or any(not isinstance(refs[role], dict) or refs[role].get('kind') != role
                   for role in ('bundle', 'index'))):
        raise ValueError('Invalid artifact source/reference role')
    return refs


def store_for_run(run, refs=None):
    refs = read_run_refs(run) if refs is None else refs
    if refs is None:
        raise ValueError('Run has no shared artifact references')
    return ArtifactStore(Path(run).resolve() / refs['root'])


def write_run_refs(run, store_root, refs):
    run = Path(run).resolve()
    save_json(run / 'artifact-refs.json', dict(refs, format=REF_FORMAT,
                                             root=os.path.relpath(Path(store_root).resolve(), run)))


def copy_run_input(source, output):
    """Publish the same input references in a new derived run, or copy legacy input."""
    source, output = Path(source).resolve(), Path(output).resolve()
    refs = read_run_refs(source)
    if refs is None:
        shutil.copytree(source / 'input', output / 'input')
    else:
        store = store_for_run(source, refs)
        for name in ('bundle', 'index'):
            store.resolve(refs[name])
        for ref in refs.get('sources', {}).values():
            store.resolve(ref)
        write_run_refs(output, store.root, refs)


def atomic_pointer(path, value):
    """Small private catalog pointer, safe for independent concurrent installers."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.pointer-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def reference_identity(run):
    """Manifest binding excludes relocatable root, includes exact object identities."""
    refs = read_run_refs(run)
    if refs is None:
        return {}
    return {'artifact_identity': fingerprint({k: v for k, v in refs.items() if k not in {'format', 'root'}})}


def reject_shared_output(output, *, source=None, store_roots=()):
    """Keep result publication outside immutable storage, before any mkdir.

    Explicit run references/read contexts supply known roots. Standalone writers
    also recognize the artifact store layout on the destination ancestry.
    """
    output = Path(output).resolve()
    roots = {Path(root).resolve() for root in store_roots}
    if source is not None:
        refs = read_run_refs(source)
        if refs is not None:
            roots.add(store_for_run(source, refs).root)
    for ancestor in (output, *output.parents):
        objects = ancestor / 'objects'
        if objects.is_dir() and any((objects / kind).is_dir() for kind in KINDS):
            roots.add(ancestor)
    if any(output.is_relative_to(root) or root.is_relative_to(output) for root in roots):
        raise ValueError('Result output must be separate from shared artifact store')
