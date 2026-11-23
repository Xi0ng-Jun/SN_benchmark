"""Fork-point snapshots.

``pre_repair/`` archives the complete workspace tree at Phase A exit (whether or not repair is
triggered), so live Phase B and offline Path 1 replay start from the identical file-system view.
Excluded: the snapshot destination itself (no recursion), sockets / FIFOs / devices, lock files,
in-flight staging directories and the isolated repair session directory. Symlinks are archived
as symlinks (never followed). ``post_repair/`` archives the updated ``best/``, the submission and
the repair stream / trace. Every archive carries ``manifest.json`` (relative path -> sha256).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from pathlib import Path
from typing import Iterable

DEFAULT_EXCLUDE_NAMES = ("promote.lock",)
DEFAULT_EXCLUDE_SUFFIXES = (".lock", ".sock", ".socket", ".pid")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def archive_tree(src: Path, dst: Path, *, exclude_paths: Iterable[Path] = (),
                 exclude_names: Iterable[str] = DEFAULT_EXCLUDE_NAMES,
                 exclude_suffixes: Iterable[str] = DEFAULT_EXCLUDE_SUFFIXES) -> dict[str, str]:
    """Copy *src* into *dst*; return ``{relpath: sha256 | "symlink:<target>"}``."""
    src, dst = Path(src), Path(dst)
    excl = {Path(p).resolve() for p in exclude_paths} | {dst.resolve()}
    names, suffixes = set(exclude_names), tuple(exclude_suffixes)
    manifest: dict[str, str] = {}
    skipped: list[str] = []
    dst.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src):
        base = Path(root)
        keep = []
        for d in sorted(dirs):
            p = base / d
            if d in names or p.resolve() in excl and not p.is_symlink():
                skipped.append(p.relative_to(src).as_posix() + "/")
                continue
            if p.is_symlink():
                files.append(d)
                continue
            keep.append(d)
            (dst / p.relative_to(src)).mkdir(parents=True, exist_ok=True)
        dirs[:] = keep
        for f in sorted(set(files)):
            p = base / f
            rel = p.relative_to(src).as_posix()
            if f in names or f.endswith(suffixes):
                skipped.append(rel)
                continue
            try:
                st = os.lstat(p)
            except OSError:
                continue
            target = dst / rel
            if stat.S_ISLNK(st.st_mode):
                link = os.readlink(p)
                target.parent.mkdir(parents=True, exist_ok=True)
                if os.path.lexists(target):
                    target.unlink()
                os.symlink(link, target)
                manifest[rel] = "symlink:" + link
            elif stat.S_ISREG(st.st_mode):
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(p, target, follow_symlinks=False)
                except OSError as exc:
                    skipped.append(f"{rel} ({exc.strerror})")
                    continue
                manifest[rel] = _sha(target)
            else:
                skipped.append(rel)
    if skipped:
        manifest["_skipped"] = json.dumps(sorted(skipped)[:200])
    return manifest


def _write_manifest(dst: Path, manifest: dict, extra: dict) -> None:
    doc = {"schema_version": 1, **extra, "files": dict(sorted(manifest.items()))}
    (dst / "manifest.json").write_text(json.dumps(doc, indent=1, sort_keys=True))


def archive_pre_repair(workspace: Path, dest: Path, *, exclude_paths: Iterable[Path] = ()) -> dict:
    """Archive the full *workspace* into ``dest`` (``.../pre_repair``); returns the manifest."""
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    tree = dest / "workspace"
    manifest = archive_tree(Path(workspace), tree, exclude_paths=[*exclude_paths, dest])
    _write_manifest(dest, manifest, {"kind": "pre_repair", "workspace": str(workspace)})
    return manifest


def archive_post_repair(dest: Path, *, best_dir: Path, submission_dir: Path,
                        files: Iterable[Path] = ()) -> dict:
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    manifest: dict[str, str] = {}
    for name, src in (("best", Path(best_dir)), ("submission", Path(submission_dir))):
        if src.is_dir():
            sub = archive_tree(src, dest / name)
            manifest.update({f"{name}/{k}": v for k, v in sub.items()})
    for f in files:
        f = Path(f)
        if f.is_file() and not f.is_symlink():
            shutil.copy2(f, dest / f.name)
            manifest[f.name] = _sha(dest / f.name)
    _write_manifest(dest, manifest, {"kind": "post_repair"})
    return manifest
