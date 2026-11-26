"""Where the developer agent runs (rule 1).

Two interchangeable implementations:

- ``ContainerSandbox`` (real use): a Docker container with no network at all
  (``--network none``). Model calls reach the model exit through its Unix socket,
  mounted into the container and exposed on 127.0.0.1 by a small forwarder.
  Mounts: materials, practice-task files, run records and harness versions
  read-only; the workspace and the request directory writable; replies read-only.
  The container runs as the calling user, so files it writes stay ours.
- ``LocalSandbox`` (tests that do not concern isolation): the same command as a
  plain process on the host, with the same environment variables.

Inside, the agent finds these variables: HB_BRIEF, HB_WORKSPACE, HB_MATERIALS,
HB_TASKS, HB_RECORDS, HB_VERSIONS, HB_OPS, HB_MODEL_URL, HB_MODEL_KEY, HB_MODEL,
HB_PYTHON, HB_SANDBOX; and ``hb`` on its PATH.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from .scenario import CommandResult, call

PACKAGE = Path(__file__).parent
FORWARD_PORT = 8790  # inside the container's own network namespace; nothing else uses it

HB_WRAPPER = """#!/bin/sh
exec "${HB_PYTHON:-python3}" "$(dirname "$0")/hb_client.py" "$@"
"""

HB_START = """#!/bin/sh
# Start of every sandbox session: put hb on PATH, start the model forwarder if the
# model exit is a socket, then run the developer agent's command.
if [ -z "$HB_PYTHON" ]; then HB_PYTHON=$(command -v python3); export HB_PYTHON; fi
PATH="$HB_BIN:$PATH"; export PATH
if [ -n "$HB_MODEL_SOCKET" ]; then
  "$HB_PYTHON" "$HB_BIN/hb_forward.py" "$HB_MODEL_SOCKET" "$HB_FORWARD_PORT" &
  i=0
  while [ $i -lt 100 ]; do
    "$HB_PYTHON" -c "import socket,sys; socket.create_connection(('127.0.0.1', int(sys.argv[1])), 1)" "$HB_FORWARD_PORT" 2>/dev/null && break
    i=$((i+1)); sleep 0.1
  done
fi
exec "$@"
"""


@dataclass
class Layout:
    """Host-side directories of one session."""
    bin: Path          # hb, hb_client.py, hb_forward.py, hb-start
    brief: Path        # the initial instructions (a file)
    requests: Path
    responses: Path
    materials: Path
    tasks: Path        # one subdirectory per practice task
    records: Path      # the dev area: runs/, scores/, enters/
    versions: Path
    workspace: Path
    socket_dir: Path | None = None


def install_bin(bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, text in (("hb", HB_WRAPPER), ("hb-start", HB_START)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    shutil.copy(PACKAGE / "client.py", bin_dir / "hb_client.py")
    shutil.copy(PACKAGE / "forward.py", bin_dir / "hb_forward.py")


class LocalSandbox:
    kind = "local"
    needs_socket = False

    def __init__(self, python: str | None = None):
        import sys
        self.python = python or sys.executable

    def view(self, lay: Layout) -> dict:
        return {"brief": str(lay.brief), "workspace": str(lay.workspace),
                "materials": str(lay.materials), "tasks": str(lay.tasks),
                "records": str(lay.records), "versions": str(lay.versions),
                "ops": str(lay.requests.parent), "bin": str(lay.bin)}

    def run(self, lay: Layout, command: list[str], env: dict, model_url: str,
            logs: Path, timeout: float | None, hidden_env: tuple[str, ...] = ()) -> dict:
        v = self.view(lay)
        full = {k: val for k, val in os.environ.items()
                if not k.startswith("HB_") and k not in hidden_env}
        full.update(env)
        full.update({"HB_" + k.upper(): val for k, val in v.items()})
        full.update(HB_MODEL_URL=model_url, HB_PYTHON=self.python, HB_SANDBOX=self.kind)
        r = call(["sh", str(lay.bin / "hb-start"), *command], cwd=lay.workspace, env=full,
                 logs=logs, name="agent", timeout=timeout)
        return {"result": r, "detail": {"kind": self.kind}}


class ContainerSandbox:
    kind = "container"
    needs_socket = True

    def __init__(self, image: str, *, python: str | None = None, memory: str | None = None,
                 cpus: float | None = None, pids_limit: int | None = None,
                 docker: str = "docker"):
        self.image, self.python, self.docker = image, python, docker
        self.memory, self.cpus, self.pids_limit = memory, cpus, pids_limit

    @staticmethod
    def view(lay: Layout) -> dict:
        return {"brief": "/hb/BRIEF.md", "workspace": "/workspace", "materials": "/materials",
                "tasks": "/tasks", "records": "/records", "versions": "/versions",
                "ops": "/hb-ops", "bin": "/hb/bin"}

    def docker_argv(self, lay: Layout, command: list[str], env_names: list[str],
                    name: str) -> list[str]:
        argv = [self.docker, "run", "--rm", "--init", "--name", name, "--network", "none",
                "--user", f"{os.getuid()}:{os.getgid()}"]
        if self.memory:
            argv += ["--memory", str(self.memory)]
        if self.cpus:
            argv += ["--cpus", str(self.cpus)]
        if self.pids_limit:
            argv += ["--pids-limit", str(self.pids_limit)]
        mounts = [(lay.bin, "/hb/bin", "ro"), (lay.brief, "/hb/BRIEF.md", "ro"),
                  (lay.requests, "/hb-ops/requests", "rw"),
                  (lay.responses, "/hb-ops/responses", "ro"),
                  (lay.materials, "/materials", "ro"), (lay.tasks, "/tasks", "ro"),
                  (lay.records, "/records", "ro"), (lay.versions, "/versions", "ro"),
                  (lay.workspace, "/workspace", "rw")]
        if lay.socket_dir is not None:
            mounts.append((lay.socket_dir, "/hb-sock", "rw"))
        for host, inside, mode in mounts:
            argv += ["-v", f"{Path(host).resolve()}:{inside}:{mode}"]
        argv += ["-w", "/workspace"]
        for k in env_names:  # values travel in the docker client's environment, not in argv
            argv += ["-e", k]
        argv += ["--entrypoint", "sh", self.image, "/hb/bin/hb-start", *command]
        return argv

    def run(self, lay: Layout, command: list[str], env: dict, model_url: str,
            logs: Path, timeout: float | None, hidden_env: tuple[str, ...] = ()) -> dict:
        inside = {"HB_" + k.upper(): val for k, val in self.view(lay).items()}
        inside.update(env)
        inside.update(HB_MODEL_URL=f"http://127.0.0.1:{FORWARD_PORT}",
                      HB_MODEL_SOCKET="/hb-sock/model.sock",
                      HB_FORWARD_PORT=str(FORWARD_PORT), HB_SANDBOX=self.kind,
                      HOME="/workspace/.home")
        if self.python:
            inside["HB_PYTHON"] = self.python
        client_env = {k: v for k, v in os.environ.items()
                      if not k.startswith("HB_") and k not in hidden_env}
        client_env.update(inside)
        name = f"hb-dev-{uuid.uuid4().hex[:12]}"
        argv = self.docker_argv(lay, command, sorted(inside), name)
        (lay.workspace / ".home").mkdir(exist_ok=True)
        try:
            r = call(argv, cwd=lay.workspace, env=client_env, logs=logs, name="agent",
                     timeout=timeout)
        finally:
            subprocess.run([self.docker, "rm", "-f", name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        return {"result": r, "detail": {"kind": self.kind, "image": self.image,
                                        "container": name, "network": "none"}}
