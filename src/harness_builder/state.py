"""Open the outside program's state directory for a scenario.

Opening checks the ledger, locks or checks the split, records the scenario
directory whenever it changed (so changes to limits and invalid-run conditions are
on record), and stores the initial harness as version v0 the first time.

A development session, a delivery check and an exam each hold the state directory
for themselves while they last (``exclusive``); commands that only add a record
(an answer, a material) do not need it.
"""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import split_lock
from .harness_repo import HarnessRepo, tree_hash
from .ledger import Ledger
from .scenario import Scenario


BUSY_NAME = "busy.lock"


class Busy(Exception):
    """The state directory is held by a development session, a delivery check or an exam."""


@contextmanager
def exclusive(root: str | Path, what: str):
    """Hold the state directory for ``what``; raise Busy if something else holds it."""
    fd = os.open(Path(root) / BUSY_NAME, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy(f"the state directory is in use: {_holder_text(fd) or 'unknown'}") from None
        os.ftruncate(fd, 0)
        os.pwrite(fd, json.dumps({"what": what, "pid": os.getpid()}).encode(), 0)
        try:
            yield
        finally:
            os.ftruncate(fd, 0)
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def holder(root: str | Path) -> dict | None:
    """Who holds the state directory right now: {"what", "pid"}, or None."""
    try:
        fd = os.open(Path(root) / BUSY_NAME, os.O_RDWR)
    except FileNotFoundError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            try:
                return json.loads(_holder_text(fd))
            except ValueError:
                return {"what": "unknown", "pid": None}
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)


def _holder_text(fd: int) -> str:
    return os.pread(fd, 4096, 0).decode(errors="replace").strip()


@dataclass
class State:
    root: Path
    scenario: Scenario
    ledger: Ledger
    repo: HarnessRepo


def open_state(root: str | Path, scenario: Scenario) -> State:
    root = Path(root).absolute()
    if root.resolve() == scenario.root or scenario.root in root.resolve().parents:
        raise ValueError(f"state directory {root} must not be inside the scenario directory")
    root.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(root / "ledger")
    ledger.check()
    split_lock.ensure(root, scenario, ledger)
    digest = tree_hash(scenario.root)
    last = ledger.last("scenario")
    if last is None or last["data"]["hash"] != digest or last["data"]["config"] != scenario.config:
        ledger.append("scenario", path=str(scenario.root), hash=digest, config=scenario.config)
    repo = HarnessRepo(root / "harness", ledger)
    if not repo.versions():
        repo.save(scenario.initial_harness, note="initial harness")
    return State(root, scenario, ledger, repo)
