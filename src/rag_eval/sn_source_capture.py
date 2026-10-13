"""Capture actual Silicon Notebook source bytes into the shared ``sn`` store.

The capture is deliberately unbound: it records the bytes that a later loader
may use, but it does not alter ``sys.path`` or claim that dependencies, config,
or a running process are frozen.  ``capture_sn_source`` returns a
``SourceCapture`` with this public shape:

``artifact_ref, actual_manifest, source_manifest_sha256, head_revision,
head_tree, modified_paths, added_paths, deleted_paths, selected_roots,
capture_status, dependencies_status, config_status``.

Selection is relative to ``project``.  The default is ``backend/app``.  A
semantic cache/runtime/storage/log/state directory is rejected when it contains a
non-generated file without source-package evidence; callers must select that
directory explicitly to accept its bytes.  This prevents a data directory
from silently hiding a source package.  Secret-file exclusions are name and
extension based and cannot detect hard-coded secrets in ordinary source.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tarfile
from typing import Iterator

from .artifact_store import ArtifactStore
from .identity import fingerprint


SOURCE_FORMAT = "sn-source-capture-v1"
_EXCLUDED_DIRS = frozenset({
    ".git", ".venv", ".tox", "__pycache__", ".local", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "node_modules", "site-packages",
})
_SEMANTIC_DATA_DIRS = frozenset({
    "cache", ".cache", "runtime", "storage", "logs", "state", "database", "databases",
    "build", "dist", "venv", "env",
})
_GENERATED_SUFFIXES = (
    ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".db-journal",
    ".sqlite-wal", ".sqlite-shm", ".sqlite-journal", ".sqlite3-wal", ".sqlite3-shm",
    ".sqlite3-journal", ".log", ".log.jsonl", ".pid", ".tmp", ".pyc", ".pyo",
)
_SECRET_EXACT_NAMES = frozenset({
    "id_rsa", "id_ed25519", "credentials.json", "secret.json", "secrets.json",
    "service-account.json",
})
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
_DEPENDENCY_NAMES = frozenset({
    "requirements.txt", "requirements-dev.txt", "environment.yml", "model-services.toml",
    "model-config.json", "model-config.yaml", "model-config.yml",
    "model_config.json", "model_config.yaml", "model_config.yml",
})


class SourceChangedDuringCapture(RuntimeError):
    """The selected path set, bytes, or Git provenance changed during capture."""


@dataclass(frozen=True)
class SourceCapture:
    artifact_ref: dict
    actual_manifest: dict[str, dict[str, int | str]]
    source_manifest_sha256: str
    head_revision: str
    head_tree: str
    modified_paths: tuple[str, ...]
    added_paths: tuple[str, ...]
    deleted_paths: tuple[str, ...]
    selected_roots: tuple[str, ...]
    capture_status: str = "captured_unbound"
    dependencies_status: str = "not_captured"
    config_status: str = "not_captured"

    @property
    def ref(self) -> dict:
        """The existing ArtifactStore ``sn`` reference."""
        return self.artifact_ref


def _git(project: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(project), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("Project must be a readable Git worktree") from exc


@contextmanager
def _directory(path: Path) -> Iterator[int]:
    """Walk from / with no-follow dirfds; never resolve through a symlink."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open("/", flags)
    try:
        for part in path.absolute().parts[1:]:
            if part in {".", ".."}:
                raise ValueError("Directory ancestry cannot contain aliases")
            info = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise ValueError("Directory ancestry cannot contain a symlink")
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("Directory ancestry must contain real directories")
            child = os.open(part, flags, dir_fd=descriptor)
            opened = os.fstat(child)
            if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                os.close(child)
                raise SourceChangedDuringCapture("Directory changed during capture")
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _validate_project(project: Path) -> Path:
    project = Path(project).absolute()
    with _directory(project):
        pass
    if _git(project, "rev-parse", "--show-toplevel") != str(project):
        raise ValueError("Project must name the Git worktree root")
    return project


def _validate_roots(project: Path, selected_roots: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[Path, ...]]:
    if not isinstance(selected_roots, tuple) or not selected_roots:
        raise ValueError("selected_roots must be a nonempty tuple of relative paths")
    normalized: list[str] = []
    paths: list[Path] = []
    for raw in selected_roots:
        if not isinstance(raw, str) or not raw or "\\" in raw or ":" in raw:
            raise ValueError("Selection roots must be relative POSIX paths")
        pure = PurePosixPath(raw)
        if (pure.is_absolute() or not pure.parts or raw != pure.as_posix()
                or any(part in {"", ".", ".."} for part in pure.parts)):
            raise ValueError("Selection roots cannot contain absolute, dot, dotdot, or alias paths")
        relative = pure.as_posix()
        path = project / Path(*pure.parts)
        if any(part in _EXCLUDED_DIRS for part in pure.parts):
            raise ValueError("Selected root names an excluded private/dependency directory")
        with _directory(path):
            pass
        normalized.append(relative)
        paths.append(path)
    pairs = sorted(zip(normalized, paths))
    for index, (left, _) in enumerate(pairs):
        for right, _ in pairs[index + 1:]:
            if right == left or right.startswith(left + "/"):
                raise ValueError("Selection roots must be nonoverlapping")
    return tuple(name for name, _ in pairs), tuple(path for _, path in pairs)


def _check_store_overlap(project: Path, store: ArtifactStore) -> None:
    store_root = store.root
    if store_root == project or store_root.is_relative_to(project) or project.is_relative_to(store_root):
        raise ValueError("Artifact store overlap with project or selection")


def _is_secret(relative: PurePosixPath) -> bool:
    name = relative.name
    if name == ".env" or name.startswith(".env."):
        return True
    if name in _SECRET_EXACT_NAMES or name.endswith(_SECRET_SUFFIXES):
        return True
    return False


def _is_generated(relative: PurePosixPath) -> bool:
    name = relative.name
    return name.endswith(_GENERATED_SUFFIXES) or name in {".coverage", "coverage.xml"}


def _is_dependency(relative: PurePosixPath) -> bool:
    name = relative.name
    return name in _DEPENDENCY_NAMES or (name.startswith("requirements-") and name.endswith(".txt"))


def _excluded(relative: PurePosixPath) -> bool:
    return (any(part in _EXCLUDED_DIRS for part in relative.parts[:-1])
            or _is_secret(relative) or _is_generated(relative) or _is_dependency(relative))


@dataclass
class _Inventory:
    files: dict[str, tuple[Path, os.stat_result]]
    directories: dict[str, tuple[int, ...]]


def _inventory(project: Path, roots: tuple[Path, ...], selected_roots: tuple[str, ...]) -> _Inventory:
    found: dict[str, tuple[Path, os.stat_result]] = {}
    directories: dict[str, tuple[int, ...]] = {}

    def visit(directory: Path, descriptor: int) -> None:
        before = os.fstat(descriptor)
        with os.scandir(descriptor) as scan:
            entries = sorted(scan, key=lambda entry: entry.name)
        for entry in entries:
            path = directory / entry.name
            info = entry.stat(follow_symlinks=False)
            mode = info.st_mode
            if stat.S_ISLNK(mode):
                raise ValueError("Selected contents cannot contain symlinks")
            relative = path.relative_to(project).as_posix()
            if stat.S_ISDIR(mode):
                if entry.name in _EXCLUDED_DIRS:
                    continue
                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                try:
                    if _stat_signature(info) != _stat_signature(os.fstat(child)):
                        raise SourceChangedDuringCapture("Directory changed during inventory")
                    visit(path, child)
                finally:
                    os.close(child)
            elif not stat.S_ISREG(mode):
                raise ValueError("Selected contents must contain regular files only")
            elif not _excluded(PurePosixPath(relative)):
                found[relative] = (path, info)
        if _stat_signature(before) != _stat_signature(os.fstat(descriptor)):
            raise SourceChangedDuringCapture("Directory changed during inventory")
        directories[directory.relative_to(project).as_posix()] = _stat_signature(before)

    for root in roots:
        with _directory(root) as descriptor:
            visit(root, descriptor)
    # The direct presence of .py source is evidence of a Python source package
    # (including namespace packages). No content or private config is opened.
    source_directories = {PurePosixPath(name).parent for name in found if name.endswith(".py")}
    for name in found:
        relative = PurePosixPath(name)
        for ancestor in relative.parents:
            if ancestor.name not in _SEMANTIC_DATA_DIRS:
                continue
            explicit = any(PurePosixPath(root).is_relative_to(ancestor) for root in selected_roots)
            if not explicit and ancestor not in source_directories:
                raise ValueError("Ambiguous runtime/data path; require explicit selection")
    return _Inventory(found, directories)


def _stat_signature(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode)


def _read_stable_file(path: Path) -> bytes:
    with _directory(path.parent) as parent:
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Selected contents must contain regular files only")
        # Nonblocking prevents a regular-to-FIFO replacement from hanging open.
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            opened = os.fstat(descriptor)
            if _stat_signature(before) != _stat_signature(opened):
                raise SourceChangedDuringCapture("File changed while opening")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                data = handle.read()
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (_stat_signature(before) != _stat_signature(after)
                or _stat_signature(after) != _stat_signature(current)
                or len(data) != after.st_size):
            raise SourceChangedDuringCapture("File changed while reading")
    return data


def _git_files(project: Path, revision: str, selected_roots: tuple[str, ...]) -> set[str]:
    try:
        raw = subprocess.check_output(
            ["git", "--literal-pathspecs", "-C", str(project), "ls-tree", "-r", "--name-only", "-z", revision, "--", *selected_roots],
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("Unable to inspect project tree") from exc
    paths = {item.decode("utf-8") for item in raw.split(b"\0") if item}
    return {path for path in paths if not _excluded(PurePosixPath(path))}


def _provenance(project: Path) -> tuple[str, str]:
    revision = _git(project, "rev-parse", "HEAD")
    tree = _git(project, "rev-parse", revision + "^{tree}")
    if _git(project, "rev-parse", "HEAD") != revision:
        raise SourceChangedDuringCapture("HEAD changed while recording provenance")
    return revision, tree


def _archive(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:") as archive:
        for name in sorted(files):
            member = tarfile.TarInfo(name)
            member.type = tarfile.REGTYPE
            member.mode = 0o600
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            member.mtime = 0
            member.size = len(files[name])
            archive.addfile(member, io.BytesIO(files[name]))
    return output.getvalue()


def capture_sn_source(
    project: Path,
    store: ArtifactStore,
    *,
    selected_roots: tuple[str, ...] = ("backend/app",),
) -> SourceCapture:
    """Capture observed-stable selected bytes; no hostile/atomic snapshot claim.

    Rejects observed path/metadata/content/HEAD changes through the final
    pre-publication checks. Directory/file opens use Linux no-follow dirfds.
    No writer lock is held: changes after the final checks or transient changes
    reverted between observations are not proven absent. The object always
    contains the in-memory bytes actually read, never a late copy of the path.
    """
    try:
        return _capture_sn_source(project, store, selected_roots)
    except OSError:
        raise ValueError("Source capture or artifact publication failed") from None
    except UnicodeError:
        raise ValueError("Source paths must be representable as UTF-8") from None


def _capture_sn_source(project: Path, store: ArtifactStore, selected_roots: tuple[str, ...]) -> SourceCapture:
    project = _validate_project(project)
    selected_roots, roots = _validate_roots(project, selected_roots)
    _check_store_overlap(project, store)
    revision, tree = _provenance(project)
    baseline = _git_files(project, revision, selected_roots)
    try:
        first = _inventory(project, roots, selected_roots)
    except OSError:
        raise SourceChangedDuringCapture("Source changed during inventory") from None
    if not first.files:
        raise ValueError("Selected source contains no included regular files")
    try:
        first_bytes = {name: _read_stable_file(path) for name, (path, _) in first.files.items()}
    except (OSError, ValueError):
        raise SourceChangedDuringCapture("Source changed after inventory") from None
    actual_manifest = {
        name: {"sha256": hashlib.sha256(first_bytes[name]).hexdigest(), "size": len(first_bytes[name])}
        for name in sorted(first_bytes)
    }
    source_manifest_sha256 = fingerprint(actual_manifest)
    actual_paths = set(actual_manifest)
    modified = sorted(path for path in baseline & actual_paths
                      if _git_blob_hash(project, revision, path) != actual_manifest[path]["sha256"])
    added = sorted(actual_paths - baseline)
    deleted = sorted(baseline - actual_paths)
    archive_bytes = _archive(first_bytes)
    identity = {
        "source_format": SOURCE_FORMAT,
        "selected_roots": list(selected_roots),
        "exclusions": {
            "directories": sorted(_EXCLUDED_DIRS),
            "generated_suffixes": list(_GENERATED_SUFFIXES),
            "semantic_data_directories": sorted(_SEMANTIC_DATA_DIRS),
            "secret_names": sorted(_SECRET_EXACT_NAMES),
            "secret_suffixes": list(_SECRET_SUFFIXES),
            "dependency_names": sorted(_DEPENDENCY_NAMES),
            "dotenv_rule": ".env and .env.*",
            "generated_names": [".coverage", "coverage.xml"],
            "dependency_pattern": "requirements-*.txt",
            "ambiguous_directory_rule": "direct .py source evidence or explicit selection; otherwise reject",
        },
        "head_revision": revision,
        "head_tree": tree,
        "source_manifest_sha256": source_manifest_sha256,
        "actual_manifest": actual_manifest,
        "modified_paths": modified,
        "added_paths": added,
        "deleted_paths": deleted,
    }
    # Do expensive provenance comparison and tar assembly before the last read
    # and inventory barriers, so mutations during either cannot be published.
    try:
        second = _inventory(project, roots, selected_roots)
        _check_inventory(first, second)
        second_bytes = {name: _read_stable_file(path) for name, (path, _) in second.files.items()}
        if first_bytes != second_bytes:
            raise SourceChangedDuringCapture("Source bytes changed between reads")
        _check_inventory(first, _inventory(project, roots, selected_roots))
        if _provenance(project) != (revision, tree):
            raise SourceChangedDuringCapture("HEAD changed during capture")
    except (OSError, ValueError):
        raise SourceChangedDuringCapture("Source or provenance changed before publication") from None
    _check_store_overlap(project, store)
    reference = store.install_bytes("sn", {"product-source.tar": archive_bytes}, identity=identity)
    return SourceCapture(
        artifact_ref=reference,
        actual_manifest=actual_manifest,
        source_manifest_sha256=source_manifest_sha256,
        head_revision=revision,
        head_tree=tree,
        modified_paths=tuple(modified),
        added_paths=tuple(added),
        deleted_paths=tuple(deleted),
        selected_roots=selected_roots,
    )


def _check_inventory(before: _Inventory, after: _Inventory) -> None:
    if (before.directories != after.directories or set(before.files) != set(after.files)
            or any(_stat_signature(before.files[name][1]) != _stat_signature(after.files[name][1])
                   for name in before.files)):
        raise SourceChangedDuringCapture("Selected paths or metadata changed during capture")


def _git_blob_hash(project: Path, revision: str, path: str) -> str:
    try:
        # The public manifest deliberately uses SHA-256 over actual file bytes,
        # independent of Git's repository object hash algorithm.
        raw = subprocess.check_output(
            ["git", "-C", str(project), "show", f"{revision}:{path}"],
            stderr=subprocess.DEVNULL,
        )
        return hashlib.sha256(raw).hexdigest()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("Unable to inspect project tree") from exc
