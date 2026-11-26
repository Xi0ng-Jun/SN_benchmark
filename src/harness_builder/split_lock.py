"""Lock the practice/exam split before the first run (rule 1).

The first call writes ``split.lock.json`` with the split and its hash; every later
call compares the scenario's current split with it and refuses on any difference.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .ledger import Ledger
from .scenario import Scenario

LOCK_NAME = "split.lock.json"


class SplitChanged(Exception):
    pass


def split_of(scenario: Scenario) -> dict:
    return {"groups": {t.id: t.group for t in scenario.tasks.values()},
            "practice": sorted(scenario.practice), "exam": sorted(scenario.exam)}


def split_hash(split: dict) -> str:
    return hashlib.sha256(json.dumps(split, sort_keys=True).encode()).hexdigest()


def ensure(state_dir: Path, scenario: Scenario, ledger: Ledger) -> str:
    """Lock the split on first use, otherwise check it is unchanged. Returns the hash."""
    path = Path(state_dir) / LOCK_NAME
    split = split_of(scenario)
    digest = split_hash(split)
    if not path.exists():
        path.write_text(json.dumps({"hash": digest, "split": split}, indent=1, sort_keys=True))
        ledger.append("split_locked", hash=digest, split=split)
        return digest
    locked = json.loads(path.read_text())
    if locked.get("hash") != split_hash(locked.get("split")) or locked["hash"] != digest:
        old, new = locked.get("split") or {}, split
        diff = [k for k in ("groups", "practice", "exam") if old.get(k) != new.get(k)]
        raise SplitChanged(f"the split differs from the one locked in {path} "
                           f"(changed: {diff or 'lock file'}); it cannot change after locking")
    return digest
