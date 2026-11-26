"""Serve the developer agent's requests (the five operations, plus status).

The sandbox's ``hb`` command drops ``requests/<id>.json``; this server takes it,
handles it in its own thread, and writes ``responses/<id>.json`` (read-only for the
sandbox). Every request and its outcome go on the ledger, refused ones included.
Paths in requests are relative to the workspace and must stay inside it.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
from pathlib import Path

from .harness_repo import HarnessError
from .materials import MaterialsError, Materials, Questions
from .runs import Runner, RunRefused
from .scenario import ScenarioError

REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}")
POLL_SECONDS = 0.2
INLINE_BYTES = 64 * 1024  # larger outputs are only in the record files


class OpError(Exception):
    pass


def _clip(text: str) -> str:
    if len(text) <= INLINE_BYTES:
        return text
    return text[:INLINE_BYTES] + f"\n[... {len(text) - INLINE_BYTES} more characters in the file]"


def _small_files(d: Path) -> dict:
    """Text files directly under ``d`` small enough to return inline, as they are."""
    out = {}
    if d.is_dir():
        for p in sorted(d.iterdir()):
            if p.is_file() and p.stat().st_size <= INLINE_BYTES:
                try:
                    out[p.name] = p.read_text()
                except (UnicodeDecodeError, OSError):
                    pass
    return out


class OpsServer:
    def __init__(self, *, runner: Runner, questions: Questions, materials: Materials,
                 requests: Path, responses: Path, workspace: Path, view: dict,
                 parallel_runs: int, status_extra=None):
        self.runner = runner
        self.state = runner.state
        self.ledger = runner.ledger
        self.questions, self.materials = questions, materials
        self.requests, self.responses = Path(requests), Path(responses)
        self.workspace = Path(workspace).resolve()
        self.view = view  # sandbox-side paths: records, versions, materials, workspace
        self.slots = threading.Semaphore(parallel_runs)
        self.status_extra = status_extra or (lambda: {})
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._loop: threading.Thread | None = None

    # -- lifecycle -------------------------------------------------------------------------
    def start(self) -> "OpsServer":
        self.requests.mkdir(parents=True, exist_ok=True)
        self.responses.mkdir(parents=True, exist_ok=True)
        self._loop = threading.Thread(target=self._poll, daemon=True)
        self._loop.start()
        return self

    def stop(self) -> None:
        """Stop taking requests; wait for those under way."""
        self._stop.set()
        if self._loop:
            self._loop.join()
        for t in self._threads:
            t.join()

    def _poll(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(POLL_SECONDS)
        self.poll_once()  # requests that arrived just before stop

    def poll_once(self) -> None:
        for p in sorted(self.requests.glob("*.json")):
            if p.name.startswith("."):
                continue
            try:
                raw = p.read_bytes()
                p.unlink()
            except OSError:
                continue
            t = threading.Thread(target=self._handle, args=(p.stem, raw), daemon=True)
            t.start()
            self._threads.append(t)

    # -- one request -----------------------------------------------------------------------
    def _handle(self, rid: str, raw: bytes) -> None:
        if not REQUEST_ID.fullmatch(rid):
            self.ledger.append("op_refused", id=rid[:100], error="bad request id")
            return
        try:
            req = json.loads(raw)
            op, args = req["op"], req.get("args") or {}
            if not isinstance(args, dict):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            req, op, args = None, None, {}
        self.ledger.append("op_request", id=rid, op=op, args=args)
        reply = {"id": rid, "op": op}
        try:
            if req is None:
                raise OpError("request is not JSON of the form {op, args}")
            handler = getattr(self, f"_op_{op}", None) if isinstance(op, str) else None
            if handler is None:
                raise OpError(f"unknown operation {op!r}")
            reply.update(ok=True, result=handler(args))
        except (OpError, RunRefused, HarnessError, MaterialsError, ScenarioError) as e:
            reply.update(ok=False, error=str(e))
        except Exception as e:  # report, do not take the server down
            reply.update(ok=False, error=f"internal error: {e!r}")
            traceback.print_exc()
        self.ledger.append("op_done", id=rid, op=op, ok=reply["ok"],
                           error=reply.get("error"))
        tmp = self.responses / f".tmp-{rid}"
        tmp.write_text(json.dumps(reply, indent=1))
        os.replace(tmp, self.responses / f"{rid}.json")

    def _host_path(self, rel) -> Path:
        if not isinstance(rel, str) or os.path.isabs(rel):
            raise OpError(f"path {rel!r} must be relative to the workspace")
        p = (self.workspace / rel).resolve()
        if p != self.workspace and self.workspace not in p.parents:
            raise OpError(f"path {rel!r} leaves the workspace")
        return p

    def _task(self, args) -> str:
        task = args.get("task")
        if not isinstance(task, str):
            raise OpError("missing task")
        if task not in self.runner.tasks:
            raise OpError(f"task {task!r} is not a practice task")
        return task

    def _version(self, args) -> str:
        if args.get("version") is not None and args.get("harness") is not None:
            raise OpError("give a version or a harness folder, not both")
        if args.get("version") is not None:
            v = args["version"]
            if not isinstance(v, str):
                raise OpError("version must be a string")
            self.state.repo.get(v)
            return v
        folder = self._host_path(args.get("harness", "harness"))
        return self.state.repo.save(folder, note=f"from workspace {folder.name}")["version"]

    def _view(self, kind: str, *parts: str) -> str:
        return "/".join([self.view[kind].rstrip("/"), *parts])

    # -- operations ------------------------------------------------------------------------
    def _op_run(self, args) -> dict:
        task, version = self._task(args), self._version(args)
        with self.slots:
            record = self.runner.run(task, version)
        d = self.runner.run_dir(record["run"])
        return {"record": record, "run_dir": self._view("records", "runs", record["run"]),
                "score_output": _small_files(d / "score")}

    def _op_score(self, args) -> dict:
        task = self._task(args)
        product = self._host_path(args.get("product"))
        with self.slots:
            record = self.runner.score(task, product)
        d = self.runner.score_dir(record["score"])
        return {"record": record,
                "score_dir": self._view("records", "scores", record["score"]),
                "score_output": _small_files(d / "score")}

    def _op_enter(self, args) -> dict:
        task = self._task(args)
        command = args.get("command")
        if not isinstance(command, list) or not command or not all(
                isinstance(a, str) for a in command):
            raise OpError("command must be a non-empty list of strings")
        work = self._host_path(args.get("work", f"enter/{task}"))
        work.mkdir(parents=True, exist_ok=True)
        record = self.runner.enter(task, command, work)
        d = self.runner.enter_dir(record["enter"])
        return {"record": record, "exit_code": record["enter_result"]["exit_code"],
                "timed_out": record["enter_result"]["timed_out"],
                "stdout": _clip((d / "enter.stdout").read_text(errors="replace")),
                "stderr": _clip((d / "enter.stderr").read_text(errors="replace")),
                "enter_dir": self._view("records", "enters", record["enter"])}

    def _op_ask(self, args) -> dict:
        return self.questions.ask(args.get("question"), args.get("why"),
                                  args.get("assumption"))

    def _op_deliver(self, args) -> dict:
        version = self._version(args)
        if not (self.state.repo.path(version) / "MANIFEST.md").is_file():
            raise OpError(f"{version} has no MANIFEST.md; a delivery needs one")
        meta = self.state.repo.get(version)
        self.ledger.append("delivery_designated", version=version, hash=meta["hash"])
        return {"version": version, "hash": meta["hash"]}

    def _op_status(self, args) -> dict:
        gw = self.runner.gateway
        caps = self.state.scenario.limits["dev_budget"]
        used = gw.pool_usage(self.runner.pool) if gw else {}
        delivered = self.ledger.last("delivery_designated")
        return {
            "practice_tasks": sorted(self.runner.tasks),
            "versions": self.state.repo.versions(),
            "budget": {"caps": caps, "used": used,
                       "remaining": {k: cap - used.get(k, 0) for k, cap in caps.items()}},
            "questions": [{**q, "answers": [self._view("materials", m) for m in q["answers"]]}
                          for q in self.questions.list()],
            "materials": [self._view("materials", m["material"])
                          for m in self.materials.entries()],
            "delivered": delivered["data"] if delivered else None,
            "now": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **self.status_extra(),
        }
