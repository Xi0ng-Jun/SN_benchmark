"""Open the outside program's state directory for a scenario.

Opening checks the ledger, locks or checks the split, records the scenario
directory whenever it changed (so changes to limits and invalid-run conditions are
on record), and stores the initial harness as version v0 the first time.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import split_lock
from .harness_repo import HarnessRepo, tree_hash
from .ledger import Ledger
from .scenario import Scenario


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
