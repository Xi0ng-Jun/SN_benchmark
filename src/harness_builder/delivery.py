"""Delivery (rule 6): freeze the delivered version, then try it once on a practice task.

The delivered version is the last one the developer agent named. The check

1. looks at the stored version: it must still match its recorded hash and have an
   executable ``start`` and a non-empty ``MANIFEST.md``;
2. freezes it: the version, its hash and the manifest's hash go on the ledger, and
   from then on the exam accepts only a folder with exactly that hash;
3. runs one practice task with a new copy of the folder in a new temporary
   directory outside the state directory, with the scenario's own limits, on a
   budget pool of its own (so it works when the development budget is used up).

The trial passes when the run command and the score command both ended with exit
code 0 inside their time limits. The score itself is not looked at. A trial that
meets the researcher's invalid-run condition is neither passed nor failed; the
check is simply done again. A failed check sends the delivery back: the outcome is
on the ledger, the developer agent sees it in ``hb status`` and finds the trial's
records under ``trial/`` of its records folder, and the exam refuses to start.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from .harness_repo import HarnessError, make_read_only, remove_tree
from .runs import Runner
from .scenario import START_NAME
from .state import State, exclusive

TRIAL_AREA = "dev/trial"  # inside the developer agent's records folder
POOL = "delivery"
MANIFEST_NAME = "MANIFEST.md"


class DeliveryError(Exception):
    """There is nothing to check, or the check may not be made now."""


def accepted(state: State) -> dict | None:
    """The passed check of the version delivered last, or None if there is none."""
    named = state.ledger.last("delivery_designated")
    checked = state.ledger.last("delivery_checked")
    if named is None or checked is None or checked["seq"] < named["seq"]:
        return None
    data = checked["data"]
    if data["outcome"] != "passed" or data["version"] != named["data"]["version"]:
        return None
    return {**data, "seq": checked["seq"]}


def _folder_problems(state: State, version: str) -> tuple[list[str], str | None]:
    problems, manifest_hash = [], None
    try:
        folder = state.repo.path(version)
        if not state.repo.verify(version):
            problems.append("the stored version no longer matches its recorded hash")
    except (HarnessError, OSError) as e:
        return [f"the stored version cannot be read: {e}"], None
    start, manifest = folder / START_NAME, folder / MANIFEST_NAME
    if not start.is_file() or not os.access(start, os.X_OK):
        problems.append(f"{START_NAME} is missing or not executable")
    if not manifest.is_file() or not manifest.read_bytes().strip():
        problems.append(f"{MANIFEST_NAME} is missing or empty")
    else:
        manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
    return problems, manifest_hash


def check(state: State, gateway, task: str | None = None) -> dict:
    """Freeze the delivered version and try it on one practice task; return the check's record."""
    with exclusive(state.root, "delivery check"):
        ledger, scenario = state.ledger, state.scenario
        if ledger.last("exam_started") is not None:
            raise DeliveryError("an exam has been started in this state directory")
        named = ledger.last("delivery_designated")
        if named is None:
            raise DeliveryError("no version has been delivered")
        task = scenario.practice[0] if task is None else task
        if task not in scenario.practice:
            raise DeliveryError(f"task {task!r} is not a practice task")
        version, digest = named["data"]["version"], named["data"]["hash"]
        base = {"version": version, "hash": digest, "designation": named["seq"]}

        problems, manifest_hash = _folder_problems(state, version)
        if problems:
            return ledger.append("delivery_checked", **base, outcome="failed",
                                 problems=problems, task=None, area=None, run=None)["data"]
        ledger.append("delivery_frozen", **base, manifest_sha256=manifest_hash)

        fresh = Path(tempfile.mkdtemp(prefix="hb-trial-"))
        try:
            copy = fresh / "harness"
            shutil.copytree(state.repo.path(version), copy, symlinks=True)
            make_read_only(copy)
            runner = Runner(state, TRIAL_AREA, scenario.practice, gateway=gateway, pool=POOL)
            record = runner.run(task, version, harness=copy)
        finally:
            remove_tree(fresh)

        run, score = record["run_result"], record["score_result"]
        if record["invalid"]["verdict"] == "invalid":
            outcome = "invalid"
            problems = ["the trial run met the invalid-run condition: "
                        + record["invalid"].get("reason", "")]
        else:
            for name, r in (("run", run), ("score", score)):
                if r["timed_out"]:
                    problems.append(f"the {name} command ran into its time limit")
                elif r["exit_code"] != 0:
                    problems.append(f"the {name} command ended with exit code {r['exit_code']}")
            outcome = "failed" if problems else "passed"
        return ledger.append("delivery_checked", **base, outcome=outcome, problems=problems,
                             task=task, area=TRIAL_AREA, run=record["run"],
                             manifest_sha256=manifest_hash)["data"]
