"""Shared by the ALW scenario's commands (run, score, enter, invalid, prepare).

Site facts (where the benchmark checkout is, which image, which programs) are read
from ``site.json`` next to this file; ``prepare.py`` writes it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
NO_PROXY = "host.docker.internal,172.17.0.1,localhost,127.0.0.1,::1,0.0.0.0"
# what the launching shell may hand on to Harbor / docker; model credentials never do
HOST_ENV_ALLOW = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "TMPDIR", "DOCKER_HOST",
                  "DOCKER_CONFIG", "VIRTUAL_ENV")
_PROXY = re.compile(r"(?i)^.*_proxy$")


def die(msg: str, code: int = 2):
    sys.stderr.write(f"alw scenario: {msg}\n")
    sys.exit(code)


def site() -> dict:
    p = HERE / "site.json"
    if not p.is_file():
        die(f"{p} is missing; run prepare.py first")
    return json.loads(p.read_text())


def need(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        die(f"{name} is not set")
    return v


def task_dir(s: dict, task: str) -> Path:
    d = Path(s["alw_repo"]) / "tasks" / task
    if not (d / "task.toml").is_file():
        die(f"no task {task} under {s['alw_repo']}/tasks")
    return d


def task_toml(s: dict, task: str) -> dict:
    return tomllib.loads((task_dir(s, task) / "task.toml").read_text())


def host_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k in HOST_ENV_ALLOW and not _PROXY.match(k)}
    env.update(extra or {})
    return env


def chown_back(s: dict, path: Path) -> None:
    """Containers write as root; hand the files back to the invoking user (best effort)."""
    try:
        subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh",
                        "-v", f"{Path(path).resolve()}:/x", s["agent_image"], "-c",
                        f"chown -R {os.getuid()}:{os.getgid()} /x; chmod -R u+rwX /x"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        pass


def remove_containers(label: str) -> None:
    try:
        ids = subprocess.run(["docker", "ps", "-aq", "--filter", f"label={label}"],
                             capture_output=True, text=True, timeout=60).stdout.split()
        if ids:
            subprocess.run(["docker", "rm", "-f", *ids], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        pass


if __name__ == "__main__" and sys.argv[1:2] == ["cleanup"]:  # detached, see run.py
    import time
    time.sleep(15)  # let Harbor's own teardown go first
    remove_containers(sys.argv[2])
    chown_back(site(), Path(sys.argv[3]))
