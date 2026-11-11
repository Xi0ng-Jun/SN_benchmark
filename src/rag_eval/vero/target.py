"""The target agent as its own git repository: versions are commits.

``root/repo`` holds a copy of one VeRO agent project (seed = first commit). Evaluation runs in a
detached worktree per commit (``root/worktrees/eval-<sha12>``, created once, never edited).
Agents work in throwaway worktrees detached at the parent, with the access rules' excluded
files hidden (skip-worktree + removed from disk). Every materialized commit is pinned by a
ref ``refs/rag-eval/<sha>`` so it survives garbage collection.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional

from .access import AccessRules

GIT_ID = ["-c", "user.name=rag-eval", "-c", "user.email=rag-eval@localhost", "-c", "commit.gpgsign=false"]
COPY_IGNORE = shutil.ignore_patterns(".git", ".venv", "venv", "__pycache__", "*.pyc", ".pytest_cache",
                                     "*.egg-info", "results", "datasets", "uv.lock")
INFO_EXCLUDE = "__pycache__/\n*.pyc\n.pytest_cache/\nresults/\n*.egg-info/\n"


class GitError(RuntimeError):
    pass


def git(*args: str, cwd: Path, check: bool = True, timeout: float = 300) -> str:
    proc = subprocess.run(["git", *GIT_ID, *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {cwd}: {proc.stderr.strip()[:2000]}")
    return proc.stdout


class TargetRepo:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.repo = self.root / "repo"
        self.worktrees = self.root / "worktrees"
        self._lock = threading.Lock()
        self._sha_locks: dict[str, threading.Lock] = {}

    # -- setup -------------------------------------------------------------

    @classmethod
    def create(cls, root: str | Path, source: str | Path, message: str) -> tuple["TargetRepo", str]:
        """Copy *source* into a new repo (idempotent: an existing repo is reused); returns (repo, seed sha)."""
        self = cls(root)
        if (self.repo / ".git").exists():
            return self, self.seed()
        shutil.copytree(Path(source), self.repo, ignore=COPY_IGNORE)
        git("init", "-q", "-b", "main", cwd=self.repo)
        (self.repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
        (self.repo / ".git" / "info" / "exclude").write_text(INFO_EXCLUDE)
        git("add", "-A", "-f", ".", cwd=self.repo)     # the agent's own .gitignore may hide needed data
        git("commit", "-q", "-m", message, cwd=self.repo)
        sha = self.head(self.repo)
        git("update-ref", "refs/rag-eval/seed", sha, cwd=self.repo)
        return self, sha

    def seed(self) -> str:
        return git("rev-parse", "refs/rag-eval/seed", cwd=self.repo).strip()

    @staticmethod
    def head(path: Path) -> str:
        return git("rev-parse", "HEAD", cwd=path).strip()

    def exists(self, sha: str) -> bool:
        return subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=self.repo,
                              capture_output=True).returncode == 0

    def parent_of(self, sha: str) -> Optional[str]:
        out = git("rev-list", "--parents", "-n", "1", sha, cwd=self.repo).split()
        return out[1] if len(out) > 1 else None

    def read_file(self, sha: str, path: str) -> Optional[str]:
        proc = subprocess.run(["git", "show", f"{sha}:{path}"], cwd=self.repo, capture_output=True, text=True)
        return proc.stdout if proc.returncode == 0 else None

    def files(self, sha: str) -> list[str]:
        return git("ls-tree", "-r", "--name-only", sha, cwd=self.repo).splitlines()

    def diff(self, base: str, head: str) -> str:
        return git("diff", base, head, cwd=self.repo)

    # -- worktrees ---------------------------------------------------------

    def eval_tree(self, sha: str) -> Path:
        """The read-only evaluation checkout of *sha* (created on first use)."""
        with self._lock:
            lock = self._sha_locks.setdefault(sha, threading.Lock())
        with lock:
            path = self.worktrees / f"eval-{sha[:12]}"
            if not (path / ".git").exists():
                if path.exists():
                    shutil.rmtree(path)
                self.worktrees.mkdir(parents=True, exist_ok=True)
                git("worktree", "add", "-q", "--detach", str(path), sha, cwd=self.repo)
            return path

    def new_workspace(self, parent: str, name: str, rules: Optional[AccessRules] = None) -> Path:
        """A fresh worktree detached at *parent*; files the rules exclude are hidden from it."""
        path = self.worktrees / name
        with self._lock:
            if path.exists():
                self.remove_worktree(path)
            self.worktrees.mkdir(parents=True, exist_ok=True)
            git("worktree", "add", "-q", "--detach", str(path), parent, cwd=self.repo)
        if rules is not None:
            hidden = [f for f in git("ls-files", cwd=path).splitlines() if rules.excluded(f)]
            for i in range(0, len(hidden), 200):
                git("update-index", "--skip-worktree", "--", *hidden[i:i + 200], cwd=path)
            for f in hidden:
                (path / f).unlink(missing_ok=True)
        return path

    def remove_worktree(self, path: Path) -> None:
        subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=self.repo, capture_output=True)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        subprocess.run(["git", "worktree", "prune"], cwd=self.repo, capture_output=True)

    def stage_changes(self, path: Path, parent: str, rules: AccessRules) -> tuple[str, list[str]]:
        """Stage the agent's edits in workspace *path* against *parent*; returns (diff, reverted paths).

        Commits the agent made itself are folded back into the index; edits outside the
        writable paths are reverted (tracked) or deleted (untracked).
        """
        if self.head(path) != parent:
            git("reset", "-q", "--soft", parent, cwd=path)
        git("reset", "-q", cwd=path)
        status = git("status", "--porcelain=v1", "-uall", "-z", cwd=path)
        entries = [e for e in status.split("\0") if e]
        changed, i = [], 0
        while i < len(entries):
            entry = entries[i]
            code, name = entry[:2], entry[3:]
            changed.append((code, name))
            if "R" in code or "C" in code:          # rename: the next entry is the source path
                i += 1
                if i < len(entries):
                    changed.append((code, entries[i]))
            i += 1
        reverted = []
        for code, name in changed:
            if rules.writable(name):
                continue
            reverted.append(name)
            if code == "??":
                target = path / name
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
            else:
                git("checkout", parent, "--", name, cwd=path, check=False)
        git("add", "-A", cwd=path)
        return git("diff", "--cached", cwd=path), sorted(set(reverted))

    def commit_workspace(self, path: Path, message: str) -> str:
        git("commit", "-q", "--no-verify", "-m", message, cwd=path)
        sha = self.head(path)
        git("update-ref", f"refs/rag-eval/{sha}", sha, cwd=self.repo)
        return sha

    def cherry_pick(self, sha: str, onto: str, name: str) -> Optional[str]:
        """*sha*'s own change (against its parent) re-applied onto *onto*; None on conflict."""
        path = self.new_workspace(onto, name)
        try:
            proc = subprocess.run(["git", *GIT_ID, "cherry-pick", "--no-commit", sha], cwd=path,
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                return None
            if not git("diff", "--cached", "--name-only", cwd=path).strip():
                return None
            msg = git("log", "-1", "--format=%B", sha, cwd=self.repo).strip()
            return self.commit_workspace(path, f"{msg}\n\nrebased from {sha} onto {onto}")
        finally:
            self.remove_worktree(path)
