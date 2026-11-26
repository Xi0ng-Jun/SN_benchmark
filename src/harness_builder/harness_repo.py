"""Versions of the harness folder (rule 6).

``save`` copies a folder into ``versions/v<n>`` and makes the copy read-only.
Saving content that is already stored returns the existing version. A version's
hash covers every path, file content, the executable bit and symlink targets;
``verify`` recomputes it. ``__pycache__`` directories are neither copied nor hashed.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .ledger import Ledger, now

IGNORED = {"__pycache__"}


class HarnessError(Exception):
    pass


def tree_hash(root: str | Path) -> str:
    """Hash of a directory tree: paths, contents, executable bits, symlink targets."""
    h = hashlib.sha256()
    for line in _tree_lines(Path(root), ""):
        h.update(line.encode() + b"\n")
    return h.hexdigest()


def _tree_lines(base: Path, rel: str):
    with os.scandir(base / rel if rel else base) as it:
        items = sorted(it, key=lambda d: d.name)
    for d in items:
        if d.name in IGNORED:
            continue
        r = f"{rel}/{d.name}" if rel else d.name
        st = d.stat(follow_symlinks=False)
        if stat.S_ISLNK(st.st_mode):
            yield json.dumps(["l", r, os.readlink(d.path)])
        elif stat.S_ISDIR(st.st_mode):
            yield json.dumps(["d", r])
            yield from _tree_lines(base, r)
        elif stat.S_ISREG(st.st_mode):
            yield json.dumps(["f", r, bool(st.st_mode & 0o111), _file_hash(d.path)])
        else:
            raise HarnessError(f"{d.path}: not a regular file, directory or symlink")


def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_read_only(root: Path) -> None:
    """Remove write permission everywhere under ``root``; errors (e.g. foreign owner) ignored."""
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for name in filenames:
            p = os.path.join(dirpath, name)
            try:
                if not os.path.islink(p):
                    os.chmod(p, stat.S_IMODE(os.lstat(p).st_mode) & ~0o222)
            except OSError:
                pass
        try:
            os.chmod(dirpath, stat.S_IMODE(os.lstat(dirpath).st_mode) & ~0o222)
        except OSError:
            pass


class HarnessRepo:
    def __init__(self, root: str | Path, ledger: Ledger):
        self.root = Path(root)
        self.versions_dir = self.root / "versions"
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger

    @contextmanager
    def _locked(self):
        with open(self.root / "lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def versions(self) -> list[dict]:
        metas = [json.loads(p.read_text()) for p in self.versions_dir.glob("v*.json")]
        return sorted(metas, key=lambda m: int(m["version"][1:]))

    def get(self, version: str) -> dict:
        p = self.versions_dir / f"{version}.json"
        if not version.startswith("v") or not version[1:].isdigit() or not p.is_file():
            raise HarnessError(f"no harness version {version!r}")
        return json.loads(p.read_text())

    def path(self, version: str) -> Path:
        self.get(version)
        return self.versions_dir / version

    def verify(self, version: str) -> bool:
        return tree_hash(self.path(version)) == self.get(version)["hash"]

    def save(self, folder: str | Path, note: str = "") -> dict:
        """Store ``folder`` as a version (or find the identical stored one); return its record."""
        folder = Path(folder)
        if not folder.is_dir():
            raise HarnessError(f"{folder}: not a directory")
        tmp = Path(tempfile.mkdtemp(prefix=".incoming-", dir=self.versions_dir))
        try:
            copy = tmp / "h"
            shutil.copytree(folder, copy, symlinks=True,
                            ignore=shutil.ignore_patterns(*IGNORED))
            digest = tree_hash(copy)  # hash the copy, not the folder, which may still change
            with self._locked():
                for meta in self.versions():
                    if meta["hash"] == digest:
                        return meta
                taken = [int(m.group(1)) for p in self.versions_dir.iterdir()
                         if (m := re.fullmatch(r"v(\d+)(?:\.json)?", p.name))]
                version = f"v{max(taken, default=-1) + 1}"
                dst = self.versions_dir / version
                os.chmod(copy, 0o755)  # moving a directory needs write permission on it
                os.rename(copy, dst)
                make_read_only(dst)
                meta = {"version": version, "hash": digest, "note": note, "created": now()}
                (self.versions_dir / f"{version}.json").write_text(json.dumps(meta))
                self.ledger.append("harness_saved", **meta)
                return meta
        finally:
            remove_tree(tmp)


def remove_tree(path: Path) -> None:
    """rmtree that first restores write permission on directories (copies may be read-only)."""
    for dirpath, _, _ in os.walk(path):
        try:
            os.chmod(dirpath, 0o700)
        except OSError:
            pass
    shutil.rmtree(path, ignore_errors=True)
