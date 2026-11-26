"""Runs and scorings, fully recorded (rules 2 and 3).

A runner works in one area of the state directory (for example ``dev`` for the
developer agent's requests) and accepts only the tasks it was given. Layout:

    <area>/runs/r0001/    out/ (the run's output = its product), score/ (raw score
                          output), run.*, score.*, invalid.* (stdout/stderr),
                          usage.json (model use, once there is a gateway), record.json
    <area>/scores/s0001/  product/ (a copy of what was scored), score/, score.*, record.json
    <area>/enters/e0001/  enter.stdout, enter.stderr, record.json

With a gateway, each run gets its own credential for the solver model, capped by
``limits.run_budget``; its usage goes into the record. A run is refused before it
starts when the runner's budget pool is used up.

Every run is scored, whatever its exit code. Whether a run is invalid is decided
only by the scenario's invalid-run command. Nothing here reads the score.
Finished directories are made read-only.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .harness_repo import make_read_only, tree_hash
from .scenario import INVALID_EXIT, CommandResult
from .state import State


class RunRefused(Exception):
    pass


@dataclass(frozen=True)
class ModelAccess:
    url: str
    key: str
    model: str


NO_MODEL = ModelAccess("", "", "")


def result_dict(r: CommandResult) -> dict:
    return {"exit_code": r.exit_code, "timed_out": r.timed_out, "started": r.started,
            "seconds": r.seconds, "error": r.error}


class Runner:
    def __init__(self, state: State, area: str, tasks: Iterable[str], gateway=None,
                 pool: str | None = None):
        self.state = state
        self.scenario = state.scenario
        self.ledger = state.ledger
        self.area = area
        self.dir = state.root / area
        self.tasks = frozenset(tasks)
        unknown = self.tasks - set(self.scenario.tasks)
        if unknown:
            raise ValueError(f"unknown tasks {sorted(unknown)}")
        self.gateway = gateway
        self.pool = pool or area

    def run_dir(self, run_id: str) -> Path:
        return self.dir / "runs" / run_id

    def score_dir(self, score_id: str) -> Path:
        return self.dir / "scores" / score_id

    def run(self, task: str, version: str) -> dict:
        """Run harness ``version`` on ``task``, score it, apply the invalid-run condition."""
        self._allowed(task)
        meta = self.state.repo.get(version)
        harness = self.state.repo.path(version)
        if self.gateway is not None:
            over = self.gateway.pool_exceeded(self.pool)
            if over:
                reason = f"budget of pool {self.pool!r} reached: " + "; ".join(over)
                self.ledger.append("run_refused", area=self.area, task=task, version=version,
                                   reason=reason)
                raise RunRefused(reason)
        d = self._new_dir("runs", "r")
        base = {"area": self.area, "run": d.name, "task": task, "version": version,
                "harness_hash": meta["hash"]}
        self.ledger.append("run_started", **base)
        limits = self.scenario.limits
        usage = None
        try:
            key, access = None, NO_MODEL
            if self.gateway is not None:
                model = self.scenario.models["solver"]
                key = self.gateway.issue(f"{self.area}/runs/{d.name}", self.pool, model,
                                         limits["run_budget"])
                access = ModelAccess(self.gateway.url, key, model)
            try:
                run = self.scenario.run(task, harness, d / "out", d, model_url=access.url,
                                        model_key=access.key, model=access.model,
                                        timeout=limits["run_seconds"])
            finally:
                if key is not None:
                    usage = self.gateway.close(key)
                    (d / "usage.json").write_text(json.dumps(usage, indent=1))
            score = self.scenario.score(task, d / "out", d / "score", d,
                                        timeout=limits["score_seconds"])
            invalid = self._invalid(task, d)
        except BaseException as e:
            make_read_only(d)
            self.ledger.append("run_aborted", **base, error=repr(e), usage=usage)
            raise
        record = {**base, "run_result": result_dict(run), "score_result": result_dict(score),
                  "invalid": invalid, "usage": usage}
        (d / "record.json").write_text(json.dumps(record, indent=1))
        make_read_only(d)
        self.ledger.append("run_finished", **record)
        return record

    def score(self, task: str, product: str | Path) -> dict:
        """Score a copy of ``product`` (a directory) for ``task``."""
        self._allowed(task)
        product = Path(product)
        if not product.is_dir():
            raise RunRefused(f"{product}: not a directory")
        d = self._new_dir("scores", "s")
        shutil.copytree(product, d / "product", symlinks=True)
        base = {"area": self.area, "score": d.name, "task": task, "source": str(product),
                "product_hash": tree_hash(d / "product")}
        self.ledger.append("score_started", **base)
        try:
            r = self.scenario.score(task, d / "product", d / "score", d,
                                    timeout=self.scenario.limits["score_seconds"])
        except BaseException as e:
            make_read_only(d)
            self.ledger.append("score_aborted", **base, error=repr(e))
            raise
        record = {**base, "score_result": result_dict(r)}
        (d / "record.json").write_text(json.dumps(record, indent=1))
        make_read_only(d)
        self.ledger.append("score_finished", **record)
        return record

    def enter(self, task: str, command: list[str], work: Path) -> dict:
        """Run ``command`` in ``task``'s environment with ``work`` as the exchange directory."""
        self._allowed(task)
        d = self._new_dir("enters", "e")
        base = {"area": self.area, "enter": d.name, "task": task, "command": list(command),
                "work": str(work)}
        self.ledger.append("enter_started", **base)
        try:
            r = self.scenario.enter(task, list(command), work, d,
                                    timeout=self.scenario.limits["enter_seconds"])
        except BaseException as e:
            make_read_only(d)
            self.ledger.append("enter_aborted", **base, error=repr(e))
            raise
        record = {**base, "enter_result": result_dict(r)}
        (d / "record.json").write_text(json.dumps(record, indent=1))
        make_read_only(d)
        self.ledger.append("enter_finished", **record)
        return record

    def enter_dir(self, enter_id: str) -> Path:
        return self.dir / "enters" / enter_id

    def _invalid(self, task: str, d: Path) -> dict:
        r = self.scenario.check_invalid(task, d, d,
                                        timeout=self.scenario.limits["score_seconds"])
        if r is None:
            return {"verdict": "no_condition"}
        out = {"check_result": result_dict(r)}
        if r.exit_code == 0 and not r.timed_out:
            return {"verdict": "valid", **out}
        if r.exit_code == INVALID_EXIT and not r.timed_out:
            reason = r.stdout.read_text(errors="replace").strip()
            return {"verdict": "invalid", "reason": reason, **out}
        return {"verdict": "check_error", **out}

    def _allowed(self, task: str) -> None:
        if task not in self.tasks:
            raise RunRefused(f"task {task!r} is not available in area {self.area!r}")

    def _new_dir(self, sub: str, prefix: str) -> Path:
        parent = self.dir / sub
        parent.mkdir(parents=True, exist_ok=True)
        n = sum(1 for _ in parent.iterdir()) + 1
        while True:
            p = parent / f"{prefix}{n:04d}"
            try:
                p.mkdir()
                return p
            except FileExistsError:
                n += 1
