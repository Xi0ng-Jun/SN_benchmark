"""Atomic write-through promotion along the ratchet order, and direct-overwrite rescue.

Layout (all inside one workspace filesystem)::

    <root>/<submission>/<artifact>        canonical primary artifact (copied out by the host)
    <root>/<submission>/<aux>/            canonical auxiliary tree
    <root>/.mcr/best/{<artifact>, <aux>/, cert.json}
    <root>/.mcr/staging/<uuid>/           candidate copy under evaluation
    <root>/.mcr/promote.lock              advisory ``fcntl.flock``
    <root>/.mcr/last_evaluated_draft.json
    <draft>/_direct_snapshot/<seq>_<sha8>/  rescued direct writes (never overwritten)

Benchmark knowledge enters only through ``Workspace`` names and two callbacks:
``evaluate(artifact, aux_dir_or_None) -> Cert`` and ``validate(artifact) -> bool``.

Order of a promotion: stage candidate -> evaluate -> ratchet decision -> integrity callback ->
``.mcr/best/`` swap -> primary artifact via a temp file *inside* the submission directory and
``os.replace`` (same filesystem even if ``.mcr`` were elsewhere) -> auxiliary tree swap
``aux -> aux.old.<uuid>``, ``aux.new.<uuid> -> aux``, ``rmtree(aux.old.<uuid>)``. A candidate
without an auxiliary tree keeps the existing certified one. No step-level rollback of the draft
area ever happens; only the canonical submission is guarded.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import shutil
import stat
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from .ratchet import Cert, Decision, TierSpec, decide

Evaluator = Callable[[Path, "Path | None"], Cert]
Validator = Callable[[Path], bool]


@dataclass
class Workspace:
    root: Path
    artifact_name: str
    aux_name: str = "src"
    submission_rel: str = "submission"
    draft_rel: str = "work/draft"
    mcr_rel: str = ".mcr"

    def __post_init__(self):
        self.root = Path(self.root)

    @property
    def submission_dir(self) -> Path:
        return self.root / self.submission_rel

    @property
    def submission_artifact(self) -> Path:
        return self.submission_dir / self.artifact_name

    @property
    def submission_aux(self) -> Path:
        return self.submission_dir / self.aux_name

    @property
    def draft_dir(self) -> Path:
        return self.root / self.draft_rel

    @property
    def draft_artifact(self) -> Path:
        return self.draft_dir / self.artifact_name

    @property
    def draft_aux(self) -> Path:
        return self.draft_dir / self.aux_name

    @property
    def mcr_dir(self) -> Path:
        return self.root / self.mcr_rel

    @property
    def best_dir(self) -> Path:
        return self.mcr_dir / "best"

    @property
    def staging_dir(self) -> Path:
        return self.mcr_dir / "staging"

    @property
    def lock_path(self) -> Path:
        return self.mcr_dir / "promote.lock"

    @property
    def last_draft_path(self) -> Path:
        return self.mcr_dir / "last_evaluated_draft.json"

    @property
    def direct_snapshot_dir(self) -> Path:
        return self.draft_dir / "_direct_snapshot"


# ---------------------------------------------------------------------------------------------
# hashing / copying without following symlinks
# ---------------------------------------------------------------------------------------------

def is_regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def sha256_file(path: Path) -> str | None:
    if not is_regular(path):
        return None
    h = hashlib.sha256()
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def tree_sha256(root: Path) -> str | None:
    """Digest over sorted relative paths and file digests (symlinks by target); ``None`` if the
    tree is absent or is itself a symlink."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        return None
    entries = []
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            p = Path(base) / name
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                entries.append(f"L {rel} {os.readlink(p)}")
            elif p.is_dir():
                entries.append(f"D {rel}")
            elif is_regular(p):
                entries.append(f"F {rel} {sha256_file(p)}")
    h = hashlib.sha256()
    for e in sorted(entries):
        h.update(e.encode() + b"\n")
    return h.hexdigest()


def copy_regular(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(src, os.O_RDONLY | os.O_NOFOLLOW), "rb") as fi, open(dst, "wb") as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)
        fo.flush()
        os.fsync(fo.fileno())


def copy_tree_no_symlinks(src: Path, dst: Path) -> list[str]:
    """Copy regular files and directories; symlinks are skipped and reported."""
    src, skipped = Path(src), []
    dst.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src):          # followlinks=False
        base = Path(root)
        for name in sorted(dirs):
            p = base / name
            if p.is_symlink():
                skipped.append(p.relative_to(src).as_posix())
            else:
                (dst / p.relative_to(src)).mkdir(parents=True, exist_ok=True)
        for name in sorted(files):
            p = base / name
            if p.is_symlink() or not is_regular(p):
                skipped.append(p.relative_to(src).as_posix())
            else:
                copy_regular(p, dst / p.relative_to(src))
    return skipped


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{uuid.uuid4().hex}")
    with open(tmp, "w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _rename(src: Path, dst: Path) -> None:
    try:
        os.rename(src, dst)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.move(str(src), str(dst))


# ---------------------------------------------------------------------------------------------
# lock
# ---------------------------------------------------------------------------------------------

class LockTimeout(TimeoutError):
    pass


@contextmanager
def promote_lock(ws: Workspace, timeout: float = 120.0) -> Iterator[None]:
    ws.mcr_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(ws.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"promote lock busy for {timeout:.0f}s")
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# ---------------------------------------------------------------------------------------------
# best state
# ---------------------------------------------------------------------------------------------

def load_best(ws: Workspace) -> Cert | None:
    p = ws.best_dir / "cert.json"
    if not p.is_file() or not is_regular(ws.best_dir / ws.artifact_name):
        return None
    try:
        return Cert.from_dict(json.loads(p.read_text()))
    except (OSError, ValueError):
        return None


def load_best_dict(ws: Workspace) -> dict | None:
    p = ws.best_dir / "cert.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


@dataclass
class PromoteResult:
    promoted: bool
    reason: str
    decision: dict = field(default_factory=dict)
    cert: dict | None = None
    best_cert: dict | None = None
    candidate_sha256: str | None = None
    candidate_aux_sha256: str | None = None
    submission_written: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _write_submission_from_best(ws: Workspace, sync_aux: bool = True) -> None:
    sub = ws.submission_dir
    sub.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex
    tmp = sub / f".{ws.artifact_name}.mcr-tmp.{tag}"
    copy_regular(ws.best_dir / ws.artifact_name, tmp)
    os.replace(tmp, ws.submission_artifact)
    best_aux = ws.best_dir / ws.aux_name
    if not sync_aux or not best_aux.is_dir() or best_aux.is_symlink():
        return
    if tree_sha256(ws.submission_aux) == tree_sha256(best_aux):
        return
    new = sub / f"{ws.aux_name}.new.{tag}"
    copy_tree_no_symlinks(best_aux, new)
    old = sub / f"{ws.aux_name}.old.{tag}"
    if ws.submission_aux.exists() or ws.submission_aux.is_symlink():
        os.rename(ws.submission_aux, old)
    os.rename(new, ws.submission_aux)
    if old.is_symlink() or old.is_file():
        old.unlink()
    elif old.exists():
        shutil.rmtree(old)


def restore_submission_from_best(ws: Workspace) -> bool:
    if not is_regular(ws.best_dir / ws.artifact_name):
        return False
    _write_submission_from_best(ws)
    return True


def submission_matches_best(ws: Workspace, best: Cert | None = None) -> bool:
    best = best if best is not None else load_best(ws)
    if best is None:
        return False
    if sha256_file(ws.submission_artifact) != best.artifact_sha256:
        return False
    return best.aux_tree_sha256 is None or tree_sha256(ws.submission_aux) == best.aux_tree_sha256


def _install_best(ws: Workspace, stage: Path, cert: Cert, tier_spec: TierSpec) -> None:
    new = stage / "best.new"
    new.mkdir()
    os.replace(stage / "cand" / ws.artifact_name, new / ws.artifact_name)
    cand_aux = stage / "cand" / ws.aux_name
    old_aux = ws.best_dir / ws.aux_name
    if cand_aux.is_dir():
        os.rename(cand_aux, new / ws.aux_name)
    elif old_aux.is_dir() and not old_aux.is_symlink():
        copy_tree_no_symlinks(old_aux, new / ws.aux_name)
    cert.aux_tree_sha256 = tree_sha256(new / ws.aux_name)
    _atomic_write_text(new / "cert.json", json.dumps(cert.as_dict(tier_spec), indent=1, sort_keys=True,
                                                     default=str))
    old = stage / "best.old"
    if ws.best_dir.exists():
        os.rename(ws.best_dir, old)
    os.rename(new, ws.best_dir)
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)


def _record_draft(ws: Workspace, artifact: Path, aux: Path | None, res: PromoteResult) -> None:
    try:
        st = os.lstat(artifact)
        mtime, size = st.st_mtime_ns, st.st_size
    except OSError:
        mtime, size = None, None
    rec = {"schema_version": 1, "path": str(artifact), "sha256": res.candidate_sha256,
           "aux_tree_sha256": res.candidate_aux_sha256, "mtime_ns": mtime, "size": size,
           "promoted": res.promoted, "reason": res.reason, "evaluated_at_unix": round(time.time(), 3)}
    _atomic_write_text(ws.last_draft_path, json.dumps(rec, indent=1, sort_keys=True))


def _promote_locked(ws: Workspace, artifact: Path, aux: Path | None, evaluate: Evaluator,
                    tier_spec: TierSpec, validate: Validator | None, record_draft: bool) -> PromoteResult:
    artifact = Path(artifact)
    best = load_best(ws)
    best_d = best.as_dict(tier_spec) if best else None
    if artifact.is_symlink():
        res = PromoteResult(False, "rejected_symlink", best_cert=best_d)
        if record_draft:
            _record_draft(ws, artifact, aux, res)
        return res
    cand_sha = sha256_file(artifact)
    aux = Path(aux) if aux is not None and Path(aux).is_dir() and not Path(aux).is_symlink() else None
    cand_aux_sha = tree_sha256(aux) if aux is not None else None
    if (best is not None and cand_sha is not None and cand_sha == best.artifact_sha256
            and (cand_aux_sha is None or cand_aux_sha == best.aux_tree_sha256)):
        res = PromoteResult(False, "identical_to_best", best_cert=best_d, candidate_sha256=cand_sha,
                            candidate_aux_sha256=cand_aux_sha)
        if not submission_matches_best(ws, best):
            restore_submission_from_best(ws)
            res.submission_written = True
        if record_draft:
            _record_draft(ws, artifact, aux, res)
        return res
    ws.staging_dir.mkdir(parents=True, exist_ok=True)
    stage = ws.staging_dir / uuid.uuid4().hex
    (stage / "cand").mkdir(parents=True)
    try:
        staged = stage / "cand" / ws.artifact_name
        notes = []
        if cand_sha is not None:
            copy_regular(artifact, staged)
            if sha256_file(staged) != cand_sha:
                return PromoteResult(False, "candidate_changed_during_copy_no_decision", best_cert=best_d)
        staged_aux = None
        if aux is not None:
            staged_aux = stage / "cand" / ws.aux_name
            skipped = copy_tree_no_symlinks(aux, staged_aux)
            if skipped:
                notes.append(f"symlinks not copied from {ws.aux_name}/: {skipped[:10]}")
        try:
            cert = (evaluate(staged, staged_aux) if cand_sha is not None
                    else Cert.build(False, [None] * tier_spec.size, tier_spec,
                                    details={"reason": "candidate is not a regular file"}))
        except Exception as exc:  # noqa: BLE001 - a checker crash is no decision, never a false m=0
            res = PromoteResult(False, "check_error_no_decision", best_cert=best_d,
                                candidate_sha256=cand_sha, notes=notes + [f"{type(exc).__name__}: {exc}"])
            if record_draft:
                _record_draft(ws, artifact, aux, res)
            return res
        cert.file_present = bool(cert.file_present and cand_sha is not None
                                 and staged.stat().st_size > 0)
        cert.artifact_sha256 = cand_sha
        cert.aux_tree_sha256 = cand_aux_sha
        decision: Decision = decide(cert, best, tier_spec)
        res = PromoteResult(decision.promote, decision.reason, decision.as_dict(),
                            cert.as_dict(tier_spec), best_d, cand_sha, cand_aux_sha, notes=notes)
        if decision.promote and validate is not None and not validate(staged):
            res.promoted, res.reason = False, "integrity_check_failed"
        if res.promoted:
            _install_best(ws, stage, cert, tier_spec)
            _write_submission_from_best(ws)
            res.submission_written = True
            res.cert = cert.as_dict(tier_spec)
        if record_draft and not decision.reason.endswith("no_decision"):
            _record_draft(ws, artifact, aux, res)
        return res
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def promote(ws: Workspace, artifact: Path, aux: Path | None, evaluate: Evaluator, tier_spec: TierSpec,
            *, validate: Validator | None = None, lock_timeout: float = 120.0,
            record_draft: bool | None = None) -> PromoteResult:
    """Evaluate *artifact* (+ *aux*) and promote it iff it is ratchet-better than ``best``."""
    if record_draft is None:
        try:
            record_draft = Path(artifact).resolve().is_relative_to(ws.draft_dir.resolve())
        except OSError:
            record_draft = False
    try:
        with promote_lock(ws, lock_timeout):
            return _promote_locked(ws, Path(artifact), aux, evaluate, tier_spec, validate, record_draft)
    except LockTimeout:
        return PromoteResult(False, "lock_timeout_no_decision")


def draft_needs_evaluation(ws: Workspace, artifact: Path | None = None, aux: Path | None = None) -> bool:
    """True when the draft artifact (or its aux tree) differs from the last evaluated draft."""
    artifact = Path(artifact) if artifact else ws.draft_artifact
    aux = Path(aux) if aux else ws.draft_aux
    if not os.path.lexists(artifact):
        return False
    try:
        rec = json.loads(ws.last_draft_path.read_text())
    except (OSError, ValueError):
        return True
    if rec.get("path") != str(artifact):
        return True
    if is_regular(artifact) and sha256_file(artifact) != rec.get("sha256"):
        return True
    if not is_regular(artifact) and rec.get("sha256") is not None:
        return True
    cur_aux = tree_sha256(aux) if aux.is_dir() else None
    return cur_aux is not None and cur_aux != rec.get("aux_tree_sha256")


def _next_snapshot_dir(ws: Workspace, sha: str | None) -> Path:
    base = ws.direct_snapshot_dir
    base.mkdir(parents=True, exist_ok=True)
    seqs = [int(p.name.split("_", 1)[0]) for p in base.iterdir()
            if p.is_dir() and p.name.split("_", 1)[0].isdigit()]
    seq = max(seqs, default=0) + 1
    while True:
        d = base / f"{seq:04d}_{(sha or 'nofile00')[:8]}"
        try:
            d.mkdir()
            return d
        except FileExistsError:
            seq += 1


def handle_direct_submission_write(ws: Workspace, evaluate: Evaluator, tier_spec: TierSpec, *,
                                   validate: Validator | None = None,
                                   lock_timeout: float = 120.0) -> dict | None:
    """Rescue an uncertified direct write to the submission directory.

    Returns ``None`` when the submission equals the certified best (or there is nothing to
    rescue). Otherwise the written content is saved to a new versioned snapshot directory,
    evaluated, promoted if ratchet-better, else the submission is restored from ``best``; the
    returned dict carries an ``additionalContext`` notice for the agent.
    """
    try:
        with promote_lock(ws, lock_timeout):
            best = load_best(ws)
            art = ws.submission_artifact
            present = os.path.lexists(art)
            if best is None and not present:
                return None
            if best is not None and submission_matches_best(ws, best):
                return None
            sha = sha256_file(art)
            snap = _next_snapshot_dir(ws, sha)
            if sha is not None:
                copy_regular(art, snap / ws.artifact_name)
            aux_src = ws.submission_aux
            has_aux = aux_src.is_dir() and not aux_src.is_symlink()
            if has_aux:
                copy_tree_no_symlinks(aux_src, snap / ws.aux_name)
            meta = {"schema_version": 1, "source": str(art), "sha256": sha,
                    "submission_kind": ("regular" if sha else "symlink" if art.is_symlink()
                                        else "missing" if not present else "other"),
                    "saved_at_unix": round(time.time(), 3)}
            if sha is None:
                res = PromoteResult(False, "direct_write_not_a_regular_file")
            else:
                res = _promote_locked(ws, snap / ws.artifact_name,
                                      snap / ws.aux_name if has_aux else None,
                                      evaluate, tier_spec, validate, record_draft=False)
            restored = False
            if not res.promoted:
                restored = restore_submission_from_best(ws)
            meta.update(promoted=res.promoted, reason=res.reason, restored=restored)
            _atomic_write_text(snap / "rescue.json", json.dumps(meta, indent=1, sort_keys=True))
    except LockTimeout:
        return {"rescued": False, "reason": "lock_timeout_no_decision", "additionalContext": ""}
    rel = os.path.relpath(snap, ws.root)
    sub_rel = os.path.relpath(ws.submission_dir, ws.root)
    if res.promoted:
        msg = (f"[MCR] Your direct write to {sub_rel}/ was saved at {rel}/ and certified "
               f"({res.reason}); it is now the certified best.")
    elif restored:
        msg = (f"[MCR] Your uncertified direct write to {sub_rel}/ was rescued at {rel}/ "
               f"(not promoted: {res.reason}). {sub_rel}/ was restored to the certified best. "
               f"Work in {os.path.relpath(ws.draft_dir, ws.root)}/ and run mcr_promote instead.")
    else:
        msg = (f"[MCR] Your direct write to {sub_rel}/ was saved at {rel}/ but not certified "
               f"({res.reason}); no certified best exists yet, so nothing was restored.")
    return {"rescued": True, "snapshot_dir": str(snap), "promoted": res.promoted,
            "reason": res.reason, "restored": restored, "result": res.as_dict(),
            "additionalContext": msg}
