"""One development session: prepare the sandbox, serve its requests, record it.

The developer agent gets a fixed brief (no domain content), a writable copy of the
harness in its workspace, and the ``hb`` command. Its model calls go through the
gateway on a key bound to the developer model, in the ``dev`` budget pool shared
with the runs it requests. A session ends when the agent's command exits or the
remaining development time (``limits.dev_seconds`` minus earlier sessions) runs out.

Once an exam has been started in the state directory no further session is
accepted, so nothing of an exam can reach the development it examines (rule 6).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from .harness_repo import make_read_only, remove_tree
from .materials import Materials, Questions
from .ops import OpsServer
from .runs import Runner, result_dict
from .sandbox import ContainerSandbox, Layout, LocalSandbox, install_bin
from .state import State, exclusive

POOL = "dev"

BRIEF = """# Your job

Adapt a general agent to a set of tasks. You change the harness — everything
around the model that decides how the solving agent works: instructions it reads,
notes it can look up, tools and scripts, automatic checks, time keeping, finishing
steps, and (if the product is open source and allowed) the product's own code. The
model does not change. After you deliver, the outside program runs your harness on
final exam tasks you never see, scores them with the official scoring, and compares
the result with the general agent as it was. Your cost is model tokens and time.

# What you have

- {materials}: material from the researcher, in numbered folders; answers to your
  questions arrive here as new folders. Read-only.
- {tasks}: the practice tasks ({practice}), one folder each, possibly with
  reference answers. Read-only. The exam tasks are not here and never will be.
- {records}: every run, scoring and environment command you asked for, complete:
  runs/rNNNN/out (the run's whole output = its product), runs/rNNNN/score (the raw
  output of the official scoring), logs, usage.json, record.json. Read-only.
  {records}/trial holds the trial runs of delivery checks, in the same form.
- {versions}: every harness version (v0 = the general agent as it was). Read-only.
- {workspace}: yours. {workspace}/harness is a writable copy of the harness; keep
  notes, scripts and anything else here.

Operations (`hb --help` for details); every reply is JSON:

- `hb run TASK` — run the harness as it is now in {workspace}/harness (stored as a
  new version) or `--version V` on a practice task, then score it. Uses model
  budget. Runs can take long: `hb run TASK --no-wait` and later `hb wait ID`.
  `--seconds N` stops this one run after N seconds (never more than the limit for
  one run) when the first part of a run is all you need; it is scored as it stands.
- `hb score TASK DIR` — official scoring of any product directory in the
  workspace (e.g. a copy of an earlier run's out/, or one you built). No model.
- `hb enter [--work DIR] TASK -- COMMAND` — run a command in a practice task's
  environment without starting an agent; DIR (default {workspace}/enter/TASK) is
  the exchange directory. No model.
- `hb ask --question Q --why W --assumption A` — ask the researcher.
- `hb deliver [--version V]` — name the delivery. The last one named counts.
- `hb status` — budget used and left, time left, versions, questions, deliveries
  and the result of each delivery check.

The scoring output is given to you exactly as the official scoring wrote it;
nothing reads or interprets it for you.

Limits: one run may take {run_seconds} s and use {run_budget}; the whole development
may use {dev_budget} of model budget and {dev_seconds} s. When the budget is used up,
model calls and new runs are refused. The sandbox has no network except the model.
Solver settings fixed by the researcher for every run, which the harness cannot
change: {solver_settings}.

# What to deliver

A harness folder with two things at its top level:

1. `start`, an executable: the solving agent's start command. The outside program
   puts the folder read-only into the task environment and runs `start` there with
   HB_MODEL_URL, HB_MODEL_KEY and HB_MODEL set (only that model is allowed).
   From then on, what happens is decided by what is in the folder.
2. `MANIFEST.md`: in plain words, each part of the folder: what it does; why it
   was added (which material, which answer, or what you saw in which run); what
   would happen without it.

Before the exam the delivered version is tried once on a practice task in a fresh
environment; anything that only works inside this sandbox will fail there. If the
trial fails the delivery is sent back: `hb status` shows why and where its records
are, and you may deliver again.

# Asking

Ask when something is not in the materials and you cannot find it out by runs,
or when it needs a decision by someone entitled to make it. Say what you ask, why
you cannot settle it yourself, and what you assume until answered. Asking does not
block: carry on under your assumption.

# Restarts

You may be stopped and started again. Keep everything you need to continue —
progress, open items, what each version scored — in files in {workspace}.

# Suggested habits (not rules)

1. Read the materials first; write down how you understand the task and its scoring.
2. Before changing anything, run the harness as it is once, to see that running
   and scoring work.
3. Check the scoring with products whose result you know: an empty one, a broken
   one, the same one scored twice.
4. Try cheap things first (score, enter) before spending a whole run.
5. After each run, read the logs themselves, not only the score.
6. Before each run, write down what you expect; compare afterwards.
7. Keep the score of every version you tested; deliver the best tested one.
8. Write progress and open items into the workspace regularly.
"""


class SessionRefused(Exception):
    pass


def make_sandbox(scenario, kind: str):
    dev = scenario.developer
    if kind == "local":
        return LocalSandbox(python=dev.get("python"))
    if kind == "container":
        if not dev.get("image"):
            raise SessionRefused("developer.image is needed for a container sandbox")
        return ContainerSandbox(dev["image"], python=dev.get("python"),
                                memory=dev.get("memory"), cpus=dev.get("cpus"),
                                pids_limit=dev.get("pids_limit"))
    raise ValueError(f"unknown sandbox kind {kind!r}")


def _caps(caps: dict) -> str:
    return ", ".join(f"{v} {k}" for k, v in caps.items()) or "no cap"


def _pairs(settings: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in settings.items()) or "none"


class DevSession:
    def __init__(self, state: State, gateway, sandbox, *, command: list[str] | None = None):
        self.state, self.gateway, self.sandbox = state, gateway, sandbox
        self.scenario = state.scenario
        self.materials = Materials(state.root / "materials", state.ledger)
        self.questions = Questions(state.root / "questions", state.ledger, self.materials)
        self.command = list(command or self.scenario.developer["command"])

    def layout(self) -> Layout:
        r = self.state.root
        return Layout(bin=r / "sandbox" / "bin", brief=r / "sandbox" / "BRIEF.md",
                      requests=r / "ops" / "requests", responses=r / "ops" / "responses",
                      materials=r / "materials", tasks=r / "sandbox" / "tasks",
                      records=r / "dev", versions=self.state.repo.versions_dir,
                      workspace=r / "workspace")

    def prepare(self) -> Layout:
        lay = self.layout()
        install_bin(lay.bin)
        remove_tree(lay.tasks)
        lay.tasks.mkdir(parents=True)
        for tid in self.scenario.practice:
            files = self.scenario.tasks[tid].files
            dst = lay.tasks / tid
            if files is not None:
                shutil.copytree(files, dst, symlinks=True)
            else:
                dst.mkdir()
        make_read_only(lay.tasks)
        for d in (lay.requests, lay.responses, lay.records, lay.workspace):
            d.mkdir(parents=True, exist_ok=True)
        harness = lay.workspace / "harness"
        if not harness.exists():
            shutil.copytree(self.state.repo.path("v0"), harness, symlinks=True)
            for dirpath, _, filenames in os.walk(harness):
                os.chmod(dirpath, 0o755)
                for f in filenames:
                    p = os.path.join(dirpath, f)
                    if not os.path.islink(p):
                        os.chmod(p, os.stat(p).st_mode | 0o200)
        lim = self.scenario.limits
        view = self.sandbox.view(lay)
        lay.brief.write_text(BRIEF.format(
            practice=", ".join(self.scenario.practice),
            run_seconds=lim["run_seconds"] or "unlimited", run_budget=_caps(lim["run_budget"]),
            dev_budget=_caps(lim["dev_budget"]), dev_seconds=lim["dev_seconds"] or "unlimited",
            solver_settings=_pairs(self.scenario.solver_settings), **view))
        return lay

    def time_used(self) -> float:
        return sum(e["data"]["seconds"] for e in self.state.ledger.entries("dev_session_finished"))

    def run(self) -> dict:
        """One session from start to end; SessionRefused or state.Busy if it may not start."""
        with exclusive(self.state.root, "development session"):
            if self.state.ledger.last("exam_started") is not None:
                raise SessionRefused("an exam has been started in this state directory; "
                                     "its development cannot continue")
            return self._run()

    def _run(self) -> dict:
        lim, ledger = self.scenario.limits, self.state.ledger
        used = self.time_used()
        remaining = None if lim["dev_seconds"] is None else lim["dev_seconds"] - used
        if remaining is not None and remaining <= 0:
            raise SessionRefused("development time is used up")
        self.gateway.set_pool_caps(POOL, lim["dev_budget"])
        over = self.gateway.pool_exceeded(POOL)
        if over:
            raise SessionRefused("development budget is used up: " + "; ".join(over))
        lay = self.prepare()
        sessions = self.state.root / "sessions"
        sessions.mkdir(exist_ok=True)
        sid = f"s{len(ledger.entries('dev_session_started')) + 1:04d}"
        logs = sessions / sid
        logs.mkdir()
        sock_dir = None
        if self.sandbox.needs_socket:
            sock_dir = Path(tempfile.mkdtemp(prefix="hb-sock-"))
            self.gateway.start_unix(sock_dir / "model.sock")
            lay.socket_dir = sock_dir
        model = self.scenario.models["developer"]
        key = self.gateway.issue(f"developer/{sid}", POOL, model)
        runner = Runner(self.state, "dev", self.scenario.practice, gateway=self.gateway,
                        pool=POOL)
        t0 = time.monotonic()

        def time_status():
            this = time.monotonic() - t0
            return {"dev_time": {"limit_seconds": lim["dev_seconds"],
                                 "used_seconds": round(used + this, 1),
                                 "remaining_seconds": None if remaining is None
                                 else round(remaining - this, 1)}}

        ops = OpsServer(runner=runner, questions=self.questions, materials=self.materials,
                        requests=lay.requests, responses=lay.responses,
                        workspace=lay.workspace, view=self.sandbox.view(lay),
                        parallel_runs=lim["parallel_runs"], status_extra=time_status)
        ledger.append("dev_session_started", session=sid, sandbox=self.sandbox.kind,
                      command=self.command, model=model, remaining_seconds=remaining)
        ops.start()
        error = None
        try:
            out = self.sandbox.run(lay, self.command, {"HB_MODEL_KEY": key, "HB_MODEL": model},
                                   self.gateway.url, logs, remaining,
                                   hidden_env=(self.scenario.models["upstream_key_env"],))
        except BaseException as e:
            error = e
            raise
        finally:
            ops.stop()  # waits for requests still under way
            usage = self.gateway.close(key)
            if sock_dir is not None:
                self.gateway.stop_unix()
                remove_tree(sock_dir)
            if error is not None:
                ledger.append("dev_session_finished", session=sid, error=repr(error),
                              seconds=round(time.monotonic() - t0, 3), usage=usage)
        r = out["result"]
        record = {"session": sid, **out["detail"], "agent_result": result_dict(r),
                  "seconds": r.seconds, "usage": usage}
        (logs / "record.json").write_text(json.dumps(record, indent=1))
        ledger.append("dev_session_finished", **record)
        return record
