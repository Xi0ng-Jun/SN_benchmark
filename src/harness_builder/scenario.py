"""Read a scenario directory and call its three commands.

A scenario directory holds ``scenario.json`` and the commands it names. This
module passes paths in and keeps what comes out; it never interprets what the
commands write. The only reading of a command's output is ``score_value``,
which the final exam uses to record and aggregate scores. See
docs/scenario-interface.md.
"""
from __future__ import annotations

import json
import math
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .gateway import CAP_KEYS

CONFIG_NAME = "scenario.json"
COMMANDS = ("run", "score", "enter")
OPTIONAL_COMMANDS = ("invalid",)
START_NAME = "start"
TOP_LEVEL_KEYS = {"tasks", "split", "commands", "score", "initial_harness", "limits", "models",
                  "developer", "exam"}
OPTIONAL_TOP_LEVEL_KEYS = {"solver_settings"}
LIMIT_KEYS = ("run_seconds", "score_seconds", "enter_seconds", "run_budget", "dev_budget",
              "dev_seconds", "parallel_runs")
DEVELOPER_OPTIONAL = {"image": str, "python": str, "memory": str, "cpus": (int, float),
                      "pids_limit": int}
MODEL_KEYS = {"solver": str, "developer": str, "upstream_url": str, "upstream_key_env": str}
MODEL_OPTIONAL = {"upstream_auth_header": str, "upstream_auth_prefix": str,
                  "listen_host": str, "listen_port": int, "url_host": str}

EXAM_KEYS = ("compare", "repeats", "aggregate", "failure_score", "invalid_reruns", "uses")
AGGREGATES = ("mean", "median", "min", "max", "sum")
DELIVERED = "delivered"  # the name of the delivered harness among the compared objects
SETTING_PREFIX = "HB_SETTING_"  # solver settings reach the run command as HB_SETTING_<name>

# Exit code by which the invalid-run check says "this run is invalid".
INVALID_EXIT = 10

# Implementation detail, not a method parameter: how long a command gets to
# exit after SIGTERM before its whole process group receives SIGKILL.
KILL_GRACE_SECONDS = 10

_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SETTING_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class ScenarioError(Exception):
    """The scenario directory does not meet the interface."""


@dataclass(frozen=True)
class Task:
    id: str
    group: str
    files: Path | None  # what the developer agent may read when this is a practice task


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    exit_code: int | None  # None: the command could not be started (see ``error``)
    timed_out: bool
    started: str  # UTC, ISO 8601
    seconds: float
    stdout: Path
    stderr: Path
    error: str | None = None

    def to_dict(self) -> dict:
        return {"argv": list(self.argv), "exit_code": self.exit_code,
                "timed_out": self.timed_out, "started": self.started,
                "seconds": self.seconds, "stdout": str(self.stdout),
                "stderr": str(self.stderr), "error": self.error}


@dataclass(frozen=True)
class Scenario:
    root: Path
    config: dict
    tasks: dict[str, Task]
    practice: tuple[str, ...]
    exam: tuple[str, ...]
    commands: dict[str, tuple[str, ...]]
    initial_harness: Path
    limits: dict
    models: dict
    developer: dict
    exam_settings: dict    # how the final exam is held; ``compare`` maps names to folders
    solver_settings: dict  # fixed by the researcher; passed to every run, never to anything else

    def run(self, task: str, harness: Path, out: Path, logs: Path, *, model_url: str,
            model_key: str, model: str, timeout: float | None = None) -> CommandResult:
        """Run ``harness``'s start command on ``task``; product and logs go to ``out``.

        The solver settings always come from the configuration; no caller can vary them.
        """
        env = {"HB_TASK": self._task(task), "HB_HARNESS": str(_existing_dir(harness)),
               "HB_MODEL_URL": model_url, "HB_MODEL_KEY": model_key, "HB_MODEL": model}
        env.update({SETTING_PREFIX + k: v for k, v in self.solver_settings.items()})
        if timeout is not None:  # so the command can end the agent itself before it is stopped
            env["HB_SECONDS"] = repr(float(timeout))
        env["HB_OUT"] = str(_mkdir(out))  # checks above run before the directory is made
        return self._call("run", [], logs, timeout, env)

    def compare_folder(self, name: str) -> Path:
        """The harness folder of a compared object named in ``exam.compare``, checked."""
        return _harness_at(self.exam_settings["compare"][name], f"exam: compare: {name}")

    def score(self, task: str, product: Path, out: Path, logs: Path, *,
              timeout: float | None = None) -> CommandResult:
        """Apply the official scoring to ``product``; its raw output goes to ``out``."""
        env = {"HB_TASK": self._task(task), "HB_PRODUCT": str(_existing_dir(product)),
               "HB_OUT": str(_mkdir(out))}
        return self._call("score", [], logs, timeout, env)

    def enter(self, task: str, command: list[str], work: Path, logs: Path, *,
              timeout: float | None = None) -> CommandResult:
        """Execute ``command`` in ``task``'s environment without starting an agent."""
        if not isinstance(command, list) or not command or not all(
                isinstance(a, str) for a in command):
            raise ScenarioError("enter: command must be a non-empty list of strings")
        env = {"HB_TASK": self._task(task), "HB_WORK": str(_mkdir(work))}
        return self._call("enter", command, logs, timeout, env)

    def check_invalid(self, task: str, run_dir: Path, logs: Path, *,
                      timeout: float | None = None) -> CommandResult | None:
        """Apply the researcher's invalid-run condition to a run record; None if there is none."""
        if "invalid" not in self.commands:
            return None
        env = {"HB_TASK": self._task(task), "HB_RUN": str(_existing_dir(run_dir))}
        return self._call("invalid", [], logs, timeout, env)

    def score_value(self, score_out: Path) -> float | None:
        """The number at the configured place in a score command's raw output, or None."""
        spec = self.config["score"]
        try:
            text = (Path(score_out) / spec["file"]).read_text()
        except (OSError, UnicodeDecodeError):
            return None
        if "key" not in spec:
            try:
                value = float(text.strip())
            except ValueError:
                return None
        else:
            try:
                value = json.loads(text)
            except ValueError:
                return None
            for k in spec["key"].split("."):
                if not isinstance(value, dict) or k not in value:
                    return None
                value = value[k]
            if not isinstance(value, (bool, int, float)):
                return None
            value = float(value)
        return value if math.isfinite(value) else None

    def _task(self, task: str) -> str:
        if task not in self.tasks:
            raise ScenarioError(f"unknown task {task!r}")
        return task

    def _call(self, name: str, extra_argv: list[str], logs: Path, timeout: float | None,
              hb_env: dict[str, str]) -> CommandResult:
        secret = self.models["upstream_key_env"]
        env = {k: v for k, v in os.environ.items() if not k.startswith("HB_") and k != secret}
        env.update(hb_env)
        env["HB_SCENARIO"] = str(self.root)
        argv = (*self.commands[name], *extra_argv)
        return call(argv, cwd=self.root, env=env, logs=logs, name=name, timeout=timeout)


def call(argv: tuple[str, ...] | list[str], *, cwd: Path, env: dict[str, str], logs: Path,
         name: str, timeout: float | None) -> CommandResult:
    """Run ``argv`` in its own process group, stdout/stderr to ``logs/<name>.stdout|stderr``.

    On timeout the group gets SIGTERM, then SIGKILL after ``KILL_GRACE_SECONDS``.
    Processes the command leaves behind in its group are killed when it exits.
    """
    logs = _mkdir(logs)
    out_path, err_path = logs / f"{name}.stdout", logs / f"{name}.stderr"
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    t0 = time.monotonic()
    timed_out = False
    with open(out_path, "wb") as out, open(err_path, "wb") as err:
        try:
            proc = subprocess.Popen(list(argv), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=err, start_new_session=True)
        except OSError as e:
            err.write(f"could not start {list(argv)}: {e}\n".encode())
            return CommandResult(tuple(argv), None, False, started,
                                 round(time.monotonic() - t0, 3), out_path, err_path,
                                 error=str(e))
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _signal_group(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
        finally:
            # Also runs on KeyboardInterrupt; leaves nothing of the group behind.
            _signal_group(proc.pid, signal.SIGKILL)
            proc.wait()
    return CommandResult(tuple(argv), proc.returncode, timed_out, started,
                         round(time.monotonic() - t0, 3), out_path, err_path)


def load(root: str | Path) -> Scenario:
    """Read and check ``root/scenario.json``. Raises ScenarioError on any mismatch."""
    root = Path(root).resolve()
    path = root / CONFIG_NAME
    try:
        config = json.loads(path.read_text())
    except OSError as e:
        raise ScenarioError(f"{path}: {e}") from None
    except ValueError as e:
        raise ScenarioError(f"{path}: not valid JSON: {e}") from None
    if not isinstance(config, dict):
        raise ScenarioError(f"{path}: must be a JSON object")
    unknown = set(config) - TOP_LEVEL_KEYS - OPTIONAL_TOP_LEVEL_KEYS
    if unknown:
        raise ScenarioError(f"unknown keys: {sorted(unknown)}")
    missing = TOP_LEVEL_KEYS - set(config)
    if missing:
        raise ScenarioError(f"missing keys: {sorted(missing)}")

    tasks = _tasks(config["tasks"], root)
    practice, exam = _split(config["split"], tasks)
    _no_shared_files(tasks, practice, exam, root)
    _score_spec(config["score"])
    return Scenario(root=root, config=config, tasks=tasks, practice=practice, exam=exam,
                    commands=_commands(config["commands"]), initial_harness=_harness(
                        config["initial_harness"], root), limits=_limits(config["limits"]),
                    models=_models(config["models"]), developer=_developer(config["developer"]),
                    exam_settings=_exam(config["exam"], root),
                    solver_settings=_solver_settings(config.get("solver_settings")))


def _tasks(entries, root: Path) -> dict[str, Task]:
    if not isinstance(entries, list) or not entries:
        raise ScenarioError("tasks: must be a non-empty list")
    tasks: dict[str, Task] = {}
    for e in entries:
        if isinstance(e, str):
            e = {"id": e}
        if not isinstance(e, dict) or set(e) - {"id", "group", "files"}:
            raise ScenarioError(f"tasks: bad entry {e!r}")
        tid, group, files = e.get("id"), e.get("group", e.get("id")), e.get("files")
        if not isinstance(tid, str) or not _TASK_ID.fullmatch(tid):
            raise ScenarioError(f"tasks: bad id {tid!r} (letters, digits, '.', '_', '-')")
        if tid in tasks:
            raise ScenarioError(f"tasks: duplicate id {tid!r}")
        if not isinstance(group, str) or not group:
            raise ScenarioError(f"tasks: {tid}: bad group {group!r}")
        if files is not None:
            if not isinstance(files, str):
                raise ScenarioError(f"tasks: {tid}: files must be a path")
            files = (root / files).resolve()
            if not files.is_dir():
                raise ScenarioError(f"tasks: {tid}: files directory {files} not found")
        tasks[tid] = Task(tid, group, files)
    return tasks


def _split(split, tasks: dict[str, Task]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not isinstance(split, dict) or set(split) != {"practice", "exam"}:
        raise ScenarioError("split: must have exactly the keys 'practice' and 'exam'")
    seen: dict[str, str] = {}
    for side in ("practice", "exam"):
        ids = split[side]
        if not isinstance(ids, list) or not ids:
            raise ScenarioError(f"split: {side} must be a non-empty list of task ids")
        for tid in ids:
            if tid not in tasks:
                raise ScenarioError(f"split: {side}: unknown task {tid!r}")
            if tid in seen:
                raise ScenarioError(f"split: task {tid!r} listed twice")
            seen[tid] = side
    left_out = [t for t in tasks if t not in seen]
    if left_out:
        raise ScenarioError(f"split: tasks on neither side: {left_out}")
    sides_of_group: dict[str, set[str]] = {}
    for tid, side in seen.items():
        sides_of_group.setdefault(tasks[tid].group, set()).add(side)
    torn = sorted(g for g, s in sides_of_group.items() if len(s) > 1)
    if torn:
        raise ScenarioError(f"split: related tasks on both sides, groups {torn}")
    return tuple(split["practice"]), tuple(split["exam"])


def _no_shared_files(tasks: dict[str, Task], practice, exam, root: Path) -> None:
    """A practice task's files must not reveal the scenario directory or any exam task's files."""
    exam_files = [tasks[t].files for t in exam if tasks[t].files is not None]
    for tid in practice:
        f = tasks[tid].files
        if f is None:
            continue
        if f == root or f in root.parents:
            raise ScenarioError(f"tasks: {tid}: files {f} contains the scenario directory")
        for g in exam_files:
            if f == g or f in g.parents or g in f.parents:
                raise ScenarioError(f"tasks: {tid}: files {f} overlaps exam files {g}")


def _commands(commands) -> dict[str, tuple[str, ...]]:
    if (not isinstance(commands, dict) or not set(COMMANDS) <= set(commands)
            or set(commands) - set(COMMANDS) - set(OPTIONAL_COMMANDS)):
        raise ScenarioError(f"commands: must have the keys {list(COMMANDS)}, "
                            f"optionally {list(OPTIONAL_COMMANDS)}")
    out = {}
    for name in commands:
        argv = commands[name]
        if not isinstance(argv, list) or not argv or not all(
                isinstance(a, str) and a for a in argv):
            raise ScenarioError(f"commands: {name} must be a non-empty list of strings")
        out[name] = tuple(argv)
    return out


def _score_spec(spec) -> None:
    bad = ScenarioError('score: must be {"file": <relative path in the score output>, '
                        '"key": <optional dotted key into that JSON file>}')
    if not isinstance(spec, dict) or set(spec) - {"file", "key"}:
        raise bad
    f, key = spec.get("file"), spec.get("key", "x")
    if not isinstance(f, str) or not f or Path(f).is_absolute() or ".." in Path(f).parts:
        raise bad
    if not isinstance(key, str) or not key:
        raise bad


def check_limits(limits) -> dict:
    """Check a ``limits`` object as ``load`` does; raises ScenarioError."""
    return _limits(limits)


def _limits(limits) -> dict:
    if not isinstance(limits, dict) or set(limits) != set(LIMIT_KEYS):
        raise ScenarioError(f"limits: must have exactly the keys {list(LIMIT_KEYS)}")
    out = {}
    for k, v in limits.items():
        if k == "parallel_runs":
            if not (_positive(v) and isinstance(v, int)):
                raise ScenarioError("limits: parallel_runs must be a positive integer")
            out[k] = v
            continue
        if k.endswith("_seconds"):
            if v is not None and not _positive(v):
                raise ScenarioError(f"limits: {k} must be a positive number of seconds or null")
            out[k] = v
            continue
        v = {} if v is None else v
        if not isinstance(v, dict) or set(v) - set(CAP_KEYS) or not all(
                _positive(n) and isinstance(n, int) for n in v.values()):
            raise ScenarioError(f"limits: {k} must map some of {list(CAP_KEYS)} "
                                "to positive integers (or be null)")
        out[k] = dict(v)
    return out


def _positive(v) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and v > 0


def _models(models) -> dict:
    if not isinstance(models, dict):
        raise ScenarioError("models: must be an object")
    missing = set(MODEL_KEYS) - set(models)
    unknown = set(models) - set(MODEL_KEYS) - set(MODEL_OPTIONAL)
    if missing or unknown:
        raise ScenarioError(f"models: missing {sorted(missing)}, unknown {sorted(unknown)}")
    for k, v in models.items():
        t = MODEL_KEYS.get(k) or MODEL_OPTIONAL[k]
        if not isinstance(v, t) or isinstance(v, bool) or (t is str and not v and k not in (
                "upstream_auth_prefix",)):
            raise ScenarioError(f"models: {k} must be a {t.__name__}")
    return dict(models)


def _developer(dev) -> dict:
    if not isinstance(dev, dict) or set(dev) - {"command", *DEVELOPER_OPTIONAL}:
        raise ScenarioError(f"developer: must have 'command' and optionally "
                            f"{sorted(DEVELOPER_OPTIONAL)}")
    cmd = dev.get("command")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) and a for a in cmd):
        raise ScenarioError("developer: command must be a non-empty list of strings")
    for k, t in DEVELOPER_OPTIONAL.items():
        if k in dev and (isinstance(dev[k], bool) or not isinstance(dev[k], t)):
            raise ScenarioError(f"developer: {k} has the wrong type")
    return dict(dev)


def _exam(exam, root: Path) -> dict:
    if not isinstance(exam, dict) or set(exam) != set(EXAM_KEYS):
        raise ScenarioError(f"exam: must have exactly the keys {list(EXAM_KEYS)}")
    compare = exam["compare"]
    if not isinstance(compare, dict):
        raise ScenarioError("exam: compare must map names to harness folders")
    folders = {}
    for name, rel in compare.items():
        if not _TASK_ID.fullmatch(name) or name == DELIVERED:
            raise ScenarioError(f"exam: compare: bad name {name!r} (letters, digits, '.', '_', "
                                f"'-'; {DELIVERED!r} is taken by the delivered harness)")
        if not isinstance(rel, str) or not rel:
            raise ScenarioError(f"exam: compare: {name} must be a path")
        path = (root / rel).resolve()
        if root not in path.parents:
            raise ScenarioError(f"exam: compare: {name}: {path} is not inside the scenario "
                                "directory")
        folders[name] = path  # looked at when the exam starts, see Scenario.compare_folder
    if not (_positive(exam["repeats"]) and isinstance(exam["repeats"], int)):
        raise ScenarioError("exam: repeats must be a positive integer")
    if exam["aggregate"] not in AGGREGATES:
        raise ScenarioError(f"exam: aggregate must be one of {list(AGGREGATES)}")
    f = exam["failure_score"]
    if isinstance(f, bool) or not isinstance(f, (int, float)) or not math.isfinite(f):
        raise ScenarioError("exam: failure_score must be a number")
    r = exam["invalid_reruns"]
    if isinstance(r, bool) or not isinstance(r, int) or r < 0:
        raise ScenarioError("exam: invalid_reruns must be an integer, 0 or more")
    if not (_positive(exam["uses"]) and isinstance(exam["uses"], int)):
        raise ScenarioError("exam: uses must be a positive integer")
    return {**exam, "compare": folders}


def _solver_settings(settings) -> dict:
    settings = {} if settings is None else settings
    if not isinstance(settings, dict) or not all(
            isinstance(k, str) and _SETTING_NAME.fullmatch(k) and isinstance(v, str)
            for k, v in settings.items()):
        raise ScenarioError("solver_settings: must map names (letters, digits, '_', not "
                            "starting with a digit) to strings")
    return dict(settings)


def _harness(rel, root: Path) -> Path:
    if not isinstance(rel, str):
        raise ScenarioError("initial_harness: must be a path")
    return _harness_at((root / rel).resolve(), "initial_harness")


def _harness_at(path: Path, what: str) -> Path:
    start = path / START_NAME
    if not start.is_file() or not os.access(start, os.X_OK):
        raise ScenarioError(f"{what}: {start} missing or not executable")
    return path


def _mkdir(p: Path) -> Path:
    p = Path(p).absolute()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _existing_dir(p: Path) -> Path:
    p = Path(p).absolute()
    if not p.is_dir():
        raise ScenarioError(f"{p}: not a directory")
    return p


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass
