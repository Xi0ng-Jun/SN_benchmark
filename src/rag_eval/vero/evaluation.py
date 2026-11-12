"""Evaluation of target commits through VeRO's own task code, cached per (commit, task, attempt).

Each runner call is one subprocess of the agent venv's interpreter on one checkout and one
attempt index (``runner_script.py``). Records are cached in ``cache_dir/<sha>/<task>/a<n>.json``;
an existing attempt is never repeated, concurrent requests for the same attempt wait for the
one in flight.

Failure classes:
- a sample whose grader failed (``eval_error``, ``GRADER_ERROR``, no score, an evaluation-stage
  exception) is infrastructure: not cached, retried ``infra_retry`` times, then left missing
  (the loop counts it as dropped);
- an agent error during inference (exception, timeout, max turns) is the agent's behaviour:
  VeRO grades it (normally 0) and it is cached;
- a whole-batch crash (import or run) is the candidate's fault when ``blame(sha, traceback)``
  says so (the traceback runs through a writable file of a non-seed commit): every task is
  cached with score 0; otherwise it is infrastructure and raises EvaluationUnavailable after
  the retries.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from rag_eval.optimizer.adapter import BudgetExhausted, EvaluationCancelled, EvaluationUnavailable

log = logging.getLogger(__name__)

RUNNER = Path(__file__).with_name("runner_script.py")
SPLITS = ("train", "validation", "test")


def task_id(split: str, index: int) -> str:
    return f"{split}-{index:04d}"


def parse_task_id(tid: str) -> tuple[str, int]:
    split, _, index = tid.rpartition("-")
    if split not in SPLITS or not index.isdigit():
        raise ValueError(f"not a VeRO task id: {tid!r}")
    return split, int(index)


def est_tokens(record: dict) -> int:
    """Rough token count of one execution (characters of the trace and output / 4)."""
    text = json.dumps(record.get("execution_trace"), ensure_ascii=False, default=str)
    text += json.dumps(record.get("output"), ensure_ascii=False, default=str)
    return max(1, len(text) // 4)


def answered(record: dict) -> bool:
    out = record.get("output")
    if record.get("error") or out is None:
        return False
    if isinstance(out, str):
        return bool(out.strip())
    if isinstance(out, list):
        return any(o is not None for o in out)
    return True


def infra_failure(record: dict) -> Optional[str]:
    if record.get("exception"):
        return "evaluation exception: " + record["exception"].strip().splitlines()[-1][:300]
    if record.get("eval_error"):
        return f"grader error: {str(record['eval_error'])[:300]}"
    if record.get("feedback") == "GRADER_ERROR":
        return "grader error"
    if record.get("score") is None:
        return "no score"
    return None


class Evaluator:
    def __init__(self, *, python: str, dataset_path: str, module: str, task: str, import_root: str,
                 cache_dir: str | Path, scratch_dir: str | Path, eval_tree: Callable[[str], Path],
                 task_params: Optional[dict] = None, sample_concurrency: int = 8, sample_timeout: int = 300,
                 call_timeout: float = 3600, infra_retry: int = 1, max_parallel: int = 3,
                 blame: Optional[Callable[[str, str], bool]] = None, env: Optional[dict] = None,
                 extra_sys_path: tuple = ()):
        self.python, self.dataset_path, self.module, self.task = python, dataset_path, module, task
        self.import_root, self.task_params = import_root, dict(task_params or {})
        self.cache_dir, self.scratch_dir = Path(cache_dir), Path(scratch_dir)
        self.eval_tree, self.blame = eval_tree, blame or (lambda sha, tb: False)
        self.sample_concurrency, self.sample_timeout, self.call_timeout = sample_concurrency, sample_timeout, call_timeout
        self.infra_retry, self.env, self.extra_sys_path = infra_retry, env, [str(x) for x in extra_sys_path]
        self._slots = threading.BoundedSemaphore(max_parallel)
        self._lock = threading.Lock()
        self._inflight: dict[tuple, threading.Event] = {}
        self._procs: set = set()
        self._cancelled = False
        self.calls = 0                 # runner subprocesses started (this process)
        self.executions = 0            # sample executions started (this process)
        # Execution budget (VeRO-style, matched to GEPA): sample executions on budget_splits, counted
        # from the cache at first use (survives resume) plus every execution started afterwards
        # (retries of unscored samples included; a runner call that returns no sample is not an
        # execution). None = unlimited.
        self.budget: Optional[int] = None
        self.budget_splits: tuple = ("train", "validation")
        self._spent: Optional[int] = None

    def set_budget(self, budget: Optional[int], splits: tuple = ("train", "validation")) -> None:
        self.budget, self.budget_splits, self._spent = budget, tuple(splits), None

    def _budgeted(self, tid: str) -> bool:
        return parse_task_id(tid)[0] in self.budget_splits

    def spent(self) -> int:
        """Budgeted sample executions so far (cached records at first use + executions started since)."""
        with self._lock:
            return self._spent_locked()

    def _spent_locked(self) -> int:
        if self._spent is None:
            self._spent = sum(1 for f in self.cache_dir.glob("*/*/a*.json") if self._budgeted(f.parent.name)) \
                if self.cache_dir.exists() else 0
        return self._spent

    # -- control -----------------------------------------------------------

    def set_parallel(self, n: int) -> None:
        self._slots = threading.BoundedSemaphore(max(1, n))

    def cancel(self) -> None:
        self._cancelled = True
        with self._lock:
            procs = list(self._procs)
        for proc in procs:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

    def check(self) -> None:
        if self._cancelled:
            raise EvaluationCancelled("evaluation cancelled")

    # -- cache -------------------------------------------------------------

    def path(self, sha: str, tid: str, attempt: int) -> Path:
        return self.cache_dir / sha / tid / f"a{attempt}.json"

    def load(self, sha: str, tid: str, attempt: int) -> Optional[dict]:
        p = self.path(sha, tid, attempt)
        if not p.exists():
            return None
        return json.loads(p.read_text())

    def _store(self, sha: str, attempt: int, record: dict) -> None:
        p = self.path(sha, record["task_id"], attempt)
        p.parent.mkdir(parents=True, exist_ok=True)
        record = dict(record, attempt=attempt, commit=sha, est_tokens=est_tokens(record), answered=answered(record))
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False))
        tmp.replace(p)

    # -- evaluation --------------------------------------------------------

    def ensure(self, sha: str, task_ids: list[str], k: int) -> None:
        """Make attempts 0..k-1 of *sha* exist for *task_ids* where possible (missing = infra failure)."""
        self.check()
        mine: dict[int, list[str]] = {}
        theirs: list[threading.Event] = []
        events = {}
        with self._lock:
            for a in range(k):
                for t in task_ids:
                    if self.path(sha, t, a).exists():
                        continue
                    key = (sha, t, a)
                    if key in self._inflight:
                        theirs.append(self._inflight[key])
                    else:
                        events[key] = self._inflight[key] = threading.Event()
                        mine.setdefault(a, []).append(t)
            if self.budget is not None and mine:
                new = sum(self._budgeted(t) for ids in mine.values() for t in ids)
                if new and self._spent_locked() + new > self.budget:
                    for key, ev in events.items():
                        self._inflight.pop(key, None)
                        ev.set()
                    raise BudgetExhausted(f"execution budget: {self._spent} of {self.budget} sample executions "
                                          f"used, this request needs {new} more")
                self._spent += new          # reserved at admission, so concurrent requests cannot overshoot
        try:
            if mine:
                with ThreadPoolExecutor(max_workers=len(mine), thread_name_prefix="vero-eval") as pool:
                    for f in [pool.submit(self._attempt, sha, a, ids) for a, ids in mine.items()]:
                        f.result()
        finally:
            with self._lock:
                for key, ev in events.items():
                    self._inflight.pop(key, None)
                    ev.set()
        for ev in theirs:
            ev.wait()
        self.check()

    def _attempt(self, sha: str, attempt: int, ids: list[str]) -> None:
        todo = list(ids)
        last = None
        for round_ in range(1 + self.infra_retry):
            if not todo:
                return
            if round_ and self.budget is not None:      # infrastructure retries are executions too
                with self._lock:
                    self._spent = self._spent_locked() + sum(self._budgeted(t) for t in todo)
            out = self._call(sha, attempt, todo)
            fatal = out.get("fatal")
            if fatal:
                tb = fatal.get("traceback") or ""
                if self.blame(sha, tb):
                    log.warning("candidate %s crashed (%s): every task scores 0", sha[:12], fatal.get("phase"))
                    for t in todo:
                        self._store(sha, attempt, dict(task_id=t, score=0.0, error=f"candidate_crash ({fatal.get('phase')})",
                                                       error_traceback=tb[-5000:], output=None, execution_trace=None))
                    return
                last = f"runner {fatal.get('phase')} failure: {tb.strip().splitlines()[-1] if tb.strip() else '?'}"
                log.warning("%s (commit %s, attempt %d); retrying", last, sha[:12], attempt)
                if self.budget is not None:             # the call returned no sample: nothing was executed
                    with self._lock:
                        self._spent = self._spent_locked() - sum(self._budgeted(t) for t in todo)
                continue
            failed = {}
            for rec in out.get("samples", []):
                why = infra_failure(rec)
                if why:
                    failed[rec["task_id"]] = why
                else:
                    self._store(sha, attempt, rec)
            todo = [t for t in todo if not self.path(sha, t, attempt).exists()]
            if todo:
                last = f"{len(todo)} task(s) without a score, e.g. {next(iter(failed.values()), 'not returned')}"
                log.warning("commit %s attempt %d: %s", sha[:12], attempt, last)
        if todo and last and last.startswith("runner"):
            raise EvaluationUnavailable(f"VeRO evaluation of {sha[:12]} failed: {last}")

    def _call(self, sha: str, attempt: int, ids: list[str]) -> dict:
        tree = self.eval_tree(sha)
        samples = []
        for t in ids:
            split, index = parse_task_id(t)
            samples.append(dict(task_id=t, split=split, index=index))
        params = dict(self.task_params)
        req = dict(module=self.module, task=self.task, dataset_path=str(self.dataset_path), commit=sha,
                   samples=samples, task_params=params, max_concurrency=self.sample_concurrency,
                   sample_timeout=self.sample_timeout, sys_path=[str((tree / self.import_root).resolve()), *self.extra_sys_path])
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        with self._slots:
            self.check()
            with tempfile.TemporaryDirectory(dir=self.scratch_dir, prefix=f"run-{sha[:8]}-a{attempt}-") as tmp:
                tmp = Path(tmp)
                (tmp / "request.json").write_text(json.dumps(req))
                cmd = [self.python, "-I", str(RUNNER), "run", str(tmp / "request.json"), str(tmp / "out.json")]
                env = dict(os.environ if self.env is None else self.env)
                started = time.monotonic()
                with open(tmp / "stderr.log", "w") as err:
                    proc = subprocess.Popen(cmd, cwd=tmp, stdout=err, stderr=subprocess.STDOUT, env=env,
                                            start_new_session=True)
                    with self._lock:
                        self._procs.add(proc)
                        self.calls += 1
                        self.executions += len(ids)
                    try:
                        proc.wait(timeout=self.call_timeout)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                    finally:
                        with self._lock:
                            self._procs.discard(proc)
                self.check()
                log.info("evaluated %s attempt %d on %d task(s) in %.0f s (exit %s)", sha[:12], attempt, len(ids),
                         time.monotonic() - started, proc.returncode)
                if (tmp / "out.json").exists():
                    return json.loads((tmp / "out.json").read_text())
                tail = (tmp / "stderr.log").read_text(errors="replace")[-3000:]
                return dict(samples=[], fatal=dict(phase="process", traceback=f"exit {proc.returncode}\n{tail}"))


def describe(python: str, dataset_path: str, extra_sys_path: tuple = ()) -> dict[str, int]:
    """Split sizes of a VeRO dataset (read by the agent venv's interpreter)."""
    proc = subprocess.run([python, "-I", str(RUNNER), "describe", str(dataset_path), *map(str, extra_sys_path)],
                          capture_output=True, text=True,
                          timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"cannot read dataset {dataset_path}: {proc.stderr.strip()[-2000:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


if __name__ == "__main__":       # debugging aid: python -m rag_eval.vero.evaluation <python> <dataset>
    print(describe(sys.argv[1], sys.argv[2]))
